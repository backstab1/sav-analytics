FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .

ENV SAV_ANALYTICS_DATA_DIR=/data
EXPOSE 8000
CMD ["uvicorn", "sav_analytics.api:app", "--host", "0.0.0.0", "--port", "8000"]

