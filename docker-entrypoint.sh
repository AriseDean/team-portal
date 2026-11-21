#!/bin/sh
set -e
python manage.py migrate --noinput
python manage.py bootstrap
python manage.py clearsessions
exec gunicorn config.wsgi:application \
  --bind 0.0.0.0:8000 \
  --workers "${WEB_CONCURRENCY:-3}" \
  --timeout 120 \
  --access-logfile -
