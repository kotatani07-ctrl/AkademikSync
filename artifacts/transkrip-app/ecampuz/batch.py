"""Batch transcript: ambil transkrip banyak NIM.

Prinsip:
  - Satu per satu (tidak paralel) supaya tidak membebani server.
  - Checkpoint setelah tiap NIM sukses -> bisa dihentikan & dilanjutkan.
  - NIM gagal dipisah agar bisa dicoba ulang tanpa mengulang yang berhasil.
  - Opsi tanpa_raw_html untuk menghemat disk saat NIM banyak.
"""

from __future__ import annotations

import csv
import json
import re
import sys
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Callable, Iterable

from .transcript import parse, validate

NIM_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9\-/]{3,24}")


def read_nim_file(path: Path) -> list[str]:
    """Baca NIM dari file: satu per baris, atau dari kolom CSV."""
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    lines = text.splitlines()

    looks_csv = sum(1 for ln in lines if "," in ln or ";" in ln) > len(lines) * 0.5
    out: list[str] = []
    seen: set[str] = set()

    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if looks_csv:
            first = re.split(r"[,;\t]", line)[0]
            line = first.strip()
        m = NIM_RE.search(line)
        if not m:
            continue
        nim = m.group(0)
        if nim.lower() in seen:
            continue
        seen.add(nim.lower())
        out.append(nim)
    return out


@dataclass
class Progress:
    done: list[str] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)
    started: str = ""
    updated: str = ""

    @property
    def total_processed(self) -> int:
        return len(self.done) + len(self.failed)


