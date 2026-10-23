from django.urls import path, re_path

from dashboard import views

app_name = "dashboard"

urlpatterns = [
    # Halaman
    path("", views.index, name="dashboard"),
    path("mahasiswa", views.daftar_mahasiswa, name="daftar_mahasiswa"),
    path("mahasiswa/<str:nim>", views.detail_mahasiswa, name="detail_mahasiswa"),
    path("pemetaan", views.pemetaan, name="pemetaan"),
    path("scrape", views.scrape, name="scrape"),
    path("login", views.ecampuz_login_page, name="login_page"),
    path("yudisium", views.yudisium_page, name="yudisium_page"),
    path("input-nilai", views.grade_entry_page, name="grade_entry_page"),
    path("audit", views.audit_page, name="audit_page"),
    # API sesi & scraping
    path("api/session", views.api_session, name="api_session"),
    # API login eCampuz
    path("api/login/captcha", views.api_login_captcha, name="api_login_captcha"),
    path("api/login/submit", views.api_login_submit, name="api_login_submit"),
    path("api/login/browser", views.api_login_browser, name="api_login_browser"),
    path("api/login/clear", views.api_login_clear, name="api_login_clear"),
    path("api/scrape/start", views.api_scrape_start, name="api_scrape_start"),
    path("api/scrape/connect", views.api_scrape_connect_and_run, name="api_scrape_connect_and_run"),
    path("api/scrape/status", views.api_scrape_status, name="api_scrape_status"),
    # API export
    path("api/export/dhs.csv", views.export_dhs, name="export_dhs"),
    path("api/export/excel", views.export_excel, name="export_excel"),
    re_path(
        r"^api/export/transcript/(?P<nim>[0-9]+)\.xlsx$",
        views.export_transcript,
        name="export_transcript",
    ),
    path("api/config", views.api_config, name="api_config"),
    # API yudisium
    path("api/yudisium/upload", views.api_yudisium_upload, name="api_yudisium_upload"),
    path(
        "api/yudisium/template-kelulusan.xls",
        views.api_yudisium_template,
        name="api_yudisium_template",
    ),
    path(
        "api/yudisium/validate-import",
        views.api_yudisium_validate_import,
        name="api_yudisium_validate_import",
    ),
    path("api/input-nilai/preview", views.api_grade_entry_preview, name="api_grade_entry_preview"),
    path("api/yudisium/start", views.api_yudisium_start, name="api_yudisium_start"),
    path("api/yudisium/continue", views.api_yudisium_continue, name="api_yudisium_continue"),
    path("api/yudisium/status", views.api_yudisium_status, name="api_yudisium_status"),
    path("api/yudisium/log", views.api_yudisium_log, name="api_yudisium_log"),
]