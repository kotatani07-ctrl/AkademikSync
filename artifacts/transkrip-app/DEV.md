# DEV.md — Panduan Setup Lokal

Panduan ini menjelaskan cara menjalankan **AkademikSync** di komputer lokal, baik di **Windows** maupun **Linux/macOS**.

---

## Prasyarat

| Kebutuhan | Versi Minimum | Cara Cek |
|---|---|---|
| Python | 3.13+ | `python --version` |
| pip | terbaru | `pip --version` |
| Git | terbaru | `git --version` |

---

## Cara Clone Proyek

```bash
git clone <URL_REPOSITORY>
cd <nama-folder-repo>
```

Masuk ke folder aplikasi:

```bash
cd artifacts/transkrip-app
```

> Semua perintah di bawah ini dijalankan dari dalam folder `artifacts/transkrip-app/`

---

## Setup di Windows

### 1. Buat Virtual Environment

Buka **Command Prompt** atau **PowerShell**:

```cmd
py -3 -m venv venv
venv\Scripts\activate
```

Jika `py` tidak tersedia, gunakan:

```cmd
python -m venv venv
python -m venv venv
```

Jika muncul error _"execution of scripts is disabled"_ di PowerShell:

```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

### 2. Install Dependencies

```cmd
pip install -r requirements.txt
```

### 3. Konfigurasi Environment

```cmd
copy .env.example .env
```

Buka file `.env` dengan Notepad atau editor teks, lalu isi:

```env
SECRET_KEY=isi-dengan-string-acak-panjang
DEBUG=True
FEEDER_HOST=192.168.1.100
FEEDER_USERNAME=username_anda
FEEDER_PASSWORD=password_anda
DEFAULT_NAMA_DEKAN=Dr. Nama Dekan, M.T.
```

### 4. Jalankan Migrasi Database

```cmd
python manage.py migrate
```

### 5. Buat Superuser

```cmd
python manage.py createsuperuser
```

Ikuti prompt: isi username, email (opsional), dan password.

Atau gunakan superuser default (username `admin`, password `admin123`):

```cmd
python manage.py shell -c "from django.contrib.auth.models import User; User.objects.filter(username='admin').exists() or User.objects.create_superuser('admin','admin@localhost','admin123')"
```

### 6. Jalankan Server

```cmd
python manage.py runserver
```

Buka browser: [http://127.0.0.1:8000](http://127.0.0.1:8000)

---

## Setup di Linux / macOS

> Jika Anda sedang memakai Windows, gunakan perintah di bagian sebelumnya: `py -3 -m venv venv` atau `python -m venv venv`. Perintah `python3` biasanya hanya tersedia di Linux/macOS.

### 1. Buat Virtual Environment

```bash
python3 -m venv venv
source venv/bin/activate
```

### 2. Install Dependencies

```bash
pip install -r requirements.txt
```

### 3. Konfigurasi Environment

```bash
cp .env.example .env
nano .env   # atau: vim .env / gedit .env / code .env
```

Isi nilai yang diperlukan:

```env
SECRET_KEY=isi-dengan-string-acak-panjang
DEBUG=True
FEEDER_HOST=192.168.1.100
FEEDER_USERNAME=username_anda
FEEDER_PASSWORD=password_anda
DEFAULT_NAMA_DEKAN=Dr. Nama Dekan, M.T.
```

### 4. Jalankan Migrasi Database

```bash
python manage.py migrate
```

### 5. Buat Superuser

```bash
python manage.py createsuperuser
```

Atau gunakan superuser default:

```bash
python manage.py shell -c "from django.contrib.auth.models import User; User.objects.filter(username='admin').exists() or User.objects.create_superuser('admin','admin@localhost','admin123')"
```

### 6. Jalankan Server

```bash
python manage.py runserver
```

Buka browser: [http://127.0.0.1:8000](http://127.0.0.1:8000)

---

## Generate SECRET_KEY Baru

Jangan gunakan secret key default. Generate yang aman:

**Windows (PowerShell) / Linux / macOS:**

```bash
python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
```

Salin outputnya ke `SECRET_KEY=` di file `.env`.

---

## Perintah Berguna

| Perintah | Fungsi |
|---|---|
| `python manage.py runserver` | Jalankan dev server (port 8000) |
| `python manage.py runserver 0.0.0.0:8080` | Jalankan di port 8080, bisa diakses dari jaringan lokal |
| `python manage.py migrate` | Terapkan migrasi database |
| `python manage.py makemigrations` | Buat file migrasi baru setelah ubah `models.py` |
| `python manage.py createsuperuser` | Buat user admin baru |
| `python manage.py changepassword admin` | Ganti password user admin |
| `python manage.py shell` | Shell interaktif Django |
| `python manage.py collectstatic` | Kumpulkan file statis (untuk production) |

---

## Struktur Folder yang Tidak Ada di Git

Folder/file berikut tidak tersedia setelah clone — perlu dibuat manual:

| Path | Cara Membuat |
|---|---|
| `venv/` | Ikuti langkah "Buat Virtual Environment" di atas |
| `.env` | `cp .env.example .env` lalu isi nilainya |
| `media/` | Dibuat otomatis oleh Django saat pertama upload file |
| `db.sqlite3` | Dibuat otomatis saat `python manage.py migrate` |

---

## Koneksi ke Neo Feeder

Neo Feeder adalah server lokal kampus. Pastikan:

1. Komputer terhubung ke **jaringan yang sama** dengan server Feeder (biasanya intranet kampus atau VPN)
2. `FEEDER_HOST` diisi dengan IP server Feeder (tanpa `http://`), contoh: `192.168.1.100`
3. Port yang digunakan: **3003** (dikonfigurasi di `services/feeder.py`)
4. Cek koneksi: `ping 192.168.1.100` dari terminal

Jika Feeder tidak terhubung, aplikasi tetap bisa berjalan — hanya fitur generate transkrip yang tidak bisa digunakan.

---

## Troubleshooting

### `ModuleNotFoundError: No module named 'django'`
Virtual environment belum aktif. Jalankan:
- Windows: `venv\Scripts\activate`
- Linux: `source venv/bin/activate`

### `django.db.utils.OperationalError: no such table`
Migrasi belum dijalankan. Jalankan: `python manage.py migrate`

### `DisallowedHost` error
Tambahkan host ke `ALLOWED_HOSTS` di `.env`:
```env
ALLOWED_HOSTS=localhost,127.0.0.1,0.0.0.0
```

### Tidak bisa connect ke Feeder
- Cek apakah komputer terhubung ke jaringan kampus/intranet
- Cek `FEEDER_HOST` sudah benar
- Coba: `curl http://192.168.1.100:3003/ws/live2.php` dari terminal

### Port 8000 sudah dipakai
Jalankan di port lain:
```bash
python manage.py runserver 8080
```
