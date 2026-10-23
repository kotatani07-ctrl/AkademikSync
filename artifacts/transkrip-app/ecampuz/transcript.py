"""Modul transkrip nilai.

eCampuz tidak punya endpoint transkrip yang seragam antar kampus, jadi modul ini
bekerja dua tahap:

1. `candidates()`  - tebak endpoint transkrip dari halaman menu/beranda
2. `parse()`       - ubah HTML transkrip menjadi data terstruktur

Kalau struktur tabel di kampus ini berbeda, HTML mentah selalu disimpan lewat
`client.save_raw()` supaya parser bisa disesuaikan tanpa request ulang.
"""

from __future__ import annotations

import re
from typing import Any

from bs4 import BeautifulSoup, Tag

# Nilai huruf eCampuz -> bobot. Sebagian kampus memakai E\*, D+, D-.
GRADE_SCALE: dict[str, float] = {
    "A": 4.00,
    "A-": 3.75,
    "B+": 3.50,
    "B": 3.00,
    "B-": 2.75,
    "C+": 2.50,
    "C": 2.00,
    "C-": 1.75,
    "D+": 1.50,
    "D": 1.00,
    "E": 0.00,
}

_TRANSCRIPT_HINTS = ("transkrip", "transcript", "kartu hasil study", "khs", "nilai")


def candidates(html: str) -> list[dict[str, str]]:
    """Kumpulkan endpoint yang mungkin Transcript, dari teks menu & form."""
    soup = BeautifulSoup(html, "lxml")
    found: dict[str, dict[str, str]] = {}

    def add(href: str, text: str, source: str) -> None:
        if not href or href.startswith(("#", "javascript:", "mailto:")):
            return
        found.setdefault(href, {"href": href, "text": text, "source": source})

    for a in soup.find_all("a", href=True):
        text = re.sub(r"\s+", " ", a.get_text(" ", strip=True)).strip()
        low = text.lower()
        if any(h in low for h in _TRANSCRIPT_HINTS):
            add(a["href"], text, "menu")

    for node in soup.find_all(["input", "select", "a"]):
        name = node.get("name") or ""
        low = name.lower()
        if any(h in low for h in ("transkrip", "nilai", "khs")) and "id" not in low:
            parent_action = ""
            form = node.find_parent("form")
            if form and form.get("action"):
                parent_action = form["action"]
            if parent_action:
                add(parent_action, name, f"form:{name}")

    for opt in soup.find_all("option", value=True):
        label = re.sub(r"\s+", " ", opt.get_text(" ", strip=True)).strip()
        if any(h in label.lower() for h in _TRANSCRIPT_HINTS):
            add(opt["value"], label, "option")

    return list(found.values())


def _grade_weight(grade: str) -> float | None:
    if not grade:
        return None
    g = grade.strip().upper().replace(" ", "")
    if g in GRADE_SCALE:
        return GRADE_SCALE[g]
    m = re.match(r"^([A-E])[+-]?$", g)
    if m:
        return GRADE_SCALE.get(m.group(1))
    return None


LABELS: dict[str, str] = {
    "nim": r"nim|no\.?\s*induk",
    "nama": r"nama(?:\s+lengkap)?",
    "fakultas": r"fakultas",
    "jurusan": r"program\s+studi|jurusan",
    "semester": r"semester",
    "periode": r"periode|tahun\s+akademik",
    "status": r"status",
}

# Pola nilai dibuat per-key supaya tidak melebar (NIM harus alfanumerik saja).
VALUES: dict[str, str] = {
    "nim": r"((?:\d{6,}|[A-Z]{1,4})[A-Z0-9\-/]{3,19})",
    "nama": r"([A-Za-z][\w\s'\-\.]{1,60})",
    "fakultas": r"([A-Za-z][\w\s'\-\.&]{1,79})",
    "jurusan": r"([A-Za-z][\w\s'\-\.&]{1,79})",
    "semester": r"([A-Za-z0-9IVX]+(?:\s*/\s*[A-Za-z0-9IVX]+)?)",
    "periode": r"([0-9]{4}\s*/\s*[0-9]{4}|[A-Za-z0-9\-]{4,20})",
    "status": r"([A-Za-z]{3,20})",
}

