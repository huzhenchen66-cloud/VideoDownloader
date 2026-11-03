FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    nodejs \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt pyproject.toml README.md LICENSE ./
COPY videodownloader ./videodownloader
COPY run_desktop.py ./

RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir .

ENV VD_HOST=0.0.0.0 \
    VD_PORT=8765 \
    VIDEODOWNLOADER_HOME=/app/storage

VOLUME ["/app/storage"]
EXPOSE 8765

CMD ["python", "run_desktop.py", "--web", "--no-browser", "--host", "0.0.0.0", "--port", "8765"]
