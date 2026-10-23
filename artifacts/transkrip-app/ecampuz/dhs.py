"""Pembacaan Detail Hasil Studi (DHS) eCampuz.

Tiga lapis navigasi, semua read-only (GET):

    L1  nilai/?act=<LIST_ACT>&cek_filter=niu&angk_awal=..&angk_akhir=..
        &filter_awal=<NIM>&filter_akhir=<NIM>&prodi=..&action=submitted
        -> satu baris: NIM, Nama, Prodi, jumlah semester

    L2  nilai/?act=<DETAIL_ACT>&w=<NIM>&x=<angkatan>&y=<prodi>
        -> daftar per semester + link DHS tiap semester

    L3  nilai/?act=<DHS_ACT>&w=<NIM>&x=<id_semester_base64>&y=<angkatan>
        -> tabel nilai per mata kuliah + IPS/IPK

Dua hal penting yang ditemukan di lapangan:

1. **Kode `act` berubah setiap login.** Nilainya dihitung dari state sesi, jadi
   tidak boleh ditulis mati di kode. Semua act diambil dari menu yang sedang
   hidup lewat `resolve_act()`.

2. **Filter NIM butuh angkatan.** Kalau `angk_awal`/`angk_akhir` kosong, filter
   NIM diabaikan diam-diam dan hasilnya 0 baris (bukan error). Jadi angkatan
  |wajib| diisi.

3. **Skala nilai tiap kampus berbeda.** Skala asli dibaca dari tabel
   "Keterangan Nilai" di halaman DHS, bukan dari tabel konstanta di kode.
"""

from __future__ import annotations

import base64
import binascii
import re
from typing import Any
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup, Tag

from .client import clean_ws

MODULE = "nilai/"


# --------------------------------------------------------------------------
# 1. Resolver act (wajib, karena act terikat sesi)
# --------------------------------------------------------------------------

def resolve_act(
    client,
    module: str = MODULE,
    text: str | None = None,
    href_contains: str | None = None,
) -> str:
    """Ambil nilai act yang SEDANG BERLAKU dari menu hidup.

    `text`      cocokkan teks link (mis. "Nilai per Mahasiswa")
    `href_contains` cocokkan potongan href (mis. "nilai/")

    Mengambil dari dashboard, bukan dari konstanta, karena act berubah tiap
    kali login ulang.
    """
    resp = client.get(client.base, note="resolve_act")
    soup = client.soup(resp.text)
    best: str | None = None

    for a in soup.find_all("a", href=True):
        href = a["href"]
        label = clean_ws(a.get_text(" "))
        if module and module not in href:
            continue
        if text and text.lower() not in label.lower():
            continue
        if href_contains and href_contains not in href:
            continue
        act = parse_qs(urlparse(href).query).get("act", [""])[0]
        if re.fullmatch(r"[0-9a-f]{16,}", act):
            # Prioritaskan yang persis sama dengan teks, bukan "Manajemen ...".
            if text and label.lower() == text.lower():
                return act
            best = best or act

    if best:
        return best
    raise LookupError(
        f"act untuk {text or module!r} tidak ditemukan di menu. "
        "Jalankan: .\\run.bat discover"
    )


def resolve_act_from_row(html: str, module: str = MODULE) -> str | None:
    """Ambil act dari form filter pada halaman list (alternatif resolver)."""
    soup = BeautifulSoup(html, "lxml")
    node = soup.find("input", attrs={"name": "act"})
    if node and node.get("value"):
        return str(node["value"])
    return None


# --------------------------------------------------------------------------
# 2. L1 - cari mahasiswa
# --------------------------------------------------------------------------

def build_list_url(
    act: str,
    nim: str,
    angkatan: str,
    prodi: str = "all",
    module: str = MODULE,
) -> str:
    """URL daftar mahasiswa terfilter. `angkatan` WAJIB diisi."""
    from urllib.parse import unquote, urlencode

    params = {
        "prodi": unquote(str(prodi)),
        "angk_awal": str(angkatan),
        "angk_akhir": str(angkatan),
        "cek_filter": "niu",
        "filter_awal": str(nim),
        "filter_akhir": str(nim),
        "action": "submitted",
        "act": act,
    }
    return f"{module}?{urlencode(params)}"


