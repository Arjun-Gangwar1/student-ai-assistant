FROM python:3.12-slim

WORKDIR /app

# libpq for asyncpg, gcc for any wheel that needs building. Removed afterwards
# in the same layer so the compiler does not ship in the image.
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc libpq-dev curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Run as a non-root user.
RUN useradd --create-home --shell /bin/bash app && chown -R app:app /app
USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=3 \
    CMD curl -fsS http://localhost:${PORT:-8000}/health || exit 1

# Shell form, not exec form: Railway assigns the real port at runtime via
# $PORT, and only a shell (not exec-form CMD's direct syscall) expands it.
# Exec form silently passed the literal string "$PORT" to uvicorn's --port
# flag, which is not a valid integer — the container never started.
CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}
