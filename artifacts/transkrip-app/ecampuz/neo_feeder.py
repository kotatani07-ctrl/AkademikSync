"""Read-only Neo Feeder client and conservative missing-grade matching."""

from __future__ import annotations

import ipaddress
import re
from typing import Any
from urllib.parse import urlsplit

import requests

from .credentials import CredentialStore


class NeoFeederError(RuntimeError):
    """Safe-to-display Neo Feeder request or schema error."""


class NeoFeederClient:
    """Small JSON web-service client. Never logs credentials, tokens, or payloads."""

    READ_ACTIONS = {
        "GetDictionary",
        "GetListMahasiswa",
        "GetDetailNilaiPerkuliahanKelas",
        "GetRiwayatNilaiMahasiswa",
    }

    def __init__(
        self,
        endpoint: str,
        username: str,
        password: str,
        *,
        verify_tls: bool = True,
        allow_insecure_http: bool = False,
        timeout: int = 30,
        session: requests.Session | None = None,
    ) -> None:
        parts = urlsplit(endpoint)
        if parts.scheme not in {"http", "https"} or not parts.netloc:
            raise ValueError("URL Neo Feeder harus berupa URL http(s) yang valid.")
        if parts.scheme != "https":
            host = parts.hostname or ""
            try:
                private_host = ipaddress.ip_address(host).is_private or ipaddress.ip_address(host).is_loopback
            except ValueError:
                private_host = host.casefold() in {"localhost"} or host.casefold().endswith((".local", ".internal"))
            if not private_host and not allow_insecure_http:
                raise ValueError(
                    "Endpoint Neo Feeder menggunakan HTTP pada host publik. "
                    "Autentikasi diblokir; gunakan HTTPS/VPN atau opt-in risiko HTTP secara eksplisit."
                )
            if not allow_insecure_http:
                raise ValueError(
                    "HTTP tidak terenkripsi. Aktifkan opsi insecure hanya untuk jaringan privat yang disetujui."
                )
        if not username or not password:
            raise ValueError("Kredensial Neo Feeder belum dikonfigurasi.")
        self.endpoint = endpoint
        self.username = username
        self._password = password
        self.verify_tls = verify_tls
        self.timeout = timeout
        self.session = session or requests.Session()
        self.session.headers.update({
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "ecampuz-scraper/NeoFeeder-readonly",
        })
        self._token: str | None = None

    def __repr__(self) -> str:
        return (
            f"NeoFeederClient(endpoint={self.endpoint!r}, username='[redacted]', "
            "password='[redacted]', token='[redacted]')"
        )

    @classmethod
    def from_config(cls, config) -> "NeoFeederClient":
        if not config.neo_feeder_url:
            raise NeoFeederError("URL Neo Feeder belum dikonfigurasi.")
        credentials = CredentialStore(config.neo_feeder_credential_path).load()
        if not credentials:
            raise NeoFeederError(
                "Kredensial Neo Feeder belum tersimpan. Jalankan `run.bat neo-feeder configure`."
            )
        return cls(
            config.neo_feeder_url,
            credentials[0],
            credentials[1],
            verify_tls=config.neo_feeder_verify_tls,
            allow_insecure_http=config.neo_feeder_allow_insecure_http,
            timeout=config.timeout,
        )

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            response = self.session.post(
                self.endpoint,
                json=payload,
                timeout=self.timeout,
                verify=self.verify_tls,
            )
        except requests.RequestException as exc:
            raise NeoFeederError(
                f"Neo Feeder tidak dapat dihubungi ({exc.__class__.__name__})."
            ) from exc
        if response.status_code >= 400:
            raise NeoFeederError(f"Neo Feeder HTTP {response.status_code}.")
        try:
            data = response.json()
        except ValueError as exc:
            raise NeoFeederError("Respons Neo Feeder bukan JSON.") from exc
        if not isinstance(data, dict):
            raise NeoFeederError("Format respons Neo Feeder harus berupa object JSON.")
        error_code = data.get("error_code", 0)
        if str(error_code) not in {"0", "None", ""}:
            description = str(data.get("error_desc") or "kesalahan tidak dijelaskan")
            if re.search(r"token", description, re.I):
                raise NeoFeederError("Token Neo Feeder ditolak atau kedaluwarsa.")
            raise NeoFeederError(f"Neo Feeder menolak request: {description[:300]}")
        return data

    def get_token(self) -> str:
        response = self._post({
            "act": "GetToken",
            "username": self.username,
            "password": self._password,
        })
        token_data = response.get("data")
        if isinstance(token_data, dict):
            token_data = token_data.get("token") or token_data.get("Token")
        token = str(token_data or "").strip()
        if not token:
            raise NeoFeederError("GetToken sukses tetapi response tidak memuat token.")
        self._token = token
        return token

    def call_read(self, action: str, **params: Any) -> Any:
        if action not in self.READ_ACTIONS:
            raise ValueError(f"Action {action!r} bukan allowlist read-only.")
        if not self._token:
            self.get_token()
        response = self._post({"act": action, "token": self._token, **params})
        return response.get("data")

    def get_dictionary(self) -> Any:
        return self.call_read("GetDictionary")

    def _paged_rows(self, action: str, filter_text: str, page_size: int = 1000) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        offset = 0
        while True:
            data = self.call_read(
                action,
                filter=filter_text,
                order="",
                limit=str(page_size),
                offset=str(offset),
            )
            if not isinstance(data, list):
                raise NeoFeederError(f"Response {action} bukan array data.")
            if not all(isinstance(row, dict) for row in data):
                raise NeoFeederError(f"Response {action} berisi row bukan object.")
            rows.extend(data)
            if len(data) < page_size:
                return rows
            offset += page_size

    @staticmethod
    def _field(row: dict[str, Any], names: tuple[str, ...]) -> str:
        normalized = {str(key).casefold(): value for key, value in row.items()}
        for name in names:
            value = normalized.get(name.casefold())
            if value not in (None, ""):
                return str(value).strip()
        return ""

    def grades_for_student(self, nim: str, prodi: str = "") -> list[dict[str, Any]]:
        """Read grade rows for one exact NIM, never using a broad student filter."""
        nim = str(nim).strip()
        if not re.fullmatch(r"\d{6,20}", nim):
            raise ValueError("Neo Feeder lookup hanya menerima NIM numerik 6-20 digit.")
        escaped_nim = nim.replace("'", "\\'")
        students = self._paged_rows("GetListMahasiswa", f"nim='{escaped_nim}'")
        exact_students = [
            row for row in students
            if self._field(row, ("nim", "nomor_induk_mahasiswa")) == nim
        ]
        if len(exact_students) != 1:
            raise NeoFeederError(
                f"NIM {nim}: GetListMahasiswa menghasilkan {len(exact_students)} kecocokan persis."
            )
        student = exact_students[0]
        registration_id = self._field(student, ("id_registrasi_mahasiswa",))
        if not registration_id:
            raise NeoFeederError(
                f"NIM {nim}: response mahasiswa tidak memiliki id_registrasi_mahasiswa."
            )

        # Utama: nilai per NIM langsung dari GetDetailNilaiPerkuliahanKelas
        # (memuat kode & nama MK sehingga bisa dicocokkan per mata kuliah).
        try:
            grade_rows = self._paged_rows(
                "GetDetailNilaiPerkuliahanKelas",
                f"nim='{escaped_nim}'",
            )
        except NeoFeederError:
            grade_rows = []

        # Fallback: riwayat nilai via id registrasi.
        if not grade_rows:
            escaped_registration = registration_id.replace("'", "\\'")
            grade_rows = self._paged_rows(
                "GetRiwayatNilaiMahasiswa",
                f"id_registrasi_mahasiswa='{escaped_registration}'",
            )

        return [
            _normalize_grade_row(row, nim, registration_id)
            for row in grade_rows
        ]


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip().casefold()


