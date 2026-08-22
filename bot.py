import os
import re
import base64
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
import discord
from discord.ext import commands
from groq import AsyncGroq

# Biến môi trường
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
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

# Cấu hình Discord Client
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True

bot = commands.Bot(command_prefix="!", intents=intents)

# Khởi tạo Groq Client
groq_client = None
if GROQ_API_KEY:
    groq_client = AsyncGroq(api_key=GROQ_API_KEY)

@bot.event
async def on_ready():
    print(f"--> Bot đã online sẵn sàng: {bot.user}")

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

# Tự động đọc và trả lời câu hỏi (Cả Chữ & Hình Ảnh)
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
        
        # Kiểm tra xem người dùng có đính kèm ảnh không
        image_attachment = next(
            (att for att in message.attachments if att.content_type and att.content_type.startswith("image/")), 
            None
        )

        if not prompt and not image_attachment:
            return

        async with message.channel.typing():
            try:
                # Xử lý khi có ảnh đính kèm
                if image_attachment:
                    if not prompt:
                        prompt = "Hãy phân tích và đọc chi tiết nội dung trong hình ảnh này giúp tôi."

                    # Tải ảnh và chuyển sang Base64
                    image_bytes = await image_attachment.read()
                    base64_image = base64.b64encode(image_bytes).decode("utf-8")
                    mime_type = image_attachment.content_type or "image/jpeg"

                    messages_payload = [
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": prompt},
                                {
                                    "type": "image_url",
                                    "image_url": {
                                        "url": f"data:{mime_type};base64,{base64_image}"
                                    }
                                }
                            ]
                        }
                    ]
                    model_to_use = "llama-3.2-11b-vision-preview"
                else:
                    # Xử lý tin nhắn văn bản thông thường
                    messages_payload = [
                        {
                            "role": "system",
                            "content": (
                                "Bạn là trợ lý AI thông minh, nhiệt tình. "
                                "Hãy trả lời bằng tiếng Việt ngắn gọn, súc tích, tự nhiên và đúng trọng tâm."
                            )
                        },
                        {
                            "role": "user",
                            "content": prompt
                        }
                    ]
                    model_to_use = "llama-3.1-8b-instant"

                chat_completion = await groq_client.chat.completions.create(
                    messages=messages_payload,
                    model=model_to_use,
                )
                raw_answer = chat_completion.choices[0].message.content

                # Lọc bỏ suy nghĩ nội bộ <think> nếu có
                clean_answer = re.sub(r"<think>.*?</think>", "", raw_answer, flags=re.DOTALL).strip()
                if not clean_answer:
                    clean_answer = raw_answer

                if len(clean_answer) <= 2000:
                    await message.reply(clean_answer)
                else:
                    for chunk in [clean_answer[i:i+1900] for i in range(0, len(clean_answer), 1900)]:
                        await message.channel.send(chunk)

            except Exception as e:
                print(f"Lỗi xử lý AI: {e}")
                await message.reply(f"Lỗi phản hồi AI: {e}")

    await bot.process_commands(message)

if __name__ == "__main__":
    if not DISCORD_TOKEN:
        print("LỖI: Thiếu DISCORD_TOKEN trong Environment Variables!")
    else:
        bot.run(DISCORD_TOKEN)
