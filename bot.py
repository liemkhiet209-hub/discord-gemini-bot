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
from openai import AsyncOpenAI
from PIL import Image

# 1. Cấu hình biến môi trường
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")
GROQ_KEYS = [k.strip() for k in os.environ.get("GROQ_API_KEY", "").split(",") if k.strip()]
GEMINI_KEYS = [k.strip() for k in os.environ.get("GEMINI_API_KEY", "").split(",") if k.strip()]
OPENROUTER_KEY = os.environ.get("OPENROUTER_API_KEY", "").strip()

TARGET_CHANNEL = "hỏi-đáp-gemini"
USER_COOLDOWN = 3

# Bộ nhớ hội thoại (8 tin nhắn gần nhất mỗi kênh)
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

# 4. Tự động lấy danh sách Model Groq ĐANG HOẠT ĐỘNG
async def get_working_groq_model(client: AsyncGroq) -> str:
    try:
        models = await client.models.list()
        active_ids = [m.id for m in models.data if "whisper" not in m.id and "guard" not in m.id]
        for pref in ["llama-3.3-70b-versatile", "llama-3.1-8b-instant"]:
            if pref in active_ids:
                return pref
        if active_ids:
            return active_ids[0]
    except Exception:
        pass
    return "llama-3.1-8b-instant"

# 5. Xử lý câu hỏi văn bản qua Groq Pool
async def ask_groq_text(channel_id: int, prompt: str) -> str:
    if not GROQ_KEYS:
        raise ValueError("Chưa cấu hình biến GROQ_API_KEY trên Render!")

    messages = [
        {"role": "system", "content": "Bạn là trợ lý AI thông minh, hỗ trợ nhiệt tình. Hãy trả lời ngắn gọn, tự nhiên, đúng trọng tâm bằng tiếng Việt."}
    ]
    messages.extend(conversation_history[channel_id])
    messages.append({"role": "user", "content": prompt})

    keys = GROQ_KEYS.copy()
    random.shuffle(keys)
    last_err = None

    for key in keys:
        try:
            client = AsyncGroq(api_key=key)
            model_name = await get_working_groq_model(client)
            chat_completion = await client.chat.completions.create(
                messages=messages,
                model=model_name,
            )
            raw = chat_completion.choices[0].message.content
            answer = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip() or raw

            conversation_history[channel_id].append({"role": "user", "content": prompt})
            conversation_history[channel_id].append({"role": "assistant", "content": answer})
            if len(conversation_history[channel_id]) > 8:
                conversation_history[channel_id] = conversation_history[channel_id][-8:]

            return answer
        except Exception as e:
            last_err = e
            continue
    raise last_err

# 6. Xử lý đọc hình ảnh qua Gemini (Dự phòng sang OpenRouter nếu Gemini hết lượt 429)
async def ask_vision_multiprovider(channel_id: int, image_bytes: bytes, mime_type: str, prompt: str) -> str:
    text_prompt = prompt if prompt else "Hãy phân tích và đọc chi tiết nội dung trong hình ảnh này."
    pil_img = Image.open(io.BytesIO(image_bytes))

    # Thử qua từng Key Gemini
    for key in GEMINI_KEYS:
        try:
            client = genai.Client(api_key=key)
            res = await asyncio.to_thread(
                client.models.generate_content,
                model="gemini-3.6-flash",
                contents=[pil_img, text_prompt]
            )
            if res and res.text:
                answer = res.text
                conversation_history[channel_id].append({"role": "user", "content": f"[Ảnh]: {text_prompt}"})
                conversation_history[channel_id].append({"role": "assistant", "content": answer})
                if len(conversation_history[channel_id]) > 8:
                    conversation_history[channel_id] = conversation_history[channel_id][-8:]
                return answer
        except Exception as e:
            print(f"Gemini Key lỗi/hết hạn mức: {e}")
            continue

    # Nếu tất cả key Gemini đều hết lượt (429), chuyển sang OpenRouter Vision
    if OPENROUTER_KEY:
        try:
            import base64
            b64_img = base64.b64encode(image_bytes).decode("utf-8")
            or_client = AsyncOpenAI(base_url="https://openrouter.ai/api/v1", api_key=OPENROUTER_KEY)
            res = await or_client.chat.completions.create(
                model="qwen/qwen-2.5-vl-72b-instruct:free",
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": text_prompt},
                        {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{b64_img}"}}
                    ]
                }]
            )
            if res.choices and res.choices[0].message.content:
                answer = res.choices[0].message.content
                conversation_history[channel_id].append({"role": "user", "content": f"[Ảnh]: {text_prompt}"})
                conversation_history[channel_id].append({"role": "assistant", "content": answer})
                return answer
        except Exception as e:
            print(f"OpenRouter lỗi: {e}")

    raise ValueError("Tất cả các API Key đọc ảnh đều đã hết lượt hôm nay!")

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
            await message.reply(f"⏳ Vui lòng chờ `{rem}s` trước khi hỏi tiếp nhé!", delete_after=3)
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
                    mime_type = image_att.content_type or "image/jpeg"
                    answer = await ask_vision_multiprovider(channel_id, img_bytes, mime_type, prompt)
                else:
                    answer = await ask_groq_text(channel_id, prompt)

                if len(answer) <= 2000:
                    await message.reply(answer)
                else:
                    for chunk in [answer[i:i+1900] for i in range(0, len(answer), 1900)]:
                        await message.channel.send(chunk)

            except Exception as e:
                await message.reply(f"⚠️ **Lỗi:** `{e}`")

    await bot.process_commands(message)

if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)
