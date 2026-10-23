"""Perintah `dhs`: ambil transkrip nilai satu atau banyak mahasiswa.

Alur 3 lapis (semua read-only):
    L1 list   -> cari mahasiswa via filter NIM + angkatan
    L2 detail -> daftar per semester
    L3 DHS     -> nilai per mata kuliah

Kode `act` selalu diambil dari menu yang sedang hidup, karena berubah tiap
kali login ulang.
"""

from __future__ import annotations

import csv
import json
import time
from pathlib import Path
from typing import Any

from .dhs import MODULE, build_list_url, fetch_transcript, resolve_act, to_rows

FIELDS = [
    "nim", "nama", "prodi", "angkatan", "semester", "semester_id",
    "kode", "mata_kuliah", "kelas", "jenis", "sks", "nilai", "bobot",
    "sks_bobot", "dosen_pengubah", "tanggal_ubah", "sumber_nilai",
]


def read_nims(path: Path) -> list[str]:
    """Baca NIM dari file: satu per baris, atau kolom pertama CSV."""
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    rows = [r for r in text.splitlines() if r.strip()]
    if not rows:
        return []

    first = rows[0]
    if "," in first or ";" in first:
        sep = "," if "," in first else ";"
        header = [c.strip().lower() for c in first.split(sep)]
        if any("nim" in h or "no" in h for h in header):
            try:
                col = next(i for i, h in enumerate(header) if "nim" in h or "no" in h)
            except StopIteration:
                col = 0
            out = []
            for line in rows[1:]:
                parts = line.split(sep)
                if len(parts) > col and parts[col].strip():
                    out.append(parts[col].strip())
            return out

    out = []
    for line in rows:
        val = line.split(",")[0].split(";")[0].strip()
        if val and val.lower() not in ("nim", "nim_mhs", "no"):
            out.append(val)
    return out


