FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app

ENV PYTHONUNBUFFERED=1 \
    BIND=0.0.0.0 \
    PORT=8765 \
    PRINTER_HOST=192.168.20.191 \
    PRINTER_PORT=8899 \
    CAMERA_PORT=8080

EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/api/health', timeout=4)"

CMD ["python", "-m", "app"]
