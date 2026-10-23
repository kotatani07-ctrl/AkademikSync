from io import BytesIO

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from openpyxl import Workbook

from dashboard.services.grade_entry import (
    inspect_grade_workbook,
    validate_grade_workbook,
)


def _workbook_bytes():
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Nilai"
    sheet.append(["NIM", "Kode MK", "Nilai"])
    sheet.append(["251030049", "MK101", "A"])
    sheet.append(["251030049", "MK101", "B"])
    sheet.append(["invalid", "", ""])
    output = BytesIO()
    workbook.save(output)
    return output.getvalue()


class GradeEntryWorkbookTest(TestCase):
    def test_page_renders(self):
        user = User.objects.create_user("grade-page-user", password="test")
        self.client.force_login(user)
        response = self.client.get(reverse("dashboard:grade_entry_page"))

        self.assertEqual(200, response.status_code)
        self.assertContains(response, "Input Nilai Mahasiswa")
        self.assertContains(response, "Validasi &amp; dry-run")

    def test_inspect_returns_sheets_and_header_mapping(self):
        result = inspect_grade_workbook(_workbook_bytes())

        self.assertEqual(["Nilai"], result["sheets"])
        self.assertEqual(
            ["NIM", "Kode MK", "Nilai"],
            [column["label"] for column in result["columns"]],
        )

    def test_dry_run_validates_rows_and_reports_duplicate_without_submitting(self):
        result = validate_grade_workbook(
            _workbook_bytes(),
            sheet_name="Nilai",
            header_row=1,
            nim_column=0,
            course_column=1,
            grade_column=2,
        )

        self.assertEqual(3, result["total"])
        self.assertEqual(2, result["valid"])
        self.assertEqual(1, result["invalid"])
        self.assertEqual(1, result["duplicate_rows"])
        self.assertFalse(result["submitted"])
        self.assertEqual("251030049", result["preview"][0]["nim"])
        self.assertEqual(4, result["errors"][0]["row"])

    def test_preview_api_requires_local_operator_and_does_not_write_to_ecampuz(self):
        user = User.objects.create_user("grade-operator", password="test")
        self.client.force_login(user)
        response = self.client.post(
            reverse("dashboard:api_grade_entry_preview"),
            {
                "workbook": SimpleUploadedFile(
                    "grades.xlsx", _workbook_bytes(),
                    content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                ),
                "sheet_name": "Nilai",
                "header_row": "1",
                "nim_column": "0",
                "course_column": "1",
                "grade_column": "2",
            },
            REMOTE_ADDR="127.0.0.1",
        )

        self.assertEqual(200, response.status_code)
        self.assertFalse(response.json()["report"]["submitted"])

    def test_preview_api_rejects_nonlocal_operator(self):
        user = User.objects.create_user("remote-grade-operator", password="test")
        self.client.force_login(user)
        response = self.client.post(
            reverse("dashboard:api_grade_entry_preview"),
            REMOTE_ADDR="192.0.2.10",
        )

        self.assertEqual(403, response.status_code)
        self.assertFalse(response.json()["ok"])
