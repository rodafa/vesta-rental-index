#!/bin/sh
# Wrapper to run Django management commands from cron with proper env and logging
cd /app
python manage.py "$@" 2>&1 | while IFS= read -r line; do
    echo "[$(date -u '+%Y-%m-%d %H:%M:%S')] $line"
done
