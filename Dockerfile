# Один образ на API, worker и миграции: различаются только командой.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# Приложение работает не от root (P6): у процесса нет прав менять образ,
# а запись разрешена только в том данных.
RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --home-dir /app --shell /usr/sbin/nologin app

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN pip install uv==0.12.3 \
    && uv export --locked --no-dev --no-emit-project --output-file requirements.lock \
    && pip install --requirement requirements.lock \
    && pip uninstall --yes uv
COPY src ./src
RUN pip install --no-deps . \
    && mkdir /data && chown app:app /data

ENV SAV_ANALYTICS_DATA_DIR=/data \
    SAV_ANALYTICS_LOG_FORMAT=json
USER app
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4)"]

# За прокси: схема и адрес клиента берутся из X-Forwarded-*, иначе cookie
# не получит Secure, а журнал запишет адрес прокси вместо адреса человека.
CMD ["uvicorn", "sav_analytics.api:app", "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips", "*"]
