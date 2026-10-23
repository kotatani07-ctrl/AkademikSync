import json
from types import SimpleNamespace
from unittest import mock

from django.test import RequestFactory, SimpleTestCase, TestCase

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


class TranscriptExportDynamicTest(SimpleTestCase):
    def test_dynamic_export_for_new_student(self):
        from pathlib import Path
        from types import SimpleNamespace
        from dashboard.services.transcript_export import build_transcript_export
        import dashboard.services.data as d

        template_path = d.excel_data_path()
        if not template_path.is_file():
            self.skipTest("Template workbook kampus tidak ditemukan di path lokal.")

        hasil = SimpleNamespace(
            nim='209999999',
            nama='Mahasiswa Uji Otomatis',
            prodi='Ilmu Keperawatan',
            jenjang='S1',
            angkatan=2024,
            tempat_lahir='Jakarta',
            tanggal_lahir='01 Januari 2000',
            tanggal_lulus='28 Februari 2026',
            daftar_mk=[
                SimpleNamespace(
                    kode_mk='P24401', nama_mk='Agama', sks=2,
                    nilai_lambang='A', nilai_angka='4.00', mutu=8, is_kosong=False
                )
            ]
        )
        export = build_transcript_export(template_path, '209999999', hasil=hasil)
        self.assertIsNotNone(export.content)
        self.assertTrue(len(export.content) > 50000)
        self.assertEqual('TRANSKRIP NILAI 2026', export.sheet_name)


class PemetaanViewTest(TestCase):
    def setUp(self):
        from core.models import Kurikulum, MataKuliahKurikulum, Template
        from django.core.files.uploadedfile import SimpleUploadedFile
        tpl = Template.objects.create(
            nama="Template Test",
            file_excel=SimpleUploadedFile("t.xlsx", b"dummy"),
            total_mk=50,
            baris_halaman_depan=25,
            cell_mapping={}
        )
        kuri = Kurikulum.objects.create(
            jenjang="S1",
            angkatan=2024,
            prodi="ILMU_KEPERAWATAN",
            template=tpl
        )
        MataKuliahKurikulum.objects.create(
            kurikulum=kuri,
            kode_mk="P24606",
            nama_mk="Keperawatan Bencana Global",
            sks=2,
            urutan_alfabet=1
        )

    def test_mapping_rows_resolves_normalized_keys_and_names(self):
        import dashboard.services.data as d
        rows = d.mapping_rows()
        self.assertTrue(len(rows) > 0)
        first = rows[0]
        self.assertIn("kode_dhs", first)
        self.assertIn("nama_dhs", first)
        self.assertIn("kode_template", first)
        self.assertIn("nama_template", first)
        self.assertIn("status", first)

        # Baris P24606 harus memiliki nama_template yang terisi
        p24606_rows = [r for r in rows if r.get("kode_template") == "P24606"]
        self.assertTrue(len(p24606_rows) > 0)
        self.assertEqual("Keperawatan Bencana Global", p24606_rows[0]["nama_template"])

    def test_pemetaan_view_renders_table_data(self):
        from django.contrib.auth.models import User
        from django.test import Client

        user = User.objects.create_user("pemetaan_test_user", password="testpassword")
        client = Client()
        client.force_login(user)

        response = client.get("/ecampuz/pemetaan")
        self.assertEqual(200, response.status_code)
        content = response.content.decode("utf-8")
        self.assertIn("FKS701", content)
        self.assertIn("Keperawatan Bencana", content)
        self.assertIn("P24606", content)
        self.assertIn("Keperawatan Bencana Global", content)


class LoginCaptchaFlowTest(SimpleTestCase):
    def test_request_login_captcha_stores_cookies(self):
        fake_client = mock.Mock()
        cookie = mock.Mock()
        cookie.name = "PHPSESSID"
        cookie.value = "session_cookie_123"
        fake_client.session.cookies = [cookie]
        fake_client.fetch_captcha.return_value = ("5 + 2 = ?", "token_abc")

        with mock.patch.object(jobs, "_client", return_value=fake_client):
            res = jobs.request_login_captcha("user1", "pass1")

        self.assertIn("id", res)
        self.assertEqual("5 + 2 = ?", res["soal"])

        login_id = res["id"]
        self.assertIn(login_id, jobs.LOGIN_PENDING)
        saved = jobs.LOGIN_PENDING[login_id]
        self.assertEqual("user1", saved["username"])
        self.assertEqual("pass1", saved["password"])
        self.assertEqual("token_abc", saved["token"])
        self.assertEqual({"PHPSESSID": "session_cookie_123"}, saved["cookies"])

    def test_submit_login_captcha_restores_cookies_and_succeeds(self):
        login_id = "test_sub_ok"
        jobs.LOGIN_PENDING[login_id] = {
            "username": "user1",
            "password": "pass1",
            "soal": "1 + 1 = ?",
            "token": "token_xyz",
            "cookies": {"PHPSESSID": "cookie_restored_789"},
            "at": 9999999999.0,
        }

        fake_client = mock.Mock()
        fake_client.base = "https://ecampuz.example.com/"
        fake_client.cfg.username = ""
        fake_client.cfg.credential_path = mock.Mock()
        fake_client._looks_authenticated.return_value = True
        fake_resp = mock.Mock()
        fake_resp.text = "<html>dashboard</html>"
        fake_client._request.return_value = fake_resp

        with (
            mock.patch.object(jobs, "_client", return_value=fake_client),
            mock.patch("dashboard.jobs.CredentialStore") as mock_store,
        ):
            mock_store.return_value.load.return_value = None
            res = jobs.submit_login_captcha(login_id, "2")

        self.assertTrue(res["ok"])
        fake_client.session.cookies.set.assert_called_with("PHPSESSID", "cookie_restored_789")
        fake_client._save_session.assert_called_once()
        self.assertNotIn(login_id, jobs.LOGIN_PENDING)

    def test_submit_login_captcha_handles_captcha_retry_with_cookies(self):
        login_id = "test_sub_wrong"
        jobs.LOGIN_PENDING[login_id] = {
            "username": "user1",
            "password": "pass1",
            "soal": "1 + 1 = ?",
            "token": "token_xyz",
            "cookies": {"PHPSESSID": "cookie_old"},
            "at": 9999999999.0,
        }

        fake_client = mock.Mock()
        fake_client.base = "https://ecampuz.example.com/"
        fake_client.cfg.username = ""
        fake_client._looks_authenticated.return_value = False
        fake_client._extract_login_error.return_value = "Proses login tidak berhasil, Jawaban captcha salah!"
        fake_resp = mock.Mock()
        fake_resp.text = "<html>captcha error</html>"
        fake_resp.status_code = 200
        fake_client._request.return_value = fake_resp

        with (
            mock.patch.object(jobs, "_client", return_value=fake_client),
            mock.patch.object(
                jobs, "request_login_captcha", return_value={"id": "new_id_456", "soal": "3 + 3 = ?"}
            ) as mock_req_new,
        ):
            res = jobs.submit_login_captcha(login_id, "99")

        self.assertFalse(res["ok"])
        self.assertTrue(res["captcha_wrong"])
        self.assertIn("new_captcha", res)
        self.assertEqual("new_id_456", res["new_captcha"]["id"])
        mock_req_new.assert_called_with("user1", "pass1")



