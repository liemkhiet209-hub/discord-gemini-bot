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
USER_COOLDOWN = 4

# Bộ nhớ lưu 8 tin nhắn gần nhất cho mỗi Thread/Channel
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

# 4. Hàm gọi Groq có kèm toàn bộ lịch sử trò chuyện
async def ask_groq_text(channel_id: int, new_prompt: str) -> str:
    if not GROQ_KEYS:
        raise ValueError("Chưa cấu hình GROQ_API_KEY!")

    # Xây dựng danh sách tin nhắn gửi cho Groq (System + Lịch sử + Câu hỏi mới)
    messages = [
        {
            "role": "system",
            "content": (
                "Bạn là trợ lý AI thông minh, thân thiện. "
                "Hãy trả lời tự nhiên, chuẩn xác bằng tiếng Việt và theo sát ngữ cảnh của cuộc hội thoại."
            )
        }
    ]
    
    # Kèm lịch sử các câu hỏi trước (bao gồm cả các câu Gemini đã trả lời)
    messages.extend(conversation_history[channel_id])
    messages.append({"role": "user", "content": new_prompt})

    shuffled_keys = GROQ_KEYS.copy()
    random.shuffle(shuffled_keys)
    last_err = None

    for key in shuffled_keys:
        try:
            client = AsyncGroq(api_key=key)
            chat_completion = await client.chat.completions.create(
                messages=messages,
                model="llama-3.1-8b-instant",
            )
            raw = chat_completion.choices[0].message.content
            answer = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip() or raw
            
            # Cập nhật câu hỏi và câu trả lời vào lịch sử
            conversation_history[channel_id].append({"role": "user", "content": new_prompt})
            conversation_history[channel_id].append({"role": "assistant", "content": answer})
            if len(conversation_history[channel_id]) > 8:
                conversation_history[channel_id] = conversation_history[channel_id][-8:]
                
            return answer
        except Exception as e:
            last_err = e
            continue
    raise last_err

# 5. Hàm gọi Gemini Vision khi có ảnh
async def ask_gemini_vision(channel_id: int, pil_image, prompt: str) -> str:
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
                answer = res.text
                
                # Lưu thông tin ảnh và câu trả lời của Gemini vào lịch sử để Groq đọc sau này
                conversation_history[channel_id].append({"role": "user", "content": f"[Người dùng đã gửi một hình ảnh]: {text_prompt}"})
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
    print(f"--> Bot đã online với bộ nhớ đồng bộ: {bot.user}")

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
                    # Gửi qua Gemini đọc ảnh và lưu vào memory
                    img_bytes = await image_att.read()
                    pil_img = Image.open(io.BytesIO(img_bytes))
                    answer = await ask_gemini_vision(channel_id, pil_img, prompt)
                else:
                    # Gửi qua Groq kèm toàn bộ memory của Gemini
                    answer = await ask_groq_text(channel_id, prompt)

                if len(answer) <= 2000:
                    await message.reply(answer)
                else:
                    for chunk in [answer[i:i+1900] for i in range(0, len(answer), 1900)]:
                        await message.channel.send(chunk)

            except Exception as e:
                print(f"Lỗi phản hồi: {e}")
                await message.reply("⚠️ Đã xảy ra lỗi khi xử lý, vui lòng thử lại sau giây lát!")

    await bot.process_commands(message)

if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)
