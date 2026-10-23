"""
Logika pencocokan data nilai mahasiswa dengan kurikulum.
Sesuai PRD §6 dan §5 (bobot nilai).
"""
import logging
import re
import unicodedata
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

# Bobot nilai sesuai PRD §5 — tetap untuk semua angkatan
BOBOT_NILAI = {
    'A': Decimal('4.00'),
    'A-': Decimal('3.85'),
    'B+': Decimal('3.25'),
    'B': Decimal('3.00'),
    'B-': Decimal('2.85'),
    'C+': Decimal('2.35'),
    'C': Decimal('2.00'),
    'D': Decimal('1.00'),
    'E': Decimal('0.00'),
}

# Lambang yang berarti TIDAK LULUS → SKS tidak dihitung ke total SKS & IPK
NILAI_TIDAK_LULUS = {'E'}

# Toleransi pencocokan nilai angka → lambang (Feeder kadang mengirim nilai numerik)
TOLERANSI_ANGKA = Decimal('0.15')


def get_bobot(nilai_lambang: str) -> Decimal:
    """Konversi lambang nilai ke bobot angka (case-insensitive). Selain daftar = 0."""
    if nilai_lambang is None or str(nilai_lambang).strip() == '':
        return Decimal('0')
    return BOBOT_NILAI.get(str(nilai_lambang).strip().upper(), Decimal('0'))


def _ke_decimal(nilai) -> Optional[Decimal]:
    """Parse nilai apa pun (koma/poin) ke Decimal. None jika bukan angka."""
    if nilai is None:
        return None
    s = str(nilai).strip().replace(',', '.')
    if not s:
        return None
    try:
        d = Decimal(s)
    except (InvalidOperation, ValueError):
        return None
    return d if d.is_finite() else None


def _normalisasi_nama(s) -> str:
    """
    Normalisasi nama MK untuk pencocokan by nama:
    huruf kecil, tanpa spasi/tanda baca, tanpa aksen.
    """
    if s is None:
        return ''
    s = unicodedata.normalize('NFKD', str(s).strip().lower())
    s = ''.join(c for c in s if not unicodedata.combining(c))
    return re.sub(r'[^a-z0-9]', '', s)


def konversi_angka_ke_lambang(nilai) -> Optional[str]:
    """
    Konversi nilai numerik (mis. '3.85') ke lambang nilai.
    Return None jika tidak ada lambang yang cocok dalam toleransi.
    """
    a = _ke_decimal(nilai)
    if a is None:
        return None
    # 1) Cocok persis dengan tabel bobot
    for lambang, bobot in BOBOT_NILAI.items():
        if bobot == a:
            return lambang
    # 2) Terdekat dengan toleransi
    terdekat, selisih = None, None
    for lambang, bobot in BOBOT_NILAI.items():
        s = abs(bobot - a)
        if selisih is None or s < selisih:
            terdekat, selisih = lambang, s
    if selisih is not None and selisih <= TOLERANSI_ANGKA:
        return terdekat
    return None


@dataclass
class NilaiAkhir:
    """Hasil resolusi satu baris nilai dari Feeder."""
    lambang: str = ''
    angka: str = ''
    bobot: Decimal = Decimal('0')
    tidak_lulus: bool = False   # nilai E → SKS tidak dihitung
    terbaca: bool = False       # ada grade yang bisa dipakai hitung IPK
    peringatan: str = ''


