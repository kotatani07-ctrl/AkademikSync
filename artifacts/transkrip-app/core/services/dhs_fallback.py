"""
Fallback nilai dari hasil scraping eCampuz (DHS) untuk generate transkrip.

Alur: bila Neo Feeder tidak punya data nilai untuk sebuah NIM (GetRiwayatNilai
mengembalikan daftar kosong), hasil scraping DHS eCampuz yang tersimpan di
`data/ecampuz/out/dhs_<nim>.json` dipakai sebagai sumber nilai pengganti.

Format output mengikuti apa yang dikenali `cocokan_nilai` (format
GetRiwayatNilaiMahasiswa): setiap course di-flatten menjadi dict:
  - kode_mata_kuliah, nama_mata_kuliah
  - nilai_lambang (huruf, bila eCampuz menulis huruf), nilai_angka (bobot)
  - sks_mata_kuliah (integer)
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def _out_dir() -> Path:
    """Direktori hasil scraping DHS — dari config package ecampuz."""
    try:
        from ecampuz.config import OUT_DIR
        return OUT_DIR
    except Exception:  # noqa: BLE001
        return Path(__file__).resolve().parents[3] / "data" / "ecampuz" / "out"


def dhs_tersedia(nim: str, out_dir: Path | None = None) -> bool:
    """True bila ada file hasil scraping DHS untuk NIM tsb."""
    path = Path(out_dir or _out_dir()) / f"dhs_{nim}.json"
    return path.exists()


def _is_angka(value) -> bool:
    try:
        float(value)
        return True
    except (TypeError, ValueError):
        return False


def _fmt_angka(value) -> str:
    """Format nilai angka (bobot) jadi string 2 desimal; kosong bila tak ada."""
    if value in (None, ""):
        return ""
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return str(value).strip()


def _fmt_sks(value) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def get_dhs_nilai_mahasiswa(nim: str, out_dir: Path | None = None) -> list[dict] | None:
    """
    Ambil daftar nilai mahasiswa dari hasil scraping DHS eCampuz.

    Return:
      - None bila file DHS tidak ada / rusak / bukan object JSON.
      - [] bila file ada tetapi tidak memuat satupun course.
      - list[dict] format nilai yang dikenal `cocokan_nilai`.
    """
    path = Path(out_dir or _out_dir()) / f"dhs_{nim}.json"
    if not path.exists():
        return None

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("DHS %s tidak bisa dibaca: %s", path.name, exc)
        return None
    if not isinstance(data, dict):
        logger.warning("DHS %s bukan object JSON.", path.name)
        return None

    daftar: list[dict] = []
    for sem in data.get("semesters", []) or []:
        for c in sem.get("courses", []) or []:
            kode = str(c.get("kode", "")).strip()
            nama = str(c.get("mata_kuliah", "")).strip()
            if not kode or not nama:
                continue

            nilai = str(c.get("nilai", "")).strip()
            bobot = c.get("bobot", "")

            # eCampuz biasanya menulis huruf ('A', 'B+'); kadang numerik ('3.85').
            if nilai and not _is_angka(nilai):
                lambang = nilai.upper()
                angka = bobot
            elif nilai:
                lambang = ""
                angka = nilai
            else:
                lambang = ""
                angka = bobot

            if not lambang and not angka:
                continue  # tidak ada nilai maupun bobot → tidak ada data

            daftar.append({
                "kode_mata_kuliah": kode,
                "nama_mata_kuliah": nama,
                "nilai_lambang": lambang,
                "nilai_angka": _fmt_angka(angka),
                "sks_mata_kuliah": _fmt_sks(c.get("sks", 0)),
            })

    return daftar