"""Background job runner untuk dashboard eCampuz (port dari src/webapp/app.py).

Memakai threading (daemon) — cocok dengan `runserver --noreload`. Hanya satu
pekerjaan scraping ATAU satu batch yudisium yang boleh berjalan pada satu waktu.
"""
from __future__ import annotations

import hashlib
import io
import re
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urljoin

from ecampuz.audit import load_config
from ecampuz.client import eCampuzClient, SessionExpired
from ecampuz.credentials import CredentialStore, _hide
from ecampuz.yudisium_browser import _ensure_same_origin, read_records, run_yudisium

import dashboard.services.data as d

JOB_LOCK = threading.Lock()
JOB_STATE = {
    "running": False,
    "log": [],
    "started": None,
    "finished": None,
    "rc": None,
    "params": {},
    "error": None,
}
YUDI_JOB_STATE = {
    "running": False,
    "awaiting_manual_login": False,
    "message": "idle",
    "log": [],
    "started": None,
    "finished": None,
    "rc": None,
    "error": None,
    "log_file": None,
}
YUDI_JOB_EVENT = threading.Event()
YUDI_UPLOAD_DIR = d.STATE_DIR / "yudisium_uploads"
YUDI_UPLOADS: dict[str, dict] = {}
YUDI_MAX_BATCH = 50
YUDI_MAX_UPLOAD_BYTES = 20 * 1024 * 1024

GRADE_CLASS = {
    "A": "g-a",
    "A-": "g-a",
    "B+": "g-bp",
    "B": "g-b",
    "B-": "g-b",
    "C+": "g-c",
    "C": "g-c",
    "D": "g-d",
    "E": "g-e",
}


def secure_filename(name: str) -> str:
    """Sanitasi nama file (pengganti werkzeug.utils.secure_filename)."""
    name = str(name or "").strip().replace(" ", "_")
    name = re.sub(r"[^A-Za-z0-9_.\-]", "", name)
    name = re.sub(r"_+", "_", name)
    return name.strip("._") or "roster"


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class _CapturedStream(io.TextIOBase):
    def __init__(self, log: list, real):
        self.log = log
        self.real = real

    def write(self, s: str) -> int:
        for line in s.split("\n"):
            if line:
                self.log.append(line.rstrip())
        if len(self.log) > 10000:
            del self.log[: len(self.log) - 10000]
        if self.real is not None:
            try:
                self.real.write(s)
                self.real.flush()
            except (OSError, ValueError):
                pass
        return len(s)

    def flush(self) -> None:
        if self.real is not None:
            try:
                self.real.flush()
            except (OSError, ValueError):
                pass


def start_job(params: dict) -> bool:
    with JOB_LOCK:
        if JOB_STATE["running"] or YUDI_JOB_STATE["running"]:
            return False
        JOB_STATE.update(
            running=True,
            log=[],
            started=_iso(),
            finished=None,
            rc=None,
            params=params,
            error=None,
        )
    thread = threading.Thread(target=_run_job, args=(params,), daemon=True)
    thread.start()
    return True


def _client() -> eCampuzClient:
    cfg = load_config()
    if cfg.timeout < 120:
        cfg.timeout = 120
    return eCampuzClient(cfg)


def ensure_session_and_start(params: dict) -> dict:
    """Pastikan sesi eCampuz aktif, lalu jalankan job scraping.

    Login eCampuz memerlukan jawaban CAPTCHA manual, sehingga sesi kedaluwarsa
    harus diperbarui lewat halaman login sebelum job dapat dimulai.
    """
    payload = dict(params or {})
    app_cfg = d.web_config()
    payload.setdefault("nim_file", app_cfg.get("nim_file", "nim_2024_sheet.txt"))
    payload.setdefault("angkatan", app_cfg.get("angkatan", "2024"))
    payload.setdefault("prodi", app_cfg.get("prodi", "all"))
    payload.setdefault("per_mahasiswa", True)
    payload.setdefault("reset", False)
    payload.setdefault("retry_failed", False)
    payload.setdefault("neo_feeder_fallback", False)

    try:
        client = _client()
        client.cfg.timeout = min(client.cfg.timeout, 15)
        valid, detail = client.verify_session()
        if not valid:
            return {
                "ok": False,
                "message": f"Sesi eCampuz tidak aktif ({detail}). Login ulang dan jawab CAPTCHA di halaman Login eCampuz, lalu jalankan kembali.",
            }

        started = start_job(payload)
        if not started:
            return {"ok": False, "message": "Pekerjaan lain sedang berjalan."}
        return {"ok": True, "message": "Sesi eCampuz valid; pekerjaan dimulai."}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "message": f"Gagal menghubungkan ke eCampuz: {exc}"}