_ALL_LABELS = "|".join(f"(?:{lab})" for lab in LABELS.values())
_NEXT_LABEL_RE = re.compile(rf"\s+(?:{_ALL_LABELS})\s*[:\-=]?", re.I)


def _build(sep: str) -> dict[str, re.Pattern]:
    return {
        key: re.compile(rf"(?:{LABELS[key]})\s*{sep}\s*{VALUES[key]}", re.I)
        for key in LABELS
    }


PATTERNS_STRICT = _build(r"[:\-=]")
PATTERNS_LOOSE = _build(r"[\s]+")


def _tidy(val: str, key: str, strip_lead: bool) -> str:
    val = re.sub(r"\s+", " ", val).strip(" .,-")

    # Potong tepat sebelum label berikutnya yang ikut terbawa.
    m = _NEXT_LABEL_RE.search(val)
    if m and m.start() > 0:
        val = val[: m.start()]

    # Pada pola longgar, label ikut terkarena sebagai bagian nilai, mis.
    # teks "Fakultas Ilmu Sosial" -> nilai "Ilmu Sosial". Buang sisa label itu.
    # Pada pola strict, nilai memang boleh memuat kata "Fakultas", jadi jangan.
    if strip_lead:
        lead = re.match(
            rf"^({'|'.join(LABELS[key].split(chr(124)))}|program\s+studi)\b\s*",
            val,
            re.I,
        )
        if lead:
            val = val[lead.end():].strip()

    return val.strip(" .,-")


def _harvest(
    text: str,
    data: dict[str, str],
    patterns: dict[str, re.Pattern],
    strip_lead: bool,
) -> None:
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) < 4:
        return
    for key, pat in patterns.items():
        if key in data:
            continue
        m = pat.search(text)
        if not m:
            continue
        val = _tidy(m.group(1), key, strip_lead)
        if len(val) < 2 or val.lower() in LABELS:
            continue
        data[key] = val


def extract_identity(html: str) -> dict[str, str]:
    """Ambil NIM, nama, fakultas, dsb dari blok header transkrip.

    Dua fase: pola ber-separator ("NIM : 123") lebih diprioritaskan, karena
    pola longgar bisa salah tangkap, mis. nilai "Fakultas Ilmu Sosial"
    akan terbaca sebagai label "Fakultas" + nilai "Ilmu Sosial".
    """
    soup = BeautifulSoup(html, "lxml")
    data: dict[str, str] = {}

    chunks: list[str] = []

    for tr in soup.find_all("tr"):
        cells = [
            re.sub(r"\s+", " ", td.get_text(" ", strip=True))
            for td in tr.find_all(["td", "th"], recursive=False)
        ]
        cells = [c for c in cells if c]
        if not cells:
            continue
        chunks.append(" ".join(cells))
        for i in range(len(cells) - 1):
            if len(cells[i]) > 60:
                continue
            chunks.append(f"{cells[i]} : {cells[i + 1]}")

    for node in soup.find_all(string=True):
        if node.parent and node.parent.name in ("script", "style"):
            continue
        chunks.append(str(node))

    for chunk in chunks:
        _harvest(chunk, data, PATTERNS_STRICT, strip_lead=False)
    for chunk in chunks:
        _harvest(chunk, data, PATTERNS_LOOSE, strip_lead=True)

    return data


def _find_summary(soup: BeautifulSoup, text: str) -> str | None:
    m = re.search(
        re.escape(text) + r"\s*[:\-]?\s*([0-9]+[.,][0-9]{1,2})",
        soup.get_text(" ", strip=True),
        re.I,
    )
    return m.group(1).replace(",", ".") if m else None


