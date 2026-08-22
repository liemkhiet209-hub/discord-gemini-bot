import os
import io
import re
import time
import random
import asyncio
import threading
from collections import defaultdict
from http.server import HTTPServer, BaseHTTPRequestHandler
import discord
from discord.ext import commands
from groq import AsyncGroq
from google import genai
from PIL import Image

# 1. Cấu hình biến môi trường (Hỗ trợ nhiều key cách nhau bằng dấu phẩy)
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")

GROQ_KEYS_RAW = os.environ.get("GROQ_API_KEY", "")
GROQ_KEYS = [k.strip() for k in GROQ_KEYS_RAW.split(",") if k.strip()]

GEMINI_KEYS_RAW = os.environ.get("GEMINI_API_KEY", "")
GEMINI_KEYS = [k.strip() for k in GEMINI_KEYS_RAW.split(",") if k.strip()]

TARGET_CHANNEL = "hỏi-đáp-gemini"
USER_COOLDOWN_SECONDS = 5  # Thời gian chờ giữa 2 lần hỏi của 1 người

# 2. Web server mini giữ bot luôn thức trên Render
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is alive!")
    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()

def run_web():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
    server.serve_forever()

threading.Thread(target=run_web, daemon=True).start()

# 3. Khởi tạo Discord Bot
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True

bot = commands.Bot(command_prefix="!", intents=intents)
user_last_message_time = defaultdict(float)

# 4. Xử lý hỏi đáp Văn bản qua Groq Pool (Chịu tải 14.400 - 30.000 req/ngày)
async def ask_groq_text(prompt: str) -> str:
    if not GROQ_KEYS:
        raise ValueError("Chưa cấu hình GROQ_API_KEY!")

    # Xáo trộn danh sách key để chia đều tải
    shuffled_keys = GROQ_KEYS.copy()
    random.shuffle(shuffled_keys)
    last_err = None

    for key in shuffled_keys:
        try:
            client = AsyncGroq(api_key=key)
            chat_completion = await client.chat.completions.create(
                messages=[
                    {
                        "role": "system", 
                        "content": "Bạn là trợ lý AI thông minh, thân thiện. Hãy trả lời ngắn gọn, chuẩn xác, tự nhiên bằng tiếng Việt."
                    },
                    {"role": "user", "content": prompt}
                ],
                model="llama-3.1-8b-instant",
            )
            raw = chat_completion.choices[0].message.content
            return re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip() or raw
        except Exception as e:
            last_err = e
            continue
    raise last_err

# 5. Xử lý đọc Hình ảnh qua Gemini 3.6 Pool (Tự động đổi Key khi hết lượt)
async def ask_gemini_vision(pil_image, prompt: str) -> str:
    if not GEMINI_KEYS:
        raise ValueError("Chưa cấu hình GEMINI_API_KEY!")

    text_prompt = prompt if prompt else "Hãy phân tích và đọc chi tiết nội dung trong hình ảnh này."
    last_err = None

    for key in GEMINI_KEYS:
        try:
            client = genai.Client(api_key=key)
            res = await asyncio.to_thread(
                client.models.generate_content,
                model="gemini-3.6-flash",
                contents=[pil_image, text_prompt]
            )
            if res and res.text:
                return res.text
        except Exception as e:
            last_err = e
            continue
    raise last_err

@bot.event
async def on_ready():
    print(f"--> Bot đã sẵn sàng phục vụ server đông người: {bot.user}")

@bot.event
async def on_thread_create(thread):
    try:
        parent_name = getattr(thread.parent, "name", "").lower()
        if parent_name == TARGET_CHANNEL.lower() or "hỏi-đáp" in parent_name or "gemini" in parent_name:
            owner_id = thread.owner_id
            mention_str = f"<@{owner_id}>" if owner_id else "bạn"
            await thread.send(f"Xin chào {mention_str}! Bạn có cần giúp đỡ gì không? Hãy đặt câu hỏi hoặc gửi ảnh bên dưới nhé!")
    except Exception as e:
        print(f"Lỗi gửi tin chào: {e}")

@bot.event
async def on_message(message):
    if message.author.bot:
        return

    channel_name = getattr(message.channel, "name", "").lower()
    parent_name = ""
    if isinstance(message.channel, discord.Thread) and message.channel.parent:
        parent_name = message.channel.parent.name.lower()

    is_in_target = (
        channel_name == TARGET_CHANNEL.lower()
        or parent_name == TARGET_CHANNEL.lower()
        or "hỏi-đáp-gemini" in channel_name
        or "hỏi-đáp-gemini" in parent_name
    )
    is_mentioned = bot.user in message.mentions

    if is_in_target or is_mentioned:
        # Kiểm tra cooldown chống spam từng cá nhân
        user_id = message.author.id
        current_time = time.time()
        elapsed = current_time - user_last_message_time[user_id]
        if elapsed < USER_COOLDOWN_SECONDS:
            remaining = int(USER_COOLDOWN_SECONDS - elapsed)
            await message.reply(f"⏳ Vui lòng chờ `{remaining}s` trước khi gửi câu hỏi tiếp theo nhé!", delete_after=4)
            return

        prompt = message.content.replace(f"<@{bot.user.id}>", "").strip()
        image_att = next(
            (a for a in message.attachments if a.content_type and a.content_type.startswith("image/")), 
            None
        )

        if not prompt and not image_att:
            return

        user_last_message_time[user_id] = current_time

        async with message.channel.typing():
            try:
                if image_att:
                    # Gửi qua luồng Vision (Gemini)
                    img_bytes = await image_att.read()
                    pil_img = Image.open(io.BytesIO(img_bytes))
                    answer = await ask_gemini_vision(pil_img, prompt)
                else:
                    # Gửi qua luồng Text tốc độ cao (Groq)
                    answer = await ask_groq_text(prompt)

                if len(answer) <= 2000:
                    await message.reply(answer)
                else:
                    for chunk in [answer[i:i+1900] for i in range(0, len(answer), 1900)]:
                        await message.channel.send(chunk)

            except Exception as e:
                print(f"Lỗi xử lý AI: {e}")
                await message.reply("⚠️ Hệ thống AI hiện đang xử lý quá tải, vui lòng thử lại sau giây lát!")

    await bot.process_commands(message)

if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)