# ─── Login eCampuz via web (captcha dua-langkah) ──────────────────────────────
# State login sementara di memori (username/password/soal/token). Satu id dipakai
# sekali; bila jawaban captcha salah, server membuat soal baru untuk percobaan
# berikutnya. Tidak pernah menyimpan password ke disk di jalur ini (kecuali
# DPAPI saat remember di login()).
LOGIN_PENDING: dict[str, dict] = {}
LOGIN_PENDING_LOCK = threading.Lock()
LOGIN_PENDING_TTL = 300  # detik


def _prune_pending_login() -> None:
    now = time.time()
    for key in [k for k, v in LOGIN_PENDING.items() if now - v["at"] > LOGIN_PENDING_TTL]:
        LOGIN_PENDING.pop(key, None)


def request_login_captcha(username: str = "", password: str = "") -> dict:
    """Langkah 1: ambil soal captcha baru dan simpan state login sementara."""
    client = _client()
    soal, token = client.fetch_captcha()
    login_id = uuid.uuid4().hex[:12]
    with LOGIN_PENDING_LOCK:
        _prune_pending_login()
        LOGIN_PENDING[login_id] = {
            "username": username.strip(),
            "password": password,
            "soal": soal,
            "token": token,
            "at": time.time(),
        }
    return {"id": login_id, "soal": soal}


def submit_login_captcha(login_id: str, answer: str) -> dict:
    """Langkah 2: kirim jawaban captcha (satu percobaan per-request web).

    Bila captcha salah, kembalikan soal baru ('new_captcha') agar pengguna bisa
    langsung menjawab lagi tanpa mengulang username/password.
    """
    with LOGIN_PENDING_LOCK:
        pend = LOGIN_PENDING.pop(login_id, None)
    if not pend:
        return {"ok": False, "captcha_wrong": False,
                "message": "Sesi captcha tidak ditemukan atau sudah dipakai. Minta captcha baru."}
    if time.time() - pend["at"] > LOGIN_PENDING_TTL:
        return {"ok": False, "captcha_wrong": False,
                "message": "Captcha kedaluwarsa (5 menit). Minta captcha baru."}
    if not pend["username"] or not pend["password"]:
        return {"ok": False, "captcha_wrong": False, "message": "Username/password kosong."}
    if not answer.strip():
        return {"ok": False, "captcha_wrong": False, "message": "Jawaban captcha kosong."}

    client = _client()
    client.clear_session()
    client.cfg.username = pend["username"]

    payload = {
        "user[ID]": pend["username"],
        "user[passwd]": pend["password"],
        "captcha_answer": answer.strip(),
        "captcha_token": pend["token"],
        "action": "submitted",
        "gsLclStrg": "",
        "setLangActive": "",
    }
    try:
        resp = client._request(
            "POST",
            client.base + "?act=login",
            data=payload,
            headers={"Referer": client.base},
            note="web_login_attempt",
        )
    except SessionExpired as exc:
        return {"ok": False, "captcha_wrong": False, "message": f"Sesi/HTTP ditolak: {exc}"}
    except RuntimeError as exc:
        return {"ok": False, "captcha_wrong": False, "message": f"Jaringan eCampuz: {exc}"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "captcha_wrong": False, "message": f"error: {exc}"}

    if client._looks_authenticated(resp.text):
        client.authenticated = True
        client._save_session()
        client.audit.event("login_success", via="web", attempts=1)
        # Remember (DPAPI) — hasil login ini jadi kredensial default scraper.
        try:
            store = CredentialStore(client.cfg.credential_path)
            if store.load() != (pend["username"], pend["password"]):
                store.save(pend["username"], pend["password"])
                client.audit.event("credentials_saved", method="dpapi")
        except Exception:  # noqa: BLE001
            pass
        return {"ok": True, "message": "Login eCampuz berhasil. Sesi tersimpan."}

    last_err = client._extract_login_error(resp.text) or (
        f"Login ditolak (status {resp.status_code})"
    )
    if "captcha" in last_err.lower():
        try:
            nc = request_login_captcha(pend["username"], pend["password"])
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "captcha_wrong": True,
                    "message": f"Captcha salah; gagal memuat soal baru: {exc}"}
        return {
            "ok": False,
            "captcha_wrong": True,
            "message": "Captcha salah. Soal baru sudah disiapkan — jawab dan kirim lagi.",
            "new_captcha": nc,
        }
    if "salah" in last_err.lower() or "password" in last_err.lower():
        return {"ok": False, "captcha_wrong": False,
                "message": f"Username/password ditolak: {last_err}"}
    return {"ok": False, "captcha_wrong": False, "message": last_err}


