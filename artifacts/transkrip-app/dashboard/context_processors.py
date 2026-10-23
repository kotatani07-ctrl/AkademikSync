"""Context processor: info sesi eCampuz untuk semua template (dipakai di base.html)."""
from __future__ import annotations


def inject_session(request):
    from dashboard.services.data import session_file_exists

    return {"session_file": session_file_exists()}