def find_student(client, nim: str, angkatan: str, prodi: str = "all") -> dict[str, Any]:
    """Cari satu mahasiswa di list Nilai per Mahasiswa."""
    act = resolve_act(client, MODULE, "Nilai per Mahasiswa")
    url = build_list_url(act, nim, angkatan, prodi)
    resp = client.get(url, note="dhs_l1")
    soup = client.soup(resp.text)

    from .niu_filter import summarize

    info = summarize(soup)
    if not info["found"]:
        raise LookupError("Halaman list tidak memuat tabel hasil.")
    if info["count"] == 0:
        raise LookupError(
            f"NIM {nim} angkatan {angkatan} tidak ditemukan. "
            "Coba cek angka angkatan, atau isi --prodi dengan kode prodi."
        )

    row = info["rows"][0]
    link = None
    for cand in row.get("links", []):
        href = cand.get("href", "")
        if re.search(r"[?&]w=", href):
            link = href
            break
    if not link:
        raise LookupError(f"Tidak ada link detail pada baris NIM {nim}.")

    return {
        "nim": row.get("nim") or nim,
        "nama": row.get("Nama", ""),
        "prodi": row.get("Program Studi", ""),
        "jumlah_semester": row.get("Jumlah Semester Telah Ditempuh", ""),
        "detail_href": link,
        "list_act": act,
    }


# --------------------------------------------------------------------------
# 3. L2 - daftar per semester
# --------------------------------------------------------------------------

def parse_semesters(html: str) -> dict[str, Any]:
    """Baca halaman per-semester + kumpulkan link DHS."""
    soup = BeautifulSoup(html, "lxml")
    identity = parse_identity(soup)

    semesters: list[dict[str, Any]] = []
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if len(rows) < 2:
            continue
        head = [clean_ws(td.get_text(" ")) for td in rows[0].find_all(["td", "th"])]
        if not any("semester" in h.lower() for h in head):
            continue

        for tr in rows[1:]:
            cells = [clean_ws(td.get_text(" ")) for td in tr.find_all("td", recursive=False)]
            if len(cells) < 3:
                continue
            link = None
            for a in tr.find_all("a", href=True):
                if re.search(r"[?&]x=", a["href"]):
                    link = a["href"]
                    break
            semesters.append(
                {
                    "no": cells[0],
                    "semester": cells[2] if len(cells) > 2 else "",
                    "sks": cells[3] if len(cells) > 3 else "",
                    "ip": cells[-1] if cells else "",
                    "dhs_href": link,
                }
            )

    return {"identity": identity, "semesters": semesters}


# --------------------------------------------------------------------------
# 4. L3 - parse DHS
# --------------------------------------------------------------------------

_GRADE_RE = re.compile(r"^[A-E][+-]?$")


def parse_grade_scale(soup: BeautifulSoup) -> dict[str, float]:
    """Baca tabel 'Keterangan Nilai' -> skala bobot asli kampus ini."""
    scale: dict[str, float] = {}
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if len(rows) < 4:
            continue
        first = clean_ws(rows[0].get_text(" ")).lower()
        if "keterangan nilai" not in first:
            continue
        for tr in rows[2:]:
            cells = [clean_ws(td.get_text(" ")) for td in tr.find_all("td", recursive=False)]
            if len(cells) < 2:
                continue
            grade, weight = cells[0], cells[1]
            if not _GRADE_RE.match(grade):
                continue
            try:
                scale[grade] = float(weight.replace(",", "."))
            except ValueError:
                continue
        if scale:
            return scale
    return scale


