"""Build a single-student workbook from the campus transcript workbook."""
from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

import openpyxl
from openpyxl.cell.cell import MergedCell


CURRICULA = (
    ("Nilai MHS 2026", "TRANSKRIP NILAI 2026", "2026"),
    ("Nilai MHS 2026 P22", "TRANSKRIP NILAI 22 (2026)", "P22"),
    ("Nilai MHS 2026 P21", "TRANSKRIP NILAI 21 (2026)", "P21"),
)
ROSTER_SHEET = "Mahasiswa 2026"


@dataclass(frozen=True)
class TranscriptExport:
    content: bytes
    filename: str
    sheet_name: str
    curriculum: str


def _cell_nim(value: object) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return text[:-2] if text.endswith(".0") and text[:-2].isdigit() else text


def _clear_row(ws, row: int) -> None:
    for cell in ws[row]:
        if not isinstance(cell, MergedCell):
            cell.value = None


def build_transcript_export(template_path: Path, nim: str) -> TranscriptExport:
    """Return a sanitized, formatted workbook for one NIM."""
    nim = _cell_nim(nim)
    if not nim:
        raise ValueError("NIM wajib diisi.")
    if not template_path.is_file():
        raise FileNotFoundError(f"File template Excel tidak ditemukan: {template_path}")

    wb = openpyxl.load_workbook(template_path, data_only=False)
    try:
        matches: list[tuple[str, str, str, list[int]]] = []
        for source_name, transcript_name, curriculum in CURRICULA:
            if source_name not in wb.sheetnames or transcript_name not in wb.sheetnames:
                continue
            source = wb[source_name]
            rows = [
                row
                for row in range(2, source.max_row + 1)
                if _cell_nim(source.cell(row, 2).value) == nim
            ]
            if rows:
                matches.append((source_name, transcript_name, curriculum, rows))

        if not matches:
            raise ValueError(f"NIM {nim} tidak ditemukan pada data kurikulum workbook.")
        if len(matches) > 1:
            raise ValueError(f"NIM {nim} ditemukan pada lebih dari satu kurikulum.")

        source_name, transcript_name, curriculum, course_rows = matches[0]
        if ROSTER_SHEET not in wb.sheetnames:
            raise ValueError("Sheet identitas mahasiswa tidak ditemukan pada workbook.")
        roster = wb[ROSTER_SHEET]
        roster_row = next(
            (
                row
                for row in range(2, roster.max_row + 1)
                if _cell_nim(roster.cell(row, 3).value) == nim
            ),
            None,
        )
        if roster_row is None:
            raise ValueError(f"NIM {nim} belum memiliki data identitas di workbook.")

        selector = roster.cell(roster_row, 1).value
        student_name = str(roster.cell(roster_row, 5).value or "").strip()
        if selector is None or not student_name:
            raise ValueError(f"Identitas NIM {nim} belum lengkap di workbook.")

        for ws in wb.worksheets:
            if ws.title == "Kurikulum":
                for row in range(1, ws.max_row + 1):
                    _clear_row(ws, row)
            elif ws.title == ROSTER_SHEET:
                for row in range(2, ws.max_row + 1):
                    if row != roster_row:
                        _clear_row(ws, row)
            elif ws.title in {item[0] for item in CURRICULA}:
                for row in range(2, ws.max_row + 1):
                    keep = ws.title == source_name and row in course_rows
                    if not keep:
                        _clear_row(ws, row)

        transcript = wb[transcript_name]
        if curriculum == "2026":
            transcript["L2"] = selector
        elif curriculum == "P21":
            transcript["M2"] = selector
        else:
            transcript["M2"] = f"{nim} - {student_name}"

        for _, other_transcript, _ in CURRICULA:
            if other_transcript != transcript_name and other_transcript in wb.sheetnames:
                for column in range(12, 16):
                    wb[other_transcript].cell(2, column).value = None

        for ws in wb.worksheets:
            ws.sheet_state = "hidden"
        transcript.sheet_state = "visible"
        wb.active = wb.sheetnames.index(transcript_name)
        if getattr(wb, "calculation", None) is not None:
            wb.calculation.fullCalcOnLoad = True
            wb.calculation.forceFullCalc = True
            wb.calculation.calcMode = "auto"

        output = BytesIO()
        wb.save(output)
        safe_curriculum = curriculum.lower().replace(" ", "-")
        return TranscriptExport(
            content=output.getvalue(),
            filename=f"transkrip_{nim}_{safe_curriculum}.xlsx",
            sheet_name=transcript_name,
            curriculum=curriculum,
        )
    finally:
        wb.close()