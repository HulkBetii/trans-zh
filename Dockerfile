FROM python:3.11-slim-bookworm

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    tesseract-ocr \
    tesseract-ocr-eng \
    tesseract-ocr-vie \
    tesseract-ocr-chi-sim \
    espeak-ng \
    fontconfig \
    fonts-noto-core \
    fonts-noto-cjk \
    curl \
    ca-certificates \
    gcc \
    g++ \
    && fc-cache -f \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN python -m pip install --upgrade pip setuptools wheel \
    && python -m pip install --no-cache-dir -r requirements.txt \
    && python -m pip check

COPY . .
RUN mkdir -p /app/temp /root/.cache/huggingface

ENV HOST=0.0.0.0 \
    PORT=8000 \
    WHISPER_MODEL_SIZE=base \
    WHISPER_DEVICE=cpu \
    WHISPER_COMPUTE_TYPE=int8 \
    WORKER_THREADS=8 \
    UPLOAD_MAX_MB=200 \
    CORS_ORIGINS="" \
    APP_AUTH_TOKEN=""

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD curl --fail --silent http://127.0.0.1:8000/api/health || exit 1

CMD ["python", "start.py", "--prod"]
