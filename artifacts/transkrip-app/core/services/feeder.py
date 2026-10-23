"""
Integrasi dengan Neo Feeder PDDIKTI.
Read-only: TIDAK mengimplementasi fungsi Insert/Update/Delete apapun.

Alur yang benar (sudah diverifikasi terhadap web service live2.php):

  GetToken                                   → token JWT
  GetDataLengkapMahasiswaProdi  filter nim   → profil + data akademik
                                               (tempat lahir, prodi, status, periode masuk)
    fallback: GetListMahasiswa  filter nim
  GetListRiwayatPendidikanMahasiswa filter nim → data registrasi & kelulusan
  GetDetailNilaiPerkuliahanKelas filter nim  → semua nilai MK mahasiswa
    fallback: GetRiwayatNilaiMahasiswa filter id_registrasi_mahasiswa

Catatan penting — INI AKAR MASALAH VERSI LAMA:
  * Kolom NIM di Feeder bernama `nim`, BUKAN `nim_mahasiswa`.
    Filter yang benar: nim='251030033' (nilai WAJIB dikutip).
  * TIDAK ADA fungsi `GetNilaiPerkuliahanMahasiswa`. Fungsi nilai yang tersedia
    adalah `GetDetailNilaiPerkuliahanKelas` (bisa langsung difilter by `nim`)
    dan `GetRiwayatNilaiMahasiswa` (harus difilter by `id_registrasi_mahasiswa`).
  * TIDAK ADA fungsi `GetRiwayatPendidikanMahasiswa`; yang ada adalah
    `GetListRiwayatPendidikanMahasiswa`. Memanggil nama yang salah membuat
    request menggantung (bukan sekadar error).
  * `GetBiodataMahasiswa` hanya menerima filter `id_mahasiswa` (tidak punya
    kolom `nim`).

Flow:
  get_biodata_mahasiswa(nim)           → biodata + data akademik
  get_riwayat_pendidikan(nim)          → status, tgl lulus, tahun lulus, lama studi
  get_nilai_perkuliahan_mahasiswa(nim) → list nilai MK
"""
import base64
import json
import time
import logging
from threading import Lock

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

_token_cache = {'token': None, 'exp': 0}
_token_lock = Lock()

BUFFER_MENIT = 5        # refresh token 5 menit sebelum expired
TIMEOUT_DETIK = 90      # timeout per request ke Feeder
HEALTH_CHECK_TIMEOUT = 5  # timeout untuk pemeriksaan koneksi singkat

# Error code Feeder yang artinya "kolom tidak dikenali di filter"
_FILTER_COLUMN_ERROR_CODES = {121, '121'}


def _decode_jwt_payload(token: str) -> dict:
    try:
        parts = token.split('.')
        if len(parts) != 3:
            return {}
        payload_b64 = parts[1]
        padding = 4 - len(payload_b64) % 4
        if padding != 4:
            payload_b64 += '=' * padding
        return json.loads(base64.urlsafe_b64decode(payload_b64).decode('utf-8'))
    except Exception as e:
        logger.warning(f"Gagal decode JWT payload: {e}")
        return {}


def _extract_token(response: dict) -> str | None:
    """Ambil token dari format Neo Feeder: data string, data.token, atau token."""
    if not isinstance(response, dict):
        return None
    token_data = response.get('data')
    if isinstance(token_data, dict):
        token_data = token_data.get('token') or token_data.get('Token')
    token = token_data or response.get('token')
    if not isinstance(token, str):
        return None
    token = token.strip()
    return token or None


def _get_feeder_url() -> str:
    host = settings.FEEDER_HOST
    if not host:
        raise ValueError("FEEDER_HOST belum dikonfigurasi di environment secrets")
    return f"http://{host}:3003/ws/live2.php"


class FeederFilterError(RuntimeError):
    """Raised ketika Feeder menolak filter karena kolom tidak dikenali (error_code 121)."""
    pass


