"""
Management command: seed master data (Template, Kurikulum, MK, DataPendukung,
PredikatKelulusan) dari workbook transkrip kampus `Template_Transkrip_2026_FIXED (5) - FILLED.xlsx`.

Pemakaian:
  python manage.py seed_master_data [--excel <path>]

Sumber data (dalam workbook):
  - 'Kurikulum'           → kurikulum S1 2021 (kode P21xxx, A51-D51) & S1 2022 (P22xxx, A54-D105)
  - 'Nilai MHS 2026'      → kurikulum S1 2024 (kode P24xxx, distinct by kode)
  - 'Mahasiswa 2026'      → DataPendukung (NIM, judul skripsi/LTA, no ijazah)
  - sheet 'TRANSKRIP NILAI *' → 3 Template, dirender via engine workbook (formula bawaan)

Catatan:
  - Profesi Ners TIDAK diimpor (sesuai permintaan pemilik data).
  - Command idempoten: aman dijalankan ulang.
"""
from pathlib import Path

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.models import (
    DataPendukung, Kurikulum, MataKuliahKurikulum, PredikatKelulusan, Template,
)

DEFAULT_EXCEL = Path('data/ecampuz/Template_Transkrip_2026_FIXED (5) - FILLED.xlsx')

CURRICULA = [
    # (nama_template, total_mk, jenjang, prodi, angkatan, sumber_mk)
    (
        'Transkrip 2026 – S1 Keperawatan (P21)', 50,
        'S1', 'ILMU_KEPERAWATAN', 2021, 'kurikulum_sheet',
    ),
    (
        'Transkrip 2026 – S1 Keperawatan (P22)', 51,
        'S1', 'ILMU_KEPERAWATAN', 2022, 'kurikulum_sheet',
    ),
    (
        'Transkrip 2026 – S1 Keperawatan (2024)', 55,
        'S1', 'ILMU_KEPERAWATAN', 2024, 'nilai_sheet',
    ),
]

PREDIKAT = [
    # (tahun_mulai, ipk_min, ipk_max, predikat)
    (2026, '3.51', '4.00', 'Dengan Pujian (Cum Laude)'),
    (2026, '3.01', '3.50', 'Sangat Memuaskan'),
    (2026, '2.76', '3.00', 'Memuaskan'),
    (2026, '2.00', '2.75', 'Cukup'),
]


def _sel(wb, sheet):
    return wb[sheet]


def _cells(row):
    return list(row)


def _teks(v):
    if v is None:
        return ''
    return str(v).strip()


def _angka(v):
    if v is None:
        return None
    try:
        return int(round(float(v)))
    except (TypeError, ValueError):
        return None


