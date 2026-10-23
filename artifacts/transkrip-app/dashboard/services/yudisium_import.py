"""Template creation and preflight validation for eCampuz graduation imports."""

from __future__ import annotations

import io
import re
from datetime import date, datetime
from typing import Any

IMPORT_SHEET_NAME = "Import data kelulusan"
IMPORT_HEADER_ROW = 6
IMPORT_HEADERS = [
    "No",
    "NIM",
    "Nama",
    "Status Lulus",
    "Semester Lulus",
    "Tanggal Yudisium",
    "No Surat Yudisium",
    "Tanggal Keluar/Lulus",
    "Jalur Lulus (Skripsi/Non Skripsi)",
    "Nomor Ijazah",
    "Tanggal Ijazah",
    "No ijazah lain",
]
_ALLOWED_STATUS = {"lulus", "belum lulus"}
_ALLOWED_PATHS = {"skripsi", "non skripsi"}
_ALLOWED_DIPLOMA_CODES = {"a", "b"}
_DATE_FORMATS = ("%d-%m-%Y", "%d/%m/%Y")


def build_graduation_template() -> bytes:
    """Return a clean BIFF .xls workbook matching the campus import layout."""
    import xlwt

    workbook = xlwt.Workbook(encoding="utf-8")
    sheet = workbook.add_sheet(IMPORT_SHEET_NAME)

    title_style = xlwt.easyxf(
        "font: bold on, height 260; align: horiz center, vert center;"
        "pattern: pattern solid, fore_colour ice_blue;"
        "borders: bottom thin, bottom_colour gray25"
    )
    note_style = xlwt.easyxf("font: bold on, colour dark_red; align: vert center")
    guide_style = xlwt.easyxf("font: colour dark_blue; align: vert center, wrap on")
    guide_example_style = xlwt.easyxf(
        "font: colour dark_green; align: vert center, wrap on"
    )
    header_style = xlwt.easyxf(
        "font: bold on, colour white; align: horiz center, vert center, wrap on;"
        "pattern: pattern solid, fore_colour dark_blue;"
        "borders: left thin, right thin, top thin, bottom thin,"
        "left_colour gray25, right_colour gray25, top_colour gray25, bottom_colour gray25"
    )
    text_style = xlwt.easyxf(
        "align: vert center; borders: bottom thin, bottom_colour gray25"
    )
    nim_style = xlwt.easyxf(
        "align: vert center; borders: bottom thin, bottom_colour gray25",
        num_format_str="@",
    )
    number_style = xlwt.easyxf(
        "align: horiz center, vert center; borders: bottom thin, bottom_colour gray25",
        num_format_str="0",
    )
    date_style = xlwt.easyxf(
        "align: vert center; borders: bottom thin, bottom_colour gray25",
        num_format_str="DD-MM-YYYY",
    )

    sheet.write_merge(0, 0, 0, 11, "DAFTAR MAHASISWA LULUS", title_style)
    sheet.write(1, 0, "Note", note_style)
    sheet.write_merge(
        1,
        1,
        1,
        11,
        "Isi satu baris untuk setiap mahasiswa. Jangan mengubah nama sheet, posisi header, atau urutan kolom.",
        guide_style,
    )
    sheet.write(2, 3, "Status Lulus", guide_style)
    sheet.write(2, 4, "Semester Lulus", guide_style)
    sheet.write(2, 5, "Tanggal Yudisium", guide_style)
    sheet.write(2, 6, "No Surat Yudisium", guide_style)
    sheet.write(2, 7, "Tanggal Keluar/Lulus", guide_style)
    sheet.write(2, 8, "Jalur Lulus", guide_style)
    sheet.write(2, 10, "Tanggal Ijazah", guide_style)
    sheet.write(2, 11, "No Ijazah Lain", guide_style)
    for column, value in {
        3: "Lulus / Belum Lulus",
        4: "Contoh: Gasal 2026-2027",
        5: "dd-mm-yyyy",
        6: "Nomor surat / teks",
        7: "dd-mm-yyyy",
        8: "Skripsi / Non Skripsi",
        9: "A / B",
        10: "dd-mm-yyyy",
        11: "Opsional, teks",
    }.items():
        sheet.write(3, column, value, guide_example_style)
    sheet.write(4, 3, "Isi status persis sesuai pilihan.", guide_example_style)
    sheet.write(4, 4, "Contoh format semester.", guide_example_style)
    sheet.write(4, 5, "Contoh: 10-03-2027", guide_example_style)
    sheet.write(4, 7, "Contoh: 10-03-2027", guide_example_style)
    sheet.write(4, 8, "Pilih salah satu jalur.", guide_example_style)
    sheet.write(4, 9, "Kode ijazah A atau B.", guide_example_style)
    sheet.write(4, 10, "Contoh: 10-03-2027", guide_example_style)
    sheet.write_merge(
        4,
        4,
        0,
        2,
        "Baris data dimulai pada baris 8. NIM disimpan sebagai teks.",
        guide_example_style,
    )

    sheet.row(0).height = 792
    for row_index in range(1, IMPORT_HEADER_ROW):
        sheet.row(row_index).height = 420
    sheet.row(IMPORT_HEADER_ROW).height = 720
    for column, header in enumerate(IMPORT_HEADERS):
        sheet.write(IMPORT_HEADER_ROW, column, header, header_style)

    widths = [8, 16, 28, 18, 23, 19, 24, 22, 31, 18, 18, 24]
    for column, width in enumerate(widths):
        sheet.col(column).width = width * 256

    for row_index in range(IMPORT_HEADER_ROW + 1, IMPORT_HEADER_ROW + 101):
        sheet.write(row_index, 0, "", number_style)
        sheet.write(row_index, 1, "", nim_style)
        sheet.write(row_index, 2, "", text_style)
        for column in (3, 4, 6, 8, 9, 11):
            sheet.write(row_index, column, "", text_style)
        for column in (5, 7, 10):
            sheet.write(row_index, column, "", date_style)

    output = io.BytesIO()
    workbook.save(output)
    return output.getvalue()


