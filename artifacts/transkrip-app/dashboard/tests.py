import json
from types import SimpleNamespace
from unittest import mock

from django.test import RequestFactory, SimpleTestCase

from dashboard import jobs
from dashboard import views


class AutoConnectScrapeTest(SimpleTestCase):
    def test_starts_job_when_session_is_valid(self):
        client = mock.Mock()
        client.cfg.timeout = 120
        client.verify_session.return_value = (True, "valid")
        with (
            mock.patch.object(jobs, "_client", return_value=client),
            mock.patch.object(jobs.d, "web_config", return_value={}),
            mock.patch.object(jobs, "start_job", return_value=True) as start_job,
        ):
            result = jobs.ensure_session_and_start({"nim_file": "roster.txt"})

        self.assertTrue(result["ok"])
        self.assertIn("dimulai", result["message"].lower())
        self.assertEqual(15, client.cfg.timeout)
        self.assertEqual("roster.txt", start_job.call_args.args[0]["nim_file"])

    def test_invalid_session_requires_manual_login(self):
        client = mock.Mock()
        client.cfg.timeout = 120
        client.verify_session.return_value = (False, "sesi tidak valid")
        with mock.patch.object(jobs, "_client") as mock_client:
            mock_client.return_value = client
            with mock.patch.object(jobs.d, "web_config", return_value={}):
                result = jobs.ensure_session_and_start({
                    "nim_file": "nim_2024_sheet.txt",
                    "angkatan": "2024",
                    "prodi": "all",
                })

        self.assertFalse(result["ok"])
        self.assertIn("login", result["message"].lower())
        client.login.assert_not_called()

    def test_connect_api_returns_job_start_result(self):
        request = RequestFactory().post(
            "/ecampuz/api/scrape/connect",
            data=json.dumps({"nim_file": "roster.txt"}),
            content_type="application/json",
        )
        request.user = SimpleNamespace(is_authenticated=True)

        with mock.patch.object(
            jobs,
            "ensure_session_and_start",
            return_value={"ok": True, "message": "Sesi valid; job dimulai."},
        ) as ensure_session:
            response = views.api_scrape_connect_and_run(request)

        self.assertEqual(200, response.status_code)
        self.assertEqual({"ok": True, "message": "Sesi valid; job dimulai."}, json.loads(response.content))
        ensure_session.assert_called_once_with({"nim_file": "roster.txt"})

    def test_expired_session_requires_manual_captcha_login(self):
        client = mock.Mock()
        client.cfg.timeout = 120
        client.verify_session.return_value = (False, "expired")
        with (
            mock.patch.object(jobs, "_client", return_value=client),
            mock.patch.object(jobs, "load_config", return_value=SimpleNamespace(credential_path="credentials.json")),
            mock.patch.object(jobs.d, "web_config", return_value={}),
            mock.patch.object(jobs.CredentialStore, "load", return_value=("user", "password")),
            mock.patch.object(jobs, "start_job") as start_job,
        ):
            result = jobs.ensure_session_and_start({})

        self.assertFalse(result["ok"])
        self.assertIn("captcha", result["message"].lower())
        client.login.assert_not_called()
        start_job.assert_not_called()

    def test_session_api_reports_false_for_expired_session(self):
        request = RequestFactory().get("/ecampuz/api/session")
        request.user = SimpleNamespace(is_authenticated=True)
        client = mock.Mock()
        client.authenticated = True
        client.cfg.timeout = 120
        client.verify_session.return_value = (False, "expired")

        with mock.patch.object(jobs, "_client", return_value=client):
            response = views.api_session(request)

        self.assertFalse(json.loads(response.content)["ok"])