def resolve_nilai(nilai_lambang, nilai_angka) -> NilaiAkhir:
    """
    Tentukan lambang, angka, bobot, dan status lulus dari satu baris nilai Feeder.
    Prioritas: lambang dikenal → nilai angka (fallback) → tidak terbaca.
    Mencegah nilai 'hilang diam-diam' karena nama field/format tidak dikenali.
    """
    lambang = str(nilai_lambang).strip() if nilai_lambang else ''
    angka = str(nilai_angka).strip() if nilai_angka else ''
    if lambang.upper() in BOBOT_NILAI:
        lambang = lambang.upper()
    angka_dec = _ke_decimal(angka)

    # 1. Lambang dikenal tabel bobot
    if lambang in BOBOT_NILAI:
        bobot = BOBOT_NILAI[lambang]
        if angka_dec is None:
            angka = f"{bobot:.2f}"
        return NilaiAkhir(
            lambang=lambang, angka=angka, bobot=bobot,
            tidak_lulus=lambang in NILAI_TIDAK_LULUS,
            terbaca=True,
        )

    # 2. Lambang kosong/tidak dikenal → andalkan nilai angka
    if angka_dec is not None:
        if not lambang:
            lambang = konversi_angka_ke_lambang(angka_dec) or ''
            if not lambang:
                return NilaiAkhir(
                    angka=f"{angka_dec:.2f}", bobot=angka_dec, terbaca=True,
                    peringatan=(
                        f'Nilai angka {angka_dec} tidak bisa dikonversi ke lambang; '
                        f'bobot dipakai langsung dari angka'
                    ),
                )
        else:
            logger.debug(f'Lambang "{lambang}" di luar tabel bobot, pakai angka {angka_dec}')
        return NilaiAkhir(
            lambang=lambang, angka=angka or f"{angka_dec:.2f}", bobot=angka_dec,
            tidak_lulus=lambang in NILAI_TIDAK_LULUS,
            terbaca=True,
            peringatan=(
                f'Lambang "{lambang}" tidak ada di tabel bobot, '
                f'bobot diambil dari nilai angka {angka_dec}'
                if lambang not in BOBOT_NILAI else ''
            ),
        )

    # 3. Tidak ada lambang & tidak ada angka → tidak terbaca
    if lambang:
        peringatan = f'Lambang "{lambang}" tidak dikenal dan tidak ada nilai angka'
    else:
        peringatan = 'Nilai kosong dari Feeder'
    return NilaiAkhir(lambang=lambang, angka=angka, bobot=Decimal('0'),
                      terbaca=False, peringatan=peringatan)


@dataclass
class HasilMK:
    """Hasil pencocokan satu mata kuliah."""
    kode_mk: str
    nama_mk: str
    sks: int
    urutan: int
    # Hasil pencocokan
    nilai_lambang: Optional[str] = None   # misal 'A', 'B+', dll
    nilai_angka: Optional[str] = None     # nilai numerik (string)
    bobot: Decimal = Decimal('0')
    mutu: Decimal = Decimal('0')          # SKS × bobot
    # Status
    is_retake: bool = False               # ada nilai >1 → ambil tertinggi
    is_kosong: bool = False               # tidak ada nilai di API
    is_tidak_lulus: bool = False          # nilai E → SKS tidak dihitung
    is_tidak_terbaca: bool = False        # baris nilai ada tapi gagal dibaca
    is_tidak_dikenali: bool = False       # MK dari API tak ada di kurikulum (tetap dihitung)
    cocok_by_nama: bool = False           # kode beda tetapi nama cocok
    sks_api: int = 0                      # SKS menurut Feeder (untuk cross-check)
    catatan: str = ''                     # keterangan untuk sheet Laporan
    # Catatan retake
    semua_nilai: list = field(default_factory=list)


@dataclass
class HasilTidakDikenali:
    """MK dari API yang kode-nya tidak ada di kurikulum."""
    kode_mk: str
    nama_mk: str
    sks_api: str
    nilai_lambang: str
    nilai_angka: str


