FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN pip install --no-cache-dir uv==0.12.3 \
    && uv export --locked --no-dev --no-emit-project --output-file requirements.lock \
    && pip install --no-cache-dir --requirement requirements.lock
COPY src ./src
RUN pip install --no-cache-dir --no-deps .

ENV SAV_ANALYTICS_DATA_DIR=/data
EXPOSE 8000
CMD ["uvicorn", "sav_analytics.api:app", "--host", "0.0.0.0", "--port", "8000"]

