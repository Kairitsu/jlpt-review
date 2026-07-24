FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    TOKENIZERS_PARALLELISM=false
WORKDIR /app
COPY requirements.txt .
RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc libc6-dev \
    && pip install --disable-pip-version-check --no-cache-dir --prefix=/install -r requirements.txt \
    && find /install -type d -name __pycache__ -prune -exec rm -r {} + \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    TOKENIZERS_PARALLELISM=false
WORKDIR /app
COPY --from=builder /install /usr/local
COPY app.py db.py fsrs_service.py memory.py security.py auth.py tokenizer.py font_active.py ./
COPY kwja_analyzer.py kwja_service.py reading_cards.py card_service.py ./
COPY kwja-config.yaml ./
COPY scripts ./scripts
COPY static ./static
COPY font-sources ./font-sources
RUN useradd -u 1000 -m appuser \
    && mkdir -p /app/data/fonts/active /app/data/kwja-models /app/data/kwja-analysis-cache \
    && chown -R appuser:appuser /app/data
USER appuser
EXPOSE 8000
CMD ["gunicorn", "--workers", "1", "--threads", "4", "--worker-class", "gthread", "--bind", "0.0.0.0:8000", "--timeout", "90", "--access-logfile", "-", "--error-logfile", "-", "app:app"]
