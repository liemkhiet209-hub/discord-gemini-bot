import os
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
import discord
from discord.ext import commands
from google import genai

# Lấy token từ biến môi trường của server
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
TARGET_CHANNEL = "hỏi-đáp"

# Web server giả lập để Render duy trì hoạt động
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is alive!")

def run_web():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
    server.serve_forever()

threading.Thread(target=run_web, daemon=True).start()

# Cấu hình AI và Discord Bot
ai_client = genai.Client(api_key=GEMINI_API_KEY)
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"--> Bot đã online thành công: {bot.user}")

@bot.event
async def on_message(message):
    if message.author.bot:
        return

    is_in_target_channel = getattr(message.channel, "name", "") == TARGET_CHANNEL
    is_mentioned = bot.user in message.mentions

    if is_in_target_channel or is_mentioned:
        prompt = message.content.replace(f"<@{bot.user.id}>", "").strip()

        if not prompt:
            return

        async with message.channel.typing():
            try:
                response = ai_client.models.generate_content(
                    model="gemini-2.5-flash",
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

bot.run(DISCORD_TOKEN)