def canonical_period(value: Any) -> str:
    """Normalize supported feeder period IDs/names to YYYY-T (odd/even)."""
    text = str(value or "").strip().casefold()
    numeric = re.fullmatch(r"(\d{4})\s*([12])", text)
    if numeric:
        return f"{numeric.group(1)}-{numeric.group(2)}"
    named = re.search(r"(ganjil|gasal|odd|genap|even)\s*(\d{4})\s*[/\-]\s*(\d{4})", text)
    if named:
        term = "1" if named.group(1) in {"ganjil", "gasal", "odd"} else "2"
        return f"{named.group(2)}-{term}"
    compact = re.fullmatch(r"(\d{4})\s*[-/]\s*(\d{4})\s*[-/]\s*([12])", text)
    if compact:
        return f"{compact.group(1)}-{compact.group(3)}"
    return ""


def _normalize_grade_row(row: dict[str, Any], nim: str, registration_id: str) -> dict[str, Any]:
    field = NeoFeederClient._field
    code = field(row, ("kode_mata_kuliah", "kode_mk", "kode_matkul"))
    grade = field(row, ("nilai_huruf", "nilai_grade", "grade"))
    weight_text = field(row, ("nilai_indeks", "nilai_mutu", "bobot"))
    period_name = field(row, ("nama_periode", "semester", "nama_semester"))
    period_id = field(row, ("id_periode", "periode", "id_semester"))
    sks_text = field(row, ("sks_mata_kuliah", "sks", "jumlah_sks"))
    weight = None
    if weight_text:
        try:
            weight = float(weight_text.replace(",", "."))
        except ValueError:
            weight = None
    sks = None
    if sks_text:
        try:
            sks = float(sks_text.replace(",", "."))
        except ValueError:
            sks = None
    period_key = canonical_period(period_name) or canonical_period(period_id)
    return {
        "nim": nim,
        "id_registrasi_mahasiswa": registration_id,
        "kode": code,
        "mata_kuliah": field(row, ("nama_mata_kuliah", "nama_mk", "mata_kuliah")),
        "semester": period_name or period_id,
        "period_key": period_key,
        "nilai": grade,
        "bobot": weight,
        "sks": sks,
        "source_id": field(row, ("id_riwayat_nilai", "id_nilai")),
    }