def start_browser_login(username: str = "", password: str = "") -> dict:
    """Login lewat jendela browser sungguhan (Playwright). Wajib dijawab manual."""
    from ecampuz.browser_login import login_via_browser

    cfg = load_config()

    def _run() -> None:
        try:
            result = login_via_browser(
                cfg,
                headless=False,
                username=username or None,
                password=password or None,
            )
            print(f"[web] login browser: {result.message}")
        except Exception as exc:  # noqa: BLE001
            print(f"[web] login browser error: {exc}")

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return {
        "ok": True,
        "message": "Jendela browser dibuka di komputer ini. Jawab captcha lalu klik LOGIN — "
                   "jendela menutup otomatis setelah berhasil.",
    }


def clear_ecampuz_session() -> dict:
    try:
        _client().clear_session()
        return {"ok": True, "message": "Sesi eCampuz dihapus."}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "message": f"Gagal menghapus sesi: {exc}"}


def _grade_fallback(config, enabled: bool):
    if not enabled:
        return None
    from ecampuz.neo_feeder import NeoFeederClient, NeoFeederError

    # Satu sumber kredensial: pakai .env Django (sama dengan generate transkrip).
    # Endpoint yang dipakai: http://{FEEDER_HOST}:3003/ws/live2.php (default Feeder).
    try:
        from django.conf import settings as dj

        host = (getattr(dj, 'FEEDER_HOST', '') or '').strip()
        username = (getattr(dj, 'FEEDER_USERNAME', '') or '').strip()
        password = (getattr(dj, 'FEEDER_PASSWORD', '') or '').strip()
        if host and username and password:
            endpoint = host if host.startswith('http') else f"http://{host}:3003/ws/live2.php"
            feeder = NeoFeederClient(
                endpoint=endpoint,
                username=username,
                password=password,
                verify_tls=False,      # Feeder kampus umumnya HTTP di jaringan privat
                allow_insecure_http=True,
                timeout=60,
            )
            print(
                "[web] Neo Feeder fallback aktif (kredensial dari .env Django); "
                "hanya nilai eCampus yang kosong dicari."
            )
            return feeder
        print("[web] .env Django belum berisi FEEDER_* — coba kredensial dari file state scraper.")
    except (NeoFeederError, OSError, ValueError) as exc:
        print(
            f"[web] kredensial Neo Feeder dari .env tidak dipakai ({exc}); "
            "coba kredensial dari file state scraper."
        )

    try:
        feeder = NeoFeederClient.from_config(config)
    except (NeoFeederError, OSError, ValueError) as exc:
        print(
            f"[web] Neo Feeder fallback tidak aktif ({exc}); "
            "scraping eCampuz tetap dilanjutkan. "
            "Konfigurasikan via menu dashboard / ecampuz neo-feeder jika diperlukan."
        )
        return None
    print("[web] Neo Feeder fallback aktif; hanya nilai eCampus yang kosong dicari.")
    return feeder