def parse_dhs(html: str) -> dict[str, Any]:
    """Parse halaman Detail Hasil Studi -> identitas + nilai + ringkasan."""
    soup = BeautifulSoup(html, "lxml")
    identity = parse_identity(soup)

    scale = parse_grade_scale(soup)

    # Kolom DHS (header 2 tingkat):
    # No | Aksi | Kode | Nama | Kelas | Jenis | SKS | KE | Status | Bobot
    #    | Nilai | SKS*Bobot | Aplikasi | Pengubah | Tanggal
    idx = {"kode": 2, "mata_kuliah": 3, "kelas": 4, "jenis": 5,
           "sks": 6, "bobot": 9, "nilai": 10, "sks_bobot": 11,
           "aplikasi": 12, "pengubah": 13, "tanggal": 14}

    courses: list[dict[str, Any]] = []
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if len(rows) < 3:
            continue
        head = [clean_ws(td.get_text(" ")) for td in rows[0].find_all(["td", "th"])]
        if not any("matakuliah" in h.lower() for h in head):
            continue
        if not any("nilai" in h.lower() for h in head):
            continue

        for tr in rows[1:]:
            cells = [clean_ws(td.get_text(" ")) for td in tr.find_all("td", recursive=False)]
            if len(cells) < 11:
                continue

            def at(key: str) -> str:
                i = idx[key]
                return cells[i] if i < len(cells) else ""

            kode = at("kode")
            nama = at("mata_kuliah")
            if not nama:
                continue
            if nama.lower() == "total" or not kode:
                continue

            grade = at("nilai")
            bobot = at("bobot")
            try:
                weight = float(bobot.replace(",", ".")) if bobot else None
            except ValueError:
                weight = None
            if weight is None and _GRADE_RE.match(grade):
                weight = scale.get(grade)

            try:
                sks = float(at("sks").replace(",", ".")) if at("sks") else None
            except ValueError:
                sks = None

            courses.append(
                {
                    "kode": kode,
                    "mata_kuliah": nama,
                    "kelas": at("kelas"),
                    "jenis": at("jenis"),
                    "sks": sks,
                    "nilai": grade,
                    "bobot": weight,
                    "sks_bobot": at("sks_bobot"),
                    "dosen_pengubah": at("pengubah"),
                    "tanggal_ubah": at("tanggal"),
                }
            )
        break

    text = clean_ws(soup.get_text(" "))
    ips = _grab(text, r"IP\s*Semester\s*\(IPS\)\s*:\s*([0-9]+[.,][0-9]{1,2})")
    ipk = _grab(text, r"IP\s*Kumulatif\s*\(IPK\)\s*:\s*([0-9]+[.,][0-9]{1,2})")

    return {
        "identity": identity,
        "courses": courses,
        "ips": ips,
        "ipk": ipk,
        "grade_scale": scale,
    }


def _grab(text: str, pattern: str) -> str | None:
    m = re.search(pattern, text, re.I)
    return m.group(1).replace(",", ".") if m else None


# --------------------------------------------------------------------------
# 5. Orkestrasi
# --------------------------------------------------------------------------

def parse_identity(soup: BeautifulSoup) -> dict[str, str]:
    """Baca baris identitas "Label : Nilai".

    Bentuk nyata di eAkademik: <tr><th>Label</th><td>: Nilai</td></tr>,
    tapi juga bisa 3 sel atau label dan nilai menyatu dalam satu sel.
    Ketiganya ditangani supaya tidak rapuh.
    """
    out: dict[str, str] = {}
    for tr in soup.find_all("tr"):
        cells = [
            clean_ws(td.get_text(" "))
            for td in tr.find_all(["td", "th"], recursive=False)
        ]
        if not cells:
            continue

        key = ""
        val = ""

        if len(cells) >= 3 and cells[1].strip() in (":", "=", "-", ""):
            key, val = cells[0], " ".join(cells[2:])
        elif len(cells) == 2 and ":" in cells[1]:
            key, val = cells[0], cells[1].split(":", 1)[1]
        elif ":" in cells[0]:
            key, val = cells[0].split(":", 1)

        key = clean_ws(key).lower().rstrip(":").strip()
        val = clean_ws(val).lstrip(": =-").strip()
        if key and val and len(key) <= 40 and key != "total":
            out[key] = val
    return out