@dataclass
class HasilPencocokan:
    """Hasil lengkap pencocokan satu mahasiswa."""
    nim: str
    nama: str
    jenjang: str
    angkatan: int
    prodi: str
    tempat_lahir: str
    tanggal_lahir: str
    tanggal_lulus: str
    tahun_lulus: int
    status_mahasiswa: str = ''
    lama_studi: str = ''

    daftar_mk: list[HasilMK] = field(default_factory=list)
    mk_tidak_dikenali: list[HasilTidakDikenali] = field(default_factory=list)

    total_sks: int = 0
    total_mutu: Decimal = Decimal('0')
    ipk: Decimal = Decimal('0')
    predikat: str = ''

    jumlah_mk_retake: int = 0
    jumlah_mk_kosong: int = 0
    jumlah_mk_tidak_dikenali: int = 0
    jumlah_mk_tidak_terbaca: int = 0
    jumlah_mk_sks_beda: int = 0
    mk_tidak_dikenali_dihitung: bool = False
    # 'feeder' = nilai dari Neo Feeder (normal); 'dhs' = fallback hasil scraping eCampuz
    sumber_nilai: str = 'feeder'


def _extract_field(data: dict, *keys, default='') -> str:
    """Coba beberapa nama field alternatif, return yang pertama ditemukan."""
    for key in keys:
        if key in data and data[key] is not None:
            return str(data[key]).strip()
    return default


def _extract_int(data: dict, *keys, default=0) -> int:
    for key in keys:
        if key in data and data[key] is not None:
            try:
                return int(data[key])
            except (ValueError, TypeError):
                pass
    return default


def parse_biodata(biodata: dict, riwayat_pendidikan: dict | None = None) -> dict:
    """
    Normalisasi biodata dari berbagai kemungkinan nama field Neo Feeder.
    Field names dari dokumentasi Feeder bisa berbeda-beda versi.

    `riwayat_pendidikan` (opsional) adalah baris dari GetListRiwayatPendidikanMahasiswa,
    dipakai sebagai sumber utama data kelulusan: tanggal lulus, tahun lulus,
    status mahasiswa, dan lama studi — karena field-field itu umumnya TIDAK ada
    di GetBiodataMahasiswa.
    """
    rp = riwayat_pendidikan or {}

    tanggal_lulus = (
        _extract_field(
            biodata,
            'tanggal_lulus', 'tgl_lulus', 'tanggal_kelulusan',
            'tgl_kelulusan', 'tanggal_sidang', 'tgl_sidang',
        )
        or _extract_field(
            rp,
            'tanggal_kelulusan', 'tgl_kelulusan', 'tanggal_lulus',
            'tgl_lulus', 'tanggal_kelulusan_mahasiswa', 'tanggal_wisuda',
        )
    )

    # Tahun lulus: dari riwayat pendidikan bila ada, selain itu dari parse tanggal
    tahun_lulus = _extract_int(
        rp, 'tahun_kelulusan', 'tahun_lulus', 'tahun_kelulusan_mahasiswa'
    ) or _parse_tahun_lulus(tanggal_lulus)

    status_mahasiswa = (
        _extract_field(rp, 'status_mahasiswa', 'status_kelulusan', 'status')
        or _extract_field(biodata, 'status_mahasiswa', 'status_kelulusan')
    )

    lama_studi = _extract_field(
        rp, 'lama_studi', 'lama_studi_semester', 'lamastudi'
    ) or _extract_field(biodata, 'lama_studi', 'lama_studi_semester')

    return {
        'nim': _extract_field(biodata, 'nim_mahasiswa', 'nim'),
        'nama': _extract_field(biodata, 'nama_mahasiswa', 'nama'),
        'jenjang': _extract_field(biodata, 'jenjang_didik', 'jenjang', 'id_jenjang'),
        'angkatan': _extract_int(biodata, 'tahun_masuk', 'angkatan'),
        'prodi': _extract_field(biodata, 'nama_program_studi', 'prodi', 'nama_prodi'),
        'id_prodi': (_extract_field(biodata, 'id_prodi', 'id_program_studi')
                     or _extract_field(rp, 'id_prodi', 'id_program_studi')),
        'tempat_lahir': _extract_field(biodata, 'tempat_lahir', 'kota_lahir'),
        'tanggal_lahir': _extract_field(biodata, 'tanggal_lahir', 'tgl_lahir'),
        'tanggal_lulus': tanggal_lulus,
        'tahun_lulus': tahun_lulus,
        'status_mahasiswa': status_mahasiswa,
        'lama_studi': lama_studi,
    }


