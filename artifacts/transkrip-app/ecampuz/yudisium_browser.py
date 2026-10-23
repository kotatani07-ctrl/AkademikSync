"""Guarded, sequential browser workflow for yudisium registration."""

from __future__ import annotations

import csv
import hashlib
import json
import re
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urljoin, urlsplit

from .config import Config, STATE_DIR
from .credentials import CredentialStore, write_hidden



def _cell_text(value: Any, number_format: str = "") -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if isinstance(value, int) and re.fullmatch(r"0{2,}", number_format or ""):
        return f"{value:0{len(number_format)}d}"
    return str(value).strip()


def _normalize_headers(headers: list[Any]) -> list[str]:
    normalized = [str(value or "").strip() for value in headers]
    if not normalized or any(not value for value in normalized):
        raise ValueError("Semua kolom input harus memiliki nama header.")
    lowered = [value.casefold() for value in normalized]
    if len(lowered) != len(set(lowered)):
        raise ValueError("Nama header input tidak boleh duplikat.")
    return normalized


def read_records(path: Path, nim_column: str, sheet: str | None = None) -> list[dict[str, str]]:
    """Read and validate student rows from CSV or XLSX."""
    if not path.is_file():
        raise FileNotFoundError(f"File input tidak ditemukan: {path}")

    if path.suffix.casefold() == ".csv":
        with path.open("r", encoding="utf-8-sig", newline="") as stream:
            sample = stream.read(4096)
            stream.seek(0)
            try:
                delimiter = csv.Sniffer().sniff(sample, delimiters=",;\t").delimiter
            except csv.Error:
                delimiter = ","
            reader = csv.DictReader(stream, delimiter=delimiter)
            original_headers = reader.fieldnames or []
            headers = _normalize_headers(original_headers)
            source_rows = [
                {
                    header: _cell_text(row.get(original))
                    for header, original in zip(headers, original_headers)
                }
                for row in reader
            ]
    elif path.suffix.casefold() in {".xlsx", ".xlsm"}:
        import openpyxl

        workbook = openpyxl.load_workbook(path, data_only=True, read_only=True)
        try:
            if sheet:
                if sheet not in workbook.sheetnames:
                    raise ValueError(f"Sheet {sheet!r} tidak ditemukan: {workbook.sheetnames}")
                worksheet = workbook[sheet]
            else:
                worksheet = workbook.active
            rows = worksheet.iter_rows()
            header_cells = next(rows, None)
            headers = _normalize_headers(
                [cell.value for cell in header_cells] if header_cells else []
            )
            source_rows = []
            for cells in rows:
                source_rows.append(
                    {
                        header: _cell_text(cells[index].value, cells[index].number_format)
                        if index < len(cells)
                        else ""
                        for index, header in enumerate(headers)
                    }
                )
        finally:
            workbook.close()
    else:
        raise ValueError("Format input harus .csv, .xlsx, atau .xlsm.")

    header_lookup = {header.casefold(): header for header in headers}
    actual_nim_column = header_lookup.get(nim_column.casefold())
    if not actual_nim_column:
        raise ValueError(
            f"Kolom NIM {nim_column!r} tidak ditemukan. Header tersedia: {headers}"
        )

    records: list[dict[str, str]] = []
    seen: set[str] = set()
    for row_number, row in enumerate(source_rows, start=2):
        if not any(value.strip() for value in row.values()):
            continue
        nim = row.get(actual_nim_column, "").strip()
        if not nim:
            raise ValueError(f"NIM kosong pada baris {row_number}.")
        key = nim.casefold()
        if key in seen:
            raise ValueError(f"NIM duplikat {nim!r} pada baris {row_number}.")
        seen.add(key)
        row[actual_nim_column] = nim
        records.append(row)

    if not records:
        raise ValueError("Tidak ada baris mahasiswa untuk diproses.")
    return records


