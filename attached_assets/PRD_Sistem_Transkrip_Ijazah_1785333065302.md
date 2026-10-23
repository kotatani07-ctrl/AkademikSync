# PRD: Sistem Otomasi Transkrip & Ijazah (Neo Feeder Integration)

## 1. Ringkasan
Aplikasi web internal (Django, lokal, single-user) untuk generate Transkrip Akademik mahasiswa secara otomatis dari data Neo Feeder PDDIKTI, dicocokkan dengan kurikulum resmi per angkatan, output berupa file Excel siap cetak (format resmi 2 halaman/mahasiswa). Read-only terhadap Neo Feeder — sistem hanya memanggil fungsi `Get*`.

## 2. Tech Stack
- Backend: Django + SQLite
- Excel processing: `openpyxl`
- Auth: Django auth bawaan, 1 akun user
- Deploy: lokal (`runserver`/lokal WSGI), akses Neo Feeder via jaringan (`http://<ip_neofeeder>:3003`)

## 3. Integrasi Neo Feeder
- Endpoint: `POST http://<HOST>:3003/ws/live2.php`
- Header: `Content-Type: application/json`
- Auth: `{"act":"GetToken","username":...,"password":...}` → response `data.token` (JWT)
- Kredensial di `.env` (`FEEDER_HOST`, `FEEDER_USERNAME`, `FEEDER_PASSWORD`)
- Token JWT: decode payload (base64) untuk ambil `exp` (unix timestamp). Cache token di DB/memori. Sebelum tiap panggilan `Get*`, cek `exp` — jika kurang dari buffer 5 menit, panggil `GetToken` ulang sebelum lanjut.
- Fungsi dipakai: `GetToken`, `GetBiodataMahasiswa` (filter NIM), `GetNilaiPerkuliahanMahasiswa` (filter NIM). **Jangan implementasi fungsi Insert/Update/Delete apapun.**

## 4. Data Model (Django Models)

### User
Django default `User`. Hanya 1 akun dipakai, tapi model tetap standar (buka opsi multi-user nanti).

### Kurikulum
- `jenjang` (choice: D3/S1)
- `angkatan` (int, tahun)
- `template` (FK ke Template)
- `created_at`
- Unique together: (`jenjang`, `angkatan`)

### MataKuliahKurikulum
- `kurikulum` (FK ke Kurikulum)
- `kode_mk` (str)
- `nama_mk` (str)
- `sks` (int)
- `urutan_alfabet` (int, auto-generated saat parsing: hasil sort by `nama_mk` A-Z)

Saat upload file kurikulum (Excel): parsing ambil HANYA kolom Kode MK, Mata Kuliah, SKS (header lain diabaikan). Setelah parsing, sort seluruh baris berdasarkan `nama_mk` A-Z, simpan `urutan_alfabet` sesuai hasil sort.

### Template
- `nama` (str, misal "Template 34 MK D3")
- `file_excel` (FileField, upload template asli)
- `total_mk` (int) — kapasitas total MK yang ditampung (34/50/51/55)
- `baris_halaman_depan` (int) — jumlah baris MK di halaman 1 (sisanya otomatis ke halaman 2)
- `cell_mapping` (JSONField) — koordinat cell/named-range di template untuk field: nama, nim, prodi, tempat_lahir, tgl_lahir, dst, dan area mulai tabel MK (baris awal, kolom no/kode/nama/sks/nilai/lambang/mutu), area total SKS/IPK, area predikat, area judul skripsi, area no ijazah, area ttd dekan.

> Catatan build: `cell_mapping` didefinisikan manual sesuai struktur asli tiap file template setelah diupload dan diperiksa strukturnya.

### PredikatKelulusan
- `tahun_lulus_mulai` (int)
- `tahun_lulus_selesai` (int, nullable = berlaku seterusnya sampai ada aturan baru)
- `ipk_min` (decimal)
- `ipk_max` (decimal)
- `predikat` (str)

### DataPendukung (sumber: spreadsheet internal, di-upload/sync manual)
- `nim` (str, unique)
- `judul_skripsi` (str)
- `no_ijazah` (str)
- `nama_dekan` (str, default statis dari config, override per baris jika perlu)

