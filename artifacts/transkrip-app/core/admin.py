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
from django.utils.safestring import mark_safe

from .models import (
    BatchJob,
    DataPendukung,
    Kurikulum,
    MataKuliahKurikulum,
    PredikatKelulusan,
    RiwayatGenerate,
    Template,
)

# Kustomisasi Brand Admin
admin.site.site_header = "Transkrip & Ijazah Admin"
admin.site.site_title = "Transkrip & Ijazah"
admin.site.index_title = "Panel Administrasi"


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
    list_display = [
        'jenjang_badge',
        'prodi_display',
        'angkatan',
        'template_link',
        'jumlah_mk_badge',
        'created_at_formatted',
    ]
    list_filter = ['jenjang', 'prodi', 'angkatan']
    search_fields = ['angkatan', 'prodi', 'jenjang', 'template__nama']
    inlines = [MataKuliahInline]
    change_list_template = 'admin/core/kurikulum/change_list.html'
    list_per_page = 20

    @admin.display(description="Jenjang", ordering='jenjang')
    def jenjang_badge(self, obj):
        colors = {
            'S1': 'background:rgba(99,102,241,0.12);color:#4338ca;border:1px solid rgba(99,102,241,0.25);',
            'D3': 'background:rgba(14,165,233,0.12);color:#0369a1;border:1px solid rgba(14,165,233,0.25);',
            'PROF': 'background:rgba(16,185,129,0.12);color:#15803d;border:1px solid rgba(16,185,129,0.25);',
        }
        style = colors.get(obj.jenjang, 'background:#f3f4f6;color:#374151;border:1px solid #e5e7eb;')
        return format_html(
            '<span style="display:inline-block;padding:2px 10px;border-radius:9999px;font-size:0.75rem;font-weight:700;{}">{}</span>',
            style, obj.get_jenjang_display()
        )

    @admin.display(description="Program Studi", ordering='prodi')
    def prodi_display(self, obj):
        if obj.prodi:
            return format_html('<span style="font-weight:600;color:#1e1b4b;">{}</span>', obj.get_prodi_display())
        return mark_safe('<span style="color:#9ca3af;font-style:italic;">(Umum / Semua Prodi)</span>')

    @admin.display(description="Template Excel", ordering='template__nama')
    def template_link(self, obj):
        if obj.template:
            return format_html(
                '<a href="/admin/core/template/{}/change/" style="font-weight:600;color:#6366f1;text-decoration:none;">📄 {}</a>',
                obj.template.pk, obj.template.nama
            )
        return mark_safe('<span style="color:#9ca3af;">- Belum Ditentukan -</span>')

    @admin.display(description="Jumlah MK")
    def jumlah_mk_badge(self, obj):
        count = MataKuliahKurikulum.objects.filter(kurikulum=obj).count()
        return format_html(
            '<span style="display:inline-flex;align-items:center;padding:2px 10px;border-radius:9999px;font-size:0.75rem;font-weight:600;background:rgba(99,102,241,0.08);color:#4f46e5;border:1px solid rgba(99,102,241,0.2);">📚 {} MK</span>',
            count
        )

    @admin.display(description="Dibuat", ordering='created_at')
    def created_at_formatted(self, obj):
        return obj.created_at.strftime('%d %b %Y')

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
    list_display = ['nama', 'kapasitas_display', 'file_link', 'created_at_formatted']
    search_fields = ['nama']
    fields = ['nama', 'file_excel', 'total_mk', 'baris_halaman_depan', 'cell_mapping']
    list_per_page = 20

    @admin.display(description="Kapasitas Baris")
    def kapasitas_display(self, obj):
        return format_html(
            '<strong style="color:#1e1b4b;">{} MK</strong> <span style="color:#6b7280;font-size:0.75rem;">(Hal 1: {} baris)</span>',
            obj.total_mk, obj.baris_halaman_depan
        )

    @admin.display(description="File Template")
    def file_link(self, obj):
        if obj.file_excel:
            return format_html(
                '<a href="{}" target="_blank" style="display:inline-flex;align-items:center;gap:4px;padding:3px 10px;background:rgba(99,102,241,0.08);color:#4f46e5;border:1px solid rgba(99,102,241,0.22);border-radius:6px;font-size:0.75rem;font-weight:600;text-decoration:none;">'
                '⬇ Download'
                '</a>',
                obj.file_excel.url
            )
        return mark_safe('<span style="color:#9ca3af;">-</span>')

    @admin.display(description="Dibuat", ordering='created_at')
    def created_at_formatted(self, obj):
        return obj.created_at.strftime('%d %b %Y')


