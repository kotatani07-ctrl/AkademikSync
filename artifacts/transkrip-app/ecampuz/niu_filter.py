"""Filter daftar mahasiswa berbasis NIM pada halaman list eCampuz.

Halaman list eCampuz (Nilai per Mahasiswa, Mahasiswa, dll) memakai form GET
yang sama persis di semua modul. Parameter yang sudah terverifikasi dari
HTML server:

    prodi        select, "all" atau kode prodi ter-URL-encode
    angk_awal    select, tahun angkatan awal
    angk_akhir   select, tahun angkatan akhir
    cek_filter   hidden, selalu "niu" (artinya filter memakai NIM)
    filter_awal  text, NIM/rentang awal
    filter_akhir text, NIM/rentang akhir
    nama         text, pencarian nama
    action       hidden, selalu "submitted"
    act          hidden, sama dengan act di URL

Fungsi di modul ini hanya melakukan GET (membaca daftar). Tidak pernah
mengirim POST, jadi tidak mengubah data apa pun.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import unquote, urlencode

from bs4 import BeautifulSoup, Tag

# Kolom "Aksi"/"Nama" pada halaman list: link ke detail per mahasiswa.
_DETAIL_HINTS = ("detail", "lihat", "cetak", "nilai", "transkrip", "profil", "view", "edit")
_ACT_RE = re.compile(r"[?&]act=([0-9a-f]{8,})", re.I)


def build_url(
    act_path: str,
    act: str | None = None,
    nim: str | None = None,
    nim_from: str | None = None,
    nim_to: str | None = None,
    prodi: str = "all",
    ang_from: str | None = None,
    ang_to: str | None = None,
    nama: str | None = None,
) -> str:
    """Bangun URL GET untuk halaman list dengan filter terisi.

    `nim` (satu NIM) mengesampingkan `nim_from`/`nim_to`. Rentang diisi
    otomatis dari `nim` bila `nim_to` tidak diberikan.
    """
    if nim:
        nim_from = nim
        nim_to = nim_to or nim

    params: dict[str, str] = {}
    # Nilai <option> eCampuz sudah ter-URL-encode ("NTY%3D"). Decode dulu
    # supaya urlencode() di bawah tidak meng-encode dua kali ("NTY%253D").
    if prodi:
        params["prodi"] = unquote(str(prodi))
    if ang_from:
        params["angk_awal"] = str(ang_from)
    if ang_to:
        params["angk_akhir"] = str(ang_to)
    params["cek_filter"] = "niu"
    if nim_from:
        params["filter_awal"] = str(nim_from).strip()
    if nim_to:
        params["filter_akhir"] = str(nim_to).strip()
    if nama:
        params["nama"] = str(nama).strip()
    params["action"] = "submitted"

    base = act_path.lstrip("/")
    if act:
        params["act"] = act

    sep = "&" if "?" in base else "?"
    return f"{base}{sep}{urlencode(params)}"


def act_from_html(html: str) -> str | None:
    """Ambil nilai act dari form filter di halaman itu sendiri."""
    soup = BeautifulSoup(html, "lxml")
    node = soup.find("input", attrs={"name": "act"})
    if node and node.get("value"):
        return str(node["value"])
    m = _ACT_RE.search(html)
    return m.group(1) if m else None


def _cell_text(td: Tag) -> str:
    return re.sub(r"\s+", " ", td.get_text(" ", strip=True)).strip()


def find_result_table(soup: BeautifulSoup) -> Tag | None:
    """Cari tabel hasil (bukan tabel form filter).

    Tabel filter selalu punya class 'table-form'. Tabel hasil yang kosong
    pun tetap perlu dikenali (hanya baris header), supaya pemanggil bisa
    membedakan "filter tidak cocok" dari "halaman tidak punya tabel".
    """
    best: Tag | None = None
    best_cols = 0
    for table in soup.find_all("table"):
        classes = " ".join(table.get("class") or [])
        if "table-form" in classes:
            continue
        rows = table.find_all("tr")
        if not rows:
            continue
        cols = len(rows[0].find_all(["td", "th"]))
        if cols < 3:
            continue
        if cols > best_cols:
            best = table
            best_cols = cols
    return best


def headers(table: Tag) -> list[str]:
    first = table.find("tr")
    if not first:
        return []
    cells = first.find_all(["td", "th"])
    return [_cell_text(c) for c in cells]


def extract_rows(table: Tag) -> list[dict[str, Any]]:
    """Baca baris hasil filter + link detail per baris."""
    heads = headers(table)
    out: list[dict[str, Any]] = []

    for tr in table.find_all("tr")[1:]:
        cells = [_cell_text(td) for td in tr.find_all("td", recursive=False)]
        if not cells or all(not c for c in cells):
            continue

        links: list[dict[str, str]] = []
        for a in tr.find_all("a", href=True):
            href = a["href"]
            if href.startswith("#") or href.lower().startswith("javascript:"):
                continue
            links.append({"href": href, "text": _cell_text(a) or _cell_text(tr)})

        row: dict[str, Any] = {
            "cells": cells,
            "links": links,
            "nim": _guess_nim(cells),
        }
        for i, head in enumerate(heads):
            if i < len(cells) and head:
                row[head] = cells[i]
        out.append(row)

    return out


def _guess_nim(cells: list[str]) -> str:
    """Ambil NIM dari sel: cari yang mirip nomor induk mahasiswa."""
    for c in cells:
        s = c.strip()
        if 8 <= len(s) <= 20 and re.fullmatch(r"[0-9]{6,}[A-Za-z0-9\-/]*|[A-Za-z]{1,4}[0-9]{4,}", s):
            return s
    return ""


def pick_detail_link(row: dict[str, Any]) -> dict[str, str] | None:
    """Pilih link yang paling mungkin menuju detail nilai mahasiswa."""
    links = row.get("links") or []
    if not links:
        return None

    for link in links:
        low = (link["href"] + " " + link.get("text", "")).lower()
        if any(h in low for h in _DETAIL_HINTS):
            return link

    # Tanpa teks yang membantu, ambil link pertama yang punya act=.
    for link in links:
        if "act=" in link["href"].lower():
            return link
    return links[0] if links else None


def summarize(soup: BeautifulSoup) -> dict[str, Any]:
    """Ringkasan hasil filter: header, jumlah baris, dan link detail."""
    table = find_result_table(soup)
    if table is None:
        return {"found": False, "headers": [], "rows": [], "count": 0, "sample_detail_link": None}

    rows = extract_rows(table)
    detail = None
    for row in rows:
        link = pick_detail_link(row)
        if link:
            detail = link
            break

    return {
        "found": True,
        "headers": headers(table),
        "count": len(rows),
        "rows": rows,
        "sample_detail_link": detail,
    }
