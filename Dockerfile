# One image for both the API and the web interface (compose picks the command).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencies first, so editing code doesn't reinstall them.
COPY pyproject.toml README.md ./
COPY backend ./backend
RUN pip install ".[frontend]"

COPY frontend ./frontend
COPY .streamlit ./.streamlit
COPY data ./data

# Never run as root. These folders are volumes in docker-compose.yml.
RUN useradd --create-home --uid 10001 apex \
    && mkdir -p /app/storage/uploads /app/vector_db \
    && chown -R apex:apex /app/storage /app/vector_db
USER apex

EXPOSE 8000 8501

# The API by default; the compose file overrides this for the web interface.
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=4).status == 200 else 1)"

# One worker: the embedded vector store allows only one process to open it.
CMD ["sh", "-c", "alembic -c backend/alembic.ini upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips=127.0.0.1"]