class Command(BaseCommand):
    help = 'Impor Template, Kurikulum, MK, DataPendukung & PredikatKelulusan dari workbook transkrip.'

    def add_arguments(self, parser):
        parser.add_argument('--excel', default=None, help='Path workbook (default: <BASE_DIR>/data/ecampuz/...FILLED.xlsx)')

    def _load_workbook(self, path: Path):
        import openpyxl
        if not path.is_file():
            raise CommandError(f'Workbook tidak ditemukan: {path}')
        return openpyxl.load_workbook(path, data_only=True)

    def _copy_template_file(self) -> str:
        """Salin workbook ke media/templates/ dan kembalikan nama file Django-nya."""
        from shutil import copyfile

        src = Path(self.excel).resolve()
        rel_name = f'templates/{src.name}'
        dst = Path(settings.MEDIA_ROOT) / rel_name
        if not dst.is_file():
            dst.parent.mkdir(parents=True, exist_ok=True)
            copyfile(src, dst)
            self.stdout.write(f'  media: salin -> {rel_name}')
        else:
            self.stdout.write(f'  media: sudah ada -> {rel_name}')
        return rel_name

    def _mk_dari_sheet_kurikulum(self, ws, baris_mulai: int, baris_akhir: int):
        mk = []
        for r in range(baris_mulai, baris_akhir + 1):
            kode = _teks(ws.cell(r, 2).value)   # B = kd mtkull
            nama = _teks(ws.cell(r, 3).value)   # C = matkul
            sks = _angka(ws.cell(r, 4).value)   # D = sks
            if kode and nama:
                mk.append({'kode': kode, 'nama': nama, 'sks': sks or 0})
        return mk

    def _mk_dari_nilai_sheet(self, ws):
        seen = {}
        for r in range(2, ws.max_row + 1):
            kode = _teks(ws.cell(r, 4).value)      # kolom 4 = Kode MK
            if not kode:
                continue
            nama = _teks(ws.cell(r, 5).value)      # kolom 5 = MATAKULIAH ID
            sks = _angka(ws.cell(r, 7).value)      # kolom 7 = Bobot MK
            if kode not in seen:
                seen[kode] = {'kode': kode, 'nama': nama or kode, 'sks': sks or 0}
        return list(seen.values())

    def _upsert_kurikulum(self, nama_template, total_mk, jenjang, prodi, angkatan, mk):
        template = Template.objects.filter(nama=nama_template).first()
        if template is None:
            raise CommandError(f'Template belum dibuat: {nama_template}')

        kurikulum, created = Kurikulum.objects.get_or_create(
            jenjang=jenjang, prodi=prodi, angkatan=angkatan,
            defaults={'template': template},
        )
        if not created and kurikulum.template_id != template.id:
            kurikulum.template = template
            kurikulum.save(update_fields=['template'])

        with transaction.atomic():
            existing = {
                m.kode_mk.strip().lower(): m for m in kurikulum.matakuliah.all()
            }
            baru = diperbarui = 0
            for i, item in enumerate(mk):
                kunci = item['kode'].strip().lower()
                m = existing.pop(kunci, None)
                if m is None:
                    MataKuliahKurikulum.objects.create(
                        kurikulum=kurikulum,
                        kode_mk=item['kode'],
                        nama_mk=item['nama'],
                        sks=item['sks'],
                        urutan_alfabet=i,
                    )
                    baru += 1
                elif m.nama_mk != item['nama'] or m.sks != item['sks']:
                    m.nama_mk = item['nama']
                    m.sks = item['sks']
                    m.save(update_fields=['nama_mk', 'sks'])
                    diperbarui += 1
            hapus = list(existing.values())
            if hapus:
                MataKuliahKurikulum.objects.filter(pk__in=[m.pk for m in hapus]).delete()
            # urutan alfabetis ulang agar konsisten
            for i, m in enumerate(
                kurikulum.matakuliah.all().order_by('nama_mk')
            ):
                if m.urutan_alfabet != i:
                    m.urutan_alfabet = i
                    m.save(update_fields=['urutan_alfabet'])

        status = 'dibuat' if created else 'diperbarui'
        self.stdout.write(self.style.SUCCESS(
            f'  Kurikulum {jenjang} {prodi} {angkatan}: {status}, '
            f'{kurikulum.matakuliah.count()} MK (+{baru} baru, ~{diperbarui} ubah, '
            f'{len(hapus)} hapus), template={nama_template!r}'
        ))

    def handle(self, *args, **opts):
        source = opts['excel'] or (settings.BASE_DIR / DEFAULT_EXCEL)
        self.excel = Path(source)
        wb = self._load_workbook(self.excel)
        self.stdout.write(f'Workbook: {self.excel}')
        self.stdout.write('Sheets: ' + ', '.join(wb.sheetnames))

        template_file = self._copy_template_file()

        # ── 1. Template ──────────────────────────────────────────────
        self.stdout.write('\n[1/4] Template')
        for nama, total_mk, *_ in CURRICULA:
            obj, _ = Template.objects.get_or_create(
                nama=nama,
                defaults={
                    'file_excel': template_file,
                    'total_mk': total_mk,
                    'baris_halaman_depan': 30,
                    'cell_mapping': {'engine': 'workbook'},
                },
            )
            if obj.file_excel.name != template_file:
                obj.file_excel.save(template_file, ContentFile(
                    (Path(settings.MEDIA_ROOT) / template_file).read_bytes()), save=False)
                obj.total_mk = total_mk
                obj.cell_mapping = {'engine': 'workbook'}
                obj.save()
            self.stdout.write(f'  {nama}: total_mk={total_mk} (engine workbook)')

        # ── 2. Kurikulum + MK ────────────────────────────────────────
        self.stdout.write('\n[2/4] Kurikulum & Mata Kuliah')
        ws_kur = _sel(wb, 'Kurikulum')
        ws_nilai = _sel(wb, 'Nilai MHS 2026')

        mk_p21 = self._mk_dari_sheet_kurikulum(ws_kur, 2, 51)
        mk_p22 = self._mk_dari_sheet_kurikulum(ws_kur, 55, 105)
        mk_p24 = self._mk_dari_nilai_sheet(ws_nilai)

        self.stdout.write(f'  MK dari sheet Kurikulum: P21={len(mk_p21)}, P22={len(mk_p22)}')
        self.stdout.write(f'  MK dari sheet Nilai MHS 2026 (distinct): P24={len(mk_p24)}')

        for nama, total_mk, jenjang, prodi, angkatan, sumber in CURRICULA:
            mk = {'kurikulum_sheet': mk_p21 if angkatan == 2021 else mk_p22,
                  'nilai_sheet': mk_p24}[sumber]
            self._upsert_kurikulum(nama, total_mk, jenjang, prodi, angkatan, mk)

        # ── 3. Data pendukung ────────────────────────────────────────
        self.stdout.write('\n[3/4] DataPendukung (Mahasiswa 2026)')
        ws_roster = _sel(wb, 'Mahasiswa 2026')
        dibaca = dibuat = diperbarui = 0
        for r in range(2, ws_roster.max_row + 1):
            nim = _teks(ws_roster.cell(r, 3).value)   # C = NIM
            nama = _teks(ws_roster.cell(r, 5).value)  # E = Nama Lengkap
            judul = _teks(ws_roster.cell(r, 8).value)  # H = LTA (judul skripsi)
            no_ijazah = _teks(ws_roster.cell(r, 9).value)  # I = No ijazah
            if not (nim.isdigit() and nama):
                continue
            dibaca += 1
            dp, created = DataPendukung.objects.get_or_create(
                nim=nim,
                defaults={'judul_skripsi': judul, 'no_ijazah': no_ijazah},
            )
            if created:
                dibuat += 1
            elif dp.judul_skripsi != judul or dp.no_ijazah != no_ijazah:
                dp.judul_skripsi = judul
                dp.no_ijazah = no_ijazah
                dp.save(update_fields=['judul_skripsi', 'no_ijazah'])
                diperbarui += 1
        self.stdout.write(f'  roster: {dibaca} NIM valid, {dibuat} baru, {diperbarui} diperbarui')

        # ── 4. Predikat kelulusan ────────────────────────────────────
        self.stdout.write('\n[4/4] PredikatKelulusan')
        for tahun, ipk_min, ipk_max, predikat in PREDIKAT:
            obj, created = PredikatKelulusan.objects.get_or_create(
                tahun_lulus_mulai=tahun,
                ipk_min=ipk_min,
                ipk_max=ipk_max,
                defaults={'predikat': predikat},
            )
            if not created and obj.predikat != predikat:
                obj.predikat = predikat
                obj.save(update_fields=['predikat'])
            self.stdout.write(f'  {predikat} (IPK {ipk_min}-{ipk_max}, {tahun}+)')

        self.stdout.write(self.style.SUCCESS('\nSelesai. Master data siap digunakan.'))