def _display_value(value: Any, cell_type: int, datemode: int) -> str:
    import xlrd

    if value is None or value == "":
        return ""
    if cell_type == xlrd.XL_CELL_DATE:
        parsed = xlrd.xldate_as_datetime(value, datemode)
        return parsed.strftime("%d-%m-%Y")
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _valid_date(value: Any, cell_type: int, datemode: int) -> bool:
    import xlrd

    if value in (None, ""):
        return True
    if cell_type == xlrd.XL_CELL_DATE:
        try:
            xlrd.xldate_as_datetime(value, datemode)
            return True
        except (ValueError, OverflowError):
            return False
    text = str(value).strip()
    if not re.fullmatch(r"(?:\d{2}-\d{2}-\d{4}|\d{2}/\d{2}/\d{4})", text):
        return False
    for fmt in _DATE_FORMATS:
        try:
            datetime.strptime(text, fmt)
            return True
        except ValueError:
            continue
    return False


def validate_graduation_import(
    content: bytes,
    filename: str = "graduation.xls",
) -> dict[str, Any]:
    """Validate a completed legacy .xls without exposing student names."""
    import xlrd

    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    if not filename.casefold().endswith(".xls"):
        return {
            "valid": False,
            "total": 0,
            "errors": [{"row": None, "field": "file", "message": "File import eCampuz harus berformat .xls."}],
            "warnings": [],
        }
    try:
        workbook = xlrd.open_workbook(file_contents=content, formatting_info=False)
    except (xlrd.XLRDError, ValueError, OSError) as exc:
        return {
            "valid": False,
            "total": 0,
            "errors": [{"row": None, "field": "file", "message": f"File bukan workbook XLS yang bisa dibaca: {exc}"}],
            "warnings": [],
        }

    if IMPORT_SHEET_NAME not in workbook.sheet_names():
        workbook.release_resources()
        return {
            "valid": False,
            "total": 0,
            "errors": [{"row": None, "field": "sheet", "message": f"Nama sheet harus tepat {IMPORT_SHEET_NAME!r}."}],
            "warnings": [],
        }

    sheet = workbook.sheet_by_name(IMPORT_SHEET_NAME)
    if sheet.nrows <= IMPORT_HEADER_ROW:
        workbook.release_resources()
        return {
            "valid": False,
            "total": 0,
            "errors": [{"row": None, "field": "header", "message": "Header baris 7 atau baris data tidak ditemukan."}],
            "warnings": [],
        }
    actual_headers = [
        _display_value(sheet.cell_value(IMPORT_HEADER_ROW, column), sheet.cell_type(IMPORT_HEADER_ROW, column), workbook.datemode)
        for column in range(len(IMPORT_HEADERS))
    ]
    if actual_headers != IMPORT_HEADERS:
        workbook.release_resources()
        return {
            "valid": False,
            "total": 0,
            "errors": [{
                "row": IMPORT_HEADER_ROW + 1,
                "field": "header",
                "message": "Header baris 7 tidak sama dengan format eCampuz. Unduh template resmi dari halaman ini.",
            }],
            "warnings": [],
        }

    seen_nims: set[str] = set()
    records: list[dict[str, Any]] = []
    for row_index in range(IMPORT_HEADER_ROW + 1, sheet.nrows):
        raw = [sheet.cell_value(row_index, col) for col in range(len(IMPORT_HEADERS))]
        types = [sheet.cell_type(row_index, col) for col in range(len(IMPORT_HEADERS))]
        row = [_display_value(value, types[index], workbook.datemode) for index, value in enumerate(raw)]
        if not any(value for value in row):
            continue
        excel_row = row_index + 1
        record_errors: list[dict[str, Any]] = []

        number_text = row[0]
        try:
            number = int(number_text)
            if number < 1:
                raise ValueError
            if number != len(records) + 1:
                record_errors.append({"row": excel_row, "field": IMPORT_HEADERS[0], "message": f"Nomor baris harus urut; nilai berikutnya {len(records) + 1}."})
        except ValueError:
            record_errors.append({"row": excel_row, "field": IMPORT_HEADERS[0], "message": "Nomor harus bilangan bulat positif."})
        nim = row[1]
        if not re.fullmatch(r"[0-9]{6,20}", nim):
            record_errors.append({"row": excel_row, "field": IMPORT_HEADERS[1], "message": "NIM wajib berisi 6-20 digit angka; gunakan sel teks agar nol di depan tidak hilang."})
        elif nim in seen_nims:
            record_errors.append({"row": excel_row, "field": IMPORT_HEADERS[1], "message": "NIM duplikat di dalam file."})
        else:
            seen_nims.add(nim)
        if not row[2]:
            record_errors.append({"row": excel_row, "field": IMPORT_HEADERS[2], "message": "Nama wajib diisi."})

        status = row[3].casefold()
        if status not in _ALLOWED_STATUS:
            record_errors.append({"row": excel_row, "field": IMPORT_HEADERS[3], "message": "Isi tepat 'Lulus' atau 'Belum Lulus'."})
        if status == "lulus":
            semester = row[4]
            if not semester or not re.search(r"\d{4}\s*[-/]\s*\d{4}", semester):
                record_errors.append({"row": excel_row, "field": IMPORT_HEADERS[4], "message": "Untuk status Lulus, isi semester seperti 'Gasal 2026-2027'."})
            for col in (5, 7, 10):
                if not row[col]:
                    record_errors.append({"row": excel_row, "field": IMPORT_HEADERS[col], "message": "Tanggal wajib diisi untuk status Lulus."})
                elif not _valid_date(raw[col], types[col], workbook.datemode):
                    record_errors.append({"row": excel_row, "field": IMPORT_HEADERS[col], "message": "Gunakan format tanggal dd-mm-yyyy."})
            for col in (6,):
                if not row[col]:
                    record_errors.append({"row": excel_row, "field": IMPORT_HEADERS[col], "message": "Nomor surat yudisium wajib diisi untuk status Lulus."})
            if row[8].casefold() not in _ALLOWED_PATHS:
                record_errors.append({"row": excel_row, "field": IMPORT_HEADERS[8], "message": "Isi tepat 'Skripsi' atau 'Non Skripsi'."})
            if row[9].casefold() not in _ALLOWED_DIPLOMA_CODES:
                record_errors.append({"row": excel_row, "field": IMPORT_HEADERS[9], "message": "Isi kode ijazah 'A' atau 'B'."})
        else:
            for col in (5, 7, 10):
                if row[col] and not _valid_date(raw[col], types[col], workbook.datemode):
                    record_errors.append({"row": excel_row, "field": IMPORT_HEADERS[col], "message": "Gunakan format tanggal dd-mm-yyyy."})
            if row[8] and row[8].casefold() not in _ALLOWED_PATHS:
                record_errors.append({"row": excel_row, "field": IMPORT_HEADERS[8], "message": "Isi tepat 'Skripsi' atau 'Non Skripsi'."})
            if row[9] and row[9].casefold() not in _ALLOWED_DIPLOMA_CODES:
                record_errors.append({"row": excel_row, "field": IMPORT_HEADERS[9], "message": "Isi kode ijazah 'A' atau 'B'."})

        errors.extend(record_errors)
        records.append({"row": excel_row, "nim": nim, "status": row[3], "errors": len(record_errors)})

    workbook.release_resources()
    if not records:
        errors.append({"row": None, "field": "data", "message": "Tidak ada baris data mahasiswa mulai baris 8."})
    return {
        "valid": bool(records) and not errors,
        "total": len(records),
        "valid_rows": sum(1 for record in records if not record["errors"]),
        "invalid_rows": sum(1 for record in records if record["errors"]),
        "preview": records[:20],
        "remaining": max(0, len(records) - 20),
        "errors": errors[:200],
        "warnings": warnings,
    }