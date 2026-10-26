import unittest
from unittest import mock

from django.test import SimpleTestCase
from ecampuz.yudisium_browser import (
    _button_by_text,
    _classify_registration_result,
    _find_exact_student_row,
    _parse_registration_date,
    _period_key,
    _resolve_profile_credentials,
    _student_action,
    _try_reset_filter,
)


class YudisiumBrowserActionDisambiguationTest(SimpleTestCase):
    def test_resolve_credentials_non_interactive_skips_prompt(self):
        config = mock.Mock()
        with mock.patch("ecampuz.yudisium_browser.CredentialStore") as store_cls:
            store_cls.return_value.load.side_effect = RuntimeError("tidak ada kredensial")
            with mock.patch("builtins.input") as input_mock:
                result = _resolve_profile_credentials(config, allow_prompt=False)

        self.assertEqual(("", ""), result)
        input_mock.assert_not_called()

    def test_period_key_normalizes_campaign_aliases(self):
        self.assertEqual("genap 2022/2023", _period_key("Genap 2022-2023"))
        self.assertEqual("genap 2022/2023", _period_key("20222"))
        self.assertEqual("ganjil 2022/2023", _period_key("Ganjil 2022/2023"))
        self.assertEqual("genap 2022/2023", _period_key("genap 2022 / 2023"))

    def test_parse_registration_date(self):
        self.assertEqual(("31", "03", "2023"), _parse_registration_date("31-03-2023"))
        self.assertEqual(("01", "01", "2024"), _parse_registration_date("1/1/2024"))
        self.assertIsNone(_parse_registration_date("31-02-2023"))
        self.assertIsNone(_parse_registration_date("2023-03-31"))
        self.assertIsNone(_parse_registration_date(""))

    def test_student_action_single_action(self):
        fake_row = mock.Mock()
        fake_actions = mock.Mock()
        fake_actions.count.return_value = 1
        fake_first = mock.Mock()
        fake_actions.first = fake_first
        fake_row.locator.return_value = fake_actions

        chosen = _student_action(fake_row)
        self.assertEqual(fake_first, chosen)

    def test_student_action_prefers_daftar_over_detail(self):
        fake_row = mock.Mock()
        fake_actions = mock.Mock()
        fake_actions.count.return_value = 2

        # Action 0: Tombol Detail
        act_detail = mock.Mock()
        act_detail.inner_text.return_value = "Detail"
        act_detail.get_attribute.side_effect = lambda attr: {
            "title": "Detail Mahasiswa",
            "class": "btn btn-info",
            "onclick": "detail('0901201007')",
        }.get(attr, "")
        act_detail.evaluate.side_effect = lambda expr: "fa-eye" if "querySelectorAll" in expr else "a"

        # Action 1: Tombol Daftar
        act_daftar = mock.Mock()
        act_daftar.inner_text.return_value = "Daftar"
        act_daftar.get_attribute.side_effect = lambda attr: {
            "title": "Daftar Yudisium",
            "class": "btn btn-success",
            "onclick": "daftar('0901201007')",
        }.get(attr, "")
        act_daftar.evaluate.side_effect = lambda expr: "fa-plus" if "querySelectorAll" in expr else "button"

        fake_actions.nth.side_effect = lambda idx: [act_detail, act_daftar][idx]
        fake_row.locator.return_value = fake_actions

        chosen = _student_action(fake_row)
        self.assertEqual(act_daftar, chosen)

    def test_try_reset_filter_returns_false_if_no_reset_button(self):
        fake_page = mock.Mock()
        fake_candidates = mock.Mock()
        fake_candidates.count.return_value = 0
        fake_page.locator.return_value = fake_candidates

        # Must not raise exception
        result = _try_reset_filter(fake_page)
        self.assertFalse(result)

    def test_try_reset_filter_clicks_reset_when_present(self):
        fake_page = mock.Mock()
        btn = mock.Mock()
        btn.is_visible.return_value = True
        btn.evaluate.return_value = "button"
        btn.inner_text.return_value = "Reset Filter"
        btn.get_attribute.return_value = ""

        fake_candidates = mock.Mock()
        fake_candidates.count.return_value = 1
        fake_candidates.nth.return_value = btn
        fake_page.locator.return_value = fake_candidates

        result = _try_reset_filter(fake_page)
        self.assertTrue(result)
        btn.click.assert_called_once()

    def test_button_by_text_case_insensitive_and_fallback(self):
        fake_page = mock.Mock()
        btn1 = mock.Mock()
        btn1.is_visible.return_value = True
        btn1.evaluate.return_value = "button"
        btn1.inner_text.return_value = "TAMPILKAN"
        btn1.get_attribute.return_value = ""

        fake_candidates = mock.Mock()
        fake_candidates.count.return_value = 1
        fake_candidates.nth.return_value = btn1
        fake_page.locator.return_value = fake_candidates

        chosen = _button_by_text(fake_page, "tampilkan")
        self.assertEqual(btn1, chosen)

    def test_classify_registration_result(self):
        status, reason = _classify_registration_result([], [], "Mahasiswa berhasil didaftarkan ke yudisium")
        self.assertEqual("submitted", status)

        status, reason = _classify_registration_result([], ["IPK belum memenuhi syarat minimal"], "")
        self.assertEqual("failed", status)

    def test_abort_yudisium_job_clears_running_and_wakes_event(self):
        from dashboard import jobs
        with jobs.JOB_LOCK:
            jobs.YUDI_JOB_STATE["running"] = True
            jobs.YUDI_JOB_STATE["awaiting_manual_login"] = True
            jobs.YUDI_JOB_EVENT.clear()
            jobs.YUDI_JOB_CANCEL.clear()

        was_running = jobs.abort_yudisium_job()
        self.assertTrue(was_running)
        # Status running tetap True sampai worker benar-benar berhenti.
        self.assertTrue(jobs.YUDI_JOB_STATE["running"])
        self.assertFalse(jobs.YUDI_JOB_STATE["awaiting_manual_login"])
        self.assertTrue(jobs.YUDI_JOB_CANCEL.is_set())
        self.assertTrue(jobs.YUDI_JOB_EVENT.is_set())
        self.assertEqual(
            "Menghentikan worker browser...", jobs.YUDI_JOB_STATE["message"]
        )
        with jobs.JOB_LOCK:
            jobs.YUDI_JOB_STATE.update(
                running=False, message="idle", finished=None, rc=None, error=None
            )
            jobs.YUDI_JOB_CANCEL.clear()
            jobs.YUDI_JOB_EVENT.clear()

    def test_start_rejects_when_previous_worker_is_still_alive(self):
        from dashboard import jobs
        params = {
            "file_path": "/tmp/tidak-dipakai.xlsx",
            "url": "https://example.invalid/kelulusan/?act=x",
            "campaign_id": "Genap 2022/2023",
            "limit": 1,
            "log_file": "/tmp/tidak-dipakai.csv",
            "state_file": "/tmp/tidak-dipakai.json",
        }
        with jobs.JOB_LOCK:
            jobs.YUDI_JOB_STATE["running"] = False
            jobs.YUDI_JOB_ACTIVE = True
        try:
            self.assertFalse(jobs._start_yudisium_job(params))
        finally:
            with jobs.JOB_LOCK:
                jobs.YUDI_JOB_ACTIVE = False

    def test_api_yudisium_abort_endpoint(self):
        from types import SimpleNamespace
        from django.test import RequestFactory
        from dashboard import views, jobs

        request = RequestFactory().post("/ecampuz/api/yudisium/abort")
        request.user = SimpleNamespace(is_authenticated=True)

        with mock.patch.object(views, "_is_local_operator_request", return_value=True):
            resp = views.api_yudisium_abort(request)
            self.assertEqual(200, resp.status_code)
            import json
            data = json.loads(resp.content)
            self.assertTrue(data["ok"])
            self.assertIn("batch", data["message"].casefold())
