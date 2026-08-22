import os
import re
import base64
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
import discord
from discord.ext import commands
from groq import AsyncGroq

# Cấu hình biến môi trường
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

# Hàm tự động quét danh sách model ĐANG HOẠT ĐỘNG từ máy chủ Groq
async def get_active_model(need_vision: bool = False) -> str:
    try:
        models_data = await groq_client.models.list()
        # Loại bỏ các model âm thanh và kiểm duyệt
        available_ids = [
            m.id for m in models_data.data 
            if "whisper" not in m.id.lower() and "guard" not in m.id.lower()
        ]

        if need_vision:
            # Tìm model thị giác (Vision) đang mở
            vision_models = [m for m in available_ids if "vision" in m.lower() or "vl" in m.lower()]
            if vision_models:
                print(f"--> Sử dụng Vision Model: {vision_models[0]}")
                return vision_models[0]
            raise ValueError("Hiện tại Groq tạm thời không mở model Vision miễn phí nào.")

        # Ưu tiên các model văn bản ổn định nhất theo thứ tự
        for pref in ["llama-3.3", "llama-3.1", "gemma2", "qwen", "llama3"]:
            for m in available_ids:
                if pref in m.lower() and "vision" not in m.lower():
                    return m

        if available_ids:
            return available_ids[0]
    except Exception as e:
        print(f"Lỗi khi quét model Groq: {e}")

    return "llama-3.1-8b-instant"

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
            print(f"--> Đã gửi lời chào trong bài đăng: {thread.name}")
    except Exception as e:
        print(f"Lỗi gửi tin chào: {e}")

# Tự động đọc và trả lời câu hỏi (Cả Text & Hình Ảnh)
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

        # Kiểm tra file ảnh đính kèm
        image_attachment = next(
            (att for att in message.attachments if att.content_type and att.content_type.startswith("image/")), 
            None
        )

        if not prompt and not image_attachment:
            return

        async with message.channel.typing():
            try:
                # Trường hợp có ảnh
                if image_attachment:
                    if not prompt:
                        prompt = "Hãy xem và đọc chi tiết nội dung trong hình ảnh này giúp tôi."

                    image_bytes = await image_attachment.read()
                    base64_image = base64.b64encode(image_bytes).decode("utf-8")
                    mime_type = image_attachment.content_type or "image/jpeg"

                    model_to_use = await get_active_model(need_vision=True)
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
                else:
                    # Trường hợp chỉ có văn bản
                    model_to_use = await get_active_model(need_vision=False)
                    messages_payload = [
                        {
                            "role": "system",
                            "content": "Bạn là trợ lý AI thông minh, thân thiện. Luôn trả lời ngắn gọn, súc tích, đi thẳng vào trọng tâm bằng tiếng Việt."
                        },
                        {
                            "role": "user",
                            "content": prompt
                        }
                    ]

                chat_completion = await groq_client.chat.completions.create(
                    messages=messages_payload,
                    model=model_to_use,
                )
                raw_answer = chat_completion.choices[0].message.content

                # Lọc bỏ phần suy nghĩ nội bộ nếu có
                clean_answer = re.sub(r"<think>.*?</think>", "", raw_answer, flags=re.DOTALL).strip()
                if not clean_answer:
                    clean_answer = raw_answer

                if len(clean_answer) <= 2000:
                    await message.reply(clean_answer)
                else:
                    for chunk in [clean_answer[i:i+1900] for i in range(0, len(answer), 1900)]:
                        await message.channel.send(chunk)

            except Exception as e:
                print(f"Lỗi AI: {e}")
                await message.reply(f"Lỗi phản hồi AI: {e}")

    await bot.process_commands(message)

if __name__ == "__main__":
    if not DISCORD_TOKEN:
        print("LỖI: Thiếu DISCORD_TOKEN trong Environment Variables!")
    else:
        bot.run(DISCORD_TOKEN)