def _call_feeder(payload: dict, timeout: int | None = None) -> dict:
    """
    Kirim POST ke Neo Feeder. Raise exception sesuai jenis error:
    - FeederFilterError  : error_code 121 (kolom filter tidak dikenali)
    - RuntimeError       : error logis Feeder lainnya
    - ConnectionError    : tidak bisa konek
    - TimeoutError       : timeout
    """
    url = _get_feeder_url()
    headers = {'Content-Type': 'application/json', 'Connection': 'close'}
    try:
        resp = requests.post(
            url,
            json=payload,
            headers=headers,
            timeout=timeout if timeout is not None else TIMEOUT_DETIK,
        )
        resp.raise_for_status()
        data = resp.json()

        if isinstance(data, dict):
            err_code = data.get('error_code')
            if err_code not in (None, 0, '0', ''):
                err_desc = data.get('error_desc', 'Tidak ada deskripsi')
                act = payload.get('act', '?')
                if err_code in _FILTER_COLUMN_ERROR_CODES:
                    raise FeederFilterError(
                        f"Filter tidak dikenali pada '{act}' (error_code={err_code}): {err_desc}"
                    )
                raise RuntimeError(
                    f"Feeder menolak '{act}' (error_code={err_code}): {err_desc}"
                )
        return data

    except requests.exceptions.ConnectionError as e:
        raise ConnectionError(
            f"Tidak bisa terhubung ke Neo Feeder ({url}). "
            f"Pastikan FEEDER_HOST sudah benar dan server aktif. Detail: {e}"
        )
    except requests.exceptions.Timeout:
        raise TimeoutError(
            f"Koneksi ke Neo Feeder timeout (>{timeout or TIMEOUT_DETIK} detik) "
            f"saat memanggil '{payload.get('act', '?')}'. "
            f"Server mungkin tidak bisa diakses dari jaringan ini (Replit ≠ intranet kampus)."
        )
    except (FeederFilterError, RuntimeError, ConnectionError, TimeoutError):
        raise
    except Exception as e:
        raise RuntimeError(f"Error memanggil Neo Feeder '{payload.get('act', '?')}': {e}")


def get_token(timeout: int = TIMEOUT_DETIK) -> str:
    """
    Ambil token Neo Feeder.
    Gunakan cache; refresh otomatis jika akan expired dalam BUFFER_MENIT menit.
    """
    with _token_lock:
        now = time.time()
        if _token_cache['token'] and _token_cache['exp'] - now > BUFFER_MENIT * 60:
            return _token_cache['token']

        logger.info("Mengambil token baru dari Neo Feeder...")
        payload = {
            "act": "GetToken",
            "username": settings.FEEDER_USERNAME,
            "password": settings.FEEDER_PASSWORD,
        }
        response = _call_feeder(payload, timeout=timeout)

        token = _extract_token(response)
        if not token:
            raise ValueError(f"GetToken tidak mengembalikan token. Response: {response}")

        payload_data = _decode_jwt_payload(token)
        exp = payload_data.get('exp', now + 3600)
        _token_cache['token'] = token
        _token_cache['exp'] = exp
        logger.info(f"Token berhasil didapat, berlaku hingga: {exp}")
        return token


def check_feeder_connection() -> tuple[bool, str]:
    """Periksa apakah Neo Feeder dapat dijangkau dan memberikan token."""
    return check_connection()


def _extract_list_from_response(response) -> list:
    """Ekstrak list dari berbagai format response Feeder."""
    if isinstance(response, list):
        return response
    if isinstance(response, dict):
        data = response.get('data', response)
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return [data]
        if data in (None, '', []):
            return []
    return []


def _field(row: dict, *keys, default='') -> str:
    for key in keys:
        if key in row and row[key] is not None:
            return str(row[key]).strip()
    return default


def _field_int(row: dict, *keys, default=0) -> int:
    for key in keys:
        if key in row and row[key] is not None:
            try:
                return int(row[key])
            except (TypeError, ValueError):
                pass
    return default


def _tahun_dari_periode(periode: str) -> int:
    """Ambil tahun dari id periode (mis. '20251' → 2025)."""
    teks = str(periode or '').strip()
    if len(teks) >= 4 and teks[:4].isdigit():
        return int(teks[:4])
    return 0


