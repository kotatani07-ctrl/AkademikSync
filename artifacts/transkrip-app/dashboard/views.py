"""View dashboard eCampuz — port dari src/webapp/app.py (Flask) ke Django."""
from __future__ import annotations

import json
import time
from pathlib import Path
from urllib.parse import urlsplit

from django.contrib.auth.decorators import login_required
from django.http import FileResponse, HttpResponse, JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt

from ecampuz.yudisium_browser import read_records

from dashboard import jobs
import dashboard.services.data as d
from dashboard.services.transcript_export import build_transcript_export
from dashboard.services.yudisium_import import (
    build_graduation_template,
    validate_graduation_import,
)
from dashboard.services.grade_entry import (
    inspect_grade_workbook,
    validate_grade_workbook,
)

TEMPLATE_FILE_NAME = "Template_Transkrip_2026_FIXED (5) - FILLED.xlsx"


def _json_body(request) -> dict:
    try:
        data = json.loads(request.body or b"{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _is_local_operator_request(request) -> bool:
    remote = request.META.get("REMOTE_ADDR", "")
    if remote not in {"127.0.0.1", "::1"}:
        return False
    origin = request.META.get("HTTP_ORIGIN")
    if not origin:
        return True
    origin_host = urlsplit(origin).netloc.casefold()
    return origin_host == request.get_host().casefold()


def _json_file(content: bytes, filename: str, content_type: str) -> HttpResponse:
    resp = HttpResponse(content, content_type=content_type)
    resp["Content-Disposition"] = f'attachment; filename="{filename}"'
    return resp


# ─── Halaman ──────────────────────────────────────────────────────────────────

@login_required
def index(request):
    sums = d.student_summaries()
    filled = d.excel_filled_stats()
    mapping = d.mapping_rows()
    angkatan = d.web_config().get("angkatan") or "2024"
    ckpt = d.checkpoint(angkatan)
    rows = d.dhs_rows()
    total_ipk = [s["ipk"] for s in sums.values()]
    top = sorted(sums.values(), key=lambda s: s["ipk"], reverse=True)[:8]
    sisa = max(0, len(sums) - len(ckpt["done"]) - len(ckpt["failed"]))
    exact = rec = skip = 0
    for m in mapping:
        st = m.get("status", "")
        if st == "APPROVED-EXACT":
            exact += 1
        elif st == "APPROVED-REC":
            rec += 1
        elif st == "SKIP":
            skip += 1
    return render(request, "dashboard/index.html", {
        "sums": sums,
        "filled": filled,
        "mapping": mapping,
        "mapping_counts": {"exact": exact, "rec": rec, "skip": skip},
        "ckpt": ckpt,
        "ckpt_nim": angkatan,
        "sisa": sisa,
        "row_count": len(rows),
        "avg_ipk": (sum(total_ipk) / len(total_ipk)) if total_ipk else 0.0,
        "top": top,
        "grade_class": jobs.GRADE_CLASS,
    })


@login_required
def daftar_mahasiswa(request):
    sums = d.student_summaries()
    excel = d.excel_data()
    ckpt = d.checkpoint(d.web_config().get("angkatan") or "2024")
    rows = []
    for nim, s in sorted(sums.items()):
        rows.append({
            **s,
            "baris_excel": len(excel.get(nim, [])),
            "terisi_excel": sum(1 for r in excel.get(nim, []) if r["terisi"]),
            "template_available": bool(excel.get(nim)),
            "ada_json": d.nim_transcript(nim) is not None,
            "status_dhs": "OK" if nim in ckpt["done"]
            else "GAGAL" if nim in ckpt["failed"] else "-",
        })
    return render(request, "dashboard/mahasiswa.html", {"rows": rows})


@login_required
def detail_mahasiswa(request, nim: str):
    sums = d.student_summaries()
    excel = d.excel_data()
    transcript = d.nim_transcript(nim)
    excel_rows = excel.get(nim, [])
    return render(request, "dashboard/mahasiswa_detail.html", {
        "nim": nim,
        "ringkas": sums.get(nim),
        "excel_rows": excel_rows,
        "excel_terisi": sum(1 for r in excel_rows if r["terisi"]),
        "template_available": bool(excel_rows),
        "transcript": transcript,
        "grade_class": jobs.GRADE_CLASS,
    })


@login_required
def pemetaan(request):
    all_mapping = d.mapping_rows()
    exact = sum(1 for m in all_mapping if m.get("status") == "APPROVED-EXACT")
    rec = sum(1 for m in all_mapping if m.get("status") == "APPROVED-REC")
    skip = sum(1 for m in all_mapping if m.get("status") == "SKIP")
    counts = {
        "total": len(all_mapping),
        "exact": exact,
        "rec": rec,
        "skip": skip,
    }

    group = request.GET.get("kelompok", "all")
    if group != "all":
        mapping = [m for m in all_mapping if m.get("status") == group]
    else:
        mapping = all_mapping

    return render(request, "dashboard/pemetaan.html", {
        "mapping": mapping,
        "group": group,
        "counts": counts,
    })


@login_required
def scrape(request):
    cfg = d.web_config()
    ckpt = d.checkpoint(cfg.get("angkatan") or "2024")
    prodi_options = d.prodi_options()
    return render(request, "dashboard/scrape.html", {
        "cfg": cfg,
        "ckpt": ckpt,
        "prodi_options": prodi_options,
        "prodi_values": [p["value"] for p in prodi_options],
    })


@login_required
def yudisium_page(request):
    return render(request, "dashboard/yudisium.html", {
        "job": jobs._yudisium_snapshot(),
    })


@login_required
def grade_entry_page(request):
    return render(request, "dashboard/grade_entry.html")


@login_required
def audit_page(request):
    return render(request, "dashboard/audit.html", {
        "rows": d.audit_tail(),
    })


@login_required
def ecampuz_login_page(request):
    cfg = d.web_config()
    try:
        ec_cfg = jobs.load_config()
        default_user = ec_cfg.username or "AKADEMIK@AKD"
    except Exception:  # noqa: BLE001
        default_user = "AKADEMIK@AKD"
    return render(
        request,
        "dashboard/login.html",
        {"cfg": cfg, "default_username": default_user},
    )


# ─── API: sesi & scraping ─────────────────────────────────────────────────────

@login_required
def api_session(request):
    try:
        client = jobs._client()
        if not client.authenticated:
            return JsonResponse({"ok": False, "detail": "Belum ada sesi tersimpan"})
        client.cfg.timeout = min(client.cfg.timeout, 15)
        ok, detail = client.verify_session()
        return JsonResponse({"ok": ok, "detail": detail})
    except Exception as exc:  # noqa: BLE001
        return JsonResponse({"ok": False, "detail": f"error: {exc}"})


# ─── API: login eCampuz (captcha dua-langkah + browser) ───────────────────────

@csrf_exempt
@login_required
def api_login_captcha(request):
    """Langkah 1: username/password + minta soal captcha."""
    body = _json_body(request)
    try:
        data = jobs.request_login_captcha(
            body.get("username", ""), body.get("password", "")
        )
    except Exception as exc:  # noqa: BLE001
        return JsonResponse({"ok": False, "message": f"Gagal memuat captcha: {exc}"} )
    return JsonResponse({"ok": True, "login_id": data["id"], "soal": data["soal"]})


@csrf_exempt
@login_required
def api_login_submit(request):
    """Langkah 2: kirim jawaban captcha. Bila salah, server siapkan soal baru."""
    body = _json_body(request)
    result = jobs.submit_login_captcha(
        body.get("login_id", ""),
        body.get("answer", ""),
        body.get("username", ""),
        body.get("password", ""),
    )
    return JsonResponse(result)


@csrf_exempt
@login_required
def api_login_browser(request):
    """Buka jendela browser (Playwright) untuk login manual captcha."""
    body = _json_body(request)
    try:
        result = jobs.start_browser_login(
            body.get("username", ""), body.get("password", "")
        )
    except Exception as exc:  # noqa: BLE001
        return JsonResponse({"ok": False, "message": f"error: {exc}"})
    return JsonResponse(result)


@csrf_exempt
@login_required
def api_login_clear(request):
    if not _is_local_operator_request(request):
        return JsonResponse(
            {"ok": False, "message": "Hapus sesi hanya tersedia dari browser lokal."},
            status=403,
        )
    return JsonResponse(jobs.clear_ecampuz_session())


@csrf_exempt
@login_required
def api_scrape_start(request):
    params = _json_body(request)
    cfg = d.web_config()
    params.setdefault("nim_file", cfg.get("nim_file", "nim_2024_sheet.txt"))
    params.setdefault("angkatan", cfg.get("angkatan", "2024"))
    params.setdefault("prodi", cfg.get("prodi", "all"))
    params.setdefault("per_mahasiswa", True)
    params.setdefault("reset", False)
    params.setdefault("retry_failed", False)
    params.setdefault("neo_feeder_fallback", False)
    started = jobs.start_job(params)
    if not started:
        return JsonResponse({"ok": False, "message": "Pekerjaan lain sedang berjalan"})
    return JsonResponse({"ok": True, "message": "Pekerjaan dimulai"})


@csrf_exempt
@login_required
def api_scrape_connect_and_run(request):
    payload = _json_body(request)
    result = jobs.ensure_session_and_start(payload)
    return JsonResponse(result)


@login_required
def api_scrape_status(request):
    payload = {
        "running": jobs.JOB_STATE["running"],
        "started": jobs.JOB_STATE["started"],
        "finished": jobs.JOB_STATE["finished"],
        "rc": jobs.JOB_STATE["rc"],
        "params": jobs.JOB_STATE["params"],
        "error": jobs.JOB_STATE["error"],
        "log": jobs.JOB_STATE["log"][-400:],
    }
    return JsonResponse(payload)


# ─── API: yudisium ────────────────────────────────────────────────────────────

@csrf_exempt
@login_required
def api_yudisium_upload(request):
    if not _is_local_operator_request(request):
        return JsonResponse({"ok": False, "message": "Aksi yudisium hanya tersedia dari browser lokal."}, status=403)
    _file = request.FILES.get("roster")
    if not _file or not _file.name:
        return JsonResponse({"ok": False, "message": "Pilih file Excel atau CSV terlebih dahulu."}, status=400)
    original_name = _file.name
    try:
        info = jobs.prep_yudisium_upload(_file, original_name)
    except ValueError as exc:
        return JsonResponse({"ok": False, "message": str(exc)}, status=400)

    nim_column = request.POST.get("nim_column", "NIM").strip() or "NIM"
    sheet = request.POST.get("sheet", "").strip() or None
    try:
        records = read_records(info["path"], nim_column, sheet=sheet)
    except (OSError, ValueError, ImportError) as exc:
        Path(info["path"]).unlink(missing_ok=True)
        return JsonResponse({"ok": False, "message": str(exc)}, status=400)

    actual_column = next(
        column for column in records[0]
        if column.casefold() == nim_column.casefold()
    )
    nims = [record[actual_column] for record in records]
    jobs.YUDI_UPLOADS[info["token"]] = {
        "path": info["path"],
        "filename": original_name,
        "nim_column": actual_column,
        "sheet": sheet,
        "count": len(nims),
        "created": time.time(),
    }
    return JsonResponse({
        "ok": True,
        "upload_id": info["token"],
        "filename": original_name,
        "total": len(nims),
        "preview": nims[:20],
        "remaining": max(0, len(nims) - 20),
    })


@login_required
def api_yudisium_template(request):
    content = build_graduation_template()
    return _json_file(
        content,
        "Template_Import_Data_Kelulusan.xls",
        "application/vnd.ms-excel",
    )


@csrf_exempt
@login_required
def api_yudisium_validate_import(request):
    if not _is_local_operator_request(request):
        return JsonResponse({"ok": False, "message": "Validasi import hanya tersedia dari browser lokal."}, status=403)
    uploaded = request.FILES.get("graduation_file")
    if not uploaded or not uploaded.name:
        return JsonResponse({"ok": False, "message": "Pilih file .xls untuk divalidasi."}, status=400)
    if not jobs.secure_filename(uploaded.name).casefold().endswith(".xls"):
        return JsonResponse({"ok": False, "message": "Import Data Kelulusan eCampuz harus .xls; .xlsx/CSV tidak diterima di tahap ini."}, status=400)
    report = validate_graduation_import(uploaded.read(), uploaded.name)
    return JsonResponse({"ok": True, "report": report})


@login_required
def api_grade_entry_preview(request):
    if request.method != "POST":
        return JsonResponse({"ok": False, "message": "Gunakan POST untuk mengirim file."}, status=405)
    if not _is_local_operator_request(request):
        return JsonResponse(
            {"ok": False, "message": "Preview input nilai hanya tersedia dari browser lokal."},
            status=403,
        )
    uploaded = request.FILES.get("workbook")
    if not uploaded or not uploaded.name:
        return JsonResponse({"ok": False, "message": "Pilih file Excel terlebih dahulu."}, status=400)
    if Path(uploaded.name).suffix.casefold() not in {".xlsx", ".xlsm"}:
        return JsonResponse({"ok": False, "message": "Gunakan file Excel .xlsx atau .xlsm."}, status=400)
    if uploaded.size > 20 * 1024 * 1024:
        return JsonResponse({"ok": False, "message": "Ukuran file maksimal 20 MB."}, status=400)

    content = uploaded.read(20 * 1024 * 1024 + 1)
    if len(content) > 20 * 1024 * 1024:
        return JsonResponse({"ok": False, "message": "Ukuran file maksimal 20 MB."}, status=400)
    try:
        header_row = int(request.POST.get("header_row", "1"))
        sheet_name = request.POST.get("sheet_name", "").strip() or None
        mapping = (
            request.POST.get("nim_column"),
            request.POST.get("course_column"),
            request.POST.get("grade_column"),
        )
        if all(value is not None for value in mapping):
            report = validate_grade_workbook(
                content,
                sheet_name=sheet_name or "",
                header_row=header_row,
                nim_column=int(mapping[0]),
                course_column=int(mapping[1]),
                grade_column=int(mapping[2]),
            )
            return JsonResponse({"ok": True, "report": report})
        inspected = inspect_grade_workbook(content, sheet_name, header_row)
    except (ValueError, TypeError) as exc:
        return JsonResponse({"ok": False, "message": str(exc)}, status=400)
    return JsonResponse({"ok": True, **inspected})


@csrf_exempt
@login_required
def api_yudisium_start(request):
    if not _is_local_operator_request(request):
        return JsonResponse({"ok": False, "message": "Aksi yudisium hanya tersedia dari browser lokal."}, status=403)
    payload = _json_body(request)
    token = str(payload.get("upload_id", ""))
    uploaded = jobs.YUDI_UPLOADS.get(token)
    if not uploaded or not Path(uploaded["path"]).is_file():
        return JsonResponse({"ok": False, "message": "Roster tidak ditemukan atau upload sudah kedaluwarsa."}, status=400)

    try:
        limit = int(payload.get("limit", 0))
    except (TypeError, ValueError):
        limit = 0
    if limit < 1 or limit > jobs.YUDI_MAX_BATCH:
        return JsonResponse({"ok": False, "message": f"Batas batch harus antara 1 dan {jobs.YUDI_MAX_BATCH} mahasiswa."}, status=400)
    if limit > uploaded["count"]:
        return JsonResponse({"ok": False, "message": "Batas batch melebihi jumlah NIM pada roster."}, status=400)
    if payload.get("approved") is not True:
        return JsonResponse({"ok": False, "message": "Konfirmasi kewenangan admin wajib dicentang."}, status=400)
    expected = f"DAFTAR YUDISIUM {limit}"
    if str(payload.get("confirmation", "")).strip() != expected:
        return JsonResponse({"ok": False, "message": f"Ketik tepat: {expected}"}, status=400)

    try:
        params = jobs.make_yudisium_params(uploaded, payload)
    except (ValueError, RuntimeError) as exc:
        return JsonResponse({"ok": False, "message": str(exc)}, status=400)

    if not jobs._start_yudisium_job(params):
        return JsonResponse({"ok": False, "message": "Pekerjaan lain sedang berjalan."}, status=409)
    jobs.YUDI_UPLOADS.pop(token, None)
    return JsonResponse({"ok": True, "message": "Batch yudisium dimulai.", "log_file": str(params["log_file"])})


@csrf_exempt
@login_required
def api_yudisium_continue(request):
    if not _is_local_operator_request(request):
        return JsonResponse({"ok": False, "message": "Aksi yudisium hanya tersedia dari browser lokal."}, status=403)
    with jobs.JOB_LOCK:
        if not jobs.YUDI_JOB_STATE["running"] or not jobs.YUDI_JOB_STATE["awaiting_manual_login"]:
            return JsonResponse({"ok": False, "message": "Bot tidak sedang menunggu login manual."}, status=409)
        jobs.YUDI_JOB_EVENT.set()
    return JsonResponse({"ok": True, "message": "Bot melanjutkan proses."})


@login_required
def api_yudisium_status(request):
    return JsonResponse(jobs._yudisium_snapshot())


@login_required
def api_yudisium_log(request):
    path = Path(jobs.YUDI_JOB_STATE.get("log_file") or "")
    if not path.is_file():
        return JsonResponse({"ok": False, "message": "Log yudisium belum tersedia."}, status=404)
    with path.open("rb") as fh:
        return _json_file(fh.read(), path.name, "text/csv")


# ─── API: export & konfigurasi ────────────────────────────────────────────────

@login_required
def export_dhs(request):
    path = d.OUT_DIR / "dhs_semua.csv"
    if not path.exists():
        return JsonResponse({"ok": False, "message": "dhs_semua.csv belum ada"}, status=404)
    return FileResponse(open(path, "rb"), as_attachment=True, filename=path.name)


@login_required
def export_excel(request):
    path = d.excel_data_path()
    if not path.exists():
        return JsonResponse({"ok": False, "message": "File Excel belum tersedia"}, status=404)
    return FileResponse(open(path, "rb"), as_attachment=True, filename=path.name)


@login_required
def export_transcript(request, nim: str):
    try:
        export = build_transcript_export(d.excel_data_path(), nim)
    except (FileNotFoundError, ValueError) as exc:
        return JsonResponse({"ok": False, "message": str(exc)}, status=404)
    return _json_file(
        export.content,
        export.filename,
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@csrf_exempt
@login_required
def api_config(request):
    data = _json_body(request)
    cfg = d.web_config()
    for key in ("filled_excel", "nim_file", "angkatan", "prodi"):
        if key in data:
            cfg[key] = str(data[key]).strip()
    d.save_web_config(cfg)
    return JsonResponse({"ok": True, "config": cfg})