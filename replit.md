# Transkrip & Ijazah

Aplikasi Django untuk generate transkrip akademik mahasiswa dari data **Neo Feeder PDDIKTI**, dicocokkan dengan kurikulum, dan diekspor ke Excel.

## Cara Menjalankan

Workflow `Start application` sudah dikonfigurasi. Klik **Run** atau restart workflow untuk menjalankan.

Workflow menjalankan: `cd artifacts/transkrip-app && PORT=23524 bash run.sh`

`run.sh` otomatis:
1. Membaca env vars dari Replit Secrets
2. Menjalankan migrasi database
3. Membuat superuser `admin` jika belum ada
4. Menjalankan Django dev server di port `$PORT`

## Akun Default

| Field    | Nilai     |
|----------|-----------|
| Username | `admin`   |
| Password | `admin123` |

⚠️ Ganti password sebelum dipakai di lingkungan publik.

## Environment Variables (Replit Secrets)

| Key                  | Keterangan                                      |
|----------------------|-------------------------------------------------|
| `SECRET_KEY`         | Django secret key (sudah di-set)                |
| `FEEDER_HOST`        | IP/hostname server Neo Feeder                   |
| `FEEDER_USERNAME`    | Username Neo Feeder                             |
| `FEEDER_PASSWORD`    | Password Neo Feeder                             |
| `DEBUG`              | `True` (development)                            |
| `DEFAULT_NAMA_DEKAN` | Nama dekan default di transkrip                 |

## Stack

- **Python 3.11 / Django 5.2**
- **SQLite** (`artifacts/transkrip-app/db.sqlite3`)
- **openpyxl** — baca/tulis Excel
- **python-dotenv** — env management

## Struktur Utama

```
artifacts/transkrip-app/
├── core/
│   ├── models.py        ← Semua model database
│   ├── views.py         ← Dashboard, generate, riwayat
│   ├── admin.py         ← Admin + upload kurikulum
│   └── services/
│       ├── feeder.py    ← Integrasi Neo Feeder
│       ├── matching.py  ← Algoritma pencocokan MK
│       └── excel_gen.py ← Generate output Excel
├── transkrip_project/settings.py
├── run.sh
└── requirements.txt
```

## Halaman Utama

| URL        | Fungsi                        |
|------------|-------------------------------|
| `/`        | Generate transkrip single NIM |
| `/batch/`  | Generate batch dari file      |
| `/riwayat/`| Riwayat generate              |
| `/admin/`  | Admin Django (data master)    |
| `/login/`  | Halaman login                 |

## User Preferences