def _parse_tahun_lulus(tanggal_lulus: str) -> int:
    """Ekstrak tahun dari string tanggal (YYYY-MM-DD atau DD/MM/YYYY atau YYYY)."""
    if not tanggal_lulus:
        return 0
    # Coba format YYYY-MM-DD
    if len(tanggal_lulus) >= 4:
        tahun_str = tanggal_lulus[:4]
        try:
            return int(tahun_str)
        except ValueError:
            pass
    # Coba DD/MM/YYYY
    if '/' in tanggal_lulus:
        parts = tanggal_lulus.split('/')
        if len(parts) == 3:
            try:
                return int(parts[2])
            except ValueError:
                pass
    return 0


def cocokan_nilai(
    daftar_mk_kurikulum: list,  # list MataKuliahKurikulum objects
    daftar_nilai_api: list,     # list dict nilai dari Feeder
    biodata_parsed: dict,
    predikat_qs,                # queryset PredikatKelulusan
    data_pendukung=None,        # optional DataPendukung object
    default_nama_dekan: str = '',
    include_mk_tidak_dikenali: bool = True,
    sumber_nilai: str = 'feeder',
) -> HasilPencocokan:
    """
    Lakukan pencocokan nilai mahasiswa dengan kurikulum.
    Implementasi sesuai PRD §6.

    Pencocokan dilakukan 2 tahap:
      1. by kode MK (hurut kecil)
      2. fallback by nama MK ternormalisasi (kode Feeder ≠ kode kurikulum)

    `include_mk_tidak_dikenali=True`: MK dari Feeder yang tidak ada di kurikulum
    tetap dihitung ke total SKS & IPK (pakai SKS dari Feeder) dan dicatat di
    laporan — mencegah IPK meleset karena MK elektif/tidak terdokumentasi.
    """
    tanggal_lulus = biodata_parsed.get('tanggal_lulus', '')
    tahun_lulus = biodata_parsed.get('tahun_lulus', 0) or _parse_tahun_lulus(tanggal_lulus)

    hasil = HasilPencocokan(
        nim=biodata_parsed.get('nim', ''),
        nama=biodata_parsed.get('nama', ''),
        jenjang=biodata_parsed.get('jenjang', ''),
        angkatan=biodata_parsed.get('angkatan', 0),
        prodi=biodata_parsed.get('prodi', ''),
        tempat_lahir=biodata_parsed.get('tempat_lahir', ''),
        tanggal_lahir=biodata_parsed.get('tanggal_lahir', ''),
        tanggal_lulus=tanggal_lulus,
        tahun_lulus=tahun_lulus,
        status_mahasiswa=biodata_parsed.get('status_mahasiswa', ''),
        lama_studi=biodata_parsed.get('lama_studi', ''),
        mk_tidak_dikenali_dihitung=include_mk_tidak_dikenali,
        sumber_nilai=sumber_nilai,
    )

    # Index nilai API berdasarkan kode MK (lower) & nama MK ternormalisasi
    by_kode: dict[str, list[dict]] = {}
    by_nama: dict[str, list[dict]] = {}
    for nilai in daftar_nilai_api:
        kode = _extract_field(nilai, 'kode_mata_kuliah', 'kode_mk', 'kode_matkul').lower()
        nama = _normalisasi_nama(
            _extract_field(nilai, 'nama_mata_kuliah', 'nama_mk', 'nama_matkul')
        )
        if kode:
            by_kode.setdefault(kode, []).append(nilai)
        if nama:
            by_nama.setdefault(nama, []).append(nilai)
        nilai['_nama_norm'] = nama

    kode_kurikulum_dikenali: set[str] = set()
    nama_kurikulum_dikenali: set[str] = set()

    # Proses setiap MK di kurikulum (urutan alfabetis)
    for mk in daftar_mk_kurikulum:
        kode = str(mk.kode_mk).strip().lower()
        kode_kurikulum_dikenali.add(kode)
        nama_kurikulum_dikenali.add(_normalisasi_nama(mk.nama_mk))

        # 1) cocok by kode, 2) fallback by nama
        ditemukan = by_kode.get(kode, [])
        cocok_by_nama = False
        if not ditemukan:
            nama_norm = _normalisasi_nama(mk.nama_mk)
            if nama_norm:
                via_nama = by_nama.get(nama_norm, [])
                if via_nama:
                    ditemukan = via_nama
                    cocok_by_nama = True

        hasil_mk = HasilMK(
            kode_mk=mk.kode_mk,
            nama_mk=mk.nama_mk,
            sks=mk.sks,
            urutan=mk.urutan_alfabet,
        )
        hasil_mk.cocok_by_nama = cocok_by_nama

        if len(ditemukan) == 0:
            # MK kosong → tandai merah
            hasil_mk.is_kosong = True
            hasil.jumlah_mk_kosong += 1

        elif len(ditemukan) == 1:
            # Normal
            n = ditemukan[0]
            r = resolve_nilai(
                _extract_field(n, 'nilai_lambang', 'lambang', 'grade_lambang', 'nilai_huruf'),
                _extract_field(n, 'nilai_angka', 'nilai_numerik', 'angka'),
            )
            hasil_mk.nilai_lambang = r.lambang
            hasil_mk.nilai_angka = r.angka
            hasil_mk.bobot = r.bobot
            hasil_mk.mutu = Decimal(str(mk.sks)) * r.bobot
            hasil_mk.is_tidak_lulus = r.tidak_lulus
            hasil_mk.semua_nilai = [r.lambang or r.angka]
            if r.peringatan:
                hasil_mk.catatan = r.peringatan
                logger.warning(f"[{hasil.nim}] {mk.kode_mk} ({mk.nama_mk}): {r.peringatan}")
            if not r.terbaca:
                hasil_mk.is_tidak_terbaca = True
                hasil.jumlah_mk_tidak_terbaca += 1

        else:
            # Retake → ambil mutu tertinggi
            hasil_mk.is_retake = True
            hasil.jumlah_mk_retake += 1

            resolved: list[NilaiAkhir] = []
            for n in ditemukan:
                r = resolve_nilai(
                    _extract_field(n, 'nilai_lambang', 'lambang', 'grade_lambang', 'nilai_huruf'),
                    _extract_field(n, 'nilai_angka', 'nilai_numerik', 'angka'),
                )
                resolved.append(r)
                if r.peringatan:
                    logger.warning(
                        f"[{hasil.nim}] {mk.kode_mk} (retake {len(resolved)}): {r.peringatan}"
                    )

            hasil_mk.semua_nilai = [r.lambang or r.angka or '-' for r in resolved]
            terbaca = [r for r in resolved if r.terbaca]

            if terbaca:
                best = max(terbaca, key=lambda x: x.bobot)
                hasil_mk.nilai_lambang = best.lambang
                hasil_mk.nilai_angka = best.angka
                hasil_mk.bobot = best.bobot
                hasil_mk.mutu = Decimal(str(mk.sks)) * best.bobot
                hasil_mk.is_tidak_lulus = best.tidak_lulus
                if len(terbaca) < len(resolved):
                    hasil_mk.catatan = 'Sebagian baris nilai retake tidak terbaca'
            else:
                hasil_mk.is_tidak_terbaca = True
                hasil.jumlah_mk_tidak_terbaca += 1
                hasil_mk.catatan = 'Semua baris nilai retake tidak terbaca'

        # Cross-check SKS Feeder vs SKS kurikulum (perbaikan #3)
        if ditemukan:
            sks_api = _extract_int(ditemukan[0], 'sks_mata_kuliah', 'sks_mk', 'sks')
            hasil_mk.sks_api = sks_api
            if (not hasil_mk.is_kosong and sks_api and mk.sks
                    and sks_api != mk.sks):
                tambahan = f'SKS Feeder {sks_api} ≠ SKS kurikulum {mk.sks}'
                hasil_mk.catatan = (
                    f"{hasil_mk.catatan}; {tambahan}"
                    if hasil_mk.catatan else tambahan
                )
                hasil.jumlah_mk_sks_beda += 1

        hasil.daftar_mk.append(hasil_mk)

    # MK di API yang TIDAK cocok dengan kurikulum (kode & nama)
    # → dicatat di laporan, dan (opsional) tetap dihitung ke total SKS & IPK
    sks_ekstra = 0
    mutu_ekstra = Decimal('0')
    for n in daftar_nilai_api:
        kode = _extract_field(n, 'kode_mata_kuliah', 'kode_mk', 'kode_matkul').lower()
        nama = n.get('_nama_norm', '')
        if kode in kode_kurikulum_dikenali:
            continue
        if nama and nama in nama_kurikulum_dikenali:
            continue

        mk_tidak_dikenali = HasilTidakDikenali(
            kode_mk=_extract_field(n, 'kode_mata_kuliah', 'kode_mk', 'kode_matkul'),
            nama_mk=_extract_field(n, 'nama_mata_kuliah', 'nama_mk', 'nama_matkul'),
            sks_api=_extract_field(n, 'sks_mata_kuliah', 'sks'),
            nilai_lambang=_extract_field(n, 'nilai_lambang', 'lambang', 'nilai_huruf'),
            nilai_angka=_extract_field(n, 'nilai_angka', 'nilai_numerik', 'angka'),
        )
        hasil.mk_tidak_dikenali.append(mk_tidak_dikenali)
        hasil.jumlah_mk_tidak_dikenali += 1

        if include_mk_tidak_dikenali:
            r = resolve_nilai(
                _extract_field(n, 'nilai_lambang', 'lambang', 'grade_lambang', 'nilai_huruf'),
                _extract_field(n, 'nilai_angka', 'nilai_numerik', 'angka'),
            )
            sks_api = _extract_int(n, 'sks_mata_kuliah', 'sks_mk', 'sks')
            if r.terbaca and not r.tidak_lulus and sks_api > 0:
                sks_ekstra += sks_api
                mutu_ekstra += Decimal(str(sks_api)) * r.bobot

    # Hitung total SKS dan IPK
    # Tidak dihitung: MK kosong, nilai tidak terbaca, dan nilai E (tidak lulus)
    total_sks = 0
    total_mutu = Decimal('0')
    for mk in hasil.daftar_mk:
        if mk.is_kosong or mk.is_tidak_terbaca or mk.is_tidak_lulus:
            continue
        total_sks += mk.sks
        total_mutu += mk.mutu

    # Tambahkan SKS & mutu MK tak dikenali yang dihitung (bila diaktifkan)
    total_sks += sks_ekstra
    total_mutu += mutu_ekstra

    hasil.total_sks = total_sks
    hasil.total_mutu = total_mutu

    if total_sks > 0:
        hasil.ipk = (total_mutu / Decimal(str(total_sks))).quantize(
            Decimal('0.01'), rounding=ROUND_HALF_UP
        )
    else:
        hasil.ipk = Decimal('0.00')

    # Tentukan predikat
    hasil.predikat = tentukan_predikat(predikat_qs, tahun_lulus, hasil.ipk)

    return hasil


def tentukan_predikat(predikat_qs, tahun_lulus: int, ipk: Decimal) -> str:
    """
    Cari predikat kelulusan berdasarkan tahun lulus dan IPK.
    Sesuai model PredikatKelulusan di PRD §4.
    """
    if not tahun_lulus:
        return ''
    for p in predikat_qs:
        # Cek rentang tahun
        if p.tahun_lulus_mulai <= tahun_lulus:
            if p.tahun_lulus_selesai is None or tahun_lulus <= p.tahun_lulus_selesai:
                # Cek rentang IPK
                if Decimal(str(p.ipk_min)) <= ipk <= Decimal(str(p.ipk_max)):
                    return p.predikat
    return ''
