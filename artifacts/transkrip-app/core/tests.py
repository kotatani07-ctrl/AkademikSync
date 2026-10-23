"""
Unit test untuk logika pencocokan nilai & parsing biodata.
Jalankan:  python manage.py test core
"""
import json
import tempfile
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from django.test import SimpleTestCase, TestCase, override_settings

from .services import feeder as feeder_service
from .services.dhs_fallback import dhs_tersedia, get_dhs_nilai_mahasiswa
from .services.matching import (
    BOBOT_NILAI,
    cocokan_nilai,
    get_bobot,
    konversi_angka_ke_lambang,
    parse_biodata,
    resolve_nilai,
)


def _mk(kode='MK101', nama='Mata Kuliah', sks=3, urutan=1):
    return SimpleNamespace(kode_mk=kode, nama_mk=nama, sks=sks, urutan_alfabet=urutan)


def _nilai(kode, lambang='', angka=''):
    return {'kode_mata_kuliah': kode, 'nilai_lambang': lambang, 'nilai_angka': angka}


class FeederTokenResponseTest(SimpleTestCase):
    def test_extract_token_from_string_data(self):
        self.assertEqual('jwt-token', feeder_service._extract_token({'data': 'jwt-token'}))

    def test_extract_token_from_nested_data(self):
        self.assertEqual('jwt-token', feeder_service._extract_token({'data': {'token': 'jwt-token'}}))

    def test_extract_token_from_top_level(self):
        self.assertEqual('jwt-token', feeder_service._extract_token({'token': 'jwt-token'}))

    @override_settings(FEEDER_HOST='feeder.local', FEEDER_USERNAME='operator', FEEDER_PASSWORD='secret')
    @mock.patch('core.services.feeder.requests.post')
    def test_connection_accepts_string_token_response(self, post):
        response = mock.Mock()
        response.json.return_value = {'data': 'jwt-token'}
        post.return_value = response
        feeder_service._conn_cache.update(ok=None, msg='', checked_at=0)

        ok, message = feeder_service.check_connection()

        self.assertTrue(ok)
        self.assertEqual('feeder.local', message)
        self.assertEqual('jwt-token', feeder_service._token_cache['token'])


class BobotNilaiTest(SimpleTestCase):
    def test_bobot_lengkap(self):
        self.assertEqual(Decimal('4.00'), get_bobot('A'))
        self.assertEqual(Decimal('3.85'), get_bobot('A-'))
        self.assertEqual(Decimal('1.00'), get_bobot('D'))
        self.assertEqual(Decimal('0.00'), get_bobot('E'))
        self.assertEqual(Decimal('0.00'), get_bobot('Z'))  # di luar tabel

    def test_bobot_case_insensitive(self):
        self.assertEqual(Decimal('3.00'), get_bobot('b'))


class KonversiAngkaTest(SimpleTestCase):
    def test_cocok_persis(self):
        self.assertEqual('A', konversi_angka_ke_lambang('4.00'))
        self.assertEqual('A-', konversi_angka_ke_lambang('3.85'))
        self.assertEqual('B', konversi_angka_ke_lambang('3.00'))
        self.assertEqual('D', konversi_angka_ke_lambang('1.00'))
        self.assertEqual('E', konversi_angka_ke_lambang('0'))

    def test_koma_desimal(self):
        self.assertEqual('B', konversi_angka_ke_lambang('3,00'))

    def test_tidak_dikenali(self):
        self.assertIsNone(konversi_angka_ke_lambang('9.99'))
        self.assertIsNone(konversi_angka_ke_lambang('abc'))


class ResolveNilaiTest(SimpleTestCase):
    def test_lambang_dikenal(self):
        r = resolve_nilai('B+', '3.25')
        self.assertTrue(r.terbaca)
        self.assertEqual(Decimal('3.25'), r.bobot)
        self.assertFalse(r.tidak_lulus)

    def test_angka_saja(self):
        r = resolve_nilai('', '3.00')
        self.assertTrue(r.terbaca)
        self.assertEqual('B', r.lambang)
        self.assertEqual(Decimal('3.00'), r.bobot)

    def test_lambang_tidak_dikenal_pakai_angka(self):
        r = resolve_nilai('A+', '4.00')
        self.assertTrue(r.terbaca)
        self.assertEqual(Decimal('4.00'), r.bobot)
        self.assertNotEqual('', r.peringatan)  # tetap diberi peringatan

    def test_tidak_terbaca(self):
        r = resolve_nilai('', '')
        self.assertFalse(r.terbaca)
        self.assertNotEqual('', r.peringatan)

    def test_e_tidak_lulus(self):
        r = resolve_nilai('E', '0')
        self.assertTrue(r.terbaca)
        self.assertTrue(r.tidak_lulus)


