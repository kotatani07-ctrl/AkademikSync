#!/bin/bash
set -e

cd "$(dirname "$0")"

# Load PORT dari .env jika ada (variabel lain dimuat otomatis oleh python-dotenv di settings.py)
if [ -f .env ]; then
  ENV_PORT=$(grep -E '^PORT=' .env 2>/dev/null | cut -d'=' -f2- | tr -d '"'\'' ' || true)
  if [ -n "$ENV_PORT" ]; then
    PORT="$ENV_PORT"
  fi
fi

PYTHON_BIN="python3"
if [ -f ".venv/bin/python" ]; then
  PYTHON_BIN=".venv/bin/python"
fi

# Jalankan migrasi jika perlu
$PYTHON_BIN manage.py migrate --noinput

# Buat superuser default jika belum ada (user: admin, pass: admin123)
$PYTHON_BIN manage.py shell -c "
from django.contrib.auth.models import User
if not User.objects.filter(username='admin').exists():
    User.objects.create_superuser('admin', 'admin@localhost', 'admin123')
    print('Superuser admin dibuat.')
else:
    print('Superuser admin sudah ada.')
" 2>/dev/null || true

# Jalankan Django development server
exec $PYTHON_BIN manage.py runserver 0.0.0.0:${PORT:-8000}
