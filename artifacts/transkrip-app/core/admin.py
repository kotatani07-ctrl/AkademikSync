"""
Django Admin untuk data master sistem transkrip.
Fitur: upload Excel kurikulum (auto-parse), upload template, dsb.
"""
from django import forms
from django.contrib import admin, messages
from django.db import transaction
from django.shortcuts import redirect, render
from django.urls import path
from django.utils.html import format_html

from .models import (
    BatchJob,
    DataPendukung,
    Kurikulum,
    MataKuliahKurikulum,
    PredikatKelulusan,
    RiwayatGenerate,
    Template,
)


def parse_excel_kurikulum(file_obj):
    """
    Parse file Excel kurikulum.
    Ambil HANYA kolom Kode MK, Mata Kuliah, SKS.
    Header lain diabaikan. Auto-sort alfabetis by nama_mk.
    Return list of dict {'kode_mk', 'nama_mk', 'sks'}.
    """
    import openpyxl
    wb = openpyxl.load_workbook(file_obj, read_only=True, data_only=True)
    try:
        ws = wb.active
        rows = list(ws.iter_rows(values_only=True))
    finally:
        wb.close()

    if not rows:
        return []

    # Cari baris header — baris yang mengandung kata kunci
    header_row_idx = None
    col_kode = col_nama = col_sks = None

    KEYWORDS_KODE = ['kode mk', 'kode_mk', 'kode mata kuliah', 'kode matakuliah', 'kodemk', 'kode']
    KEYWORDS_NAMA = ['mata kuliah', 'matakuliah', 'nama mk', 'nama_mk', 'nama mata kuliah', 'matkul']
    KEYWORDS_SKS = ['sks', 'bobot sks', 'bobot']

    for ri, row in enumerate(rows):
        row_lower = [str(v).lower().strip() if v is not None else '' for v in row]
        has_kode = any(any(k in c for k in KEYWORDS_KODE) for c in row_lower)
        has_nama = any(any(k in c for k in KEYWORDS_NAMA) for c in row_lower)
        has_sks = any(any(k in c for k in KEYWORDS_SKS) for c in row_lower)

        if has_kode and has_nama and has_sks:
            header_row_idx = ri
            # Map kolom
            for ci, cell_val in enumerate(row_lower):
                if any(k in cell_val for k in KEYWORDS_KODE) and col_kode is None:
                    col_kode = ci
                elif any(k in cell_val for k in KEYWORDS_NAMA) and col_nama is None:
                    col_nama = ci
                elif any(k in cell_val for k in KEYWORDS_SKS) and col_sks is None:
                    col_sks = ci
            break

    if header_row_idx is None or col_kode is None or col_nama is None or col_sks is None:
        raise ValueError(
            "Tidak bisa menemukan header kolom Kode MK, Mata Kuliah, dan SKS di file Excel. "
            "Pastikan file kurikulum memiliki baris header dengan kolom-kolom tersebut."
        )

    matakuliah = []
    for row in rows[header_row_idx + 1:]:
        if len(row) <= max(col_kode, col_nama, col_sks):
            continue
        kode = str(row[col_kode]).strip() if row[col_kode] is not None else ''
        nama = str(row[col_nama]).strip() if row[col_nama] is not None else ''
        sks_raw = row[col_sks]
        if not kode or not nama or sks_raw is None:
            continue
        try:
            sks = int(float(str(sks_raw)))
        except (ValueError, TypeError):
            continue
        if sks <= 0:
            continue
        matakuliah.append({'kode_mk': kode, 'nama_mk': nama, 'sks': sks})

    # Sort alfabetis berdasarkan nama_mk A-Z
    matakuliah.sort(key=lambda x: x['nama_mk'].lower())

    return matakuliah


class UploadKurikulumForm(forms.Form):
    file_kurikulum = forms.FileField(
        label="File Excel Kurikulum",
        help_text="Format .xlsx/.xls. Harus punya kolom: Kode MK, Mata Kuliah, SKS."
    )


class MataKuliahInline(admin.TabularInline):
    model = MataKuliahKurikulum
    extra = 0
    fields = ['urutan_alfabet', 'kode_mk', 'nama_mk', 'sks']
    readonly_fields = ['urutan_alfabet']
    ordering = ['urutan_alfabet']


