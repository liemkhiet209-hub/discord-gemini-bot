import os
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
import discord
from discord.ext import commands
from google import genai

# Lấy token từ biến môi trường
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
TARGET_CHANNEL = "hỏi-đáp"

# Web server mini giữ kết nối cho Render
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

# Cấu hình Discord & Gemini
ai_client = genai.Client(api_key=GEMINI_API_KEY)
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True

bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"--> Bot đã online thành công: {bot.user}")

# Tự động gửi tin nhắn chào hỏi khi có bài đăng mới được tạo
@bot.event
async def on_thread_create(thread):
    # Kiểm tra xem bài đăng được tạo trong kênh/diễn đàn hỏi đáp hay không
    parent = thread.parent
    if parent and (parent.name == TARGET_CHANNEL or "hoi" in parent.name.lower() or "hỏi" in parent.name.lower()):
        author = thread.owner.mention if thread.owner else "bạn"
        await thread.send(f"Xin chào {author}! Bạn có cần giúp đỡ gì không? Hãy đặt câu hỏi chi tiết bên dưới nhé.")

@bot.event
async def on_message(message):
    if message.author.bot:
        return

    # Kiểm tra tin nhắn trong kênh thường, bài đăng (thread) hoặc được tag tên
    channel_name = getattr(message.channel, "name", "").lower()
    parent_name = getattr(getattr(message.channel, "parent", None), "name", "").lower()

    is_in_target = (
        channel_name == TARGET_CHANNEL.lower()
        or "hoi" in channel_name
        or "hỏi" in channel_name
        or parent_name == TARGET_CHANNEL.lower()
        or "hoi" in parent_name
        or "hỏi" in parent_name
    )
    is_mentioned = bot.user in message.mentions

    if is_in_target or is_mentioned:
        prompt = message.content.replace(f"<@{bot.user.id}>", "").strip()

        if not prompt:
            return

        async with message.channel.typing():
            try:
                response = ai_client.models.generate_content(
                    model="gemini-3.6-flash",
                    contents=prompt
                )
                answer = response.text

                if len(answer) <= 2000:
                    await message.reply(answer)
                else:
                    for chunk in [answer[i:i+1900] for i in range(0, len(answer), 1900)]:
                        await message.channel.send(chunk)

            except Exception as e:
                await message.reply(f"Đã xảy ra lỗi: {e}")

    await bot.process_commands(message)

if __name__ == "__main__":
    if not DISCORD_TOKEN:
        print("LỖI: DISCORD_TOKEN chưa được cài đặt!")
    else:
        bot.run(DISCORD_TOKEN)