def _tahun_dari_tanggal(tanggal: str) -> int:
    """Ambil tahun dari tanggal 'DD-MM-YYYY' / 'YYYY-MM-DD' / 'YYYY'."""
    teks = str(tanggal or '').strip()
    if len(teks) >= 4 and teks[:4].isdigit():
        return int(teks[:4])
    bagian = teks.replace('/', '-').split('-')
    for b in reversed(bagian):
        if len(b) == 4 and b.isdigit():
            return int(b)
    return 0


def _infer_jenjang(nama_prodi: str) -> str:
    """
    Tebak jenjang dari nama program studi, dikembalikan dalam bentuk yang
    dikenali oleh normalisasi di core/views.py (S1/D3/D4/Profesi/...).
    """
    n = (nama_prodi or '').upper()
    if 'PROFESI' in n or 'NERS' in n:
        return 'Profesi'
    if 'D3' in n or 'DIPLOMA TIGA' in n or 'DIPLOMA III' in n or 'AHLI MADYA' in n:
        return 'D3'
    if 'D4' in n or 'DIPLOMA EMPAT' in n or 'DIPLOMA IV' in n or 'SARJANA TERAPAN' in n:
        return 'Sarjana Terapan'
    if 'S2' in n or 'MAGISTER' in n:
        return 'S2'
    if 'S1' in n or 'SARJANA' in n or 'STRATA SATU' in n:
        return 'S1'
    return ''


def _call_feeder_paged(act: str, filter_: str | None = None, order: str = '',
                       limit: int = 200, max_pages: int = 50) -> list:
    """Panggil act list Feeder dengan pagination, gabungkan semua halaman."""
    token = get_token()
    semua: list = []
    offset = 0
    for _ in range(max_pages):
        payload = {"act": act, "token": token, "limit": limit, "offset": offset, "order": order}
        if filter_:
            payload["filter"] = filter_
        rows = _extract_list_from_response(_call_feeder(payload))
        semua.extend(rows)
        if len(rows) < limit:
            break
        offset += limit
    return semua


# ─── Biodata & riwayat pendidikan ───────────────────────────────────────────────

def _find_biodata_by_nim(nim: str, token: str) -> dict:
    """
    Ambil biodata + data akademik mahasiswa berdasarkan NIM.

    1. Utama : GetDataLengkapMahasiswaProdi filter nim (paling lengkap:
               tempat lahir, prodi, status, periode masuk, id_registrasi).
    2. Fallback: GetListMahasiswa filter nim.
    3. Pelengkap: GetBiodataMahasiswa filter id_mahasiswa (bila tempat_lahir kosong).
    """
    nim = str(nim).strip()
    filter_str = f"nim='{nim}'"

    rows: list = []
    try:
        rows = _extract_list_from_response(
            _call_feeder({
                "act": "GetDataLengkapMahasiswaProdi",
                "token": token,
                "filter": filter_str,
                "order": "",
                "limit": 50,
                "offset": 0,
            })
        )
    except FeederFilterError as e:
        logger.warning(f"GetDataLengkapMahasiswaProdi filter nim ditolak: {e}")
    except (ConnectionError, TimeoutError):
        raise
    except RuntimeError as e:
        logger.warning(f"GetDataLengkapMahasiswaProdi error: {e}")

    exact = [r for r in rows if _field(r, "nim") == nim]

    if not exact:
        try:
            rows = _extract_list_from_response(
                _call_feeder({
                    "act": "GetListMahasiswa",
                    "token": token,
                    "filter": filter_str,
                    "order": "",
                    "limit": 50,
                    "offset": 0,
                })
            )
        except FeederFilterError as e:
            logger.warning(f"GetListMahasiswa filter nim ditolak: {e}")
            rows = []
        except (ConnectionError, TimeoutError):
            raise
        except RuntimeError as e:
            logger.warning(f"GetListMahasiswa error: {e}")
            rows = []
        exact = [r for r in rows if _field(r, "nim") == nim]

    if not exact:
        raise ValueError(
            f"Mahasiswa dengan NIM '{nim}' tidak ditemukan di Feeder. "
            f"Cek kembali NIM atau pastikan data mahasiswa sudah sync."
        )

    # Ambil entri terbaru bila ada duplikat
    data = dict(exact[-1])

    id_mahasiswa = _field(data, "id_mahasiswa")
    if not id_mahasiswa:
        id_mahasiswa = _field(data, "id_mahasiswa")
    if not _field(data, "tempat_lahir") and id_mahasiswa:
        try:
            bio_rows = _extract_list_from_response(
                _call_feeder({
                    "act": "GetBiodataMahasiswa",
                    "token": token,
                    "filter": f"id_mahasiswa='{id_mahasiswa}'",
                    "order": "",
                    "limit": 1,
                    "offset": 0,
                })
            )
            if bio_rows:
                bio = bio_rows[0]
                for key in ("tempat_lahir", "tanggal_lahir", "jenis_kelamin", "nik"):
                    if not _field(data, key) and _field(bio, key):
                        data[key] = bio[key]
        except (FeederFilterError, RuntimeError) as e:
            logger.warning(f"GetBiodataMahasiswa pelengkap gagal untuk NIM {nim}: {e}")
        except (ConnectionError, TimeoutError):
            raise

    # Angkatan dari periode masuk (mis. '20251' → 2025)
    periode = _field(data, "id_periode_masuk", "id_periode")
    tahun = _tahun_dari_periode(periode)
    if tahun:
        data["angkatan"] = tahun
        data["tahun_masuk"] = tahun

    # Jenjang dari nama prodi
    jenjang = _infer_jenjang(_field(data, "nama_program_studi", "prodi", "nama_prodi"))
    if jenjang:
        data["jenjang"] = jenjang

    # Field kompatibilitas
    if not _field(data, "nim_mahasiswa"):
        data["nim_mahasiswa"] = nim
    if not _field(data, "nama_mahasiswa"):
        data["nama_mahasiswa"] = _field(data, "nama")
    if not _field(data, "nama_program_studi"):
        data["nama_program_studi"] = _field(data, "prodi", "nama_prodi")
    if not _field(data, "status_mahasiswa"):
        data["status_mahasiswa"] = _field(data, "nama_status_mahasiswa")

    return data