def _x_param(href: str) -> str:
    """Ambil nilai param x dari sebuah href."""
    if not href:
        return ""
    return parse_qs(urlparse(href).query).get("x", [""])[0]


def decode_x(x: str) -> str:
    """Decode param x (base64 id semester) supaya mudah dibaca."""
    if not x:
        return ""
    from urllib.parse import unquote

    try:
        raw = base64.b64decode(unquote(x) + "===")
        return raw.decode("utf-8", "replace").strip("\x00")
    except (binascii.Error, ValueError):
        return ""


def fetch_transcript(
    client,
    nim: str,
    angkatan: str,
    prodi: str = "all",
    save_html: bool = False,
) -> dict[str, Any]:
    """Ambil transkrip lengkap satu mahasiswa (semua semester)."""
    student = find_student(client, nim, angkatan, prodi)
    detail = client.get(MODULE + student["detail_href"], note="dhs_l2")
    if save_html:
        client.save_raw(f"dhs_l2_{nim}", detail.text)

    info = parse_semesters(detail.text)
    out: dict[str, Any] = {
        "nim": student["nim"],
        "nama": student["nama"],
        "prodi": student["prodi"],
        "angkatan": angkatan,
        "identity": info["identity"],
        "semesters": [],
    }

    for sem in info["semesters"]:
        entry = {
            "semester": sem["semester"],
            "sks": sem["sks"],
            "ip": sem["ip"],
            "semester_id": decode_x(_x_param(sem["dhs_href"])),
            "courses": [],
            "ips": None,
            "ipk": None,
        }
        if sem.get("dhs_href"):
            resp = client.get(MODULE + sem["dhs_href"], note="dhs_l3")
            if save_html:
                client.save_raw(f"dhs_l3_{nim}_{entry['semester_id']}", resp.text)
            dhs = parse_dhs(resp.text)
            entry["courses"] = dhs["courses"]
            entry["ips"] = dhs["ips"]
            entry["ipk"] = dhs["ipk"]
            entry["dosen_pa"] = dhs["identity"].get("dosen pa", "")
            entry["grade_scale"] = dhs["grade_scale"]
        out["semesters"].append(entry)

    graded = [
        c
        for s in out["semesters"]
        for c in s["courses"]
        if c.get("bobot") is not None and c.get("sks")
    ]
    out["total_sks"] = sum(c["sks"] for c in graded)
    out["jumlah_mk"] = len(graded)
    out["ips_keseluruhan"] = _calc_ips(graded)
    out["ipk_terakhir"] = next(
        (s["ipk"] for s in reversed(out["semesters"]) if s.get("ipk")), None
    )
    return out


def _calc_ips(courses: list[dict[str, Any]]) -> float | None:
    if not courses:
        return None
    total = sum(c["bobot"] * c["sks"] for c in courses)
    sks = sum(c["sks"] for c in courses)
    return round(total / sks, 2) if sks else None


def to_rows(result: dict[str, Any]) -> list[dict[str, Any]]:
    """Ratakan hasil transkrip jadi baris CSV."""
    rows: list[dict[str, Any]] = []
    for sem in result["semesters"]:
        for c in sem["courses"]:
            rows.append(
                {
                    "nim": result["nim"],
                    "nama": result["nama"],
                    "prodi": result["prodi"],
                    "angkatan": result["angkatan"],
                    "semester": sem["semester"],
                    "semester_id": sem.get("semester_id", ""),
                    "kode": c["kode"],
                    "mata_kuliah": c["mata_kuliah"],
                    "kelas": c["kelas"],
                    "jenis": c["jenis"],
                    "sks": c["sks"],
                    "nilai": c["nilai"],
                    "bobot": c["bobot"],
                    "sks_bobot": c["sks_bobot"],
                    "dosen_pengubah": c["dosen_pengubah"],
                    "tanggal_ubah": c["tanggal_ubah"],
                    "sumber_nilai": c.get("sumber_nilai", "eCampuz") if c.get("nilai") else "",
                }
            )
    return rows