def inspect_forms(page) -> list[dict[str, Any]]:
    """Return form structure without exposing hidden token values."""
    return page.locator("form").evaluate_all(
        """forms => forms.map((form, index) => ({
          index,
          action: form.getAttribute('action') || '',
          method: (form.getAttribute('method') || 'get').toLowerCase(),
          controls: Array.from(form.querySelectorAll('input, select, textarea, button'))
            .map(el => ({
              tag: el.tagName.toLowerCase(),
              name: el.name || '',
              id: el.id || '',
              type: (el.type || '').toLowerCase(),
              required: Boolean(el.required),
              disabled: Boolean(el.disabled),
              placeholder: el.getAttribute('placeholder') || '',
              label: el.labels && el.labels.length
                ? Array.from(el.labels).map(label => label.innerText.trim()).join(' ')
                : (el.getAttribute('aria-label') || ''),
              options: el.tagName === 'SELECT'
                ? Array.from(el.options).map(option => ({
                    value: option.value,
                    label: option.text.trim()
                  }))
                : []
            }))
        }))"""
    )


def _is_login_page(page) -> bool:
    return page.locator("#aid_username, #gsFormLogin, #gsformlogin, input[name='user[passwd]']").count() > 0


def _manual_login(
    page,
    base_url: str,
    username: str,
    password: str,
    pause_callback=None,
) -> str | None:
    logged_in = False
    if not page.url.startswith(base_url):
        page.goto(base_url, wait_until="domcontentloaded")
    if _is_login_page(page):
        logged_in = True
        from .browser_login import _fill_login_credentials

        _fill_login_credentials(page, username, password)
        print("Login dan CAPTCHA secara manual di jendela browser.")
        print("Sesudah login, buka halaman Tambah - Mahasiswa Lain untuk sesi aktif.")
        if pause_callback:
            pause_callback(page)
        else:
            input("Setelah halaman tersebut terbuka, tekan Enter di terminal ini: ")
        try:
            page.wait_for_load_state("domcontentloaded", timeout=10_000)
        except Exception:
            pass
    if _is_login_page(page):
        raise RuntimeError("Halaman login masih tampil; sesi browser belum aktif.")
    current = urlsplit(page.url)
    base = urlsplit(base_url)
    if current.scheme != base.scheme or current.netloc.casefold() != base.netloc.casefold():
        raise RuntimeError("Browser harus tetap berada pada origin eCampuz setelah login.")
    if logged_in and "/kelulusan/" not in current.path.casefold():
        raise RuntimeError("Setelah login, buka halaman Tambah - Mahasiswa Lain sebelum menekan Enter.")
    return page.url if logged_in else None


def _find_nim_range(page):
    rows = page.locator("tr")
    for index in range(rows.count()):
        row = rows.nth(index)
        text = re.sub(r"\s+", " ", row.inner_text()).strip()
        if not re.search(r"\bNIM\b", text, re.I):
            continue
        inputs = row.locator("input[type='text'], input:not([type])")
        visible = [
            inputs.nth(input_index)
            for input_index in range(inputs.count())
            if inputs.nth(input_index).is_visible() and inputs.nth(input_index).is_enabled()
        ]
        if len(visible) >= 2:
            return visible[0], visible[1]
    raise ValueError("Tidak menemukan dua input rentang NIM pada baris filter NIM.")


def _button_by_text(page, label: str):
    candidates = page.locator(
        "button, input[type='submit'], input[type='button'], input[type='reset']"
    )
    matches = []
    for index in range(candidates.count()):
        candidate = candidates.nth(index)
        text = candidate.inner_text() if candidate.evaluate("el => el.tagName.toLowerCase() === 'button'") else ""
        text = text or candidate.get_attribute("value") or ""
        if re.sub(r"\s+", " ", text).strip().casefold() == label.casefold() and candidate.is_visible():
            matches.append(candidate)
    if len(matches) != 1:
        raise ValueError(f"Tombol {label!r} harus ditemukan tepat satu kali; ditemukan {len(matches)}.")
    return matches[0]


