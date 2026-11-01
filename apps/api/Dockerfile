FROM python:3.13-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app/apps/api
WORKDIR /app

RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/* \
    && addgroup --system clipos \
    && adduser --system --ingroup clipos clipos
COPY pyproject.toml README.md /app/
COPY apps/api /app/apps/api
COPY alembic /app/alembic
COPY alembic.ini /app/alembic.ini
RUN pip install --no-cache-dir .
RUN mkdir -p /app/data/storage && chown -R clipos:clipos /app/data

USER clipos
EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && uvicorn clipos.main:app --host 0.0.0.0 --port 8000"]
