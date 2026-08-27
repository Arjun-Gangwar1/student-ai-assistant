FROM python:3.12-slim

WORKDIR /app

# libpq for asyncpg, gcc for any wheel that needs building. Removed afterwards
# in the same layer so the compiler does not ship in the image.
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc libpq-dev curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

# Install CPU-only torch BEFORE requirements.txt. sentence-transformers pulls
# torch from PyPI, which defaults to the CUDA build: 1.2GB of torch plus 2.7GB
# of nvidia/* CUDA runtime libraries that a container with no GPU can never
# load. Installing the +cpu wheel first satisfies the dependency, so the CUDA
# wheel is never fetched -- site-packages drops 5.5GB -> 1.3GB, and resident
# memory before any encoding drops ~675MB -> ~385MB.
RUN pip install --no-cache-dir \
        --index-url https://download.pytorch.org/whl/cpu \
        torch==2.12.0+cpu

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
