FROM node:22-bookworm-slim AS node
FROM python:3.13-slim-bookworm
COPY --from=node /usr/local/bin/node /usr/local/bin/node
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir 'yt-dlp[default]'
RUN printf '%s\n' '--js-runtimes node' > /etc/yt-dlp.conf
WORKDIR /app
COPY worker.py .
CMD ["python", "-u", "worker.py"]
