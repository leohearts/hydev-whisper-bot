# 使用轻量级 Python 镜像
FROM python:3.11-slim

# 设置工作目录
WORKDIR /app

# 安装依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制源代码
COPY bot.py .

# 设置环境变量默认值（留空，运行时由用户提供）
ENV TELEGRAM_BOT_TOKEN=""

# 运行程序
CMD ["python", "bot.py"]
