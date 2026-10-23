"""Login lewat browser sungguhan (Playwright).

Dipakai ketika login POST biasa ditolak padahal kredensial jelas benar.
Alasannya: browser menjalankan persis skrip yang dipakai situs - lengkap
dengan cookie session, referer, dan token yang mungkin tidak kita tls suka.

Username dan password dapat diisi dari penyimpanan DPAPI lokal. CAPTCHA tetap
dijawab manual oleh pengguna di form aslinya; tidak ada pembacaan atau
pemecahan CAPTCHA otomatis.

Setelah login berhasil, cookie sesi diambil dari browser lalu dipakai ulang
oleh scraper HTTP biasa, sehingga request berikutnya tetap ringan.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from .config import Config
from .credentials import write_hidden


class BrowserLoginResult:
    def __init__(
        self,
        ok: bool,
        message: str,
        cookies: dict[str, str] | None = None,
        user_agent: str | None = None,
    ) -> None:
        self.ok = ok
        self.message = message
        self.cookies = cookies or {}
        self.user_agent = user_agent


def _is_login_form(html: str) -> bool:
    low = html.lower()
    return any(
        m in low
        for m in ('id="gsformlogin"', "gsformlogin", 'name="user[passwd]"', 'id="aid_password"')
    )


def _fill_login_credentials(page, username: str, password: str) -> None:
    """Fill only the account fields; leave the CAPTCHA for the user."""
    if not username or not password:
        raise ValueError("Username dan password harus tersedia untuk autofill.")
    page.locator("#aid_username, input[name='user[ID]']").first.fill(username)
    page.locator("#aid_password, input[name='user[passwd]']").first.fill(password)


def browser_login(
    cfg: Config,
    headless: bool = False,
    timeout_ms: int = 180_000,
    slow_mo: int = 0,
    username: str | None = None,
    password: str | None = None,
) -> BrowserLoginResult:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return BrowserLoginResult(
            False,
            "Playwright belum terpasang. Jalankan: pip install playwright\n"
            "Lalu: python -m playwright install chromium",
        )

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless, slow_mo=slow_mo)
        ctx = browser.new_context(
            user_agent=cfg.user_agent,
            viewport={"width": 1366, "height": 900},
            locale="id-ID",
        )
        page = ctx.new_page()

        try:
            page.goto(cfg.base_url, wait_until="domcontentloaded", timeout=timeout_ms)
            page.wait_for_selector("#aid_username", timeout=20_000)
        except Exception as exc:
            browser.close()
            return BrowserLoginResult(False, f"Gagal membuka halaman login: {exc}")

        if username is not None or password is not None:
            try:
                _fill_login_credentials(page, username or "", password or "")
            except Exception as exc:
                browser.close()
                return BrowserLoginResult(False, f"Gagal mengisi field akun: {exc}")

        if not headless:
            print()
            print("=" * 64)
            print("  LOGIN DI JENDELA BROWSER")
            print("=" * 64)
            print(f"  Halaman : {cfg.base_url}")
            print()
            print("  Username dan password sudah diisi.")
            print("  Jawab captcha manual lalu klik LOGIN. Jendela akan tertutup otomatis")
            print("  setelah login berhasil.")
            print()

        try:
            # Tunggu user isi sendiri + klik tombol
            page.wait_for_function(
                "() => !document.querySelector('#gsFormLogin, #gsformlogin, #aid_password, [name=\"user[passwd]\"]')",
                timeout=timeout_ms,
            )
        except Exception:
            pass

        try:
            html = page.content()
        except Exception:
            browser.close()
            return BrowserLoginResult(False, "Gagal membaca halaman setelah login")

        if _is_login_form(html):
            err = ""
            for sel in ("#login-error-alert", "#captcha-error"):
                try:
                    if page.is_visible(sel):
                        err = page.inner_text(sel).strip()
                        if err:
                            break
                except Exception:
                    pass
            if not err:
                err = "Form login masih tampil - login belum berhasil."
            browser.close()
            return BrowserLoginResult(False, err)

        cookies = {c["name"]: c["value"] for c in ctx.cookies()}
        ua = page.evaluate("() => navigator.userAgent")
        final_url = page.url
        browser.close()

        return BrowserLoginResult(
            True,
            "Login berhasil lewat browser",
            cookies=cookies,
            user_agent=ua or cfg.user_agent,
        )


def login_via_browser(
    cfg: Config,
    headless: bool = False,
    timeout_ms: int = 180_000,
    username: str | None = None,
    password: str | None = None,
) -> BrowserLoginResult:
    result = browser_login(
        cfg,
        headless=headless,
        timeout_ms=timeout_ms,
        username=username,
        password=password,
    )
    if not result.ok:
        return result

    payload = {
        "saved_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "base_url": cfg.base_url,
        "user_agent": result.user_agent or cfg.user_agent,
        "cookies": result.cookies,
        "via": "playwright",
    }
    write_hidden(cfg.session_path, json.dumps(payload, indent=2))
    result.message += f" ({len(result.cookies)} cookie disimpan)"
    return result
