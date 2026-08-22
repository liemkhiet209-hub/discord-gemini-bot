import os
import asyncio
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
import discord
from discord.ext import commands
from google import genai

# Cấu hình từ Environment Variables
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
TARGET_CHANNEL = "hỏi-đáp-gemini"

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
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True

ai_client = genai.Client(api_key=GEMINI_API_KEY)
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"--> Bot đã online: {bot.user}")

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

# Xử lý tin nhắn và gọi Gemini
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
                # Gọi trực tiếp model 3.6-flash không qua vòng lặp chờ
                response = await asyncio.to_thread(
                    ai_client.models.generate_content,
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
                print(f"Lỗi Gemini API: {e}")
                await message.reply(f"Lỗi phản hồi từ AI: {e}")

    await bot.process_commands(message)

if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)