@admin.register(MataKuliahKurikulum)
class MataKuliahKurikulumAdmin(admin.ModelAdmin):
    list_display = ['urutan_badge', 'kode_mk', 'nama_mk', 'sks_badge', 'kurikulum']
    list_filter = ['kurikulum__jenjang', 'kurikulum__prodi', 'kurikulum__angkatan']
    search_fields = ['kode_mk', 'nama_mk', 'kurikulum__angkatan']
    ordering = ['kurikulum', 'urutan_alfabet']
    list_per_page = 50

    @admin.display(description="No.", ordering='urutan_alfabet')
    def urutan_badge(self, obj):
        return format_html(
            '<span style="display:inline-block;min-width:24px;text-align:center;padding:1px 6px;border-radius:4px;background:#f3f4f6;color:#4b5563;font-size:0.75rem;font-weight:700;">{}</span>',
            obj.urutan_alfabet
        )

    @admin.display(description="SKS", ordering='sks')
    def sks_badge(self, obj):
        return format_html(
            '<span style="display:inline-block;padding:2px 8px;border-radius:6px;background:rgba(99,102,241,0.08);color:#4338ca;font-weight:700;font-size:0.75rem;">{} SKS</span>',
            obj.sks
        )


@admin.register(PredikatKelulusan)
class PredikatKelulusanAdmin(admin.ModelAdmin):
    list_display = ['predikat_badge', 'rentang_ipk', 'periode_kelulusan']
    search_fields = ['predikat']
    list_filter = ['tahun_lulus_mulai']
    ordering = ['-tahun_lulus_mulai', '-ipk_min']

    @admin.display(description="Predikat", ordering='predikat')
    def predikat_badge(self, obj):
        return format_html(
            '<span style="font-weight:700;color:#1e1b4b;">🏆 {}</span>',
            obj.predikat
        )

    @admin.display(description="Rentang IPK")
    def rentang_ipk(self, obj):
        val_min = f"{float(obj.ipk_min):.2f}"
        val_max = f"{float(obj.ipk_max):.2f}"
        return format_html(
            '<span style="display:inline-block;padding:2px 8px;border-radius:6px;background:rgba(99,102,241,0.08);color:#4338ca;font-weight:600;font-size:0.8rem;">{} &ndash; {}</span>',
            val_min, val_max
        )

    @admin.display(description="Tahun Kelulusan")
    def periode_kelulusan(self, obj):
        selesai = obj.tahun_lulus_selesai or 'sekarang'
        return f"{obj.tahun_lulus_mulai} s/d {selesai}"


@admin.register(DataPendukung)
class DataPendukungAdmin(admin.ModelAdmin):
    list_display = ['nim_code', 'no_ijazah_badge', 'nama_dekan', 'judul_skripsi_singkat']
    search_fields = ['nim', 'no_ijazah', 'nama_dekan', 'judul_skripsi']
    list_per_page = 25

    @admin.display(description="NIM", ordering='nim')
    def nim_code(self, obj):
        return format_html('<code style="font-family:monospace;font-weight:600;color:#1e1b4b;">{}</code>', obj.nim)

    @admin.display(description="No. Ijazah", ordering='no_ijazah')
    def no_ijazah_badge(self, obj):
        if obj.no_ijazah:
            return format_html('<span style="font-weight:500;color:#374151;">{}</span>', obj.no_ijazah)
        return mark_safe('<span style="color:#9ca3af;font-style:italic;">Belum Ada</span>')

    @admin.display(description="Judul Skripsi")
    def judul_skripsi_singkat(self, obj):
        if obj.judul_skripsi:
            text = obj.judul_skripsi[:80] + '...' if len(obj.judul_skripsi) > 80 else obj.judul_skripsi
            return format_html('<span title="{}">{}</span>', obj.judul_skripsi, text)
        return mark_safe('<span style="color:#9ca3af;">-</span>')


