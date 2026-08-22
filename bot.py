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

# 1. Biến môi trường
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")
GROQ_KEYS = [k.strip() for k in os.environ.get("GROQ_API_KEY", "").split(",") if k.strip()]
GEMINI_KEYS = [k.strip() for k in os.environ.get("GEMINI_API_KEY", "").split(",") if k.strip()]
TARGET_CHANNEL = "hỏi-đáp-gemini"
USER_COOLDOWN = 3

# Bộ nhớ ngữ cảnh (lưu 8 tin nhắn gần nhất mỗi kênh)
conversation_history = defaultdict(list)
user_last_time = defaultdict(float)

# 2. Web server duy trì bot trên Render
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

# 3. Khởi tạo bot Discord
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True

bot = commands.Bot(command_prefix="!", intents=intents)

# Danh sách model Groq hoạt động ổn định
GROQ_MODELS = [
    "llama-3.3-70b-versatile",
    "llama-3.1-8b-instant",
    "gemma2-9b-it"
]

# 4. Xử lý câu hỏi chữ qua Groq (Kèm lịch sử)
async def ask_groq_text(channel_id: int, new_prompt: str) -> str:
    if not GROQ_KEYS:
        raise ValueError("Chưa cấu hình biến `GROQ_API_KEY` trên Render!")

    messages = [
        {
            "role": "system",
            "content": "Bạn là trợ lý AI thông minh, thân thiện. Hãy trả lời ngắn gọn, chuẩn xác, tự nhiên bằng tiếng Việt theo sát ngữ cảnh."
        }
    ]
    messages.extend(conversation_history[channel_id])
    messages.append({"role": "user", "content": new_prompt})

    shuffled_keys = GROQ_KEYS.copy()
    random.shuffle(shuffled_keys)
    last_err = None

    for key in shuffled_keys:
        client = AsyncGroq(api_key=key)
        for model_name in GROQ_MODELS:
            try:
                chat_completion = await client.chat.completions.create(
                    messages=messages,
                    model=model_name,
                )
                raw = chat_completion.choices[0].message.content
                answer = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip() or raw
                
                # Lưu vào bộ nhớ ngữ cảnh
                conversation_history[channel_id].append({"role": "user", "content": new_prompt})
                conversation_history[channel_id].append({"role": "assistant", "content": answer})
                if len(conversation_history[channel_id]) > 8:
                    conversation_history[channel_id] = conversation_history[channel_id][-8:]
                    
                return answer
            except Exception as e:
                last_err = e
                continue
    raise last_err

# 5. Xử lý đọc hình ảnh qua Gemini (Lưu vào bộ nhớ chung)
async def ask_gemini_vision(channel_id: int, pil_image, prompt: str) -> str:
    if not GEMINI_KEYS:
        raise ValueError("Chưa cấu hình biến `GEMINI_API_KEY` trên Render!")

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
                answer = res.text
                conversation_history[channel_id].append({"role": "user", "content": f"[Ảnh đính kèm]: {text_prompt}"})
                conversation_history[channel_id].append({"role": "assistant", "content": answer})
                if len(conversation_history[channel_id]) > 8:
                    conversation_history[channel_id] = conversation_history[channel_id][-8:]
                return answer
        except Exception as e:
            last_err = e
            continue
    raise last_err

@bot.event
async def on_ready():
    print(f"--> Bot đã online sẵn sàng: {bot.user}")

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
        user_id = message.author.id
        now = time.time()
        if now - user_last_time[user_id] < USER_COOLDOWN:
            rem = int(USER_COOLDOWN - (now - user_last_time[user_id]))
            await message.reply(f"⏳ Vui lòng chờ `{rem}s` trước khi gửi tiếp nhé!", delete_after=3)
            return

        prompt = message.content.replace(f"<@{bot.user.id}>", "").strip()
        image_att = next(
            (a for a in message.attachments if a.content_type and a.content_type.startswith("image/")), 
            None
        )

        if not prompt and not image_att:
            return

        user_last_time[user_id] = now
        channel_id = message.channel.id

        async with message.channel.typing():
            try:
                if image_att:
                    img_bytes = await image_att.read()
                    pil_img = Image.open(io.BytesIO(img_bytes))
                    answer = await ask_gemini_vision(channel_id, pil_img, prompt)
                else:
                    answer = await ask_groq_text(channel_id, prompt)

                if len(answer) <= 2000:
                    await message.reply(answer)
                else:
                    for chunk in [answer[i:i+1900] for i in range(0, len(answer), 1900)]:
                        await message.channel.send(chunk)

            except Exception as e:
                print(f"Chi tiết lỗi: {e}")
                await message.reply(f"⚠️ **Lỗi chi tiết:** `{e}`")

    await bot.process_commands(message)

if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)