### RiwayatGenerate
- `nim`
- `nama_mahasiswa`
- `jenjang`, `angkatan`
- `mode` (choice: single/batch)
- `jumlah_mk_retake` (int)
- `jumlah_mk_kosong` (int)
- `jumlah_mk_tidak_dikenali` (int)
- `status` (success/error)
- `error_message` (nullable)
- `generated_at` (datetime)
- `generated_by` (FK User)

## 5. Bobot Nilai (tetap semua angkatan)
```
A=4.00, A-=3.85, B+=3.25, B=3.00, B-=2.85, C+=2.35, C=2.00, lainnya=0
```
IPK = Σ(SKS × Bobot) / Σ SKS. Dihitung ulang sistem dari data nilai final per MK (setelah dedup retake).

## 6. Logika Pencocokan Data (per NIM)

1. `GetBiodataMahasiswa(nim)` → ambil jenjang, angkatan, tanggal_lulus.
2. Cari `Kurikulum` by (jenjang, angkatan) → ambil `Template` terkait & daftar `MataKuliahKurikulum` (urut `urutan_alfabet`).
3. `GetNilaiPerkuliahanMahasiswa(nim)` → list nilai per kode_mk (bisa >1 per kode jika retake).
4. Untuk tiap baris `MataKuliahKurikulum`:
   - Cari semua nilai API dengan `kode_mk` sama.
   - 0 hasil → baris kosong (kode_mk & nama_mk tetap tampil, nilai kosong), tandai **MERAH**.
   - 1 hasil → isi normal.
   - >1 hasil (retake) → ambil mutu tertinggi, tandai **KUNING**.
5. Data API dengan `kode_mk` yang TIDAK match ke manapun di kurikulum → tidak masuk transkrip, dicatat ke Sheet Laporan, tandai **ORANYE**.
6. Hitung total SKS, IPK dari hasil langkah 4.
7. Tentukan predikat: cari `PredikatKelulusan` yang mencakup `tahun_lulus` mahasiswa dan `ipk_min <= IPK <= ipk_max`.
8. Ambil `DataPendukung` by NIM (judul skripsi, no ijazah, nama dekan).

## 7. Fitur

### 7.1 Django Admin (data master)
- CRUD `Kurikulum`: pilih jenjang+angkatan+template, upload Excel → auto-parse & sort.
- CRUD `Template`: upload file + isi metadata (`total_mk`, `baris_halaman_depan`, `cell_mapping`).
- CRUD `PredikatKelulusan`.
- CRUD `DataPendukung` (manual atau nanti bisa import Excel — versi awal input manual/manual DB entry cukup).

### 7.2 Generate Single (input 1 NIM)
- Form input NIM → proses sesuai §6 → output file `.xlsx`:
  - Sheet 1: Transkrip (2 halaman via page break, sesuai template)
  - Sheet 2: Laporan (baris bermasalah + warna + keterangan sesuai §6)
- Simpan 1 baris ke `RiwayatGenerate`.

### 7.3 Generate Batch
- Upload file (.txt/.csv/.xlsx) berisi daftar NIM.
- Proses tiap NIM sesuai §6 (sinkron, tampilkan progress/loading). Rekomendasi batas ≤100 NIM/run.
- Group hasil per (jenjang, angkatan) → 1 sheet Excel per grup, isi berupa blok transkrip 2-halaman berurutan per mahasiswa (page break tiap blok agar cetak terpisah).
- Sheet Laporan gabungan di akhir file (semua NIM, semua grup).
- Simpan 1 baris `RiwayatGenerate` per NIM yang diproses (mode=batch).

### 7.4 Riwayat/Log
- List semua `RiwayatGenerate`, filter by tanggal/angkatan/status.
- Tampilan/export dikelompokkan per sheet per angkatan.

## 8. Non-Functional
- Read-only terhadap Neo Feeder: hanya fungsi `Get*` yang diimplementasi.
- Kredensial di `.env`, tidak hardcode.
- Login wajib untuk akses seluruh aplikasi (kecuali halaman login).
- Rate limit ringan antar panggilan API saat batch (jeda kecil antar NIM) agar tidak membebani Web Service Feeder.