def _run_job(params: dict) -> None:
    real_out = sys.stdout
    try:
        sys.stdout = _CapturedStream(JOB_STATE["log"], real_out)
        print("[web] mulai pekerjaan scraping ...")
        client = _client()
        client.ensure_alive()
        print(f"[web] sesi eCampuz valid, menyiapkan {params['nim_file']}")

        from ecampuz.dhs_runner import read_nims, run_dhs

        grade_fallback = _grade_fallback(
            client.cfg, bool(params.get("neo_feeder_fallback"))
        )

        nim_file = d.resolve_nim_file(params["nim_file"])
        nims = read_nims(nim_file)
        if not nims:
            raise ValueError(f"tidak ada NIM di {nim_file}")
        print(f"[web] {len(nims)} NIM dari {nim_file}")
        angkatan = params.get("angkatan") or None
        prodi = params.get("prodi") or "all"
        limit = params.get("limit") or None
        run_dhs(
            client,
            nims,
            angkatan=angkatan,
            prodi=prodi,
            out_dir=d.OUT_DIR,
            per_mahasiswa=params.get("per_mahasiswa", True),
            limit=limit,
            resume=not params.get("reset", False),
            retry_failed=bool(params.get("retry_failed", False)),
            verbose=bool(params.get("verbose", False)),
            grade_fallback=grade_fallback,
        )
        print("[web] selesai.")
        JOB_STATE["rc"] = 0
    except SessionExpired as exc:
        print(f"[web] SESI HABIS: {exc}")
        print("[web] login ulang ke eCampuz (halaman Dashboard eCampuz belum ada; "
              "gunakan halaman ini setelah sesi dibuat), lalu ulangi.")
        JOB_STATE["rc"] = 2
        JOB_STATE["error"] = str(exc)
    except Exception as exc:  # noqa: BLE001
        print(f"[web] error: {exc}")
        JOB_STATE["rc"] = 1
        JOB_STATE["error"] = str(exc)
    finally:
        sys.stdout = real_out
        JOB_STATE["running"] = False
        JOB_STATE["finished"] = _iso()


def _append_yudisium_log(message: str) -> None:
    with JOB_LOCK:
        YUDI_JOB_STATE["log"].append(message)
        if len(YUDI_JOB_STATE["log"]) > 2000:
            del YUDI_JOB_STATE["log"][:-2000]


def _start_yudisium_job(params: dict) -> bool:
    with JOB_LOCK:
        if JOB_STATE["running"] or YUDI_JOB_STATE["running"]:
            return False
        YUDI_JOB_STATE.update(
            running=True,
            awaiting_manual_login=False,
            message="Menyiapkan browser",
            log=[],
            started=_iso(),
            finished=None,
            rc=None,
            error=None,
            log_file=params["log_file"],
        )
        YUDI_JOB_EVENT.clear()
    thread = threading.Thread(target=_run_yudisium_job, args=(params,), daemon=True)
    thread.start()
    return True


def _run_yudisium_job(params: dict) -> None:
    uploaded_path = Path(params["file_path"])

    def log(message: str) -> None:
        _append_yudisium_log(message)

    def pause_for_manual_login(page) -> None:
        with JOB_LOCK:
            YUDI_JOB_STATE["awaiting_manual_login"] = True
            YUDI_JOB_STATE["message"] = "Login dan CAPTCHA manual diperlukan"
            YUDI_JOB_EVENT.clear()
        log("[web] Browser menunggu login/CAPTCHA manual. Setelah login, buka halaman Tambah - Mahasiswa Lain lalu tekan tombol lanjut di halaman web.")
        YUDI_JOB_EVENT.wait()
        with JOB_LOCK:
            YUDI_JOB_STATE["awaiting_manual_login"] = False
            YUDI_JOB_STATE["message"] = "Melanjutkan proses"
        log("[web] Melanjutkan setelah intervensi manual.")

    try:
        cfg = load_config()
        args = SimpleNamespace(
            inspect=False,
            file=str(uploaded_path),
            nim_column=params["nim_column"],
            sheet=params.get("sheet") or None,
            url=params["url"],
            campaign_id=params.get("campaign_id") or None,
            limit=params["limit"],
            submit=True,
            retry_failed=bool(params.get("retry_failed", False)),
            state_file=params["state_file"],
            log_file=params["log_file"],
            user_data_dir=None,
            no_reset_filter=False,
            delay=params.get("delay", 3.0),
            timeout=60,
            confirmed=True,
            pause_for_manual_login=pause_for_manual_login,
            on_log=log,
        )
        log(f"[web] Memulai batch {params['limit']} NIM dari roster {uploaded_path.name}.")
        result = run_yudisium(args, cfg)
        with JOB_LOCK:
            YUDI_JOB_STATE["rc"] = result
            YUDI_JOB_STATE["message"] = "Selesai" if result == 0 else "Berhenti dengan status non-sukses"
    except Exception as exc:  # noqa: BLE001
        log(f"[web] Error: {exc}")
        with JOB_LOCK:
            YUDI_JOB_STATE["rc"] = 1
            YUDI_JOB_STATE["error"] = str(exc)
            YUDI_JOB_STATE["message"] = "Gagal"
    finally:
        uploaded_path.unlink(missing_ok=True)
        with JOB_LOCK:
            YUDI_JOB_STATE["running"] = False
            YUDI_JOB_STATE["awaiting_manual_login"] = False
            YUDI_JOB_STATE["finished"] = _iso()


