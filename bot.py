import os
import re
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
import discord
from discord.ext import commands
from groq import AsyncGroq

# Biến môi trường
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
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

# Khởi tạo Groq Client
groq_client = None
if GROQ_API_KEY:
    groq_client = AsyncGroq(api_key=GROQ_API_KEY)

cached_model = None

async def get_best_model():
    global cached_model
    if cached_model:
        return cached_model

    try:
        models_data = await groq_client.models.list()
        available_models = [
            m.id for m in models_data.data 
            if "whisper" not in m.id and "guard" not in m.id and "vision" not in m.id
        ]
        
        for preference in ["llama-3.3", "llama-3.1", "llama3", "gemma2", "deepseek", "qwen"]:
            for m in available_models:
                if preference in m:
                    cached_model = m
                    return cached_model
                    
        if available_models:
            cached_model = available_models[0]
            return cached_model
    except Exception as e:
        print(f"Lỗi danh sách model: {e}")

    return "llama-3.1-8b-instant"

@bot.event
async def on_ready():
    print(f"--> Bot đã online sẵn sàng: {bot.user}")
    if groq_client:
        await get_best_model()

# Tự động gửi lời chào khi tạo bài viết mới
@bot.event
async def on_thread_create(thread):
    try:
        parent_name = getattr(thread.parent, "name", "").lower()
        if parent_name == TARGET_CHANNEL.lower() or "hỏi-đáp" in parent_name or "gemini" in parent_name:
            owner_id = thread.owner_id
            mention_str = f"<@{owner_id}>" if owner_id else "bạn"
            await thread.send(f"Xin chào {mention_str}! Bạn có cần giúp đỡ gì không? Hãy đặt câu hỏi bên dưới nhé!")
    except Exception as e:
        print(f"Lỗi gửi tin chào: {e}")

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
        if not groq_client:
            await message.reply("Lỗi: Chưa cấu hình GROQ_API_KEY trên Render!")
            return

        prompt = message.content.replace(f"<@{bot.user.id}>", "").strip()
        if not prompt:
            return

        async with message.channel.typing():
            try:
                selected_model = await get_best_model()
                chat_completion = await groq_client.chat.completions.create(
                    messages=[
                        {
                            "role": "system",
                            "content": (
                                "Bạn là một trợ lý AI thông minh, thân thiện. "
                                "Hãy trả lời bằng tiếng Việt ngắn gọn, súc tích, tự nhiên và đi thẳng vào trọng tâm câu hỏi. "
                                "Không trả lời dài dòng lan man."
                            )
                        },
                        {
                            "role": "user",
                            "content": prompt
                        }
                    ],
                    model=selected_model,
                )
                raw_answer = chat_completion.choices[0].message.content

                # Lọc bỏ toàn bộ khối suy nghĩ <think>...</think> nếu có
                clean_answer = re.sub(r"<think>.*?</think>", "", raw_answer, flags=re.DOTALL).strip()
                if not clean_answer:
                    clean_answer = raw_answer

                if len(clean_answer) <= 2000:
                    await message.reply(clean_answer)
                else:
                    for chunk in [clean_answer[i:i+1900] for i in range(0, len(clean_answer), 1900)]:
                        await message.channel.send(chunk)

            except Exception as e:
                print(f"Lỗi Groq API: {e}")
                await message.reply(f"Lỗi phản hồi AI: {e}")

    await bot.process_commands(message)

if __name__ == "__main__":
    if not DISCORD_TOKEN:
        print("LỖI: Thiếu DISCORD_TOKEN trong Environment Variables!")
    else:
        bot.run(DISCORD_TOKEN)
