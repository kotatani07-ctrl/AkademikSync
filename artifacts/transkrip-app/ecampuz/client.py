from __future__ import annotations

import getpass
import json
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from .audit import Audit, RateLimiter, load_config
from .config import Config
from .credentials import CredentialStore, _unhide, write_hidden

ENCODING = "iso-8859-1"

CAPTCHA_SOAL_RE = re.compile(
    r'id="captcha-soal"[^>]*>(.*?)</strong>', re.DOTALL
)
CAPTCHA_TOKEN_RE = re.compile(
    r'id="captcha_token"[^>]*value="([^"]+)"'
)
ERROR_ALERT_RE = re.compile(
    r'id="login-error-alert"[^>]*>(.*?)</div>', re.DOTALL
)
CAPTCHA_ERR_RE = re.compile(
    r'id="captcha-error"[^>]*>(.*?)</div>', re.DOTALL
)

_WS_RE = re.compile(r"[\s\u00a0\u200b\ufeff]+")


def clean_ws(text: str | None) -> str:
    return _WS_RE.sub(" ", text or "").strip()


class LoginError(RuntimeError):
    pass


class SessionExpired(LoginError):
    pass


@dataclass
class LoginResult:
    ok: bool
    message: str
    attempts: int = 0
    landing_url: str = ""