def _find_riwayat_pendidikan_by_nim(nim: str, token: str) -> list:
    """Ambil baris GetListRiwayatPendidikanMahasiswa untuk NIM (filter `nim`)."""
    try:
        rows = _extract_list_from_response(
            _call_feeder({
                "act": "GetListRiwayatPendidikanMahasiswa",
                "token": token,
                "filter": f"nim='{nim}'",
                "order": "",
                "limit": 50,
                "offset": 0,
            })
        )
    except FeederFilterError as e:
        logger.warning(f"Filter GetListRiwayatPendidikanMahasiswa ditolak: {e}")
        return []
    except (ConnectionError, TimeoutError):
        raise
    except RuntimeError as e:
        logger.warning(f"GetListRiwayatPendidikanMahasiswa error: {e}")
        return []
    return [r for r in rows if _field(r, "nim") == nim] or rows


def _hitung_lama_studi(periode_masuk: str, periode_keluar: str) -> str:
    """Hitung lama studi (jumlah semester) dari id periode masuk → keluar."""
    p1, p2 = str(periode_masuk or '').strip(), str(periode_keluar or '').strip()
    if len(p1) < 5 or len(p2) < 5 or not (p1[:4].isdigit() and p2[:4].isdigit()):
        return ''
    s1 = int(p1[:4]) * 2 + (1 if p1[4] == '1' else 2)
    s2 = int(p2[:4]) * 2 + (1 if p2[4] == '1' else 2)
    jumlah = s2 - s1 + 1
    return f"{jumlah} semester" if jumlah > 0 else ''


