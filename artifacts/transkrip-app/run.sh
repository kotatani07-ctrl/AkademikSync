#!/bin/bash
set -e

cd "$(dirname "$0")"

# Load .env jika ada
if [ -f .env ]; then
  export $(grep -v '^#' .env | xargs)
fi

# Jalankan migrasi jika perlu
python manage.py migrate --noinput

# Buat superuser default jika belum ada (user: admin, pass: admin123)
python manage.py shell -c "
from django.contrib.auth.models import User
if not User.objects.filter(username='admin').exists():
    User.objects.create_superuser('admin', 'admin@localhost', 'admin123')
    print('Superuser admin dibuat.')
else:
    print('Superuser admin sudah ada.')
" 2>/dev/null || true

# Jalankan Django development server
exec python manage.py runserver 0.0.0.0:${PORT:-8000}
