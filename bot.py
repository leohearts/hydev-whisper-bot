import asyncio
import httpx
import os
import logging
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes
from telegram.request import HTTPXRequest

# 日志设置
logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)

BASE_URL = "https://whisper0.hydev.org"
HEADERS = {
    "Referer": "https://whisper.hydev.org/",
    "Origin": "https://whisper.hydev.org",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

async def handle_any_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # 自动获取附件对象 (可能是 Voice, Audio 或 Document)
    attachment = update.message.effective_attachment
    if not attachment:
        return

    # 获取文件名（文档有文件名，语音和音频可能没有，给定默认值）
    file_name = getattr(attachment, 'file_name', 'audio_file')
    # 确保文件名有意义（某些语音消息没有后缀，默认补齐 .ogg）
    if '.' not in file_name:
        file_name += ".ogg"

    status_msg = await update.message.reply_text(f"📥 收到文件: {file_name}\n正在准备上传...")

    try:
        # 1. 下载文件
        file = await context.bot.get_file(attachment.file_id)
        audio_content = bytes(await file.download_as_bytearray())

        async with httpx.AsyncClient(headers=HEADERS, timeout=120) as client:
            # 2. 上传 (使用获取到的原始文件名和 MIME 类型)
            mime_type = getattr(attachment, 'mime_type', 'application/octet-stream')
            files = {'file': (file_name, audio_content, mime_type)}

            logging.info(f"正在上传 {file_name} ({mime_type})...")
            upload_res = await client.post(f"{BASE_URL}/upload", files=files)
            upload_res.raise_for_status()
            audio_id = upload_res.json().get("audio_id")

            # 3. 轮询进度
            last_status = ""
            while True:
                prog_res = await client.get(f"{BASE_URL}/progress/{audio_id}")
                prog_data = prog_res.json()

                if prog_data.get("done"):
                    break

                curr_status = prog_data.get("status", "处理中...")
                if curr_status != last_status:
                    await status_msg.edit_text(f"⏳ {curr_status}")
                    last_status = curr_status

                await asyncio.sleep(2)

            # 4. 获取结果
            result_res = await client.get(f"{BASE_URL}/result/{audio_id}.json")
            result_data = result_res.json()
            transcription = result_data.get("output", {}).get("text")

            if transcription:
                await status_msg.edit_text(f"{transcription}")
            else:
                await status_msg.edit_text("❌ 转录完成，但未提取到内容。")

    except Exception as e:
        logging.error(f"Error handling {file_name}: {e}")
        await status_msg.edit_text(f"❌ 发生错误: {str(e)}")

async def main():
    TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
    if not TOKEN:
        print("错误: 请设置 TELEGRAM_BOT_TOKEN 环境变量")
        return

    # 配置请求超时
    t_request = HTTPXRequest(connect_timeout=30, read_timeout=30, write_timeout=30)
    app = ApplicationBuilder().token(TOKEN).request(t_request).build()

    # 过滤器升级：支持语音、音频以及任何文件文档
    # filters.Document.ALL 涵盖了用户以“文件”形式发送的所有内容
    media_filter = (filters.VOICE | filters.AUDIO | filters.Document.ALL)
    app.add_handler(MessageHandler(media_filter, handle_any_media))

    async with app:
        await app.initialize()
        await app.start()
        logging.info("全能版 Whisper Bot 已启动，支持任何文件上传...")
        await app.updater.start_polling(drop_pending_updates=True)
        while True:
            await asyncio.sleep(1)

if __name__ == '__main__':
    asyncio.run(main())