class ParseBiodataTest(SimpleTestCase):
    def test_tanggal_lulus_dari_riwayat_pendidikan(self):
        biodata = {'nim_mahasiswa': '2024001', 'nama_mahasiswa': 'Budi'}
        rp = {
            'tanggal_kelulusan': '2024-06-10',
            'status_mahasiswa': 'LULUS',
            'lama_studi': '14',
        }
        parsed = parse_biodata(biodata, rp)
        self.assertEqual('2024-06-10', parsed['tanggal_lulus'])
        self.assertEqual(2024, parsed['tahun_lulus'])
        self.assertEqual('LULUS', parsed['status_mahasiswa'])
        self.assertEqual('14', parsed['lama_studi'])

    def test_tanpa_riwayat_tetap_aman(self):
        biodata = {'nim_mahasiswa': '2024001', 'nama_mahasiswa': 'Budi'}
        parsed = parse_biodata(biodata)
        self.assertEqual('', parsed['tanggal_lulus'])
        self.assertEqual(0, parsed['tahun_lulus'])
        self.assertEqual('', parsed['status_mahasiswa'])


class CocokanNilaiTest(SimpleTestCase):
    def test_nilai_d_dihitung_bobot_1(self):
        daftar_mk = [_mk('MK101')]
        daftar_nilai = [_nilai('mk101', lambang='D', angka='1.00')]
        biodata = {'nim': '2024001', 'nama': 'Budi', 'jenjang': 'S1', 'angkatan': 2024}
        hasil = cocokan_nilai(daftar_mk, daftar_nilai, biodata, [])
        self.assertEqual(3, hasil.total_sks)          # SKS tetap dihitung
        self.assertEqual(Decimal('3.00'), hasil.total_mutu)  # 3 × 1.00
        self.assertEqual(Decimal('1.00'), hasil.ipk)

    def test_nilai_e_tidak_dihitung(self):
        daftar_mk = [_mk('MK101')]
        daftar_nilai = [_nilai('mk101', lambang='E', angka='0')]
        biodata = {'nim': '2024001', 'nama': 'Budi', 'jenjang': 'S1', 'angkatan': 2024}
        hasil = cocokan_nilai(daftar_mk, daftar_nilai, biodata, [])
        mk = hasil.daftar_mk[0]
        self.assertTrue(mk.is_tidak_lulus)
        self.assertEqual(0, hasil.total_sks)
        self.assertEqual(Decimal('0.00'), hasil.ipk)

    def test_nilai_tidak_terbaca_dicatat(self):
        daftar_mk = [_mk('MK101')]
        daftar_nilai = [_nilai('mk101')]  # kosong semua
        biodata = {'nim': '2024001', 'nama': 'Budi', 'jenjang': 'S1', 'angkatan': 2024}
        hasil = cocokan_nilai(daftar_mk, daftar_nilai, biodata, [])
        self.assertEqual(1, hasil.jumlah_mk_tidak_terbaca)
        self.assertEqual(0, hasil.total_sks)
        self.assertEqual(0, hasil.total_mutu)

    def test_predikat_terisi_dari_tahun_lulus(self):
        daftar_mk = [_mk('MK101'), _mk('MK202', sks=3, urutan=2)]
        daftar_nilai = [
            _nilai('mk101', lambang='A', angka='4.00'),
            _nilai('mk202', lambang='A', angka='4.00'),
        ]
        biodata = {
            'nim': '2024001', 'nama': 'Budi', 'jenjang': 'S1', 'angkatan': 2024,
            'tahun_lulus': 2024,
        }
        predikat_qs = [
            SimpleNamespace(
                tahun_lulus_mulai=2024, tahun_lulus_selesai=None,
                ipk_min=Decimal('3.50'), ipk_max=Decimal('4.00'),
                predikat='Dengan Pujian',
            )
        ]
        hasil = cocokan_nilai(daftar_mk, daftar_nilai, biodata, predikat_qs)
        self.assertEqual(Decimal('4.00'), hasil.ipk)
        self.assertEqual('Dengan Pujian', hasil.predikat)

    def test_retake_ambil_tertinggi(self):
        daftar_mk = [_mk('MK101')]
        daftar_nilai = [
            _nilai('mk101', lambang='C', angka='2.00'),
            _nilai('mk101', lambang='B', angka='3.00'),
        ]
        biodata = {'nim': '2024001', 'nama': 'Budi', 'jenjang': 'S1', 'angkatan': 2024}
        hasil = cocokan_nilai(daftar_mk, daftar_nilai, biodata, [])
        mk = hasil.daftar_mk[0]
        self.assertTrue(mk.is_retake)
        self.assertEqual('B', mk.nilai_lambang)
        self.assertEqual(Decimal('3.00'), mk.bobot)
        self.assertEqual(Decimal('3.00'), hasil.ipk)