def _yudisium_snapshot() -> dict:
    with JOB_LOCK:
        snapshot = {**YUDI_JOB_STATE, "log": list(YUDI_JOB_STATE["log"][-400:])}
        snapshot["log_file"] = bool(YUDI_JOB_STATE.get("log_file"))
        return snapshot


def prep_yudisium_upload(upload, original_name: str) -> dict:
    """Simpan roster upload (file object Django) → jalur token. Return info dict."""
    suffix = Path(original_name).suffix.casefold()
    if suffix not in {".csv", ".xlsx", ".xlsm"}:
        raise ValueError("Format file harus CSV, XLSX, atau XLSM.")
    YUDI_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    cutoff = time.time() - 24 * 60 * 60
    for old_token, old_upload in list(YUDI_UPLOADS.items()):
        old_path = Path(old_upload["path"])
        if old_upload.get("created", 0) < cutoff:
            old_path.unlink(missing_ok=True)
            YUDI_UPLOADS.pop(old_token, None)
    token = uuid.uuid4().hex
    saved_path = YUDI_UPLOAD_DIR / f"{token}{suffix}"
    with saved_path.open("wb") as fh:
        for chunk in upload.chunks():
            fh.write(chunk)
    _hide(saved_path)
    if saved_path.stat().st_size > YUDI_MAX_UPLOAD_BYTES:
        saved_path.unlink(missing_ok=True)
        raise ValueError("Ukuran roster melebihi 20 MB.")
    return {"token": token, "path": saved_path}


def make_yudisium_params(uploaded: dict, payload: dict) -> dict:
    """Bangun params batch yudisium + hitung path log/state."""
    cfg = load_config()
    raw_url = str(payload.get("url", "")).strip()
    if not raw_url:
        raise ValueError("URL halaman Tambah - Mahasiswa Lain wajib diisi.")
    try:
        target_url = _ensure_same_origin(urljoin(cfg.base_url, raw_url), cfg.base_url)
    except ValueError as exc:
        raise ValueError(str(exc)) from exc
    if "/kelulusan/" not in target_url.casefold():
        raise ValueError("URL harus menuju halaman modul kelulusan eCampuz.")

    credentials = CredentialStore(cfg.credential_path).load()
    if not credentials:
        raise RuntimeError(
            "Kredensial DPAPI belum tersedia. Jalankan login-browser sekali "
            "(isi CAPTCHA manual), lalu ulangi."
        )

    campaign = str(payload.get("campaign_id", "")).strip()
    if not campaign:
        raise ValueError("ID periode/sesi yudisium wajib diisi untuk checkpoint.")
    campaign_hash = hashlib.sha256(str(campaign).encode("utf-8")).hexdigest()[:8]
    stem = secure_filename(Path(uploaded["filename"]).stem)[:50] or "roster"
    log_path = d.OUT_DIR / f"yudisium_{stem}_{campaign_hash}_log.csv"
    state_path = d.STATE_DIR / f"yudisium_{stem}_{campaign_hash}_state.json"

    try:
        delay = float(payload.get("delay", 3))
    except (TypeError, ValueError):
        raise ValueError("Nilai jeda harus berupa angka.") from None

    return {
        "file_path": str(uploaded["path"]),
        "filename": uploaded["filename"],
        "nim_column": uploaded["nim_column"],
        "sheet": uploaded["sheet"],
        "limit": payload["limit"],
        "url": target_url,
        "campaign_id": campaign,
        "retry_failed": bool(payload.get("retry_failed", False)),
        "log_file": str(log_path),
        "state_file": str(state_path),
        "delay": max(1.5, min(delay, 30.0)),
    }