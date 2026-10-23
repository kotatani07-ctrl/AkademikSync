from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Any, Iterable

from bs4 import BeautifulSoup, Tag

_NOISE_RE = re.compile(r"[\u00a0\u200b\ufeff]+")


def clean(text: str | None) -> str:
    if not text:
        return ""
    t = _NOISE_RE.sub(" ", text)
    t = t.replace("\r", " ").replace("\n", " ").replace("\t", " ")
    return re.sub(r"\s{2,}", " ", t).strip()


def normalize_key(text: str) -> str:
    key = clean(text).lower()
    key = re.sub(r"[^a-z0-9]+", "_", key)
    return key.strip("_") or "col"


def _cells(row: Tag) -> list[str]:
    out: list[str] = []
    for td in row.find_all(["td", "th"], recursive=True):
        if td.find(["table"]):
            continue
        out.append(clean(td.get_text(" ", strip=True)))
    return out


def _is_header_row(row: Tag) -> bool:
    ths = row.find_all("th", recursive=False)
    if len(ths) >= 2:
        return True
    tds = row.find_all("td", recursive=False)
    if not tds:
        return False
    text = " ".join(clean(td.get_text()) for td in tds).lower()
    filler = {"", "-", "&nbsp;"}
    meaningful = [t for t in text.split("  ") if t]
    if all(t in filler for t in [clean(td.get_text()) for td in tds]):
        return True
    return len(meaningful) == 0 and len(tds) > 2


_FOOTER_RE = re.compile(
    r"^\s*(total|grand total|total\s*:|jumlah|jumlah\s*:|sum|subtotal|"
    r"total\s+[a-z]+|daftar|hasil|jumlah\s+semua)\b",
    re.I,
)


def _is_footer_row(cells: list[str], row: Tag) -> bool:
    """Deteksi baris ringkasan di kaki tabel (Total/Jumlah/dll)."""
    tds = row.find_all(["td", "th"], recursive=False)
    if tds and any(td.get("colspan") and int(td.get("colspan", 1)) > 1 for td in tds):
        first = next((c for c in cells if c), "")
        if not first or _FOOTER_RE.match(first):
            return True
    nonempty = [c for c in cells if c]
    if nonempty and len(nonempty) == 1 and _FOOTER_RE.match(nonempty[0]):
        return True
    return False


def extract_tables(html: str, min_rows: int = 1) -> list[list[dict[str, Any]]]:
    """Kembalikan daftar tabel sebagai list-of-dict, header dideteksi otomatis."""
    soup = BeautifulSoup(html, "lxml")
    results: list[list[dict[str, Any]]] = []

    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if len(rows) < min_rows + 1:
            continue

        header_idx = None
        for i, row in enumerate(rows[:4]):
            if _is_header_row(row):
                header_idx = i
                break
        if header_idx is None:
            continue

        header_cells = _cells(rows[header_idx])
        if len([h for h in header_cells if h]) < 2:
            continue

        headers: list[str] = []
        seen: dict[str, int] = {}
        for idx, raw in enumerate(header_cells):
            name = normalize_key(raw) if raw else f"col{idx + 1}"
            if name in seen:
                seen[name] += 1
                name = f"{name}_{seen[name]}"
            else:
                seen[name] = 0
            headers.append(name)

        records: list[dict[str, Any]] = []
        for row in rows[header_idx + 1 :]:
            cells = _cells(row)
            if not cells or all(not c for c in cells):
                continue
            if _is_footer_row(cells, row):
                continue
            rec: dict[str, Any] = {}
            for idx, key in enumerate(headers):
                rec[key] = cells[idx] if idx < len(cells) else ""
            records.append(rec)

        if records:
            results.append(records)

    return results


def find_pagination(html: str) -> dict[str, Any]:
    """Deteksi parameter paginasi eCampuz dari link/select di halaman."""
    soup = BeautifulSoup(html, "lxml")
    info: dict[str, Any] = {"param": None, "max_page": 1, "current": 1, "total_rows": None}

    for sel in ("select[name*='page']", "select[name*='limit']", "select#page", "select[name='per_page']"):
        for node in soup.select(sel):
            name = node.get("name")
            if not name:
                continue
            opts = [o.get("value") for o in node.find_all("option") if o.get("value")]
            nums = [int(o) for o in opts if str(o).isdigit()]
            if nums:
                info["param"] = name
                info["max_page"] = max(nums)
                selected = node.find("option", selected=True)
                if selected and (selected.get("value") or "").isdigit():
                    info["current"] = int(selected.get("value"))
                return info

    candidates: list[tuple[str, int]] = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        m = re.search(r"([?&][A-Za-z_]*(?:page|start|offset|hal)[A-Za-z_]*=(\d+))", href, re.I)
        if m:
            candidates.append((m.group(1), int(m.group(2))))
    if candidates:
        info["param"] = candidates[0][0].split("=")[0].lstrip("?&")
        info["max_page"] = max(p for _, p in candidates)

    m = re.search(
        r"(?:jumlah|total|found|record|hasil)[^0-9]{0,20}([0-9][0-9.,]*)",
        BeautifulSoup(html, "lxml").get_text(" ", strip=True),
        re.I,
    )
    if m:
        info["total_rows"] = m.group(1)

    return info


def extract_personal_info(html: str) -> dict[str, str]:
    """Ambil blok data pribadi dari halaman detail (label: nilai)."""
    soup = BeautifulSoup(html, "lxml")
    data: dict[str, str] = {}
    for table in soup.find_all("table"):
        for tr in table.find_all("tr"):
            cells = tr.find_all(["td", "th"], recursive=False)
            if len(cells) == 2:
                key = clean(cells[0].get_text(" ", strip=True))
                val = clean(cells[1].get_text(" ", strip=True))
                if key and val and len(key) < 80:
                    data[normalize_key(key)] = val
            elif len(cells) == 4:
                for i in (0, 2):
                    if i + 1 < len(cells):
                        key = clean(cells[i].get_text(" ", strip=True))
                        val = clean(cells[i + 1].get_text(" ", strip=True))
                        if key and val and len(key) < 80:
                            data[normalize_key(key)] = val
    return data


def write_csv(records: Iterable[dict[str, Any]], path: Path) -> Path:
    records = list(records)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not records:
        path.write_text("", encoding="utf-8")
        return path
    fieldnames: list[str] = []
    for rec in records:
        for k in rec:
            if k not in fieldnames:
                fieldnames.append(k)
    with path.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records)
    return path


def discover_links(html: str, base_path: str) -> list[dict[str, str]]:
    """Kumpulkan link internal yang relevan (nav/menu/form action)."""
    soup = BeautifulSoup(html, "lxml")
    seen: dict[str, dict[str, str]] = {}

    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        text = clean(a.get_text(" ", strip=True))
        seen.setdefault(href, {"href": href, "text": text, "source": "a"})

    for f in soup.find_all("form", action=True):
        action = f["action"].strip()
        if action:
            seen.setdefault(action, {"href": action, "text": "", "source": "form"})

    for inp in soup.find_all(["input", "select"]):
        name = inp.get("name")
        if name and "passwd" not in name.lower():
            seen.setdefault(
                f"{base_path}?{name}=1",
                {"href": f"{base_path}?{name}=1", "text": "", "source": f"field:{name}"},
            )

    out = []
    for href, meta in seen.items():
        if href.startswith("http") and "ecampuz.com" not in href:
            continue
        out.append(meta)
    return out