class CocokByNamaTest(SimpleTestCase):
    def test_kode_beda_nama_sama_tetap_cocok(self):
        daftar_mk = [_mk('MK-A01', 'Pemrograman Web', sks=3)]
        # kode di Feeder beda ('WEB101') tapi nama sama
        daftar_nilai = [_nilai('web101', lambang='B', angka='3.00')]
        daftar_nilai[0]['nama_mata_kuliah'] = 'Pemrograman Web'
        biodata = {'nim': '2024001', 'nama': 'Budi', 'jenjang': 'S1', 'angkatan': 2024}
        hasil = cocokan_nilai(daftar_mk, daftar_nilai, biodata, [])
        mk = hasil.daftar_mk[0]
        self.assertTrue(mk.cocok_by_nama)
        self.assertFalse(mk.is_kosong)
        self.assertEqual('B', mk.nilai_lambang)
        self.assertEqual(3, hasil.total_sks)
        self.assertEqual(Decimal('3.00'), hasil.ipk)
        self.assertEqual(0, hasil.jumlah_mk_tidak_dikenali)

    def test_nama_dengan_aksen_tetap_cocok(self):
        daftar_mk = [_mk('MK02', 'Pendidikan Agama', sks=2)]
        daftar_nilai = [_nilai('x999', lambang='A', angka='4.00')]
        daftar_nilai[0]['nama_mata_kuliah'] = 'Pendidikan Agama Islam'
        daftar_mk2 = [_mk('MK02', 'Pendidikan Agama', sks=2)]
        hasil_beda = cocokan_nilai(daftar_mk, daftar_nilai, {}, [])
        self.assertFalse(hasil_beda.daftar_mk[0].cocok_by_nama)
        # kurikulum yang namanya persis dengan nilai → cocok
        daftar_mk2[0].nama_mk = 'Pendidikan Agama Islam'
        hasil = cocokan_nilai(daftar_mk2, daftar_nilai, {}, [])
        self.assertTrue(hasil.daftar_mk[0].cocok_by_nama)


class SksMismatchTest(SimpleTestCase):
    def test_sks_beda_dicatat(self):
        daftar_mk = [_mk('MK101', sks=3)]
        daftar_nilai = [_nilai('mk101', lambang='B', angka='3.00')]
        daftar_nilai[0]['sks_mata_kuliah'] = '2'
        biodata = {'nim': '2024001', 'nama': 'Budi', 'jenjang': 'S1', 'angkatan': 2024}
        hasil = cocokan_nilai(daftar_mk, daftar_nilai, biodata, [])
        mk = hasil.daftar_mk[0]
        self.assertEqual(2, mk.sks_api)
        self.assertEqual(1, hasil.jumlah_mk_sks_beda)
        self.assertIn('SKS Feeder 2 ≠ SKS kurikulum 3', mk.catatan)


class MkTidakDikenaliTest(SimpleTestCase):
    def _setup(self):
        daftar_mk = [_mk('MK101', sks=3)]
        daftar_nilai = [
            _nilai('mk101', lambang='C', angka='2.00'),
            _nilai('elek101', lambang='A', angka='4.00'),
        ]
        daftar_nilai[1]['nama_mata_kuliah'] = 'Elektif'
        daftar_nilai[1]['sks_mata_kuliah'] = '3'
        biodata = {'nim': '2024001', 'nama': 'Budi', 'jenjang': 'S1', 'angkatan': 2024}
        return daftar_mk, daftar_nilai, biodata

    def test_mk_tak_dikenali_ikut_dihitung_default(self):
        daftar_mk, daftar_nilai, biodata = self._setup()
        hasil = cocokan_nilai(daftar_mk, daftar_nilai, biodata, [])
        self.assertTrue(hasil.mk_tidak_dikenali_dihitung)
        self.assertEqual(1, hasil.jumlah_mk_tidak_dikenali)
        self.assertEqual(6, hasil.total_sks)             # 3 + 3 elektif
        self.assertEqual(Decimal('3.00'), hasil.ipk)     # (6 + 12) / 6

    def test_mk_tak_dikenali_tidak_dihitung_bila_off(self):
        daftar_mk, daftar_nilai, biodata = self._setup()
        hasil = cocokan_nilai(daftar_mk, daftar_nilai, biodata, [],
                              include_mk_tidak_dikenali=False)
        self.assertFalse(hasil.mk_tidak_dikenali_dihitung)
        self.assertEqual(1, hasil.jumlah_mk_tidak_dikenali)
        self.assertEqual(3, hasil.total_sks)
        self.assertEqual(Decimal('2.00'), hasil.ipk)