class eCampuzClient:
    def __init__(
        self,
        config: Config | None = None,
        audit_enabled: bool = True,
    ) -> None:
        self.cfg = config or load_config()
        self.cfg.validate()
        self.base = self.cfg.base_url
        self.audit = Audit(self.cfg.audit_path, enabled=audit_enabled)
        self.limiter = RateLimiter(self.cfg.rate.min_delay, self.cfg.rate.max_delay)
        self.session = requests.Session()
        self.session.verify = self.cfg.verify_tls
        self.session.headers.update(
            {
                "User-Agent": self.cfg.user_agent,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "id-ID,id;q=0.9,en;q=0.8",
                "Connection": "keep-alive",
            }
        )
        self.authenticated = False
        self.last_html: str | None = None
        self._load_session()

    # ---------- session persistence ----------

    def _session_file(self) -> Path:
        return self.cfg.session_path

    def _load_session(self) -> None:
        path = self._session_file()
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return
        for name, value in (data.get("cookies") or {}).items():
            self.session.cookies.set(name, value)
        if data.get("user_agent"):
            self.session.headers["User-Agent"] = data["user_agent"]
        self.authenticated = True
        self.audit.event("session_restored", cookies=len(self.session.cookies))

    def _save_session(self) -> None:
        path = self._session_file()
        payload = {
            "saved_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "base_url": self.base,
            "user_agent": self.cfg.user_agent,
            "cookies": {c.name: c.value for c in self.session.cookies},
        }
        write_hidden(path, json.dumps(payload, indent=2))

    def clear_session(self) -> None:
        path = self._session_file()
        if path.exists():
            _unhide(path)
            path.unlink()
        self.session.cookies.clear()
        self.authenticated = False
        self.audit.event("session_cleared")

    # ---------- http ----------

    def _request(
        self,
        method: str,
        url: str,
        note: str = "",
        **kwargs: Any,
    ) -> requests.Response:
        abs_url = url if url.startswith("http") else urljoin(self.base, url)
        last_exc: Exception | None = None

        for attempt in range(1, self.cfg.rate.max_retries + 1):
            self.limiter.wait()
            started = time.monotonic()
            try:
                resp = self.session.request(
                    method,
                    abs_url,
                    timeout=self.cfg.timeout,
                    allow_redirects=True,
                    **kwargs,
                )
            except requests.RequestException as exc:
                last_exc = exc
                self.audit.log(method, abs_url, note=f"network_error: {exc.__class__.__name__}")
                self._sleep_backoff(attempt)
                continue

            elapsed_ms = int((time.monotonic() - started) * 1000)
            self.audit.log(
                method,
                abs_url,
                status=resp.status_code,
                bytes_len=len(resp.content),
                elapsed_ms=elapsed_ms,
                note=note,
            )

            if resp.status_code in (401, 403):
                raise SessionExpired(
                    f"HTTP {resp.status_code} pada {abs_url} - sesi ditolak server"
                )
            if resp.status_code in (429, 502, 503, 504):
                self.audit.event("throttled", url=abs_url, status=resp.status_code)
                self._sleep_backoff(attempt)
                continue
            if resp.status_code >= 500:
                self._sleep_backoff(attempt)
                continue

            resp.encoding = resp.encoding or ENCODING
            self.last_html = resp.text
            return resp

        raise RuntimeError(f"Gagal setelah {self.cfg.rate.max_retries} percobaan: {last_exc}")

    def _sleep_backoff(self, attempt: int) -> None:
        delay = min(
            self.cfg.rate.backoff_base * (2 ** (attempt - 1)),
            self.cfg.rate.backoff_cap,
        )
        time.sleep(delay)

    def get(self, url: str, note: str = "") -> requests.Response:
        return self._request("GET", url, note=note or "get")

    def get_text(self, url: str, note: str = "") -> str:
        return self.get(url, note=note).text

    # ---------- captcha ----------

    def fetch_captcha(self) -> tuple[str, str]:
        """Ambil soal + token captcha terbaru dari handler AJAX."""
        resp = self._request(
            "POST",
            urljoin(self.base, "login_captcha_handler.php"),
            data={"action": "generate"},
            headers={"X-Requested-With": "XMLHttpRequest"},
            note="captcha_generate",
        )
        try:
            data = resp.json()
        except ValueError as exc:
            raise LoginError(
                f"Respons captcha bukan JSON (status {resp.status_code})"
            ) from exc
        if not data.get("success"):
            raise LoginError(data.get("message") or "Captcha gagal dibuat")
        return str(data.get("soal", "")).strip(), str(data.get("token", "")).strip()

    @staticmethod
    def scrape_captcha_from_html(html: str) -> tuple[str, str] | None:
        soal = CAPTCHA_SOAL_RE.search(html)
        token = CAPTCHA_TOKEN_RE.search(html)
        if soal and token:
            return soal.group(1).strip(), token.group(1).strip()
        return None

    def prompt_captcha(self, soal: str) -> str:
        sys.stdout.write(
            f"\n  Captcha: [{soal}]\n  Jawab (script tidak menebak, ketik manual): "
        )
        sys.stdout.flush()
        answer = input().strip()
        return answer

    # ---------- login ----------

    def login(
        self,
        username: str | None = None,
        password: str | None = None,
        answer_provider: Callable[[str], str] | None = None,
        interactive: bool = True,
        remember: bool = True,
    ) -> LoginResult:
        store = CredentialStore(self.cfg.credential_path)

        user, pwd = username, password

        if (not user or not pwd) and store.exists():
            creds = store.load()
            if creds:
                user = user or creds[0]
                pwd = pwd or creds[1]
                self.audit.event("credentials_loaded", source="dpapi")

        if not user:
            if not interactive:
                raise LoginError("Username kosong")
            user = input("Username eAkademik: ").strip()
        if not pwd:
            if not interactive:
                raise LoginError("Password kosong")
            pwd = getpass.getpass("Password eAkademik: ")

        if remember and store.exists():
            if store.load() != (user, pwd):
                store.save(user, pwd)
                self.audit.event("credentials_saved", method="dpapi")

        self.cfg.username = user

        provider = answer_provider or (
            self.prompt_captcha if interactive else (lambda _: (_ for _ in ()).throw(
                LoginError("Butuh captcha non-interaktif")
            ))
        )

        self.clear_session()
        last_err = ""
        attempts = 0

        for attempt in range(1, self.cfg.rate.max_retries + 1):
            attempts = attempt
            soal, token = self.fetch_captcha()
            if not soal or not token:
                raise LoginError("Soal/token captcha kosong")

            answer = provider(soal)
            if not answer:
                raise LoginError("Jawaban captcha kosong")

            sys.stdout.write(
                f"  Mengirim (soal: {soal}, jawab: {answer})... "
            )
            sys.stdout.flush()

            started = time.monotonic()
            resp = self._request(
                "POST",
                urljoin(self.base, "?act=login"),
                data={
                    "user[ID]": user,
                    "user[passwd]": pwd,
                    "captcha_answer": answer,
                    "captcha_token": token,
                    "action": "submitted",
                    "gsLclStrg": "",
                    "setLangActive": "",
                },
                headers={"Referer": self.base},
                note=f"login_attempt_{attempt}",
            )
            took = time.monotonic() - started
            sys.stdout.write(
                f"{took:.1f}s, status {resp.status_code}\n"
            )
            sys.stdout.flush()

            if self._looks_authenticated(resp.text):
                self.authenticated = True
                self._save_session()
                self.audit.event("login_success", attempts=attempt)
                return LoginResult(
                    ok=True,
                    message="Login berhasil",
                    attempts=attempt,
                    landing_url=resp.url,
                )

            last_err = self._extract_login_error(resp.text) or (
                f"Login ditolak (status {resp.status_code})"
            )
            self.audit.event("login_failed", attempt=attempt, reason=last_err)
            if "captcha" in last_err.lower():
                sys.stdout.write(f"  Captcha salah, ambil yang baru...\n")
            elif "salah" in last_err.lower() or "password" in last_err.lower():
                raise LoginError(
                    f"Username/password ditolak: {last_err}\n"
                    "Periksa kredensial; percobaan dihentikan agar tidak terkunci akun."
                )
            time.sleep(1.0)

        raise LoginError(f"Gagal login setelah {attempts} percobaan. {last_err}")

    @staticmethod
    def _looks_authenticated(html: str) -> bool:
        """Deteksi login dengan prinsip 'default TIDAK authenticated'.

        Halaman login eCampuz memuat <meta name="c-token"> sehingga marker
        longgar seperti 'c-token' atau 'logout' selalu cocok - jangan pakai itu.
        """
        low = html.lower()

        if 'id="gsformlogin"' in low or "gsformlogin" in low:
            return False
        if 'name="user[passwd]"' in low or "user[passwd]" in low:
            return False
        if 'id="aid_password"' in low:
            return False
        if 'name="captcha_token"' in low and 'id="captcha_answer"' in low:
            return False

        if eCampuzClient._has_login_error(html):
            return False

        strong = (
            'id="sidebar"',
            'class="sidebar"',
            'id="main-content"',
            'class="px-content-body"',
            'id="wrapper"',
            'name="logout"',
        )
        return any(m in low for m in strong)

    @staticmethod
    def _has_login_error(html: str) -> bool:
        """Cek penanda error login yang spesifik saja.

        Jangan pakai selector .alert-* umum: halaman login punya .alert-info
        untuk kotak captcha, dan dashboard sah bisa punya .alert-info info.
        """
        soup = BeautifulSoup(html, "lxml")
        for sel in ("#login-error-alert", "#captcha-error"):
            node = soup.select_one(sel)
            if node and clean_ws(node.get_text(" ", strip=True)):
                return True
        return False

    @staticmethod
    def _looks_login_page(html: str) -> bool:
        low = html.lower()
        return any(
            m in low
            for m in ('id="gsformlogin"', "gsformlogin", 'name="user[passwd]"', 'id="aid_password"')
        )

    @staticmethod
    def _extract_login_error(html: str) -> str:
        soup = BeautifulSoup(html, "lxml")
        for sel in ("#login-error-alert", "#captcha-error"):
            node = soup.select_one(sel)
            if node:
                text = clean_ws(node.get_text(" ", strip=True))
                if text:
                    return text
        for sel in (".alert-danger", ".alert-info"):
            node = soup.select_one(sel)
            if node:
                text = clean_ws(node.get_text(" ", strip=True))
                if text and len(text) < 300:
                    return text
        return ""

    # ---------- fetch helpers ----------

    def save_raw(self, name: str, html: str) -> Path:
        self.cfg.raw_dir.mkdir(parents=True, exist_ok=True)
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("_")[:120]
        path = self.cfg.raw_dir / f"{safe}.html"
        path.write_text(html, encoding="utf-8")
        return path

    def soup(self, html: str) -> BeautifulSoup:
        return BeautifulSoup(html, "lxml")

    def verify_session(self) -> tuple[bool, str]:
        """Cek sesi dengan request nyata. Return (valid, detail)."""
        if not self.authenticated:
            return False, "tidak ada sesi tersimpan"
        try:
            resp = self.get("", note="verify_session")
        except Exception as exc:
            return False, f"request gagal: {exc}"

        if self._looks_login_page(resp.text):
            return False, "server mengembalikan halaman login (sesi tidak valid)"

        soup = self.soup(resp.text)
        title = soup.title.get_text(strip=True) if soup.title else "-"
        if self._looks_authenticated(resp.text):
            return True, f"OK - judul: {title} ({len(resp.text)} byte)"
        return False, f"tidak yakin (judul: {title})"

    def ensure_alive(self) -> None:
        ok, detail = self.verify_session()
        if not ok:
            self.authenticated = False
            self.clear_session()
            raise SessionExpired(f"Sesi tidak valid: {detail}")