class Checkpoint:
    """Simpan NIM yang sudah beres supaya bisa dilanjutkan."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.done: list[str] = []
        self.failed: dict[str, str] = {}
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                self.done = list(data.get("done", []))
                self.failed = dict(data.get("failed", {}))
            except (json.JSONDecodeError, OSError):
                pass

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(
                {"done": self.done, "failed": self.failed, "saved_at": time.strftime("%Y-%m-%dT%H:%M:%S")},
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    def reset(self) -> None:
        self.done = []
        self.failed = {}
        self.save()


def _print_student(result: dict[str, Any], verbose: bool = True) -> None:
    if not verbose:
        return
    print()
    print(f"  {result['nama']}  ({result['nim']})")
    print(f"  {result['prodi']}  angkatan {result['angkatan']}")
    print(f"  total {result['total_sks']:.0f} SKS / {result['jumlah_mk']} mata kuliah"
          f"  IPS {result['ips_keseluruhan']}  IPK {result['ipk_terakhir']}")
    print()
    for sem in result["semesters"]:
        print(f"    {sem['semester']}  (SKS {sem['sks']}, IP {sem['ip']})")
        for c in sem["courses"]:
            print(f"      {c['kode']:<9} {c['mata_kuliah'][:56]:<56} "
                  f"{c['sks']:>4.0f}  {c['nilai']:<3} {c['bobot']}")
        print()


def run_dhs(
    client,
    nims: list[str],
    angkatan: str,
    prodi: str = "all",
    out_dir: Path | None = None,
    state_path: Path | None = None,
    per_mahasiswa: bool = False,
    limit: int | None = None,
    resume: bool = True,
    retry_failed: bool = False,
    verbose: bool = True,
    grade_fallback=None,
) -> int:
    """Jalankan pengambilan DHS untuk daftar NIM."""
    out_dir = out_dir or Path("data/out")
    out_dir.mkdir(parents=True, exist_ok=True)
    state_path = state_path or out_dir / f"dhs_state_{angkatan}.json"

    ckpt = Checkpoint(state_path)
    if not resume or retry_failed:
        ckpt.reset()

    todo = [n for n in nims if n not in ckpt.done]
    if retry_failed:
        todo = [n for n in nims if n in ckpt.failed] or todo
    if limit:
        todo = todo[:limit]

    if not todo:
        print(f"[ok] Semua NIM sudah selesai ({len(ckpt.done)} mahasiswa).")
        return 0

    if verbose:
        print(f"[i] {len(todo)} mahasiswa | angkatan {angkatan} | prodi {prodi}")
        print(f"[i] checkpoint: {state_path}")
        print()

    all_rows: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []
    ok = 0

    for i, nim in enumerate(todo, 1):
        tag = f"[{i}/{len(todo)}] {nim}"
        try:
            result = fetch_transcript(client, nim, angkatan, prodi)
        except LookupError as exc:
            ckpt.failed[nim] = str(exc)
            print(f"{tag} GAKAL: {exc}")
            continue
        except Exception as exc:  # noqa: BLE001
            msg = f"{exc.__class__.__name__}: {exc}"
            ckpt.failed[nim] = msg
            print(f"{tag} GAKAL: {msg}")
            continue

        if grade_fallback is not None:
            missing = sum(
                1
                for semester in result.get("semesters", [])
                for course in semester.get("courses", [])
                if not course.get("nilai")
            )
            if missing:
                try:
                    from .neo_feeder import merge_missing_grades

                    feeder_rows = grade_fallback.grades_for_student(
                        result["nim"], result.get("prodi", "")
                    )
                    merged_result = merge_missing_grades(result, feeder_rows)
                    result = merged_result["result"]
                    print(
                        f"{tag} Neo Feeder: {merged_result['merged']} nilai dilengkapi; "
                        f"{len(merged_result['ambiguous'])} pasangan ambigu dibiarkan kosong."
                    )
                except Exception as exc:  # noqa: BLE001
                    print(f"{tag} Neo Feeder fallback gagal (data eCampus dipertahankan): {exc}")

        rows = to_rows(result)
        all_rows.extend(rows)
        summaries.append(
            {
                "nim": result["nim"],
                "nama": result["nama"],
                "prodi": result["prodi"],
                "angkatan": result["angkatan"],
                "jumlah_mk": result["jumlah_mk"],
                "total_sks": result["total_sks"],
                "ips": result["ips_keseluruhan"],
                "ipk": result["ipk_terakhir"],
            }
        )
        ckpt.done.append(nim)
        ckpt.failed.pop(nim, None)
        ok += 1

        _print_student(result, verbose)
        print(f"{tag} OK - {result['nama']}, {result['total_sks']:.0f} SKS, IPK {result['ipk_terakhir']}")

        if per_mahasiswa:
            (out_dir / f"dhs_{nim}.csv").write_text(
                _to_csv(rows), encoding="utf-8-sig"
            )
            (out_dir / f"dhs_{nim}.json").write_text(
                json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
            )

        ckpt.save()

    # Gabungkan dengan hasil sebelumnya supaya file utuh.
    merged = _merge(out_dir / "dhs_semua.csv", all_rows)
    (out_dir / "dhs_semua.csv").write_text(merged, encoding="utf-8-sig")
    (out_dir / "dhs_ringkasan.json").write_text(
        json.dumps(summaries, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if ckpt.failed:
        (out_dir / "dhs_gagal.csv").write_text(
            _to_csv([{"nim": k, "alasan": v} for k, v in ckpt.failed.items()]),
            encoding="utf-8-sig",
        )

    print()
    print(f"[selesai] {ok}/{len(todo)} berhasil | total {len(merged.splitlines())-1} baris CSV")
    print(f"           -> {out_dir / 'dhs_semua.csv'}")
    if ckpt.failed:
        print(f"           -> {out_dir / 'dhs_gagal.csv'} ({len(ckpt.failed)} gagal)")
    return 0


def _to_csv(rows: list[dict[str, Any]]) -> str:
    import io

    buf = io.StringIO()
    if not rows:
        return ""
    w = csv.DictWriter(buf, fieldnames=[f for f in FIELDS if f in rows[0]], extrasaction="ignore")
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue()


def _merge(path: Path, new_rows: list[dict[str, Any]]) -> str:
    """Gabungkan baris baru ke CSV lama, unik per (nim, semester, kode)."""
    import io

    existing: list[dict[str, str]] = []
    if path.exists():
        try:
            with path.open(encoding="utf-8-sig", newline="") as f:
                existing = list(csv.DictReader(f))
        except (OSError, csv.Error):
            existing = []

    seen = {
        (r.get("nim", ""), r.get("semester", ""), r.get("kode", ""))
        for r in existing
    }
    for r in new_rows:
        seen.add((r.get("nim", ""), r.get("semester", ""), r.get("kode", "")))

    buf = io.StringIO()
    fields = [f for f in FIELDS if existing or new_rows]
    w = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore")
    w.writeheader()
    for r in existing:
        w.writerow(r)
    for r in new_rows:
        w.writerow(r)
    return buf.getvalue()