class SumberNilaiTest(SimpleTestCase):
    """Penanda sumber nilai (feeder normal vs fallback DHS eCampuz)."""

    def _hasil(self, **kwargs):
        daftar_mk = [_mk('MK101')]
        daftar_nilai = [_nilai('mk101', lambang='B', angka='3.00')]
        biodata = {'nim': '2024001', 'nama': 'Budi', 'jenjang': 'S1', 'angkatan': 2024}
        return cocokan_nilai(daftar_mk, daftar_nilai, biodata, [], **kwargs)

    def test_default_feeder(self):
        self.assertEqual('feeder', self._hasil().sumber_nilai)

    def test_ditandai_dhs(self):
        self.assertEqual('dhs', self._hasil(sumber_nilai='dhs').sumber_nilai)


class DhsFallbackTest(SimpleTestCase):
    """Konversi hasil scraping DHS eCampuz ke format nilai yang dikenali cocokan_nilai."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _tulis(self, nim, data):
        path = self.dir / f"dhs_{nim}.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def test_file_tidak_ada_mengembalikan_none(self):
        self.assertFalse(dhs_tersedia('999999', out_dir=self.dir))
        self.assertIsNone(get_dhs_nilai_mahasiswa('999999', out_dir=self.dir))

    def test_json_rusak_mengembalikan_none(self):
        (self.dir / 'dhs_111111.json').write_text('{bukan json', encoding='utf-8')
        self.assertIsNone(get_dhs_nilai_mahasiswa('111111', out_dir=self.dir))

    def test_kosong_tanpa_course_mengembalikan_list_kosong(self):
        self._tulis('100002', {'identity': {}, 'semesters': []})
        self.assertEqual([], get_dhs_nilai_mahasiswa('100002', out_dir=self.dir))

    def test_konversi_course_ke_format_nilai_api(self):
        self._tulis('241010200', {
            'identity': {'nim': '241010200', 'nama': 'Budi'},
            'semesters': [{
                'semester': 1,
                'courses': [
                    {'kode': 'FKS14208', 'mata_kuliah': 'Bahasa Jepang Dasar',
                     'sks': 2.0, 'nilai': 'A', 'bobot': 4.0},
                    {'kode': 'FKS10101', 'mata_kuliah': 'Matematika',
                     'sks': 3.0, 'nilai': 'B+', 'bobot': 3.25},
                    {'kode': 'FKS20202', 'mata_kuliah': 'Retake Course',
                     'sks': 3.0, 'nilai': '', 'bobot': 2.0},
                ],
            }],
        })
        daftar = get_dhs_nilai_mahasiswa('241010200', out_dir=self.dir)
        self.assertIsNotNone(daftar)
        self.assertEqual(3, len(daftar))
        r0 = daftar[0]
        self.assertEqual('FKS14208', r0['kode_mata_kuliah'])
        self.assertEqual('Bahasa Jepang Dasar', r0['nama_mata_kuliah'])
        self.assertEqual('A', r0['nilai_lambang'])
        self.assertEqual('4.00', r0['nilai_angka'])
        self.assertEqual(2, r0['sks_mata_kuliah'])
        self.assertEqual('B+', daftar[1]['nilai_lambang'])
        self.assertEqual('', daftar[2]['nilai_lambang'])
        self.assertEqual('2.00', daftar[2]['nilai_angka'])
        self.assertEqual(3, daftar[2]['sks_mata_kuliah'])

    def test_nilai_numerik_ditaruh_di_angka(self):
        self._tulis('100001', {'semesters': [{'courses': [
            {'kode': 'MK1', 'mata_kuliah': 'Web', 'sks': 3, 'nilai': '3.85', 'bobot': 3.85}
        ]}]})
        daftar = get_dhs_nilai_mahasiswa('100001', out_dir=self.dir)
        self.assertEqual(1, len(daftar))
        self.assertEqual('', daftar[0]['nilai_lambang'])
        self.assertEqual('3.85', daftar[0]['nilai_angka'])

    def test_course_tanpa_nilai_dan_bobot_dilewati(self):
        self._tulis('100003', {'semesters': [{'courses': [
            {'kode': 'MK1', 'mata_kuliah': 'Kosong', 'sks': 3, 'nilai': '', 'bobot': ''},
            {'kode': 'MK2', 'mata_kuliah': 'Terisi', 'sks': 2, 'nilai': 'C', 'bobot': 2.0},
        ]}]})
        daftar = get_dhs_nilai_mahasiswa('100003', out_dir=self.dir)
        self.assertEqual(1, len(daftar))
        self.assertEqual('MK2', daftar[0]['kode_mata_kuliah'])


class LaporanSumberDhsTest(SimpleTestCase):
    """Catatan 'SUMBER DHS' muncul di sheet Laporan bila nilai dari fallback."""

    def test_catatan_sumber_dhs_di_sheet_laporan(self):
        import openpyxl

        from .services.excel_gen import buat_sheet_laporan

        hasil = cocokan_nilai(
            [], [], {'nim': '2024001', 'nama': 'Budi', 'jenjang': 'S1', 'angkatan': 2024},
            [], sumber_nilai='dhs',
        )
        wb = openpyxl.Workbook()
        wb.remove(wb.active)
        ws = buat_sheet_laporan(wb, hasil)
        kolom_status = [r[7] for r in ws.iter_rows(values_only=True)]
        self.assertIn('SUMBER DHS', kolom_status)

    def test_tanpa_catatan_bila_sumber_feeder(self):
        import openpyxl

        from .services.excel_gen import buat_sheet_laporan

        hasil = cocokan_nilai(
            [], [], {'nim': '2024001', 'nama': 'Budi', 'jenjang': 'S1', 'angkatan': 2024}, [],
        )
        wb = openpyxl.Workbook()
        wb.remove(wb.active)
        ws = buat_sheet_laporan(wb, hasil)
        kolom_status = [r[7] for r in ws.iter_rows(values_only=True)]
        self.assertNotIn('SUMBER DHS', kolom_status)


class ProsesSatuNimDhsFallbackTest(TestCase):
    """Wiring _proses_satu_nim: fallback DHS aktif saat Feeder kosong."""

    @classmethod
    def setUpTestData(cls):
        from django.contrib.auth.models import User

        from .models import Kurikulum, MataKuliahKurikulum, Template

        cls.user = User.objects.create_user('admin', password='x')
        tpl = Template.objects.create(
            nama='Template S1 2024', total_mk=2, baris_halaman_depan=2,
            cell_mapping={}, file_excel='templates/dummy.xlsx',
        )
        cls.kur = Kurikulum.objects.create(jenjang='S1', angkatan=2024, template=tpl)
        cls.kur_informatika = Kurikulum.objects.create(
            jenjang='S1', prodi='INFORMATIKA', angkatan=2024,
            template=Template.objects.create(
                nama='Template Informatika', total_mk=2, baris_halaman_depan=2,
                cell_mapping={}, file_excel='templates/informatika.xlsx',
            ),
        )
        cls.kur_ners = Kurikulum.objects.create(
            jenjang='PROF', prodi='PROFESI_NERS', angkatan=2024,
            template=Template.objects.create(
                nama='Template Profesi Ners', total_mk=2, baris_halaman_depan=2,
                cell_mapping={}, file_excel='templates/ners.xlsx',
            ),
        )
        MataKuliahKurikulum.objects.create(
            kurikulum=cls.kur, kode_mk='FKS14208', nama_mk='Bahasa Jepang Dasar',
            sks=2, urutan_alfabet=1,
        )
        MataKuliahKurikulum.objects.create(
            kurikulum=cls.kur, kode_mk='FKS99999', nama_mk='MK Lain di Kurikulum',
            sks=3, urutan_alfabet=2,
        )

    def _daftar_dhs(self):
        return [
            {'kode_mata_kuliah': 'FKS14208', 'nama_mata_kuliah': 'Bahasa Jepang Dasar',
             'nilai_lambang': 'A', 'nilai_angka': '4.00', 'sks_mata_kuliah': 2},
        ]

    def _panggil(self, nilai_feeder=(), daftar_dhs=(), fallback_on=True,
                 prodi='', jenjang='S1', nama_prodi='S1 Keperawatan'):
        biodata = {
            'nim': '241010200', 'nama_mahasiswa': 'Budi', 'jenjang': jenjang,
            'tahun_masuk': 2024, 'nama_program_studi': nama_prodi,
        }
        with mock.patch(
                'core.services.feeder.get_biodata_mahasiswa', return_value=biodata), \
             mock.patch(
                'core.services.feeder.get_riwayat_pendidikan', return_value={}), \
             mock.patch(
                'core.services.feeder.get_nilai_perkuliahan_mahasiswa',
                return_value=list(nilai_feeder)), \
             mock.patch(
                'core.views.get_dhs_nilai_mahasiswa', return_value=list(daftar_dhs)), \
             self.settings(FALLBACK_DHS_SAAT_FEEDER_KOSONG=fallback_on):
            from .views import _proses_satu_nim
            return _proses_satu_nim('241010200', 'single', self.user, prodi)

    def test_prodi_terpilih_menggunakan_kurikulum_prodi(self):
        _, _, template = self._panggil(prodi='INFORMATIKA')
        self.assertEqual('Template Informatika', template.nama)

    def test_profesi_ners_menggunakan_kurikulum_profesi(self):
        _, _, template = self._panggil(prodi='PROFESI_NERS')
        self.assertEqual('Template Profesi Ners', template.nama)

    def test_profesi_ners_otomatis_dari_biodata_feeder(self):
        _, _, template = self._panggil(jenjang='Profesi', nama_prodi='Profesi Ners')
        self.assertEqual('Template Profesi Ners', template.nama)

    def test_fallback_dhs_dipakai_saat_feeder_kosong(self):
        hasil, _, _ = self._panggil(nilai_feeder=[], daftar_dhs=self._daftar_dhs())
        self.assertEqual('dhs', hasil.sumber_nilai)
        mk = next(m for m in hasil.daftar_mk if m.kode_mk == 'FKS14208')
        self.assertFalse(mk.is_kosong)
        self.assertEqual('A', mk.nilai_lambang)
        self.assertGreater(hasil.total_sks, 0)

    def test_sumber_feeder_tetap_diprioritaskan(self):
        nilai_feeder = [
            {'kode_mata_kuliah': 'FKS14208', 'nama_mata_kuliah': 'Bahasa Jepang Dasar',
             'nilai_lambang': 'B', 'nilai_angka': '3.00', 'sks_mata_kuliah': 2},
        ]
        hasil, _, _ = self._panggil(nilai_feeder=nilai_feeder, daftar_dhs=self._daftar_dhs())
        self.assertEqual('feeder', hasil.sumber_nilai)
        mk = next(m for m in hasil.daftar_mk if m.kode_mk == 'FKS14208')
        self.assertEqual('B', mk.nilai_lambang)

    def test_fallback_dimatikan_tetap_feeder(self):
        hasil, _, _ = self._panggil(nilai_feeder=[], daftar_dhs=self._daftar_dhs(),
                                    fallback_on=False)
        self.assertEqual('feeder', hasil.sumber_nilai)
        mk = next(m for m in hasil.daftar_mk if m.kode_mk == 'FKS14208')
        self.assertTrue(mk.is_kosong)


class GenerateBatchCancellationTest(TestCase):
    def test_cancel_stops_before_next_nim_and_returns_partial_excel(self):
        import uuid
        from types import SimpleNamespace
        from unittest import mock

        from django.contrib.auth.models import User
        from django.core.files.uploadedfile import SimpleUploadedFile
        from django.urls import reverse

        from .models import BatchJob

        user = User.objects.create_user('batch-user', password='test')
        self.client.force_login(user)
        batch_id = uuid.uuid4()
        processed_nims = []
        result = SimpleNamespace(
            nama='Budi',
            jumlah_mk_kosong=0,
            jumlah_mk_retake=0,
            jumlah_mk_tidak_dikenali=0,
            sumber_nilai='feeder',
            ipk=Decimal('3.50'),
        )
        template = SimpleNamespace(
            file_excel=SimpleNamespace(path='template.xlsx'),
            cell_mapping={},
        )

        def process_nim(nim, *args):
            processed_nims.append(nim)
            BatchJob.objects.filter(pk=batch_id).update(status=BatchJob.STATUS_CANCELLED)
            return result, None, template

        with mock.patch('core.views._proses_satu_nim', side_effect=process_nim), \
             mock.patch('core.views.generate_batch_excel', return_value=b'partial excel'), \
             mock.patch('core.views.time.sleep'):
            response = self.client.post(
                reverse('generate_batch'),
                {
                    'batch_id': str(batch_id),
                    'file_nim': SimpleUploadedFile(
                        'nim.txt', b'123456789\n987654321\n', content_type='text/plain'
                    ),
                },
            )

        self.assertEqual(['123456789'], processed_nims)
        self.assertEqual(b'partial excel', response.content)
        self.assertEqual(
            BatchJob.STATUS_CANCELLED,
            BatchJob.objects.get(pk=batch_id).status,
        )

    def test_cancel_endpoint_only_cancels_owner_batch(self):
        from django.contrib.auth.models import User
        from django.urls import reverse

        from .models import BatchJob

        owner = User.objects.create_user('batch-owner', password='test')
        other_user = User.objects.create_user('other-user', password='test')
        batch = BatchJob.objects.create(requested_by=owner)
        self.client.force_login(owner)

        response = self.client.post(
            reverse('cancel_generate_batch', args=[batch.pk])
        )

        self.assertEqual(200, response.status_code)
        self.assertTrue(response.json()['cancelled'])
        batch.refresh_from_db()
        self.assertEqual(BatchJob.STATUS_CANCELLED, batch.status)

        self.client.force_login(other_user)
        response = self.client.post(
            reverse('cancel_generate_batch', args=[batch.pk])
        )
        self.assertEqual(404, response.status_code)

    def test_batch_generate_returns_zip_package(self):
        import io
        import uuid
        import zipfile
        from types import SimpleNamespace
        from unittest import mock

        from django.contrib.auth.models import User
        from django.core.files.uploadedfile import SimpleUploadedFile
        from django.urls import reverse

        user = User.objects.create_user('batch-zip-user', password='test')
        self.client.force_login(user)
        batch_id = uuid.uuid4()
        result = SimpleNamespace(
            nim='241010001',
            nama='Ahmad',
            prodi='Ilmu Keperawatan',
            jenjang='S1',
            angkatan=2024,
            tempat_lahir='Jakarta',
            tanggal_lahir='01 Jan 2002',
            tanggal_lulus='28 Feb 2026',
            daftar_mk=[],
            total_sks=0,
            ipk=Decimal('3.50'),
            jumlah_mk_kosong=0,
            jumlah_mk_retake=0,
            jumlah_mk_tidak_dikenali=0,
            sumber_nilai='feeder',
        )
        template = SimpleNamespace(
            file_excel=SimpleNamespace(path='dummy.xlsx'),
            cell_mapping={'engine': 'manual'},
        )

        with mock.patch('core.views._proses_satu_nim', return_value=(result, None, template)), \
             mock.patch('core.views.generate_batch_excel', return_value=b'rekap content'), \
             mock.patch('core.views.generate_single_excel', return_value=b'single content'):
            response = self.client.post(
                reverse('generate_batch'),
                {
                    'batch_id': str(batch_id),
                    'format_output': 'zip_xlsx',
                    'file_nim': SimpleUploadedFile(
                        'nim.txt', b'241010001\n', content_type='text/plain'
                    ),
                },
            )

        self.assertEqual(200, response.status_code)
        self.assertEqual('application/zip', response['Content-Type'])
        with zipfile.ZipFile(io.BytesIO(response.content)) as zf:
            namelist = zf.namelist()
            self.assertTrue(any('00_Rekapitulasi_Batch' in n for n in namelist))
            self.assertTrue(any('Transkrip_241010001_Ahmad.xlsx' in n for n in namelist))
            self.assertEqual(b'rekap content', zf.read(namelist[0]))

    def test_single_generate_supports_pdf_format(self):
        from types import SimpleNamespace
        from unittest import mock

        from django.contrib.auth.models import User
        from django.urls import reverse

        user = User.objects.create_user('single-pdf-user', password='test')
        self.client.force_login(user)
        result = SimpleNamespace(
            nim='241010002',
            nama='Fauzi',
            prodi='Ilmu Keperawatan',
            jenjang='S1',
            angkatan=2024,
            tempat_lahir='Jakarta',
            tanggal_lahir='01 Jan 2002',
            tanggal_lulus='28 Feb 2026',
            daftar_mk=[],
            total_sks=0,
            ipk=Decimal('3.50'),
        )
        template = SimpleNamespace(
            file_excel=SimpleNamespace(path='dummy.xlsx'),
            cell_mapping={},
        )

        with mock.patch('core.views._proses_satu_nim', return_value=(result, None, template)), \
             mock.patch('core.views.generate_single_excel', return_value=b'excel content'), \
             mock.patch('dashboard.services.transcript_export.convert_excel_to_pdf', return_value=b'%PDF-1.4 mock'):
            response = self.client.post(
                reverse('generate_single'),
                {
                    'nim': '241010002',
                    'prodi': '',
                    'format_output': 'pdf',
                },
            )

        self.assertEqual(200, response.status_code)
        self.assertEqual('application/pdf', response['Content-Type'])
        self.assertEqual(b'%PDF-1.4 mock', response.content)

    def test_batch_generate_async_starts_background_job(self):
        import uuid
        from unittest import mock
        from django.contrib.auth.models import User
        from django.core.files.uploadedfile import SimpleUploadedFile
        from django.urls import reverse

        user = User.objects.create_user('async-batch-user', password='test')
        self.client.force_login(user)
        batch_id = uuid.uuid4()

        with mock.patch('core.views.threading.Thread') as mock_thread:
            response = self.client.post(
                reverse('generate_batch'),
                {
                    'batch_id': str(batch_id),
                    'format_output': 'zip_xlsx',
                    'is_async': '1',
                    'file_nim': SimpleUploadedFile(
                        'nim.txt', b'241010001\n241010002\n', content_type='text/plain'
                    ),
                },
            )

        self.assertEqual(200, response.status_code)
        data = response.json()
        self.assertTrue(data.get('ok'))
        self.assertEqual(str(batch_id), data.get('batch_id'))
        self.assertEqual(2, data.get('total'))
        self.assertTrue(mock_thread.called)

    def test_batch_status_endpoint_returns_progress(self):
        import uuid
        from django.contrib.auth.models import User
        from django.urls import reverse
        from core.views import BATCH_JOBS_CACHE, BATCH_JOBS_LOCK

        user = User.objects.create_user('batch-status-user', password='test')
        self.client.force_login(user)
        batch_id = uuid.uuid4()

        with BATCH_JOBS_LOCK:
            BATCH_JOBS_CACHE[batch_id] = {
                'status': 'running',
                'total': 10,
                'processed': 5,
                'percent': 50,
                'current_nim': '241010005',
                'success_count': 4,
                'error_count': 1,
                'results': [{'nim': '241010001', 'nama': 'Test', 'status': 'success'}],
                'download_ready': False,
                'error_message': None,
            }

        response = self.client.get(reverse('batch_status', args=[batch_id]))
        self.assertEqual(200, response.status_code)
        data = response.json()
        self.assertEqual('running', data['status'])
        self.assertEqual(50, data['percent'])
        self.assertEqual(5, data['processed'])
        self.assertEqual('241010005', data['current_nim'])

        # Not found case
        response_404 = self.client.get(reverse('batch_status', args=[uuid.uuid4()]))
        self.assertEqual(404, response_404.status_code)

    def test_batch_download_endpoint_returns_file_when_ready(self):
        import uuid
        from django.contrib.auth.models import User
        from django.urls import reverse
        from core.views import BATCH_JOBS_CACHE, BATCH_JOBS_LOCK

        user = User.objects.create_user('batch-download-user', password='test')
        self.client.force_login(user)
        batch_id = uuid.uuid4()

        with BATCH_JOBS_LOCK:
            BATCH_JOBS_CACHE[batch_id] = {
                'status': 'completed',
                'download_ready': True,
                'file_bytes': b'PK mock zip package',
                'filename': 'Paket_Transkrip_Batch.zip',
                'content_type': 'application/zip',
            }

        response = self.client.get(reverse('batch_download', args=[batch_id]))
        self.assertEqual(200, response.status_code)
        self.assertEqual('application/zip', response['Content-Type'])
        self.assertIn('Paket_Transkrip_Batch.zip', response['Content-Disposition'])
        self.assertEqual(b'PK mock zip package', response.content)

        # Not ready / not found case
        response_404 = self.client.get(reverse('batch_download', args=[uuid.uuid4()]))
        self.assertEqual(404, response_404.status_code)

    def test_preview_single_redirects_if_empty_nim(self):
        from django.contrib.auth.models import User
        from django.urls import reverse

        user = User.objects.create_user('preview-empty-user', password='test')
        self.client.force_login(user)

        response = self.client.get(reverse('preview_single'))
        self.assertEqual(302, response.status_code)
        self.assertIn(reverse('generate_single'), response['Location'])

    def test_preview_single_renders_transcript(self):
        from decimal import Decimal
        from types import SimpleNamespace
        from unittest import mock
        from django.contrib.auth.models import User
        from django.urls import reverse

        user = User.objects.create_user('preview-view-user', password='test')
        self.client.force_login(user)
        nim = '241010008'
        mk1 = SimpleNamespace(
            kode_mk='MK101',
            nama_mk='Etika Keperawatan',
            sks=2,
            nilai_lambang='A',
            bobot=Decimal('4.00'),
            mutu=Decimal('8.00'),
            is_retake=False,
            is_kosong=False,
            is_tidak_lulus=False,
        )
        hasil = SimpleNamespace(
            nim=nim,
            nama='Rina Fitriani',
            prodi='Ilmu Keperawatan',
            jenjang='S1',
            angkatan=2024,
            tempat_lahir='Tangerang',
            tanggal_lahir='15 Mei 2002',
            tanggal_lulus='28 Feb 2026',
            status_mahasiswa='LULUS',
            total_sks=2,
            total_mutu=Decimal('8.00'),
            ipk=Decimal('4.00'),
            predikat='Dengan Pujian',
            daftar_mk=[mk1],
            mk_tidak_dikenali=[],
            jumlah_mk_retake=0,
            jumlah_mk_kosong=0,
            jumlah_mk_tidak_dikenali=0,
            sumber_nilai='feeder',
        )
        data_pendukung = SimpleNamespace(
            nomor_ijazah_nasional='PIN-12345',
            nomor_sk_akreditasi='SK-Akred-99',
            judul_skripsi='Asuhan Keperawatan Pasien',
        )
        template = SimpleNamespace(file_excel=SimpleNamespace(path='dummy.xlsx'), cell_mapping={})

        with mock.patch('core.views._proses_satu_nim', return_value=(hasil, data_pendukung, template)):
            response = self.client.get(reverse('preview_single'), {'nim': nim})

        self.assertEqual(200, response.status_code)
        self.assertTemplateUsed(response, 'preview_single.html')
        content = response.content.decode('utf-8')
        self.assertIn('Rina Fitriani', content)
        self.assertIn('241010008', content)
        self.assertIn('Etika Keperawatan', content)
        self.assertIn('PIN-12345', content)
        self.assertIn('Dengan Pujian', content)

    def test_generate_single_preview_action_redirects(self):
        from django.contrib.auth.models import User
        from django.urls import reverse

        user = User.objects.create_user('gen-single-preview-user', password='test')
        self.client.force_login(user)

        response = self.client.post(
            reverse('generate_single'),
            {
                'nim': '241010009',
                'prodi': 'ILMU_KEPERAWATAN',
                'format_output': 'xlsx',
                'action': 'preview',
            },
        )
        self.assertEqual(302, response.status_code)
        self.assertIn(reverse('preview_single'), response['Location'])
        self.assertIn('nim=241010009', response['Location'])