class BatchRunner:
    def __init__(
        self,
        client,
        url_template: str,
        out_dir: Path,
        state_path: Path,
        keep_html: bool = True,
        per_student_files: bool = False,
        on_event: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> None:
        self.client = client
        self.url_template = url_template
        self.out_dir = out_dir
        self.state_path = state_path
        self.keep_html = keep_html
        self.per_student_files = per_student_files
        self.on_event = on_event or (lambda kind, data: None)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.state_path.parent.mkdir(parents=True, exist_ok=True)

    def build_url(self, nim: str) -> str:
        url = self.url_template
        if "{nim}" in url:
            return url.replace("{nim}", nim)
        joiner = "&" if "?" in url else "?"
        return f"{url}{joiner}nim={nim}"

    def load_state(self) -> Progress:
        if not self.state_path.exists():
            return Progress()
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return Progress()
        return Progress(
            done=data.get("done", []),
            failed=data.get("failed", {}),
            started=data.get("started", ""),
            updated=data.get("updated", ""),
        )

    def save_state(self, state: Progress) -> None:
        state.updated = time.strftime("%Y-%m-%dT%H:%M:%S")
        self.state_path.write_text(
            json.dumps(asdict(state), indent=2), encoding="utf-8"
        )

    def reset_state(self) -> None:
        if self.state_path.exists():
            self.state_path.unlink()
        for pattern in ("transkrip_batch_*.csv", "transkrip_gagal_*.csv"):
            for p in self.out_dir.glob(pattern):
                p.unlink()

    def run(
        self,
        nims: Iterable[str],
        limit: int | None = None,
        resume: bool = True,
        retry_failed: bool = False,
    ) -> dict[str, Any]:
        nims = list(nims)
        state = self.load_state() if resume else Progress()
        if not resume:
            self.reset_state()
            state = Progress()

        if not state.started:
            state.started = time.strftime("%Y-%m-%dT%H:%M:%S")

        done_set = {n.lower() for n in state.done}
        if retry_failed:
            state.failed = {}
        else:
            # Gagal tetap dilog, tapi tidak di-request ulang kecuali diminta.
            state.failed = dict(state.failed)

        skip_set = set(done_set) | {n.lower() for n in state.failed}
        todo = [n for n in nims if n.lower() not in skip_set]
        skipped = len(nims) - len(todo)

        self.on_event(
            "start",
            {
                "total": len(nims),
                "todo": len(todo),
                "skipped": skipped,
                "resume": resume,
            },
        )

        all_rows: list[dict[str, Any]] = []
        index = 0

        for nim in todo:
            index += 1
            url = self.build_url(nim)
            label = f"[{state.total_processed + 1}/{len(nims)}] {nim}"

            try:
                html = self.client.get_text(url, note=f"batch:{nim}")
            except Exception as exc:
                reason = f"request gagal: {exc.__class__.__name__}: {exc}"
                state.failed[nim] = reason
                self.save_state(state)
                self.on_event("fail", {"nim": nim, "index": index, "reason": reason})
                if isinstance(exc, Exception) and exc.__class__.__name__ == "SessionExpired":
                    self.on_event("abort", {"nim": nim, "reason": "sesi tidak valid"})
                    break
                continue

            if self.client._looks_login_page(html):
                state.failed[nim] = "server mengembalikan halaman login"
                self.save_state(state)
                self.on_event("fail", {"nim": nim, "index": index, "reason": "sesi tidak valid"})
                self.on_event("abort", {"nim": nim, "reason": "sesi tidak valid - login ulang"})
                break

            if self.keep_html:
                self.client.save_raw(f"batch_{nim}", html)

            result = parse(html)
            ident = result["identity"]
            summary = result["summary"]
            courses = result["courses"]
            problems = validate(result)

            if not courses:
                reason = "tidak ada mata kuliah terbaca"
                if problems:
                    reason += " - " + problems[0]
                state.failed[nim] = reason
                self.save_state(state)
                self.on_event("fail", {"nim": nim, "index": index, "reason": reason})
                continue

            for c in courses:
                all_rows.append(
                    {
                        "nim": ident.get("nim") or nim,
                        "nim_input": nim,
                        "nama": ident.get("nama", ""),
                        "fakultas": ident.get("fakultas", ""),
                        "jurusan": ident.get("jurusan", ""),
                        "semester": ident.get("semester", ""),
                        "periode": ident.get("periode", ""),
                        "kode": c["kode"],
                        "mata_kuliah": c["mata_kuliah"],
                        "sks": c["sks"] if c["sks"] is not None else "",
                        "nilai": c["nilai"],
                        "mutu": c["mutu"] if c["mutu"] is not None else "",
                    }
                )

            state.done.append(nim)
            self.save_state(state)
            self.on_event(
                "ok",
                {
                    "nim": nim,
                    "index": index,
                    "label": label,
                    "nama": ident.get("nama", ""),
                    "mk": len(courses),
                    "sks": summary["total_sks"],
                    "ips": summary["ips"],
                    "ipk": summary.get("ipk") or "",
                    "problems": problems,
                },
            )

            if self.per_student_files:
                self._write_per_student(nim, ident, summary, courses)

            if limit and index >= limit:
                self.on_event("limit", {"index": index})
                break

        return self._finalize(state, all_rows, nims)

    def _write_per_student(
        self,
        nim: str,
        ident: dict[str, str],
        summary: dict[str, Any],
        courses: list[dict[str, Any]],
    ) -> None:
        from .extract import write_csv

        safe_nim = re.sub(r"[^A-Za-z0-9\-]", "_", nim)
        stem = f"mhs_{safe_nim}"
        write_csv(courses, self.out_dir / f"{stem}.csv")
        meta = {
            "nim_input": nim,
            "identitas": ident,
            "ringkasan": summary,
            "jumlah_mk": len(courses),
        }
        (self.out_dir / f"{stem}.json").write_text(
            json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    def _finalize(
        self,
        state: Progress,
        rows: list[dict[str, Any]],
        nims: list[str],
    ) -> dict[str, Any]:
        self.save_state(state)

        all_nims = list(dict.fromkeys(nims))
        already = {n.lower() for n in state.done}

        if rows:
            combined = self.out_dir / "transkrip_batch.csv"
            existing: list[dict[str, Any]] = []
            if combined.exists():
                with combined.open(encoding="utf-8-sig", newline="") as fh:
                    existing = list(csv.DictReader(fh))
            known = {(r.get("nim_input", ""), r.get("kode", ""), r.get("mata_kuliah", "")) for r in existing}
            fresh = [
                r
                for r in rows
                if (r.get("nim_input", ""), r.get("kode", ""), r.get("mata_kuliah", "")) not in known
            ]
            from .extract import write_csv

            write_csv(existing + fresh, combined)
        else:
            combined = None

        pending = [n for n in all_nims if n.lower() not in already and n not in state.failed]
        failed_path = None
        if state.failed:
            failed_path = self.out_dir / "transkrip_gagal.csv"
            with failed_path.open("w", encoding="utf-8-sig", newline="") as fh:
                w = csv.writer(fh)
                w.writerow(["nim", "alasan"])
                for nim, reason in state.failed.items():
                    w.writerow([nim, reason])

        summary = {
            "total_diminta": len(all_nims),
            "selesai": len(state.done),
            "gagal": len(state.failed),
            "pending": len(pending),
            "baris": len(rows),
            "file_csv": str(combined) if combined else None,
            "file_gagal": str(failed_path) if failed_path else None,
            "pending_nims": pending[:200],
        }
        self.on_event("finish", summary)
        return summary


def verify_bulk_export(html: str) -> dict[str, Any]:
    """Deteksi apakah halaman menyediakan ekspor massal (lebih baik dari iterasi)."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "lxml")
    hints = ("excel", "xls", "csv", "download", "ekspor", "export", "cetak semua", "print all")
    found: list[dict[str, str]] = []

    for a in soup.find_all("a", href=True):
        label = re.sub(r"\s+", " ", a.get_text(" ", strip=True)).lower()
        href = a["href"]
        if any(h in label for h in hints) or any(h in href.lower() for h in (".xls", ".csv", "export", "download")):
            found.append({"href": href, "label": a.get_text(" ", strip=True)})

    for inp in soup.find_all(["input", "button"]):
        label = (inp.get("value") or inp.get_text(" ", strip=True) or "").lower()
        if any(h in label for h in hints):
            found.append({"href": "", "label": label})

    for f in soup.find_all("form", action=True):
        if any(h in f["action"].lower() for h in ("export", "download", "excel", "csv", "print")):
            found.append({"href": f["action"], "label": f.get("name", "")})

    return {"found": found, "count": len(found)}


def format_progress(data: dict[str, Any]) -> str:
    kind = data.get("kind")
    if kind == "start":
        return (
            f"\nTotal NIM   : {data['total']}\n"
            f"Perlu proses: {data['todo']}\n"
            f"Sudah jadi   : {data['skipped']}\n"
            f"{'-' * 62}"
        )
    if kind == "ok":
        s = f" SKS {data['sks']} | IPS {data['ips']}"
        if data.get("ipk"):
            s += f" | IPK {data['ipk']}"
        line = (
            f"[{data['index']:>3}] OK  {data['nim']:<14} "
            f"{data['nama'][:26]:<28} {data['mk']:>3} MK{s}"
        )
        if data.get("problems"):
            line += f"  [PERIKSA: {len(data['problems'])}]"
        return line
    if kind == "fail":
        return f"[{data['index']:>3}] GAGAL {data['nim']:<14} {data['reason'][:40]}"
    if kind == "abort":
        return f"\n[DIHENTIKAN] {data['reason']}\n"
    if kind == "limit":
        return f"\n[BATAS] berhenti setelah {data['index']} NIM. Jalankan ulang untuk lanjut."
    if kind == "finish":
        s = (
            f"\n{'=' * 62}\n"
            f"Selesai  : {data['selesai']}/{data['total_diminta']} mahasiswa\n"
            f"Gagal    : {data['gagal']}\n"
            f"Pending  : {data['pending']}\n"
            f"Baris CSV: {data['baris']}\n"
        )
        if data.get("file_csv"):
            s += f"CSV      : {data['file_csv']}\n"
        if data.get("file_gagal"):
            s += f"Gagal    : {data['file_gagal']}\n"
        if data.get("pending"):
            s += f"\nSisa {data['pending']} NIM belum diproses. Jalankan ulang perintah\n"
            s += "yang sama untuk melanjutkan dari checkpoint.\n"
        return s
    return ""
