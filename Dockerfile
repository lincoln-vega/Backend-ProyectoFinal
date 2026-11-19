FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000

WORKDIR /app

RUN groupadd --system app \
    && useradd --system --gid app --home-dir /app app

COPY requirements.txt ./
RUN python -m pip install --no-cache-dir -r requirements.txt

COPY main.py auth.py database.py ./
COPY routers/ ./routers/
RUN chown app:app /app

USER app

EXPOSE 8000

CMD ["sh", "-c", "exec uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]
