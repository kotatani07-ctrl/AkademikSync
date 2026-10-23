import uuid

from django.contrib.auth.models import User
from django.db import models


class Template(models.Model):
    """Template Excel untuk transkrip."""
    nama = models.CharField(max_length=200, verbose_name="Nama Template")
    file_excel = models.FileField(upload_to='templates/', verbose_name="File Excel Template")
    total_mk = models.IntegerField(verbose_name="Total Kapasitas MK", help_text="Misal: 34, 50, 51, 55")
    baris_halaman_depan = models.IntegerField(
        verbose_name="Jumlah Baris MK Halaman 1",
        help_text="MK sisanya otomatis masuk halaman 2"
    )
    cell_mapping = models.JSONField(
        verbose_name="Cell Mapping (JSON)",
        default=dict,
        blank=True,
        help_text=(
            "Koordinat cell untuk field: nama, nim, prodi, tempat_lahir, tgl_lahir, dst. "
            "Contoh: {\"nama\": \"B5\", \"nim\": \"B6\", \"tabel_mulai_baris\": 10, "
            "\"tabel_kolom\": {\"no\": \"A\", \"kode\": \"B\", \"nama\": \"C\", "
            "\"sks\": \"D\", \"nilai\": \"E\", \"lambang\": \"F\", \"mutu\": \"G\"}, "
            "\"total_sks\": \"D50\", \"ipk\": \"G50\", \"predikat\": \"B52\", "
            "\"judul_skripsi\": \"B53\", \"no_ijazah\": \"B54\", \"ttd_dekan\": \"B58\"}"
        )
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Template"
        verbose_name_plural = "Templates"
        ordering = ['nama']

    def __str__(self):
        return f"{self.nama} ({self.total_mk} MK)"


class Kurikulum(models.Model):
    JENJANG_CHOICES = [
        ('D3', 'D3'),
        ('S1', 'S1'),
        ('PROF', 'Profesi'),
    ]
    PRODI_CHOICES = [
        ('ILMU_KEPERAWATAN', 'Ilmu Keperawatan'),
        ('INFORMATIKA', 'Informatika'),
        ('KEBIDANAN', 'Kebidanan'),
        ('MANAJEMEN_RITEL', 'Manajemen Ritel'),
        ('PERPAJAKAN', 'Perpajakan'),
        ('PROFESI_NERS', 'Profesi Ners'),
        ('SISTEM_INFORMASI', 'Sistem Informasi'),
    ]

    jenjang = models.CharField(max_length=5, choices=JENJANG_CHOICES, verbose_name="Jenjang")
    prodi = models.CharField(
        max_length=40, choices=PRODI_CHOICES, blank=True, default='',
        verbose_name="Program Studi",
    )
    angkatan = models.IntegerField(verbose_name="Angkatan (Tahun)")
    template = models.ForeignKey(
        Template, on_delete=models.PROTECT, verbose_name="Template Excel",
        null=True, blank=True
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Kurikulum"
        verbose_name_plural = "Kurikulum"
        unique_together = [('jenjang', 'prodi', 'angkatan')]
        ordering = ['-angkatan', 'jenjang', 'prodi']

    def __str__(self):
        prodi = self.get_prodi_display() if self.prodi else 'Umum'
        return f"Kurikulum {prodi} ({self.jenjang}) Angkatan {self.angkatan}"


class MataKuliahKurikulum(models.Model):
    kurikulum = models.ForeignKey(
        Kurikulum, on_delete=models.CASCADE,
        related_name='matakuliah', verbose_name="Kurikulum"
    )
    kode_mk = models.CharField(max_length=50, verbose_name="Kode MK")
    nama_mk = models.CharField(max_length=300, verbose_name="Nama Mata Kuliah")
    sks = models.IntegerField(verbose_name="SKS")
    urutan_alfabet = models.IntegerField(verbose_name="Urutan Alfabetis", default=0)

    class Meta:
        verbose_name = "Mata Kuliah Kurikulum"
        verbose_name_plural = "Mata Kuliah Kurikulum"
        ordering = ['urutan_alfabet']

    def __str__(self):
        return f"[{self.kode_mk}] {self.nama_mk} ({self.sks} SKS)"


class PredikatKelulusan(models.Model):
    tahun_lulus_mulai = models.IntegerField(verbose_name="Tahun Lulus Mulai")
    tahun_lulus_selesai = models.IntegerField(
        null=True, blank=True,
        verbose_name="Tahun Lulus Selesai",
        help_text="Kosongkan jika berlaku seterusnya"
    )
    ipk_min = models.DecimalField(max_digits=4, decimal_places=2, verbose_name="IPK Minimum")
    ipk_max = models.DecimalField(max_digits=4, decimal_places=2, verbose_name="IPK Maksimum")
    predikat = models.CharField(max_length=100, verbose_name="Predikat Kelulusan")

    class Meta:
        verbose_name = "Predikat Kelulusan"
        verbose_name_plural = "Predikat Kelulusan"
        ordering = ['-tahun_lulus_mulai', '-ipk_min']

    def __str__(self):
        selesai = self.tahun_lulus_selesai or "dst"
        return f"{self.predikat} (IPK {self.ipk_min}-{self.ipk_max}, {self.tahun_lulus_mulai}-{selesai})"


class DataPendukung(models.Model):
    nim = models.CharField(max_length=20, unique=True, verbose_name="NIM")
    judul_skripsi = models.TextField(blank=True, verbose_name="Judul Skripsi/TA")
    no_ijazah = models.CharField(max_length=100, blank=True, verbose_name="No. Ijazah")
    nama_dekan = models.CharField(
        max_length=200, blank=True,
        verbose_name="Nama Dekan",
        help_text="Kosongkan untuk pakai default dari config"
    )

    class Meta:
        verbose_name = "Data Pendukung"
        verbose_name_plural = "Data Pendukung"
        ordering = ['nim']

    def __str__(self):
        return f"DataPendukung - {self.nim}"


class RiwayatGenerate(models.Model):
    MODE_CHOICES = [
        ('single', 'Single'),
        ('batch', 'Batch'),
    ]
    STATUS_CHOICES = [
        ('success', 'Berhasil'),
        ('error', 'Error'),
    ]

    nim = models.CharField(max_length=20, verbose_name="NIM")
    nama_mahasiswa = models.CharField(max_length=300, blank=True, verbose_name="Nama Mahasiswa")
    jenjang = models.CharField(max_length=5, blank=True, verbose_name="Jenjang")
    angkatan = models.IntegerField(null=True, blank=True, verbose_name="Angkatan")
    mode = models.CharField(max_length=10, choices=MODE_CHOICES, verbose_name="Mode Generate")
    jumlah_mk_retake = models.IntegerField(default=0, verbose_name="Jumlah MK Retake")
    jumlah_mk_kosong = models.IntegerField(default=0, verbose_name="Jumlah MK Kosong")
    jumlah_mk_tidak_dikenali = models.IntegerField(default=0, verbose_name="Jumlah MK Tidak Dikenali")
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, verbose_name="Status")
    error_message = models.TextField(null=True, blank=True, verbose_name="Pesan Error")
    generated_at = models.DateTimeField(auto_now_add=True, verbose_name="Waktu Generate")
    generated_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True,
        verbose_name="Di-generate oleh"
    )

    class Meta:
        verbose_name = "Riwayat Generate"
        verbose_name_plural = "Riwayat Generate"
        ordering = ['-generated_at']

    def __str__(self):
        return f"{self.nim} - {self.status} ({self.generated_at.strftime('%d/%m/%Y %H:%M')})"


class BatchJob(models.Model):
    STATUS_RUNNING = 'running'
    STATUS_CANCELLED = 'cancelled'
    STATUS_COMPLETED = 'completed'
    STATUS_CHOICES = [
        (STATUS_RUNNING, 'Berjalan'),
        (STATUS_CANCELLED, 'Dibatalkan'),
        (STATUS_COMPLETED, 'Selesai'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    requested_by = models.ForeignKey(User, on_delete=models.CASCADE)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_RUNNING)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Proses Batch"
        verbose_name_plural = "Proses Batch"

    def __str__(self):
        return f"Batch {self.id} - {self.status}"