def get_riwayat_pendidikan(nim: str) -> dict:
    """
    Ambil data kelulusan mahasiswa dari GetListRiwayatPendidikanMahasiswa:
    status, tanggal lulus, tahun lulus, lama studi.

    Return dict ternormalisasi (field yang dikenali parse_biodata), atau {}
    bila tidak ditemukan — kegagalan di sini tidak menggagalkan generate.
    ConnectionError/TimeoutError tetap di-raise (Feeder tidak terjangkau).
    """
    token = get_token()
    rows = _find_riwayat_pendidikan_by_nim(nim, token)
    if not rows:
        logger.warning(f"Riwayat pendidikan NIM {nim} tidak ditemukan di Feeder.")
        return {}

    data = dict(rows[-1])  # entri terbaru

    # Normalisasi agar dikenali parse_biodata (lihat core/services/matching.py)
    if not _field(data, "status_mahasiswa"):
        ket = _field(data, "keterangan_keluar", "nama_jenis_keluar")
        if ket:
            data["status_mahasiswa"] = ket

    tanggal_keluar = _field(data, "tanggal_keluar", "tanggal_kelulusan")
    if tanggal_keluar and not _field(data, "tanggal_kelulusan"):
        data["tanggal_kelulusan"] = tanggal_keluar

    if not _field(data, "tahun_kelulusan"):
        tahun = _tahun_dari_tanggal(tanggal_keluar) or _tahun_dari_periode(_field(data, "id_periode_keluar"))
        if tahun:
            data["tahun_kelulusan"] = tahun

    if not _field(data, "lama_studi"):
        lama = _hitung_lama_studi(
            _field(data, "id_periode_masuk"), _field(data, "id_periode_keluar")
        )
        if lama:
            data["lama_studi"] = lama

    return data


# ─── Public API ────────────────────────────────────────────────────────────────

def get_biodata_mahasiswa(nim: str) -> dict:
    """Ambil biodata + data akademik mahasiswa berdasarkan NIM."""
    token = get_token()
    return _find_biodata_by_nim(nim, token)


def get_program_mahasiswa(nim: str) -> dict:
    """Alias get_biodata_mahasiswa untuk kompatibilitas internal."""
    return get_biodata_mahasiswa(nim)


# ─── Master data (prodi, kurikulum, MK) ─────────────────────────────────────────

def get_daftar_prodi(keyword: str = '') -> list:
    """
    Ambil daftar program studi dari Feeder (GetListProgramStudi).
    `keyword` (opsional) memfilter by id_prodi atau potongan nama prodi.
    """
    rows = _call_feeder_paged('GetListProgramStudi')
    if not keyword:
        return rows
    kw = keyword.strip().lower()
    return [
        r for r in rows
        if kw in _field(r, 'id_prodi', 'id_program_studi').lower()
        or kw in _field(r, 'nama_program_studi', 'nama_prodi').lower()
        or kw in _field(r, 'kode_program_studi', 'kode_prodi').lower()
    ]


def get_daftar_kurikulum(prodi_id: str = '') -> list:
    """
    Ambil daftar kurikulum dari Feeder (GetListKurikulum).
    Filter opsional by id_prodi.
    """
    filter_ = f"id_prodi='{prodi_id}'" if prodi_id else None
    return _call_feeder_paged('GetListKurikulum', filter_)


def get_mk_kurikulum(id_kurikulum: str) -> list:
    """
    Ambil daftar mata kuliah sebuah kurikulum (GetListMataKuliahKurikulum).
    Fallback ke GetListMataKuliah bila act/filter tidak tersedia di Feeder.
    Return list dict ternormalisasi: {kode_mk, nama_mk, sks, semester}.
    Baris tanpa kode atau nama dilewati.
    """
    filter_ = f"id_kurikulum='{id_kurikulum}'"
    try:
        rows = _call_feeder_paged('GetListMataKuliahKurikulum', filter_)
    except FeederFilterError:
        logger.warning(
            'GetListMataKuliahKurikulum tidak tersedia di Feeder ini, '
            'fallback ke GetListMataKuliah (semua MK).'
        )
        rows = _call_feeder_paged('GetListMataKuliah')

    hasil = []
    for r in rows:
        kode = _field(r, 'kode_mata_kuliah', 'kode_mk', 'kode_matkul', 'kode')
        nama = _field(r, 'nama_mata_kuliah', 'nama_mk', 'nama_matkul', 'mata_kuliah')
        if not kode or not nama:
            continue
        hasil.append({
            'kode_mk': kode,
            'nama_mk': nama,
            'sks': _field_int(r, 'sks_mata_kuliah', 'sks_mk', 'sks'),
            'semester': _field(r, 'sem_mk', 'semester', 'id_semester'),
        })
    return hasil


