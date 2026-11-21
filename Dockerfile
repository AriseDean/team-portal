FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DATA_DIR=/data

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN SECRET_KEY=build-only DATA_DIR=/tmp/build python manage.py collectstatic --noinput \
 && rm -rf /tmp/build \
 && chmod +x docker-entrypoint.sh \
 && useradd --create-home --uid 10001 app \
 && mkdir -p /data && chown app:app /data \
 && chown -R app:app /app/staticfiles \
 && chmod -R u+rwX,go+rX /app/staticfiles

USER app
VOLUME ["/data"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4).status==200 else 1)"
ENTRYPOINT ["./docker-entrypoint.sh"]
