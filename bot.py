import os
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
import discord
from discord.ext import commands
from groq import AsyncGroq

# Lấy thông tin cấu hình từ biến môi trường
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
TARGET_CHANNEL = "hỏi-đáp-gemini"

# Web server mini giữ bot luôn thức trên Render
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

# Khởi tạo Groq Client
groq_client = None
if GROQ_API_KEY:
    groq_client = AsyncGroq(api_key=GROQ_API_KEY)

# Danh sách model miễn phí tốc độ cao trên Groq (tự động luân chuyển nếu gặp lỗi)
GROQ_MODELS = [
    "llama-3.1-8b-instant",
    "gemma2-9b-it",
    "llama3-8b-8192"
]

async def ask_groq_ai(prompt: str) -> str:
    last_err = None
    for model_name in GROQ_MODELS:
        try:
            chat_completion = await groq_client.chat.completions.create(
                messages=[
                    {
                        "role": "system",
                        "content": "Bạn là trợ lý AI thông minh, nhiệt tình, luôn phản hồi bằng tiếng Việt tự nhiên, súc tích và chính xác."
                    },
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                model=model_name,
            )
            return chat_completion.choices[0].message.content
        except Exception as e:
            last_err = e
            continue
    raise last_err

@bot.event
async def on_ready():
    print(f"--> Bot đã online sẵn sàng: {bot.user}")

# Tự động gửi tin nhắn chào khi tạo bài viết / thread mới
@bot.event
async def on_thread_create(thread):
    try:
        parent_name = getattr(thread.parent, "name", "").lower()
        if parent_name == TARGET_CHANNEL.lower() or "hỏi-đáp" in parent_name or "gemini" in parent_name:
            owner_id = thread.owner_id
            mention_str = f"<@{owner_id}>" if owner_id else "bạn"
            await thread.send(f"Xin chào {mention_str}! Bạn có cần giúp đỡ gì không? Hãy đặt câu hỏi bên dưới nhé!")
            print(f"--> Đã gửi lời chào trong bài đăng: {thread.name}")
    except Exception as e:
        print(f"Lỗi khi gửi lời chào: {e}")

# Tự động đọc và trả lời câu hỏi
@bot.event
async def on_message(message):
    if message.author.bot:
        return

    # Xác định kênh hiện tại hoặc kênh diễn đàn cha
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
        if not groq_client:
            await message.reply("Lỗi: Chưa cấu hình biến môi trường GROQ_API_KEY trên Render!")
            return

        prompt = message.content.replace(f"<@{bot.user.id}>", "").strip()
        if not prompt:
            return

        async with message.channel.typing():
            try:
                answer = await ask_groq_ai(prompt)

                if len(answer) <= 2000:
                    await message.reply(answer)
                else:
                    for chunk in [answer[i:i+1900] for i in range(0, len(answer), 1900)]:
                        await message.channel.send(chunk)
            except Exception as e:
                await message.reply(f"Lỗi phản hồi AI: {e}")

    await bot.process_commands(message)

if __name__ == "__main__":
    if not DISCORD_TOKEN:
        print("LỖI: Thiếu DISCORD_TOKEN trong Environment Variables!")
    else:
        bot.run(DISCORD_TOKEN)
