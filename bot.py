import os
import asyncio
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
import discord
from discord.ext import commands
from google import genai

# Lấy token từ biến môi trường
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
TARGET_CHANNEL = "hỏi-đáp-gemini"

# Web server duy trì kết nối cho Render
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

# Cấu hình Discord Client & Gemini Client
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True

ai_client = genai.Client(api_key=GEMINI_API_KEY)
bot = commands.Bot(command_prefix="!", intents=intents)

# Gọi AI theo cơ chế bất đồng bộ không làm lag bot
async def get_gemini_response(prompt: str) -> str:
    models = ["gemini-3.6-flash", "gemini-2.5-flash", "gemini-1.5-flash"]
    
    for model_name in models:
        try:
            # Chạy hàm gọi API ở luồng riêng để tránh khóa Event Loop
            response = await asyncio.to_thread(
                ai_client.models.generate_content,
                model=model_name,
                contents=prompt
            )
            if response and response.text:
                return response.text
        except Exception:
            await asyncio.sleep(0.5)
            continue

    return "Hệ thống AI hiện đang bận phản hồi nhiều người cùng lúc. Bạn vui lòng gửi lại câu hỏi sau vài giây nhé!"

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
            await thread.send(f"Xin chào {mention_str}! Bạn có cần giúp đỡ gì không? Hãy đặt câu hỏi bên dưới nhé!")
    except Exception as e:
        print(f"Lỗi khi gửi lời chào: {e}")

# Tự động đọc và trả lời câu hỏi
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
        prompt = message.content.replace(f"<@{bot.user.id}>", "").strip()

        if not prompt:
            return

        async with message.channel.typing():
            answer = await get_gemini_response(prompt)

            if len(answer) <= 2000:
                await message.reply(answer)
            else:
                for chunk in [answer[i:i+1900] for i in range(0, len(answer), 1900)]:
                    await message.channel.send(chunk)

    await bot.process_commands(message)

if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)
