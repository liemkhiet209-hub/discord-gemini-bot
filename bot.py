import os
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
import discord
from discord.ext import commands
from google import genai

# Lấy thông tin xác thực từ Environment Variables
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

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

# Cấu hình Intents
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True

ai_client = genai.Client(api_key=GEMINI_API_KEY)
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"--> Bot đã online thành công với tên: {bot.user}")

# Tự động chào khi có bài đăng / luồng mới được tạo
@bot.event
async def on_thread_create(thread):
    try:
        # Lấy ID của người tạo bài đăng
        owner_id = thread.owner_id
        mention_str = f"<@{owner_id}>" if owner_id else "bạn"
        
        await thread.send(f"Xin chào {mention_str}! Bạn có cần giúp đỡ gì không? Hãy nhập câu hỏi chi tiết bên dưới nhé!")
        print(f"--> Đã gửi lời chào trong bài đăng: {thread.name}")
    except Exception as e:
        print(f"Lỗi khi gửi lời chào tạo bài đăng: {e}")

# Tự động trả lời tin nhắn
@bot.event
async def on_message(message):
    if message.author.bot:
        return

    # Lấy tên kênh và tên kênh cha (nếu là bài đăng trong diễn đàn)
    channel_name = getattr(message.channel, "name", "").lower()
    parent_name = ""
    if isinstance(message.channel, discord.Thread) and message.channel.parent:
        parent_name = message.channel.parent.name.lower()

    # Kiểm tra điều kiện: tag bot, hoặc chat trong kênh/bài đăng hỏi đáp
    is_mentioned = bot.user in message.mentions
    is_in_qa = (
        "hoi" in channel_name or "hỏi" in channel_name or
        "hoi" in parent_name or "hỏi" in parent_name
    )

    if is_mentioned or is_in_qa:
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
                await message.reply(f"Đã xảy ra lỗi khi tạo phản hồi: {e}")

    await bot.process_commands(message)

if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)
