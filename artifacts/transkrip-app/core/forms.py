from django import forms
from .models import Kurikulum


class GenerateSingleForm(forms.Form):
    prodi = forms.ChoiceField(
        required=False,
        label="Program Studi",
        choices=[('', 'Otomatis dari data Neo Feeder'), *Kurikulum.PRODI_CHOICES],
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
    nim = forms.CharField(
        max_length=20,
        label="NIM Mahasiswa",
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'Masukkan NIM mahasiswa...',
            'autofocus': True,
        })
    )
    format_output = forms.ChoiceField(
        required=False,
        label="Format Unduhan",
        initial='xlsx',
        choices=[
            ('xlsx', 'Excel (.xlsx) — Format Asli Siap Edit'),
            ('pdf', 'PDF (.pdf) — Siap Cetak Langsung'),
        ],
        widget=forms.Select(attrs={'class': 'form-select'}),
    )


class GenerateBatchForm(forms.Form):
    prodi = forms.ChoiceField(
        required=False,
        label="Program Studi",
        choices=[('', 'Otomatis dari data Neo Feeder'), *Kurikulum.PRODI_CHOICES],
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
    format_output = forms.ChoiceField(
        required=False,
        label="Format Berkas Hasil",
        initial='zip_xlsx',
        choices=[
            ('zip_xlsx', 'Paket ZIP — Transkrip Resmi Excel (.xlsx) per Mahasiswa + Laporan Rekap'),
            ('zip_pdf', 'Paket ZIP — Transkrip Resmi PDF (.pdf) Siap Cetak per Mahasiswa + Laporan Rekap'),
            ('xlsx', 'Rekapitulasi Gabungan Saja (.xlsx)'),
        ],
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
    FILE_CHOICES = [
        ('auto', 'Deteksi otomatis'),
        ('txt', 'Text (.txt) — satu NIM per baris'),
        ('csv', 'CSV (.csv) — kolom pertama NIM'),
        ('xlsx', 'Excel (.xlsx)'),
    ]

    file_nim = forms.FileField(
        label="File Daftar NIM",
        help_text="Format: .txt (satu NIM per baris), .csv (kolom pertama NIM), atau .xlsx (kolom pertama NIM).",
        widget=forms.FileInput(attrs={'class': 'form-control', 'accept': '.txt,.csv,.xlsx'})
    )

    def clean_file_nim(self):
        f = self.cleaned_data['file_nim']
        name = f.name.lower()
        if not (name.endswith('.txt') or name.endswith('.csv') or name.endswith('.xlsx')):
            raise forms.ValidationError(
                "Format file tidak didukung. Gunakan .txt, .csv, atau .xlsx."
            )
        return f


class RiwayatFilterForm(forms.Form):
    tanggal_mulai = forms.DateField(
        required=False,
        label="Dari Tanggal",
        widget=forms.DateInput(attrs={'class': 'form-control', 'type': 'date'})
    )
    tanggal_selesai = forms.DateField(
        required=False,
        label="Sampai Tanggal",
        widget=forms.DateInput(attrs={'class': 'form-control', 'type': 'date'})
    )
    angkatan = forms.IntegerField(
        required=False,
        label="Angkatan",
        widget=forms.NumberInput(attrs={'class': 'form-control', 'placeholder': 'Misal: 2020'})
    )
    status = forms.ChoiceField(
        required=False,
        label="Status",
        choices=[('', 'Semua'), ('success', 'Berhasil'), ('error', 'Error')],
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    jenjang = forms.ChoiceField(
        required=False,
        label="Jenjang",
        choices=[('', 'Semua'), ('D3', 'D3'), ('S1', 'S1'), ('PROF', 'Profesi')],
        widget=forms.Select(attrs={'class': 'form-select'})
    )