def _wait_for_results(page, nim: str, timeout_ms: int) -> None:
    page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
    try:
        page.get_by_text(nim, exact=True).wait_for(state="visible", timeout=timeout_ms)
    except Exception:
        if page.get_by_text("Data tidak ditemukan", exact=True).count():
            raise LookupError(f"NIM {nim} tidak ditemukan pada hasil pencarian.")
        raise TimeoutError(f"Hasil pencarian NIM {nim} tidak muncul sebelum timeout.")


def _find_exact_student_row(page, nim: str):
    rows = page.locator("table tr")
    matches = []
    for index in range(rows.count()):
        row = rows.nth(index)
        cells = row.locator("th, td")
        for cell_index in range(cells.count()):
            if re.sub(r"\s+", " ", cells.nth(cell_index).inner_text()).strip() == nim:
                matches.append(row)
                break
    if len(matches) != 1:
        raise LookupError(f"NIM {nim} menghasilkan {len(matches)} baris yang cocok persis.")
    return matches[0]


def _student_action(row):
    actions = row.locator("a:visible, button:visible, input[type='button']:visible, input[type='submit']:visible")
    if actions.count() < 1:
        raise LookupError("Baris mahasiswa tidak memiliki tombol/link aksi yang terlihat.")

    icon_indexes = actions.evaluate_all(
        """nodes => nodes.flatMap((node, index) => {
          const icon = node.querySelector(
                        '[class~="fa-search"], [class~="fa-magnifying-glass"], ' +
                        '[class~="glyphicon-search"], [class~="bi-search"], ' +
                        '[data-icon="search"], [aria-label*="search" i]'
          );
          if (!icon) return [];
          const owner = icon.closest('a, button, input[type="button"], input[type="submit"]');
          return owner === node ? [index] : [];
        })"""
    )
    if len(icon_indexes) == 1:
        return actions.nth(icon_indexes[0])
    if len(icon_indexes) > 1:
        raise LookupError(
            f"Baris mahasiswa memiliki {len(icon_indexes)} aksi ikon pencarian; tidak bisa dipilih dengan aman."
        )

    hinted = []
    for index in range(actions.count()):
        action = actions.nth(index)
        hint = " ".join(
            filter(
                None,
                [
                    action.inner_text() if action.evaluate("el => el.tagName.toLowerCase() === 'button'") else "",
                    action.get_attribute("aria-label"),
                    action.get_attribute("title"),
                    action.get_attribute("class"),
                    action.locator("i, svg").first.get_attribute("class")
                    if action.locator("i, svg").count()
                    else "",
                ],
            )
        ).casefold()
        if re.search(r"search|cari|daftar|tambah|view|detail|fa-search", hint):
            hinted.append(action)
    if len(hinted) == 1:
        return hinted[0]
    if actions.count() == 1:
        return actions.first
    raise LookupError(
        f"Baris mahasiswa memiliki {actions.count()} aksi dan tidak dapat ditentukan dengan aman."
    )


def _classify_registration_result(
    dialog_messages: list[str],
    notice_messages: list[str],
    body_text: str,
) -> tuple[str, str]:
    rejection = re.compile(
        r"kurang|gagal|error|ditolak|tidak dapat|tidak bisa|belum memenuhi|tidak memenuhi",
        re.I,
    )
    success = re.compile(
        r"berhasil\s+(?:didaftarkan|ditambahkan|disimpan)|pendaftaran\s+berhasil|"
        r"sukses\s+(?:didaftarkan|ditambahkan)|telah\s+terdaftar|successfully\s+registered",
        re.I,
    )
    messages = [message.strip() for message in [*dialog_messages, *notice_messages] if message.strip()]
    for message in messages:
        if rejection.search(message):
            return "failed", message[:500]
    for message in messages:
        if success.search(message) and not re.search(r"apakah|yakin", message, re.I):
            return "submitted", message[:500]
    if rejection.search(body_text):
        return "failed", next(
            (line.strip()[:500] for line in body_text.splitlines() if rejection.search(line)),
            "Pendaftaran ditolak oleh server.",
        )
    if success.search(body_text):
        return "submitted", next(
            (line.strip()[:500] for line in body_text.splitlines() if success.search(line)),
            "Pendaftaran berhasil dikonfirmasi oleh halaman.",
        )
    return "uncertain", "Tidak ditemukan konfirmasi sukses atau penolakan yang jelas."


