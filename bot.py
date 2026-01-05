import asyncio
import httpx
import os
import logging
import tempfile
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes
from telegram.request import HTTPXRequest

logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)

# 限制最大同时处理的任务数，防止内存爆炸
semaphore = asyncio.Semaphore(1)

BASE_URL = "https://whisper0.hydev.org"
HEADERS = {"Referer": "https://whisper.hydev.org/", "Origin": "https://whisper.hydev.org"}

async def handle_any_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    attachment = update.message.effective_attachment
    if not attachment: return

    # 使用信号量：如果已经在处理2个任务，第3个用户会排队
    async with semaphore:
        status_msg = await update.message.reply_text("⏳ 排队成功，开始处理...")

        # 使用临时文件保存音频，不占用 RAM
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp_path = tmp.name

        try:
            # 1. 下载到磁盘
            file = await context.bot.get_file(attachment.file_id)
            await file.download_to_drive(tmp_path)

            async with httpx.AsyncClient(headers=HEADERS, timeout=300) as client:
                # 2. 流式上传
                with open(tmp_path, 'rb') as f:
                    files = {'file': (getattr(attachment, 'file_name', 'audio.ogg'), f, 'application/octet-stream')}
                    upload_res = await client.post(f"{BASE_URL}/upload", files=files)

                audio_id = upload_res.json().get("audio_id")

                # 3. 轮询
                while True:
                    prog_res = await client.get(f"{BASE_URL}/progress/{audio_id}")
                    prog_data = prog_res.json()
                    if prog_data.get("done"): break
                    await status_msg.edit_text(f"⏳ {prog_data.get('status', '处理中...')}")
                    await asyncio.sleep(3)

                # 4. 结果
                result_res = await client.get(f"{BASE_URL}/result/{audio_id}.json")
                text = result_res.json().get("output", {}).get("text", "无内容")
                await status_msg.edit_text(f"✅ 转录完成：\n\n{text}")

        except Exception as e:
            logging.error(f"Error: {e}")
            await status_msg.edit_text("❌ 处理失败，可能是文件太大或服务器繁忙。")
        finally:
            # 运行完一定要删除临时文件！
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

async def main():
    TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
    t_request = HTTPXRequest(connect_timeout=30, read_timeout=60)
    app = ApplicationBuilder().token(TOKEN).request(t_request).build()
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO | filters.Document.ALL, handle_any_media))

    async with app:
        await app.initialize()
        await app.start()
        await app.updater.start_polling(drop_pending_updates=True)
        while True: await asyncio.sleep(1)

if __name__ == '__main__':
    asyncio.run(main())