def get_nilai_perkuliahan_mahasiswa(nim: str) -> list:
    """
    Ambil riwayat nilai mahasiswa berdasarkan NIM.

    1. Utama  : GetDetailNilaiPerkuliahanKelas filter nim='<nim>'
                (langsung per NIM; memuat kode & nama MK, SKS, nilai huruf/angka).
    2. Fallback: GetRiwayatNilaiMahasiswa filter id_registrasi_mahasiswa (dari biodata).

    Return list kosong bila mahasiswa valid tetapi belum punya nilai,
    agar halaman generate bisa memakai fallback DHS eCampuz.
    """
    nim = str(nim).strip()

    rows: list = []
    try:
        rows = _call_feeder_paged(
            'GetDetailNilaiPerkuliahanKelas',
            filter_=f"nim='{nim}'",
            order='id_semester,kode_mata_kuliah',
        )
    except FeederFilterError as e:
        logger.warning(
            f"GetDetailNilaiPerkuliahanKelas filter nim ditolak ({e}); "
            f"fallback ke GetRiwayatNilaiMahasiswa."
        )
        rows = []

    if not rows:
        id_registrasi = None
        try:
            biodata = _find_biodata_by_nim(nim, get_token())
            id_registrasi = (
                biodata.get('id_registrasi_mahasiswa')
                or biodata.get('id_registrasi')
            )
        except (ConnectionError, TimeoutError):
            raise
        except Exception as e:
            logger.warning(f"Tidak bisa ambil id_registrasi untuk {nim}: {e}")

        if id_registrasi:
            try:
                rows = _call_feeder_paged(
                    'GetRiwayatNilaiMahasiswa',
                    filter_=f"id_registrasi_mahasiswa='{id_registrasi}'",
                )
            except FeederFilterError as e:
                logger.warning(f"GetRiwayatNilaiMahasiswa ditolak ({e}).")
                rows = []

    logger.info(f"GetNilai NIM {nim}: {len(rows)} baris nilai.")
    return rows


def clear_token_cache():
    with _token_lock:
        _token_cache['token'] = None
        _token_cache['exp'] = 0


# ─── Connection check (untuk dashboard) ───────────────────────────────────────

_conn_cache = {'ok': None, 'msg': '', 'checked_at': 0}
_conn_lock = Lock()
CONN_CACHE_TTL = 60


def check_connection() -> tuple[bool, str]:
    with _conn_lock:
        now = time.time()
        if _conn_cache['ok'] is not None and now - _conn_cache['checked_at'] < CONN_CACHE_TTL:
            return _conn_cache['ok'], _conn_cache['msg']

    host = getattr(settings, 'FEEDER_HOST', '')
    username = getattr(settings, 'FEEDER_USERNAME', '')
    password = getattr(settings, 'FEEDER_PASSWORD', '')
    if not (host and username and password):
        result = (False, 'Konfigurasi FEEDER_HOST / USERNAME / PASSWORD belum lengkap.')
        _update_conn_cache(*result)
        return result

    url = f"http://{host}:3003/ws/live2.php"
    try:
        resp = requests.post(
            url,
            json={"act": "GetToken", "username": username, "password": password},
            headers={'Content-Type': 'application/json', 'Connection': 'close'},
            timeout=5,
        )
        resp.raise_for_status()
        data = resp.json()
        token = _extract_token(data)

        if token:
            payload_data = _decode_jwt_payload(token)
            exp = payload_data.get('exp', time.time() + 3600)
            with _token_lock:
                _token_cache['token'] = token
                _token_cache['exp'] = exp
            result = (True, host)
        else:
            result = (False, f'Server merespons tapi GetToken gagal: {data}')

    except requests.exceptions.ConnectionError:
        result = (False, f'Tidak bisa menjangkau {host}:3003 — server mati atau IP salah.')
    except requests.exceptions.Timeout:
        result = (False, f'Koneksi ke {host}:3003 timeout (>5 detik).')
    except requests.exceptions.HTTPError as e:
        result = (False, f'HTTP error dari Feeder: {e}')
    except Exception as e:
        result = (False, f'Error: {e}')

    _update_conn_cache(*result)
    return result


def _update_conn_cache(ok: bool, msg: str):
    with _conn_lock:
        _conn_cache['ok'] = ok
        _conn_cache['msg'] = msg
        _conn_cache['checked_at'] = time.time()
