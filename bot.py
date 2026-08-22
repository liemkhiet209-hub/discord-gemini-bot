import os
import io
import asyncio
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
import discord
from discord.ext import commands
from google import genai
from PIL import Image

# Cấu hình biến môi trường
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
TARGET_CHANNEL = "hỏi-đáp-gemini"

# Web server mini giữ bot luôn hoạt động trên Render
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

# Cấu hình Discord Client
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True

bot = commands.Bot(command_prefix="!", intents=intents)

# Khởi tạo Google Gemini Client
ai_client = None
if GEMINI_API_KEY:
    ai_client = genai.Client(api_key=GEMINI_API_KEY)

# Hàm gọi Gemini với cơ chế dự phòng 2.0-flash và 1.5-flash (1.500 lượt/ngày)
async def generate_response(contents_payload):
    models = ["gemini-2.0-flash", "gemini-1.5-flash"]
    last_err = None

    for model_name in models:
        try:
            response = await asyncio.to_thread(
                ai_client.models.generate_content,
                model=model_name,
                contents=contents_payload
            )
            if response and response.text:
                return response.text
        except Exception as e:
            last_err = e
            continue
    raise last_err

@bot.event
async def on_ready():
    print(f"--> Bot đã online sẵn sàng: {bot.user}")

# Tự động gửi tin nhắn chào khi tạo bài viết mới
@bot.event
async def on_thread_create(thread):
    try:
        parent_name = getattr(thread.parent, "name", "").lower()
        if parent_name == TARGET_CHANNEL.lower() or "hỏi-đáp" in parent_name or "gemini" in parent_name:
            owner_id = thread.owner_id
            mention_str = f"<@{owner_id}>" if owner_id else "bạn"
            await thread.send(f"Xin chào {mention_str}! Bạn có cần giúp đỡ gì không? Hãy đặt câu hỏi hoặc gửi ảnh bên dưới nhé!")
            print(f"--> Đã gửi lời chào trong bài đăng: {thread.name}")
    except Exception as e:
        print(f"Lỗi gửi tin chào: {e}")

# Tự động đọc và trả lời (Cả Text & Hình Ảnh)
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
        if not ai_client:
            await message.reply("Lỗi: Chưa cấu hình GEMINI_API_KEY trên Render!")
            return

        prompt = message.content.replace(f"<@{bot.user.id}>", "").strip()

        # Kiểm tra file ảnh đính kèm
        image_attachment = next(
            (att for att in message.attachments if att.content_type and att.content_type.startswith("image/")), 
            None
        )

        if not prompt and not image_attachment:
            return

        async with message.channel.typing():
            try:
                # Chuẩn bị nội dung gửi cho AI
                contents_payload = []
                if image_attachment:
                    image_bytes = await image_attachment.read()
                    pil_image = Image.open(io.BytesIO(image_bytes))
                    contents_payload.append(pil_image)
                    text_prompt = prompt if prompt else "Hãy xem và đọc chi tiết nội dung trong hình ảnh này giúp tôi."
                    contents_payload.append(text_prompt)
                else:
                    contents_payload.append(prompt)

                # Gọi AI
                answer = await generate_response(contents_payload)

                if len(answer) <= 2000:
                    await message.reply(answer)
                else:
                    for chunk in [answer[i:i+1900] for i in range(0, len(answer), 1900)]:
                        await message.channel.send(chunk)

            except Exception as e:
                print(f"Lỗi Gemini: {e}")
                await message.reply(f"Lỗi phản hồi AI: {e}")

    await bot.process_commands(message)

if __name__ == "__main__":
    if not DISCORD_TOKEN:
        print("LỖI: Thiếu DISCORD_TOKEN trong Environment Variables!")
    else:
        bot.run(DISCORD_TOKEN)
