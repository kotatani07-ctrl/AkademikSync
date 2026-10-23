from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict
from pathlib import Path

# Akar penyimpanan data eCampuz — sekarang berada DI DALAM project transkrip-app.
# Bisa di-override via env ECAMPUZ_ROOT untuk memakai folder data lama scraper.
_ECAMPUZ_ROOT = os.getenv("ECAMPUZ_ROOT")
if _ECAMPUZ_ROOT:
    ROOT = Path(_ECAMPUZ_ROOT).resolve()
else:
    ROOT = Path(__file__).resolve().parents[1] / "data" / "ecampuz"
DATA_DIR = ROOT
RAW_DIR = DATA_DIR / "raw"
OUT_DIR = DATA_DIR / "out"
STATE_DIR = DATA_DIR / "state"
SESSION_FILE = STATE_DIR / "session.json"
AUDIT_FILE = STATE_DIR / "audit.jsonl"
CREDENTIAL_FILE = STATE_DIR / "credentials.json"
NEO_FEEDER_CREDENTIAL_FILE = STATE_DIR / "neo_feeder_credentials.json"

DEFAULT_BASE_URL = "https://universitasichsansatya.ecampuz.com/eakademik/"


@dataclass
class RateLimit:
    min_delay: float = 1.5
    max_delay: float = 3.0
    max_retries: int = 4
    backoff_base: float = 4.0
    backoff_cap: float = 60.0


@dataclass
class Config:
    base_url: str = DEFAULT_BASE_URL
    username: str = ""
    verify_tls: bool = True
    timeout: int = 45
    captcha_ttl_warning: int = 20
    neo_feeder_url: str = ""
    neo_feeder_verify_tls: bool = True
    neo_feeder_allow_insecure_http: bool = False
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    )
    rate: RateLimit = field(default_factory=RateLimit)

    def validate(self) -> None:
        if not self.base_url.startswith(("http://", "https://")):
            raise ValueError("base_url harus diawali http:// atau https://")
        self.base_url = self.base_url.rstrip("/") + "/"

    @classmethod
    def load(cls, path: Path | None = None) -> "Config":
        path = path or (STATE_DIR / "config.json")
        if not path.exists():
            return cls()
        raw = json.loads(path.read_text(encoding="utf-8"))
        rate = RateLimit(**raw.pop("rate", {}))
        known = {f for f in cls.__dataclass_fields__ if f != "rate"}
        filtered = {k: v for k, v in raw.items() if k in known}
        return cls(rate=rate, **filtered)

    def save(self, path: Path | None = None) -> Path:
        from .credentials import write_hidden

        path = path or (STATE_DIR / "config.json")
        data = asdict(self)
        data.pop("password", None)
        write_hidden(path, json.dumps(data, indent=2) + "\n")
        return path

    @property
    def session_path(self) -> Path:
        return SESSION_FILE

    @property
    def audit_path(self) -> Path:
        return AUDIT_FILE

    @property
    def credential_path(self) -> Path:
        return CREDENTIAL_FILE

    @property
    def neo_feeder_credential_path(self) -> Path:
        return NEO_FEEDER_CREDENTIAL_FILE

    @property
    def raw_dir(self) -> Path:
        return RAW_DIR

    @property
    def out_dir(self) -> Path:
        return OUT_DIR
