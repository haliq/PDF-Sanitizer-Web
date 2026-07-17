FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PDF_SANITIZER_DATA_DIR=/data

WORKDIR /app
COPY requirements-web.txt .
RUN pip install --no-cache-dir -r requirements-web.txt
COPY . .
RUN mkdir -p /data

EXPOSE 8000
CMD ["python", "web_app.py"]
