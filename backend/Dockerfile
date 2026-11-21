# Zoiko Rooms API. Build context = backend/.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

# OCR for identity / property documents (pytesseract + pdf2image are thin wrappers around these programs).
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr poppler-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Face-match models for identity verification (runs on this server; best effort, never fails the build).
RUN python download_face_models.py || true

EXPOSE 8000
# Apply migrations, then serve. (Safe to repeat: alembic does nothing when already at head.)
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
