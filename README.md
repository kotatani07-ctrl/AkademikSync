# Transkrip & Ijazah — Aplikasi Generate Transkrip Akademik

Aplikasi web berbasis **Django** untuk menghasilkan transkrip akademik mahasiswa secara otomatis. Data mahasiswa diambil langsung dari **Neo Feeder PDDIKTI**, dicocokkan dengan kurikulum yang tersimpan, lalu diekspor sebagai file Excel siap cetak.

---

## Daftar Isi

- [Fitur](#fitur)
- [Teknologi](#teknologi)
- [Struktur Proyek](#struktur-proyek)
- [Alur Kerja Aplikasi](#alur-kerja-aplikasi)
- [Model Data](#model-data)
- [Konfigurasi Environment](#konfigurasi-environment)
- [Cara Menjalankan](#cara-menjalankan)
- [Akun Default](#akun-default)
- [Panduan Penggunaan](#panduan-penggunaan)
- [Catatan Penting](#catatan-penting)

---

## Fitur

| Fitur | Keterangan |
|---|---|
| **Generate Single** | Buat transkrip satu mahasiswa berdasarkan NIM |
| **Generate Batch** | Buat transkrip banyak mahasiswa dari file `.txt`, `.csv`, atau `.xlsx` (maks 100 NIM) |
| **Riwayat Generate** | Log semua generate beserta status, IPK, dan catatan MK bermasalah |
| **Manajemen Kurikulum** | Upload kurikulum per jenjang & angkatan dari file Excel |
| **Template Excel** | Upload template Excel transkrip dengan cell mapping fleksibel |
| **Predikat Kelulusan** | Konfigurasi range IPK → predikat per rentang tahun lulus |
| **Data Pendukung** | Simpan data tambahan per NIM (judul skripsi, no. ijazah, nama dekan) |
| **Django Admin** | Kelola semua data master via antarmuka admin bawaan Django |

---

## Teknologi

- **Backend**: Python 3.11, Django 5.2
- **Database**: SQLite (lokal) — mudah dipindah ke PostgreSQL/MySQL
- **Export**: openpyxl (baca/tulis Excel)
- **Integrasi**: Neo Feeder PDDIKTI (HTTP/JSON, read-only)
- **Template**: Django HTML templates (server-side rendering)
- **Auth**: Django bawaan (session-based)

---

## Struktur Proyek

```
Transkrip/                          ← root repo
├── README.md                       ← dokumentasi utama (file ini)
├── artifacts/
│   └── transkrip-app/              ← aplikasi Django utama
│       ├── core/
│       │   ├── admin.py            # Django admin + form upload Excel kurikulum
│       │   ├── forms.py            # Form generate single & batch, filter riwayat
│       │   ├── models.py           # Model: Template, Kurikulum, MataKuliah, dst.
│       │   ├── views.py            # View: dashboard, generate single/batch, riwayat
│       │   ├── urls.py             # URL routing aplikasi utama
│       │   ├── migrations/         # Migrasi database Django
│       │   └── services/
│       │       ├── feeder.py       # Integrasi Neo Feeder PDDIKTI
│       │       ├── matching.py     # Logika pencocokan nilai mahasiswa ↔ kurikulum
│       │       └── excel_gen.py    # Generate file Excel output (single & batch)
│       ├── transkrip_project/
│       │   ├── settings.py         # Konfigurasi Django
│       │   ├── urls.py             # Root URL dispatcher
│       │   └── wsgi.py
│       ├── templates/              # HTML templates (login, dashboard, generate, riwayat)
│       ├── static/                 # File statis (CSS, JS, gambar)
│       ├── media/                  # Upload file (template Excel, dll.) — di-gitignore
│       ├── db.sqlite3              # Database SQLite — ikut Git
│       ├── manage.py
│       ├── requirements.txt
│       ├── run.sh                  # Script startup (migrasi + jalankan server)
│       ├── .env.example            # Template konfigurasi environment
│       ├── DEV.md                  # Panduan setup lokal (Windows & Linux)
│       └── CLAUDE.md               # Konteks proyek untuk AI — wajib diperbarui
```

---

## Alur Kerja Aplikasi

```
User input NIM
     │
     ▼
Feeder: GetToken → GetBiodataMahasiswa(NIM) → GetNilaiPerkuliahanMahasiswa(NIM)
     │
     ▼
Lookup Kurikulum (jenjang + angkatan mahasiswa)
     │
     ▼
Matching: cocokkan daftar MK kurikulum ↔ nilai dari Feeder
   - MK retake: ambil nilai terbaik
   - MK kosong: tidak ada nilai di Feeder
   - MK tidak dikenali: ada nilai Feeder tapi tidak ada di kurikulum
     │
     ▼
Hitung IPK & Predikat Kelulusan
     │
     ▼
Generate Excel menggunakan cell_mapping dari Template
     │
     ▼
Download file .xlsx
```

---

## Model Data

| Model | Fungsi |
|---|---|
| `Template` | File Excel template transkrip + cell mapping JSON |
| `Kurikulum` | Kurikulum per jenjang (D3/S1) & angkatan, terhubung ke Template |
| `MataKuliahKurikulum` | Daftar MK (kode, nama, SKS) dalam satu Kurikulum |
| `PredikatKelulusan` | Range IPK → predikat per rentang tahun lulus |
| `DataPendukung` | Data tambahan per NIM: judul skripsi, no. ijazah, nama dekan |
| `RiwayatGenerate` | Log setiap generate: NIM, status, IPK, catatan MK, waktu, user |

---

## Konfigurasi Environment

Salin `.env.example` ke `.env` lalu isi nilainya:

```bash
cp artifacts/transkrip-app/.env.example artifacts/transkrip-app/.env
```

| Variabel | Wajib | Keterangan |
|---|---|---|
| `SECRET_KEY` | ✅ | Django secret key — ganti dengan string acak panjang |
| `DEBUG` | — | `True` (dev) / `False` (production) |
| `ALLOWED_HOSTS` | — | Daftar host diizinkan, misal `localhost,127.0.0.1` |
| `FEEDER_HOST` | ✅* | IP/hostname server Neo Feeder, misal `192.168.1.100` |
| `FEEDER_USERNAME` | ✅* | Username Neo Feeder |
| `FEEDER_PASSWORD` | ✅* | Password Neo Feeder |
| `DEFAULT_NAMA_DEKAN` | — | Nama dekan default di transkrip |
| `CSRF_TRUSTED_ORIGINS` | — | Daftar origin CSRF, perlu diisi jika pakai reverse proxy |

> \* Wajib agar generate transkrip berfungsi. Tanpa ini, app tetap bisa dijalankan tapi generate akan error.

Neo Feeder terhubung ke endpoint: `http://{FEEDER_HOST}:3003/ws/live2.php`

---

## Cara Menjalankan

Lihat **[artifacts/transkrip-app/DEV.md](artifacts/transkrip-app/DEV.md)** untuk panduan lengkap setup lokal di **Windows** dan **Linux**.

---

## Akun Default

Saat pertama kali dijalankan, `run.sh` otomatis membuat superuser:

| Field | Nilai |
|---|---|
| Username | `admin` |
| Password | `admin123` |

> ⚠️ **Segera ganti password ini** sebelum digunakan di lingkungan yang bisa diakses orang lain.
>
> Ganti via: `python manage.py changepassword admin`

---

## Panduan Penggunaan

### 1. Setup Data Master (via Django Admin `/admin/`)

1. **Upload Template Excel** — masukkan file `.xlsx` template transkrip + cell mapping JSON
2. **Tambah Kurikulum** — upload file Excel kurikulum (kode MK, nama, SKS) per jenjang & angkatan, lalu hubungkan ke Template
3. **Tambah Predikat Kelulusan** — isi range IPK → predikat per tahun
4. *(Opsional)* **Tambah Data Pendukung** — judul skripsi, no. ijazah, nama dekan per NIM

### 2. Generate Transkrip

- **Single** (`/`) — masukkan NIM, klik Generate, file `.xlsx` langsung terunduh
- **Batch** (`/batch/`) — upload file `.txt`/`.csv`/`.xlsx` berisi daftar NIM, satu NIM per baris

### 3. Lihat Riwayat

Halaman `/riwayat/` menampilkan log semua generate dengan filter tanggal, angkatan, status, dan jenjang.

---

## Catatan Penting

- Neo Feeder hanya dipanggil **read-only** (tidak ada Insert/Update/Delete)
- Token Feeder di-cache in-memory, auto-refresh 5 menit sebelum expired
- Batch diproses serial dengan jeda 0,3 detik antar NIM agar tidak membebani Feeder
- File media (template Excel) disimpan di folder `media/` — **tidak masuk Git**
- Database SQLite (`db.sqlite3`) — **ikut masuk Git** agar data awal/kurikulum bisa dibagikan