@admin.register(RiwayatGenerate)
class RiwayatGenerateAdmin(admin.ModelAdmin):
    list_display = [
        'nim_badge',
        'nama_mahasiswa',
        'jenjang_angkatan',
        'mode_badge',
        'status_badge',
        'anomali_display',
        'waktu_generate',
        'generated_by',
    ]
    list_filter = ['status', 'mode', 'jenjang', 'angkatan', 'generated_at']
    search_fields = ['nim', 'nama_mahasiswa', 'error_message']
    date_hierarchy = 'generated_at'
    list_per_page = 25
    readonly_fields = [
        'nim', 'nama_mahasiswa', 'jenjang', 'angkatan', 'mode',
        'jumlah_mk_retake', 'jumlah_mk_kosong', 'jumlah_mk_tidak_dikenali',
        'status', 'error_message', 'generated_at', 'generated_by'
    ]
    ordering = ['-generated_at']

    @admin.display(description="NIM", ordering='nim')
    def nim_badge(self, obj):
        return format_html('<code style="font-family:monospace;font-size:0.85rem;font-weight:600;color:#1e1b4b;">{}</code>', obj.nim)

    @admin.display(description="Jenjang • Angkatan")
    def jenjang_angkatan(self, obj):
        jenjang = obj.jenjang or '-'
        angkatan = str(obj.angkatan) if obj.angkatan else '-'
        return f"{jenjang} • {angkatan}"

    @admin.display(description="Mode", ordering='mode')
    def mode_badge(self, obj):
        if obj.mode == 'single':
            return mark_safe(
                '<span style="display:inline-block;padding:2px 8px;border-radius:9999px;background:rgba(14,165,233,0.1);color:#0369a1;border:1px solid rgba(14,165,233,0.22);font-size:0.72rem;font-weight:600;">Single</span>'
            )
        return mark_safe(
            '<span style="display:inline-block;padding:2px 8px;border-radius:9999px;background:rgba(139,92,246,0.1);color:#6d28d9;border:1px solid rgba(139,92,246,0.22);font-size:0.72rem;font-weight:600;">Batch</span>'
        )

    @admin.display(description="Status", ordering='status')
    def status_badge(self, obj):
        if obj.status == 'success':
            return mark_safe(
                '<span style="display:inline-flex;align-items:center;gap:4px;padding:2px 9px;border-radius:9999px;background:rgba(16,185,129,0.12);color:#15803d;border:1px solid rgba(16,185,129,0.25);font-size:0.72rem;font-weight:700;">✓ Berhasil</span>'
            )
        return mark_safe(
            '<span style="display:inline-flex;align-items:center;gap:4px;padding:2px 9px;border-radius:9999px;background:rgba(239,68,68,0.12);color:#b91c1c;border:1px solid rgba(239,68,68,0.25);font-size:0.72rem;font-weight:700;">✕ Gagal</span>'
        )

    @admin.display(description="Catatan MK")
    def anomali_display(self, obj):
        badges = []
        if obj.jumlah_mk_retake > 0:
            badges.append(f'<span style="display:inline-block;padding:1px 6px;border-radius:4px;background:#fef3c7;color:#b45309;font-size:0.7rem;font-weight:600;">{obj.jumlah_mk_retake} Retake</span>')
        if obj.jumlah_mk_kosong > 0:
            badges.append(f'<span style="display:inline-block;padding:1px 6px;border-radius:4px;background:#fee2e2;color:#dc2626;font-size:0.7rem;font-weight:600;">{obj.jumlah_mk_kosong} Kosong</span>')
        if obj.jumlah_mk_tidak_dikenali > 0:
            badges.append(f'<span style="display:inline-block;padding:1px 6px;border-radius:4px;background:#f3f4f6;color:#4b5563;font-size:0.7rem;font-weight:600;">{obj.jumlah_mk_tidak_dikenali} Asing</span>')
        if not badges:
            return mark_safe('<span style="color:#9ca3af;font-size:0.75rem;">Normal</span>')
        return mark_safe(' '.join(badges))

    @admin.display(description="Waktu Generate", ordering='generated_at')
    def waktu_generate(self, obj):
        return obj.generated_at.strftime('%d %b %Y, %H:%M')


@admin.register(BatchJob)
class BatchJobAdmin(admin.ModelAdmin):
    list_display = ['job_id_short', 'requested_by', 'status_badge', 'created_at_formatted']
    list_filter = ['status', 'created_at']
    readonly_fields = ['id', 'requested_by', 'created_at']
    ordering = ['-created_at']
    list_per_page = 25

    @admin.display(description="Job ID")
    def job_id_short(self, obj):
        short = str(obj.id)[:8]
        return format_html('<code style="font-family:monospace;font-size:0.8rem;background:#f3f4f6;color:#1e1b4b;padding:2px 6px;border-radius:4px;">#{}</code>', short)

    @admin.display(description="Status", ordering='status')
    def status_badge(self, obj):
        styles = {
            BatchJob.STATUS_COMPLETED: ('rgba(16,185,129,0.12)', '#15803d', 'rgba(16,185,129,0.25)', '✓ Selesai'),
            BatchJob.STATUS_RUNNING: ('rgba(245,158,11,0.12)', '#b45309', 'rgba(245,158,11,0.25)', '⏳ Berjalan'),
            BatchJob.STATUS_CANCELLED: ('rgba(239,68,68,0.12)', '#b91c1c', 'rgba(239,68,68,0.25)', '✕ Dibatalkan'),
        }
        bg, fg, border, label = styles.get(obj.status, ('#f3f4f6', '#374151', '#e5e7eb', obj.get_status_display()))
        return format_html(
            '<span style="display:inline-block;padding:2px 9px;border-radius:9999px;background:{};color:{};border:1px solid {};font-size:0.72rem;font-weight:700;">{}</span>',
            bg, fg, border, label
        )

    @admin.display(description="Dibuat", ordering='created_at')
    def created_at_formatted(self, obj):
        return obj.created_at.strftime('%d %b %Y, %H:%M')
