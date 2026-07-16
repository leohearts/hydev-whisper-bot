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
semaphore = asyncio.Semaphore(2)

BASE_URL = "https://whisper0.hydev.org"
HEADERS = {"Referer": "https://whisper.hydev.org/", "Origin": "https://whisper.hydev.org"}

async def handle_any_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    attachment = update.message.effective_attachment
    if not attachment: return

    # 获取当前用户发送的消息 ID，用于后续所有回复的引用
    original_msg_id = update.message.message_id

    original_msg_id = update.message.message_id

    # --- 修改点：在进入信号量之前就发送提示 ---
    status_msg = await update.message.reply_text(
        "⏳ 已加入队列，请稍候...",
        reply_to_message_id=original_msg_id
    )


    # 使用信号量：如果已经在处理2个任务，第3个用户会排队
    async with semaphore:
        tmp_path = None
        try:
            await status_msg.edit_text("⏳ 正在处理...")

            # 使用临时文件保存音频，不占用 RAM
            with tempfile.NamedTemporaryFile(delete=False) as tmp:
                tmp_path = tmp.name

            # 1. 下载到磁盘
            file = await context.bot.get_file(attachment.file_id)
            await file.download_to_drive(tmp_path)

            async with httpx.AsyncClient(headers=HEADERS, timeout=300) as client:
                # 2. 流式上传
                with open(tmp_path, 'rb') as f:
                    files = {'file': (getattr(attachment, 'file_name', 'audio.ogg'), f, 'application/octet-stream')}
                    upload_res = await client.post(f"{BASE_URL}/upload", files=files)

                # 上传响应解析兜底
                try:
                    upload_json = upload_res.json()
                except Exception:
                    text = await upload_res.aread() if hasattr(upload_res, "aread") else await upload_res.text()
                    await status_msg.edit_text(f"❌ 上传失败，无法解析服务器响应：{text}")
                    return

                audio_id = upload_json.get("audio_id")
                if not audio_id:
                    err_msg = upload_json.get("error") or upload_json.get("message") or str(upload_json)
                    await status_msg.edit_text(f"❌ 上传失败：{err_msg}")
                    return

                # 3. 轮询
                # 3. 轮询进度
                last_status_text = ""  # 记录上一次发送给 Telegram 的文字内容

                while True:
                    try:
                        prog_res = await client.get(f"{BASE_URL}/progress/{audio_id}")
                    except Exception as e:
                        await status_msg.edit_text(f"❌ 查询进度失败：{e}")
                        break

                    try:
                        prog_data = prog_res.json()
                    except Exception:
                        text = await prog_res.aread() if hasattr(prog_res, "aread") else await prog_res.text()
                        await status_msg.edit_text(f"❌ 无法解析进度响应：{text}")
                        break

                    if prog_res.status_code < 200 or prog_res.status_code >= 300:
                        err = prog_data.get("error") or prog_data.get("message") or (await prog_res.aread() if hasattr(prog_res, "aread") else await prog_res.text())
                        await status_msg.edit_text(f"❌ 处理失败（服务器返回错误）：{err}")
                        break

                    if prog_data.get("error"):
                        await status_msg.edit_text(f"❌ 处理失败：{prog_data.get('error')}")
                        break

                    if prog_data.get("done"):
                        break

                    current_status = prog_data.get("status", "处理中...")
                    new_status_text = f"⏳ {current_status}"

                    # 检测状态文本里的失败关键字
                    status_lower = current_status.lower() if isinstance(current_status, str) else ""
                    if any(s in status_lower for s in ("error", "failed", "format not recogniz", "format not recognised")):
                        await status_msg.edit_text(f"❌ 处理失败：{current_status}")
                        break

                    # --- 核心修复：只有内容不同时才执行 edit_text ---
                    if new_status_text != last_status_text:
                        try:
                            await status_msg.edit_text(new_status_text)
                            last_status_text = new_status_text
                        except Exception as e:
                            # 即使判断了内容，偶尔也可能因为网络重试导致该错误，这里捕获它
                            if "Message is not modified" not in str(e):
                                logging.warning(f"Edit status error: {e}")

                    await asyncio.sleep(2)

                # 4. 结果
                try:
                    result_res = await client.get(f"{BASE_URL}/result/{audio_id}.json")
                except Exception as e:
                    await status_msg.edit_text(f"❌ 获取结果失败：{e}")
                    return
                try:
                    result_json = result_res.json()
                except Exception:
                    text = await result_res.aread() if hasattr(result_res, "aread") else await result_res.text()
                    await status_msg.edit_text(f"❌ 无法解析结果响应：{text}")
                    return
                text = result_json.get("output", {}).get("text", "无内容")
                await status_msg.edit_text(f"{text}")

        except Exception as e:
            logging.error(f"Error: {e}")
            try:
                await status_msg.edit_text("❌ 处理失败，可能是文件太大或服务器繁忙。\n由于 Telegram 的限制，机器人目前只能处理 20MB 以下的文件。")
            except Exception as e:
                logging.error(f"Error: {e}")
        finally:
            # 运行完一定要删除临时文件！
            try:
                if tmp_path and os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except Exception as e:
                logging.error(f"Error: {e}")

async def main():
    TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
    t_request = HTTPXRequest(connect_timeout=30, read_timeout=60)
    app = ApplicationBuilder().token(TOKEN).request(t_request).concurrent_updates(True).build()
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO | filters.Document.ALL, handle_any_media))

    async with app:
        await app.initialize()
        await app.start()
        await app.updater.start_polling(drop_pending_updates=True)
        while True: await asyncio.sleep(1)

if __name__ == '__main__':
    asyncio.run(main())
