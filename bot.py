import os
import time
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
import discord
from discord.ext import commands
from google import genai

# Cấu hình từ biến môi trường
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

# Thiết lập Discord Bot và Gemini AI
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True

ai_client = genai.Client(api_key=GEMINI_API_KEY)
bot = commands.Bot(command_prefix="!", intents=intents)

# Hàm gọi Gemini AI có cơ chế tự động thử lại khi máy chủ quá tải
def generate_ai_response(prompt: str) -> str:
    models_to_try = ["gemini-3.6-flash", "gemini-2.5-flash", "gemini-1.5-flash"]
    last_error = None

    for model_name in models_to_try:
        for attempt in range(2):
            try:
                response = ai_client.models.generate_content(
                    model=model_name,
                    contents=prompt
                )
                if response and response.text:
                    return response.text
            except Exception as e:
                last_error = e
                time.sleep(1)  # Đợi 1 giây rồi thử lại
                continue

    raise last_error

@bot.event
async def on_ready():
    print(f"--> Bot đã online thành công: {bot.user}")

# Tự động chào khi tạo bài đăng mới trong hỏi-đáp-gemini
@bot.event
async def on_thread_create(thread):
    try:
        parent_name = getattr(thread.parent, "name", "").lower()
        if parent_name == TARGET_CHANNEL.lower() or "hỏi-đáp" in parent_name or "gemini" in parent_name:
            owner_id = thread.owner_id
            mention_str = f"<@{owner_id}>" if owner_id else "bạn"
            await thread.send(f"Xin chào {mention_str}! Bạn có cần giúp đỡ gì không? Hãy đặt câu hỏi chi tiết bên dưới nhé!")
            print(f"--> Đã gửi lời chào trong bài đăng: {thread.name}")
    except Exception as e:
        print(f"Lỗi khi gửi lời chào tạo bài đăng: {e}")

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
            try:
                answer = generate_ai_response(prompt)

                if len(answer) <= 2000:
                    await message.reply(answer)
                else:
                    for chunk in [answer[i:i+1900] for i in range(0, len(answer), 1900)]:
                        await message.channel.send(chunk)
            except Exception:
                await message.reply("Hệ thống AI hiện đang quá tải lượt yêu cầu, bạn vui lòng đợi vài giây và thử gửi lại câu hỏi nhé!")

    await bot.process_commands(message)

if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)
