"""Penyimpanan kredensial terenkripsi Windows DPAPI.

Pakai CryptProtectData sehingga ciphertext hanya bisa didekripsi oleh
akun Windows yang sama di mesin yang sama. Tidak ada plaintext di disk.
"""

from __future__ import annotations

import base64
import json
import os
import sys
from pathlib import Path

IS_WINDOWS = sys.platform.startswith("win")

if IS_WINDOWS:
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [
            ("cbData", wintypes.DWORD),
            ("pbData", ctypes.POINTER(ctypes.c_ubyte)),
        ]

    def _to_blob(data: bytes):
        buf = ctypes.create_string_buffer(data, len(data))
        blob = DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte)))
        return blob, buf

    def _from_blob(blob) -> bytes:
        if not blob.cbData:
            return b""
        return ctypes.string_at(blob.pbData, blob.cbData)

    CRYPTPROTECT_UI_FORBIDDEN = 0x01
    CRYPTPROTECT_LOCAL_MACHINE = 0x04


def _protect(data: bytes, description: str = "ecampuz") -> bytes:
    if not IS_WINDOWS:
        raise RuntimeError("DPAPI hanya tersedia di Windows")

    crypt32 = ctypes.WinDLL("crypt32.dll", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32.dll", use_last_error=True)

    in_blob, in_buf = _to_blob(data)
    out_blob = DATA_BLOB()

    ok = crypt32.CryptProtectData(
        ctypes.byref(in_blob),
        ctypes.c_wchar_p(description),
        None,
        None,
        None,
        CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(out_blob),
    )
    if not ok:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return _from_blob(out_blob)
    finally:
        kernel32.LocalFree(out_blob.pbData)
        del in_buf


def _unprotect(data: bytes, description: str = "ecampuz") -> bytes:
    if not IS_WINDOWS:
        raise RuntimeError("DPAPI hanya tersedia di Windows")

    crypt32 = ctypes.WinDLL("crypt32.dll", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32.dll", use_last_error=True)

    in_blob, in_buf = _to_blob(data)
    out_blob = DATA_BLOB()

    ok = crypt32.CryptUnprotectData(
        ctypes.byref(in_blob),
        ctypes.byref(ctypes.c_wchar_p()),
        None,
        None,
        None,
        CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(out_blob),
    )
    if not ok:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return _from_blob(out_blob)
    finally:
        kernel32.LocalFree(out_blob.pbData)
        del in_buf


class CredentialStore:
    """Simpan/baca username + password terenkripsi."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def exists(self) -> bool:
        return self.path.exists()

    def save(self, username: str, password: str) -> None:
        if not username:
            raise ValueError("username kosong")
        if IS_WINDOWS:
            blob = _protect(password.encode("utf-8"), "ecampuz-akademik")
            payload = {
                "v": 1,
                "method": "dpapi",
                "user": username,
                "cipher": base64.b64encode(blob).decode("ascii"),
            }
        else:
            raise RuntimeError(
                "Hanya Windows yang didukung untuk penyimpanan kredensial terenkripsi. "
                "Set PYTHONPATH dan login manual di platform lain."
            )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            _unhide(self.path)
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        _hide(self.path)

    def load(self) -> tuple[str, str] | None:
        if not self.path.exists():
            return None
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None
        if payload.get("method") != "dpapi":
            return None
        try:
            blob = base64.b64decode(payload["cipher"])
            password = _unprotect(blob, "ecampuz-akademik").decode("utf-8")
        except Exception:
            return None
        return payload.get("user", ""), password

    def clear(self) -> None:
        if self.path.exists():
            _unhide(self.path)
            self.path.unlink()


def _attrib(path: Path, flag: str) -> None:
    if not IS_WINDOWS:
        return
    try:
        import subprocess

        subprocess.run(["attrib", flag, str(path)], capture_output=True, check=False)
    except Exception:
        pass


def _unhide(path: Path) -> None:
    _attrib(path, "-h")


def _hide(path: Path) -> None:
    _attrib(path, "+h")


def write_hidden(path: Path, text: str) -> None:
    """Tulis file lalu set Hidden; aman ditulis ulang walau sudah Hidden."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        _unhide(path)
    path.write_text(text, encoding="utf-8")
    _hide(path)