def _registration_notices(page) -> list[str]:
    return page.locator(".alert, .toast, [role='alert'], .swal2-html-container").evaluate_all(
        "nodes => nodes.map(node => (node.innerText || '').trim()).filter(Boolean)"
    )


def _log_result(path: Path, nim: str, status: str, reason: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_header = not path.exists() or path.stat().st_size == 0
    with path.open("a", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        if write_header:
            writer.writerow(["waktu", "nim", "status", "keterangan"])
        writer.writerow([time.strftime("%Y-%m-%dT%H:%M:%S"), nim, status, reason])


def _resolve_profile_credentials(config: Config) -> tuple[str, str]:
    store = CredentialStore(config.credential_path)
    credentials = store.load()
    if credentials:
        return credentials
    import getpass

    username = input("Username eAkademik: ").strip()
    password = getpass.getpass("Password eAkademik: ")
    if not username or not password:
        raise ValueError("Username dan password wajib diisi.")
    store.save(username, password)
    return username, password


def _ensure_same_origin(target_url: str, base_url: str) -> str:
    target = urlsplit(target_url)
    base = urlsplit(base_url)
    if target.scheme != base.scheme or target.netloc.casefold() != base.netloc.casefold():
        raise ValueError("URL pendaftaran harus berada pada origin eCampuz yang dikonfigurasi.")
    return target_url


def _campaign_fingerprint(input_path: Path, base_url: str, campaign_id: str) -> str:
    digest = hashlib.sha256()
    digest.update(base_url.encode("utf-8"))
    digest.update(campaign_id.encode("utf-8"))
    digest.update(input_path.read_bytes())
    return digest.hexdigest()


def _load_state(path: Path, fingerprint: str) -> dict[str, Any]:
    if not path.exists():
        return {"fingerprint": fingerprint, "rows": {}}
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Checkpoint yudisium tidak dapat dibaca: {exc}") from exc
    if state.get("fingerprint") != fingerprint:
        raise ValueError(
            f"Checkpoint {path} berasal dari file atau kampus berbeda. "
            "Gunakan --state-file baru setelah memeriksa pendaftaran yang sudah terkirim."
        )
    return state


def _pending_records(
    records: list[dict[str, str]],
    nim_column: str,
    state: dict[str, Any],
    retry_failed: bool,
) -> list[dict[str, str]]:
    statuses = state.get("rows", {})
    uncertain = [
        nim for nim, item in statuses.items()
        if item.get("status") in {"submitting", "uncertain"}
    ]
    if uncertain:
        raise RuntimeError(
            "Checkpoint memiliki hasil belum pasti untuk NIM: "
            + ", ".join(uncertain[:10])
            + ". Verifikasi di eCampuz secara manual; bot tidak akan mengulangnya."
        )
    skipped_statuses = {"submitted"} if retry_failed else {"submitted", "failed"}
    return [
        record for record in records
        if statuses.get(record[nim_column], {}).get("status") not in skipped_statuses
    ]


def _save_state(path: Path, state: dict[str, Any]) -> None:
    write_hidden(path, json.dumps(state, indent=2, ensure_ascii=False) + "\n")


def _emit(args, message: str) -> None:
    callback = getattr(args, "on_log", None)
    if callback:
        callback(message)
    else:
        print(message)


def _print_inspection(page) -> None:
    forms = inspect_forms(page)
    tables = page.locator("table").evaluate_all(
        """tables => tables.map((table, index) => {
          const rows = Array.from(table.querySelectorAll('tr'));
          return {
            index,
            rows: rows.length,
            headers: rows.length
              ? Array.from(rows[0].querySelectorAll('th, td')).map(cell => cell.innerText.trim())
              : []
          };
        })"""
    )
    links = page.locator("a[href]").evaluate_all(
        """nodes => nodes.map(node => ({
          text: (node.innerText || '').trim().replace(/\\s+/g, ' '),
          href: node.href
        })).filter(link => link.text || link.href)"""
    )
    buttons = page.locator("button, input[type=submit], input[type=button]").evaluate_all(
        """nodes => nodes.map(node => ({
          tag: node.tagName.toLowerCase(),
          type: (node.type || '').toLowerCase(),
          name: node.name || '',
          id: node.id || '',
          text: (node.innerText || node.value || '').trim(),
          disabled: Boolean(node.disabled)
        }))"""
    )
    result = {
        "url": page.url,
        "title": page.title(),
        "forms": forms,
        "tables": tables,
        "links": links,
        "buttons": buttons,
    }
    print(json.dumps(result, indent=2, ensure_ascii=False))
    if not forms:
        print("[info] Tidak ada form HTML pada halaman ini.")


def run_yudisium(args, config: Config) -> int:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            "Playwright belum terpasang. Jalankan pip install -r requirements.txt "
            "lalu python -m playwright install chromium."
        ) from exc

    records: list[dict[str, str]] = []
    input_path: Path | None = None
    nim_column = args.nim_column
    if not args.inspect:
        if not args.file:
            raise ValueError("Mode batch memerlukan --file berisi NIM; gunakan --inspect untuk inspeksi form.")
        input_path = Path(args.file).resolve()
        records = read_records(input_path, args.nim_column, sheet=args.sheet)
        nim_column = next(
            name for name in records[0]
            if name.casefold() == args.nim_column.casefold()
        )
        if args.limit is not None:
            if args.limit < 1:
                raise ValueError("--limit harus lebih besar dari nol.")
        if args.submit and args.limit is None:
            raise ValueError("Submit wajib memakai --limit sebagai batas eksplisit.")
        if not args.submit:
            preview = records[: args.limit] if args.limit else records
            _emit(args, f"[dry-run] {len(records)} baris valid; tidak ada browser yang dibuka dan tidak ada submit.")
            _emit(args, f"[dry-run] Kolom: {list(records[0])}")
            _emit(args, f"[dry-run] NIM pada preview: {', '.join(row[nim_column] for row in preview[:10])}")
            if len(preview) > 10:
                _emit(args, f"[dry-run] ... dan {len(preview) - 10} NIM lainnya pada preview.")
            if args.limit and len(records) > args.limit:
                _emit(args, f"[dry-run] Batas per proses: {args.limit}; total roster: {len(records)}.")
            return 0

    base_url = config.base_url
    target_url = _ensure_same_origin(urljoin(base_url, args.url), base_url) if args.url else None
    if args.submit and not target_url:
        raise ValueError("Submit memerlukan --url halaman tambah mahasiswa yang aktif pada sesi eCampuz ini.")
    state_path = Path(args.state_file) if args.state_file else None
    log_path = Path(args.log_file) if args.log_file else None
    state: dict[str, Any] = {"fingerprint": "", "rows": {}}
    pending_records = records
    if args.submit and input_path is not None:
        target_parts = urlsplit(target_url)
        campaign_id = args.campaign_id or (
            parse_qs(target_parts.query).get("w", [""])[0]
            or target_parts.path + "?" + target_parts.query
        )
        fingerprint = _campaign_fingerprint(input_path, base_url, campaign_id)
        campaign_hash = hashlib.sha256(campaign_id.encode("utf-8")).hexdigest()[:8]
        state_path = state_path or STATE_DIR / f"yudisium_{input_path.stem}_{campaign_hash}_state.json"
        log_path = log_path or config.out_dir / f"yudisium_{input_path.stem}_{campaign_hash}_log.csv"
        state = _load_state(state_path, fingerprint)
        pending_records = _pending_records(
            records,
            nim_column,
            state,
            args.retry_failed,
        )
        pending_records = pending_records[: args.limit]
        if not pending_records:
            _emit(args, "[ok] Tidak ada baris tersisa pada batas yang diminta.")
            return 0
    username, password = _resolve_profile_credentials(config)
    profile_dir = Path(args.user_data_dir) if args.user_data_dir else STATE_DIR / "yudisium_browser_profile"
    profile_dir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir.resolve()),
            headless=False,
            locale="id-ID",
            user_agent=config.user_agent,
            viewport={"width": 1366, "height": 900},
        )
        page = context.pages[0] if context.pages else context.new_page()
        try:
            if not page.url.startswith(base_url):
                page.goto(base_url, wait_until="domcontentloaded")
            pause_callback = getattr(args, "pause_for_manual_login", None)
            freshly_navigated_url = _manual_login(
                page,
                base_url,
                username,
                password,
                pause_callback=pause_callback,
            )
            registration_url = freshly_navigated_url or target_url
            if not registration_url and args.inspect:
                print("Arahkan browser ke halaman Tambah - Mahasiswa Lain yang ingin diperiksa.")
                input("Setelah halaman siap, tekan Enter di terminal ini: ")
                registration_url = _ensure_same_origin(page.url, base_url)
                if "/kelulusan/" not in urlsplit(registration_url).path.casefold():
                    raise RuntimeError("Halaman saat ini bukan halaman modul kelulusan eCampuz.")
            if not registration_url:
                raise ValueError("URL halaman yudisium tidak tersedia.")
            if page.url != registration_url:
                page.goto(registration_url, wait_until="domcontentloaded", timeout=args.timeout * 1000)
            try:
                page.wait_for_load_state("load", timeout=args.timeout * 1000)
            except Exception:
                pass
            if _is_login_page(page):
                raise RuntimeError("Halaman pendaftaran mengarah kembali ke login.")

            if args.inspect:
                _print_inspection(page)
                return 0

            assert input_path is not None and log_path is not None and state_path is not None
            _emit(args, f"[siap] {len(pending_records)} pendaftaran akan dikirim berurutan.")
            _emit(args, f"[siap] Log: {log_path}")
            _emit(args, f"[siap] Profil browser tersimpan di: {profile_dir}")
            confirmation = f"DAFTAR YUDISIUM {len(pending_records)}"
            if not getattr(args, "confirmed", False):
                answer = input(f"Ketik {confirmation!r} untuk melanjutkan: ").strip()
                if answer != confirmation:
                    _emit(args, "[batal] Konfirmasi tidak cocok; tidak ada pendaftaran dikirim.")
                    return 1

            for index, record in enumerate(pending_records, start=1):
                nim = record[nim_column]
                if _is_login_page(page):
                    refreshed_url = _manual_login(
                        page,
                        base_url,
                        username,
                        password,
                        pause_callback=pause_callback,
                    )
                    registration_url = refreshed_url or registration_url
                    if page.url != registration_url:
                        page.goto(registration_url, wait_until="domcontentloaded", timeout=args.timeout * 1000)

                if page.url != registration_url:
                    page.goto(registration_url, wait_until="domcontentloaded", timeout=args.timeout * 1000)

                dialog_messages: list[str] = []
                confirmation_seen = False

                def accept_dialog(dialog) -> None:
                    nonlocal confirmation_seen
                    dialog_messages.append(dialog.message.strip())
                    if re.search(r"apakah|yakin|konfirmasi|are you sure", dialog.message, re.I):
                        confirmation_seen = True
                    dialog.accept()

                page.on("dialog", accept_dialog)
                search_error = None
                while True:
                    try:
                        if not args.no_reset_filter:
                            _button_by_text(page, "RESET").click()
                        nim_start, nim_end = _find_nim_range(page)
                        nim_start.fill(nim)
                        nim_end.fill(nim)
                        _button_by_text(page, "TAMPILKAN").click()
                        _wait_for_results(page, nim, args.timeout * 1000)
                        row = _find_exact_student_row(page, nim)
                        action = _student_action(row)
                        break
                    except Exception as exc:
                        if _is_login_page(page):
                            _emit(args, f"[jeda] Sesi habis saat mencari {nim}; login dan CAPTCHA manual, lalu bot akan mengulang NIM ini.")
                            refreshed_url = _manual_login(
                                page,
                                base_url,
                                username,
                                password,
                                pause_callback=pause_callback,
                            )
                            registration_url = refreshed_url or registration_url
                            if page.url != registration_url:
                                page.goto(registration_url, wait_until="domcontentloaded", timeout=args.timeout * 1000)
                            continue
                        search_error = exc
                        break
                if search_error is not None:
                    page.remove_listener("dialog", accept_dialog)
                    result = {
                        "status": "failed",
                        "updated": time.strftime("%Y-%m-%dT%H:%M:%S"),
                        "reason": f"{search_error.__class__.__name__}: {search_error}",
                    }
                    state.setdefault("rows", {})[nim] = result
                    _save_state(state_path, state)
                    _log_result(log_path, nim, "Gagal", result["reason"])
                    _emit(args, f"[{index}/{len(pending_records)}] GAGAL {nim}: {search_error}")
                    continue

                dialog_messages.clear()
                state.setdefault("rows", {})[nim] = {
                    "status": "submitting",
                    "updated": time.strftime("%Y-%m-%dT%H:%M:%S"),
                }
                _save_state(state_path, state)
                body_before_action = page.locator("body").inner_text()
                body_before_lines = {line.strip() for line in body_before_action.splitlines() if line.strip()}
                try:
                    action.click(timeout=args.timeout * 1000)
                    if confirmation_seen:
                        page.wait_for_function(
                            """oldText => {
                              const body = document.body ? document.body.innerText : '';
                              const notices = Array.from(document.querySelectorAll('.alert, .toast, [role="alert"], .swal2-html-container'));
                              return body !== oldText || notices.some(node => (node.innerText || '').trim());
                            }""",
                            arg=body_before_action,
                            timeout=args.timeout * 1000,
                        )
                    else:
                        try:
                            page.wait_for_load_state("domcontentloaded", timeout=args.timeout * 1000)
                        except Exception:
                            pass
                    _ensure_same_origin(page.url, base_url)
                except Exception as exc:
                    page.remove_listener("dialog", accept_dialog)
                    result = {
                        "status": "uncertain",
                        "updated": time.strftime("%Y-%m-%dT%H:%M:%S"),
                        "reason": f"{exc.__class__.__name__}: {exc}",
                    }
                    state["rows"][nim] = result
                    _save_state(state_path, state)
                    _log_result(log_path, nim, "Tidak pasti", result["reason"])
                    _emit(args, f"[{index}/{len(pending_records)}] HASIL TIDAK PASTI {nim}; dihentikan: {exc}")
                    return 2
                finally:
                    try:
                        page.remove_listener("dialog", accept_dialog)
                    except Exception:
                        pass

                result_status, reason = _classify_registration_result(
                    dialog_messages,
                    _registration_notices(page),
                    "\n".join(
                        line for line in page.locator("body").inner_text().splitlines()
                        if line.strip() and line.strip() not in body_before_lines
                    ),
                )
                status = result_status
                state["rows"][nim] = {
                    "status": status,
                    "updated": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "reason": reason,
                }
                _save_state(state_path, state)
                display_status = {"submitted": "Sukses", "failed": "Gagal", "uncertain": "Tidak pasti"}[status]
                _log_result(log_path, nim, display_status, reason)
                _emit(args, f"[{index}/{len(pending_records)}] {display_status.upper()} {nim}: {reason}")
                if status == "uncertain":
                    return 2
                if index < len(pending_records):
                    time.sleep(max(args.delay, 1.5))
            return 0
        finally:
            context.close()