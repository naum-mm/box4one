FROM node:22-bookworm-slim AS node
FROM python:3.13-slim-bookworm
COPY --from=node /usr/local/bin/node /usr/local/bin/node
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir 'yt-dlp[default,pin-curl-cffi]==2026.8.19'
RUN printf '%s\n' '--js-runtimes node' > /etc/yt-dlp.conf
WORKDIR /app
COPY worker.py .
CMD ["python", "-u", "worker.py"]
RUN printf '%s\n' '--proxy http://download-proxy:8080' '--socket-timeout 30' >> /etc/yt-dlp.conf
RUN mv /usr/bin/ffmpeg /usr/bin/ffmpeg.real && mv /usr/bin/ffprobe /usr/bin/ffprobe.real
COPY --chmod=755 ffmpeg-safe /usr/bin/ffmpeg
COPY --chmod=755 ffprobe-safe /usr/bin/ffprobe
