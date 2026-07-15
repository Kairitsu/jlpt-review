FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --disable-pip-version-check -r requirements.txt
COPY app.py db.py memory.py security.py auth.py tokenizer.py chunk_rules.py font_active.py ./
COPY static ./static
COPY font-sources ./font-sources
RUN useradd -u 1000 -m appuser \
    && mkdir -p /app/data/fonts/active \
    && chown -R appuser:appuser /app/data
USER appuser
EXPOSE 8000
CMD ["gunicorn", "--workers", "3", "--threads", "2", "--worker-class", "gthread", "--bind", "0.0.0.0:8000", "--timeout", "90", "--access-logfile", "-", "--error-logfile", "-", "app:app"]