@admin.register(Kurikulum)
class KurikulumAdmin(admin.ModelAdmin):
    list_display = ['jenjang', 'prodi', 'angkatan', 'template', 'jumlah_mk', 'created_at']
    list_filter = ['jenjang', 'prodi']
    inlines = [MataKuliahInline]
    change_list_template = 'admin/core/kurikulum/change_list.html'

    @admin.display(description="Jumlah MK")
    def jumlah_mk(self, obj):
        return MataKuliahKurikulum.objects.filter(kurikulum=obj).count()

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                'upload-excel/',
                self.admin_site.admin_view(self.upload_excel_view),
                name='core_kurikulum_upload_excel',
            ),
        ]
        return custom_urls + urls

    def upload_excel_view(self, request):
        if request.method == 'POST':
            form = UploadKurikulumForm(request.POST, request.FILES)
            if form.is_valid():
                # Ambil kurikulum yang dipilih atau buat baru
                jenjang = request.POST.get('jenjang', '')
                prodi = request.POST.get('prodi', '')
                angkatan_str = request.POST.get('angkatan', '')
                template_id = request.POST.get('template', '')

                if not jenjang or not angkatan_str:
                    messages.error(request, "Jenjang dan Angkatan wajib diisi.")
                    return redirect(request.path)

                try:
                    angkatan = int(angkatan_str)
                except ValueError:
                    messages.error(request, "Angkatan harus berupa angka tahun.")
                    return redirect(request.path)

                kurikulum, created = Kurikulum.objects.get_or_create(
                    jenjang=jenjang, prodi=prodi, angkatan=angkatan
                )
                if template_id:
                    try:
                        tpl = Template.objects.get(pk=int(template_id))
                        kurikulum.template = tpl
                        kurikulum.save()
                    except (Template.DoesNotExist, ValueError):
                        pass

                file_obj = request.FILES['file_kurikulum']
                try:
                    matakuliah = parse_excel_kurikulum(file_obj)
                except Exception as e:
                    messages.error(request, f"Gagal parse Excel: {e}")
                    return redirect(request.path)

                # Hapus MK lama dan isi ulang secara atomik
                with transaction.atomic():
                    kurikulum.matakuliah.all().delete()
                    mk_objs = [
                        MataKuliahKurikulum(
                            kurikulum=kurikulum,
                            kode_mk=mk_data['kode_mk'],
                            nama_mk=mk_data['nama_mk'],
                            sks=mk_data['sks'],
                            urutan_alfabet=urutan,
                        )
                        for urutan, mk_data in enumerate(matakuliah, start=1)
                    ]
                    MataKuliahKurikulum.objects.bulk_create(mk_objs)

                action = "dibuat baru" if created else "diperbarui"
                messages.success(
                    request,
                    f"Kurikulum {jenjang} {angkatan} berhasil {action}. "
                    f"{len(matakuliah)} mata kuliah berhasil di-import dan diurutkan alfabet."
                )
                return redirect('../')
        else:
            form = UploadKurikulumForm()

        templates = Template.objects.all()
        context = {
            **self.admin_site.each_context(request),
            'form': form,
            'templates': templates,
            'prodi_choices': Kurikulum.PRODI_CHOICES,
            'title': 'Upload Excel Kurikulum',
            'opts': self.model._meta,
        }
        return render(request, 'admin/core/kurikulum/upload_excel.html', context)


@admin.register(Template)
class TemplateAdmin(admin.ModelAdmin):
    list_display = ['nama', 'total_mk', 'baris_halaman_depan', 'file_link', 'created_at']
    fields = ['nama', 'file_excel', 'total_mk', 'baris_halaman_depan', 'cell_mapping']

    @admin.display(description="File")
    def file_link(self, obj):
        if obj.file_excel:
            return format_html('<a href="{}">Download</a>', obj.file_excel.url)
        return '-'


@admin.register(MataKuliahKurikulum)
class MataKuliahKurikulumAdmin(admin.ModelAdmin):
    list_display = ['kurikulum', 'urutan_alfabet', 'kode_mk', 'nama_mk', 'sks']
    list_filter = ['kurikulum__jenjang', 'kurikulum__angkatan']
    search_fields = ['kode_mk', 'nama_mk']
    ordering = ['kurikulum', 'urutan_alfabet']


@admin.register(PredikatKelulusan)
class PredikatKelulusanAdmin(admin.ModelAdmin):
    list_display = ['predikat', 'ipk_min', 'ipk_max', 'tahun_lulus_mulai', 'tahun_lulus_selesai']
    ordering = ['-tahun_lulus_mulai', '-ipk_min']


@admin.register(DataPendukung)
class DataPendukungAdmin(admin.ModelAdmin):
    list_display = ['nim', 'no_ijazah', 'nama_dekan', 'judul_skripsi_singkat']
    search_fields = ['nim', 'no_ijazah']

    @admin.display(description="Judul Skripsi")
    def judul_skripsi_singkat(self, obj):
        if obj.judul_skripsi:
            return obj.judul_skripsi[:80] + '...' if len(obj.judul_skripsi) > 80 else obj.judul_skripsi
        return '-'


@admin.register(RiwayatGenerate)
class RiwayatGenerateAdmin(admin.ModelAdmin):
    list_display = [
        'nim', 'nama_mahasiswa', 'jenjang', 'angkatan',
        'mode', 'status', 'jumlah_mk_kosong', 'jumlah_mk_retake',
        'jumlah_mk_tidak_dikenali', 'generated_at', 'generated_by'
    ]
    list_filter = ['status', 'mode', 'jenjang', 'angkatan', 'generated_at']
    search_fields = ['nim', 'nama_mahasiswa']
    readonly_fields = [
        'nim', 'nama_mahasiswa', 'jenjang', 'angkatan', 'mode',
        'jumlah_mk_retake', 'jumlah_mk_kosong', 'jumlah_mk_tidak_dikenali',
        'status', 'error_message', 'generated_at', 'generated_by'
    ]
    ordering = ['-generated_at']


@admin.register(BatchJob)
class BatchJobAdmin(admin.ModelAdmin):
    list_display = ['id', 'requested_by', 'status', 'created_at']
    list_filter = ['status', 'created_at']
    readonly_fields = ['id', 'requested_by', 'created_at']
    ordering = ['-created_at']