def parse(html: str) -> dict[str, Any]:
    """Parse halaman transkrip -> identitas + daftar mata kuliah + ringkasan."""
    soup = BeautifulSoup(html, "lxml")
    identity = extract_identity(html)

    course_headers = {
        "kode": ("kode", "kode mk", "kode mata kuliah", "kd"),
        "mata_kuliah": ("mata kuliah", "nama mata kuliah", "nama mk", "mata kuliah (mk)"),
        "sks": ("sks", "sks (teori)", "sks teori", "jumlah sks"),
        "nilai": ("nilai", "nilai akhir", "huruf", "nilai huruf", "grade"),
        "mutu": ("mutu", "bobot", "mutu mutu", "nilai mutu"),
    }
    header_map: dict[str, str] = {}

    courses: list[dict[str, Any]] = []
    best_score = 0

    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if len(rows) < 2:
            continue

        for idx, row in enumerate(rows[:4]):
            cells = [
                re.sub(r"\s+", " ", td.get_text(" ", strip=True)).lower()
                for td in row.find_all(["td", "th"])
            ]
            mapping: dict[str, str] = {}
            for pos, cell in enumerate(cells):
                for key, aliases in course_headers.items():
                    if key in mapping:
                        continue
                    if any(cell == a or cell.startswith(a) for a in aliases):
                        mapping[key] = str(pos)
            if "mata_kuliah" in mapping and ("nilai" in mapping or "mutu" in mapping):
                if len(mapping) > best_score:
                    best_score = len(mapping)
                    header_map = mapping
                    header_row = idx
                break

        if not header_map or best_score != len(mapping):
            continue

        for row in rows[header_row + 1 :]:
            tds = row.find_all("td", recursive=False)
            if len(tds) < 3:
                continue
            cells = [
                re.sub(r"\s+", " ", td.get_text(" ", strip=True))
                for td in tds
            ]

            def at(key: str) -> str:
                pos = header_map.get(key)
                if pos is None or not pos.isdigit():
                    return ""
                i = int(pos)
                return cells[i] if i < len(cells) else ""

            kode = at("kode")
            mk = at("mata_kuliah")
            if not mk and not kode:
                continue
            if not mk:
                continue
            if re.match(r"^(total|jumlah|ipk?|ips)\b", mk, re.I):
                continue
            if len(mk) < 3 and not kode:
                continue

            grade = at("nilai").strip()
            mutu_txt = at("mutu").strip()
            weight = _grade_weight(grade)
            if weight is None and mutu_txt:
                try:
                    weight = float(mutu_txt.replace(",", "."))
                except ValueError:
                    weight = None

            try:
                sks = float(at("sks").replace(",", ".")) if at("sks") else None
            except ValueError:
                sks = None

            courses.append(
                {
                    "kode": kode,
                    "mata_kuliah": mk,
                    "sks": sks,
                    "nilai": grade,
                    "mutu": weight,
                }
            )

    graded = [c for c in courses if c["mutu"] is not None and c["sks"]]
    ips = None
    if graded:
        total = sum(c["mutu"] * c["sks"] for c in graded)
        sks_total = sum(c["sks"] for c in graded)
        ips = round(total / sks_total, 2) if sks_total else None

    summary = {
        "jumlah_mk_diambil": len(courses),
        "total_sks": sum(c["sks"] for c in graded) if graded else 0,
        "ips": ips,
        "ipk": _find_summary(soup, "IPK"),
        "ips_dokumen": _find_summary(soup, "IPS"),
    }

    return {"identity": identity, "courses": courses, "summary": summary}


def validate(result: dict[str, Any]) -> list[str]:
    """Cek hasil parse; kembalikan daftar masalah yang perlu diperiksa manual."""
    problems: list[str] = []
    if not result["courses"]:
        problems.append("Tidak ada baris mata kuliah terbaca - cek HTML mentah.")
        return problems
    if not any(k in result["identity"] for k in ("nim", "nama")):
        problems.append("NIM/nama tidak terdeteksi - identitas mungkin tidak ada di halaman ini.")
    for c in result["courses"]:
        if c["mutu"] is None:
            problems.append(f"Mutu tidak terbaca untuk: {c['mata_kuliah']} (nilai={c['nilai']!r})")
        if c["sks"] is None:
            problems.append(f"SKS tidak terbaca untuk: {c['mata_kuliah']}")
    return problems