## 9. Skema Warna Highlight (ringkasan)
| Warna | Kasus | Keterangan di Sheet Laporan |
|---|---|---|
| 🟡 Kuning | MK retake, otomatis ambil mutu tertinggi | "MK diulang, sistem mengambil nilai/mutu tertinggi" |
| 🔴 Merah | MK di kurikulum tanpa nilai di API | "Belum ada nilai untuk MK ini, perlu verifikasi" |
| 🟠 Oranye | Kode MK di API tak dikenali kurikulum | "Kode MK tidak dikenali, kemungkinan typo/transfer" |

## 10. Asumsi & Hal yang Perlu Diverifikasi Saat Build
- Path endpoint Web Service Feeder terkonfirmasi: `/ws/live2.php` (port 3003).
- Struktur JSON response `GetBiodataMahasiswa` & `GetNilaiPerkuliahanMahasiswa` perlu dicek langsung (field names) saat implementasi — sesuaikan parser.
- `cell_mapping` tiap Template perlu didefinisikan manual setelah file template asli diperiksa strukturnya (baris/kolom persis).
- Field `tanggal_lulus` dari `GetBiodataMahasiswa` perlu dikonfirmasi nama field-nya saat implementasi.

---

# PROMPT UNTUK AI BUILDER

```
Buatkan aplikasi web Django (Python) untuk generate Transkrip Akademik mahasiswa
otomatis dari Web Service Neo Feeder PDDIKTI, sesuai spesifikasi lengkap di PRD
terlampir (PRD_Sistem_Transkrip_Ijazah.md).

Poin wajib:
1. Integrasi Neo Feeder read-only: hanya panggil GetToken, GetBiodataMahasiswa,
   GetNilaiPerkuliahanMahasiswa. JANGAN implementasi fungsi Insert/Update/Delete.
2. Token JWT: decode payload untuk cek `exp`, auto-refresh proaktif sebelum expired
   (buffer 5 menit), cache token supaya tidak GetToken berulang.
3. Buat semua Django models sesuai §4 PRD (Kurikulum, MataKuliahKurikulum, Template,
   PredikatKelulusan, DataPendukung, RiwayatGenerate).
4. Django Admin: fitur upload Excel untuk Kurikulum (auto-parse kolom Kode MK/Mata
   Kuliah/SKS, auto-sort alfabetis by nama_mk) dan Template (upload file + input
   metadata total_mk, baris_halaman_depan, cell_mapping JSON).
5. Implementasi logika pencocokan data sesuai §6 PRD (dedup retake ambil mutu
   tertinggi, MK kosong tetap tampil kode+nama, kode tak dikenali masuk laporan)
   dengan skema warna sesuai §9.
6. Generate Excel pakai openpyxl, isi ke template asli by cell_mapping, JANGAN bikin
   layout baru dari nol — isi ke template yang sudah diupload user.
7. Dua mode generate: single NIM (form input) dan batch (upload file NIM,
   .txt/.csv/.xlsx, proses sinkron dengan loading indicator, output 1 file dengan
   sheet dikelompokkan per jenjang+angkatan, tiap mahasiswa tetap blok 2 halaman
   dengan page break).
8. Halaman Riwayat/Log: list RiwayatGenerate, filter tanggal/angkatan/status.
9. Login wajib (Django auth bawaan) untuk semua halaman.
10. Kredensial Feeder di .env, bukan hardcode.

Kerjakan bertahap: (1) models + migrasi, (2) service layer integrasi Feeder +
token handling, (3) admin upload & parsing Kurikulum/Template, (4) logika
pencocokan & perhitungan IPK/predikat, (5) generator Excel single, (6) generator
Excel batch, (7) halaman riwayat, (8) auth & polish UI dasar (Django templates,
tidak perlu framework frontend kompleks).

Jika ada bagian yang ambigu terkait struktur JSON response Feeder atau posisi
cell di template, tanyakan dulu sebelum asumsi.
```
