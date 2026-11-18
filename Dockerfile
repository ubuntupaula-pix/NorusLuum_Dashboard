FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app
RUN groupadd --system norus && useradd --system --gid norus --home-dir /app norus
COPY requirements.txt ./requirements.txt
RUN python -m pip install --upgrade pip && python -m pip install -r requirements.txt
COPY --chown=norus:norus app ./app
COPY --chown=norus:norus alembic ./alembic
COPY --chown=norus:norus alembic.ini ./alembic.ini
COPY --chown=norus:norus scripts ./scripts
COPY --chown=norus:norus index.html login.html ./
COPY --chown=norus:norus css ./css
COPY --chown=norus:norus js ./js
COPY --chown=norus:norus data ./data
COPY --chown=norus:norus vendor ./vendor
RUN mkdir -p /app/data && chown -R norus:norus /app
USER norus
EXPOSE 8765
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8765", "--workers", "2", "--proxy-headers", "--forwarded-allow-ips", "172.29.0.0/24"]