def merge_missing_grades(e_campus_result: dict[str, Any], feeder_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Fill only blank eCampus grades for unique exact course+period matches."""
    result = e_campus_result
    merged = 0
    ambiguous: list[dict[str, str]] = []
    for semester in result.get("semesters", []):
        e_period = canonical_period(semester.get("semester"))
        if not e_period:
            continue
        for course in semester.get("courses", []):
            if course.get("nilai") not in (None, ""):
                continue
            e_code = _normalize_text(course.get("kode", "")).replace(" ", "")
            if not e_code:
                continue
            matches = [
                row for row in feeder_rows
                if _normalize_text(row.get("kode", "")).replace(" ", "") == e_code
                and row.get("period_key") == e_period
                and row.get("nilai")
            ]
            if len(matches) != 1:
                if len(matches) > 1:
                    ambiguous.append({"kode": str(course.get("kode", "")), "semester": str(semester.get("semester", ""))})
                continue
            match = matches[0]
            course["nilai"] = match["nilai"]
            if course.get("mutu") is None and match.get("bobot") is not None:
                course["mutu"] = match["bobot"]
            if course.get("sks") is None and match.get("sks") is not None:
                course["sks"] = match["sks"]
            course["sumber_nilai"] = "Neo Feeder"
            course["neo_feeder_id"] = match.get("source_id", "")
            merged += 1
    graded = [
        course
        for semester in result.get("semesters", [])
        for course in semester.get("courses", [])
        if course.get("bobot") is not None and course.get("sks")
    ]
    total_sks = sum(float(course["sks"]) for course in graded)
    result["total_sks"] = total_sks
    result["jumlah_mk"] = len(graded)
    result["ips_keseluruhan"] = (
        round(sum(float(course["bobot"]) * float(course["sks"]) for course in graded) / total_sks, 2)
        if total_sks
        else None
    )
    return {"result": result, "merged": merged, "ambiguous": ambiguous}
