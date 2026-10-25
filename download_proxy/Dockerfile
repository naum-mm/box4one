FROM python:3.13-slim-bookworm
WORKDIR /app
COPY proxy.py .
USER 65532:65532
CMD ["python", "-B", "proxy.py"]
