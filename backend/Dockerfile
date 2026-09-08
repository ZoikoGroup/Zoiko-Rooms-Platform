FROM python:3.13-slim

WORKDIR /app

# psycopg[binary] ships its own libpq, so no extra system postgres client
# libraries are needed here.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PYTHONUNBUFFERED=1
EXPOSE 8000

# Applies pending migrations, then starts the API. $PORT is honored for
# platforms (Render, etc.) that inject it; defaults to 8000 otherwise.
CMD alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}
