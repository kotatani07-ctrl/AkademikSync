"""
Management command: sinkronisasi kurikulum dari Neo Feeder ke database lokal.

Contoh pemakaian:
  python manage.py sinkron_kurikulum --jenjang S1 --angkatan 2024
  python manage.py sinkron_kurikulum --prodi informatika --jenjang S1 --angkatan 2024
  python manage.py sinkron_kurikulum --kurikulum <id_kurikulum_feeder> --jenjang S1 --angkatan 2024 --replace

Alur:
  1. Pilih program studi (filter id/nama prodi via --prodi, atau pilih manual).
  2. Pilih kurikulum Feeder untuk prodi tsb (via --kurikulum, atau pilih manual).
  3. Tarik daftar mata kuliah kurikulum dari Feeder.
  4. Upsert ke MataKuliahKurikulum pada Kurikulum (jenjang, angkatan) yang ditentukan.
     --replace : hapus MK lama yang tidak ada di hasil Feeder lalu urutkan ulang.
"""
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.models import Kurikulum, MataKuliahKurikulum
from core.services import feeder


def _pilih(daftar: list, prompt: str, label=None) -> dict:
    """Tampilkan daftar & minta user memilih satu entri."""
    if not daftar:
        raise CommandError("Daftar kosong — tidak ada yang bisa dipilih.")
    if len(daftar) == 1:
        return daftar[0]
    label = label or (lambda r: str(r))
    for i, r in enumerate(daftar, 1):
        print(f"  {i}. {label(r)}")
    while True:
        pilihan = input(f"\n{prompt} [1-{len(daftar)}]: ").strip()
        if pilihan.isdigit() and 1 <= int(pilihan) <= len(daftar):
            return daftar[int(pilihan) - 1]
        print("  Pilihan tidak valid, coba lagi.")


class Command(BaseCommand):
    help = 'Tarik kurikulum & mata kuliah dari Neo Feeder ke database (Kurikulum/MataKuliahKurikulum).'

    def add_arguments(self, parser):
        parser.add_argument('--prodi', help='Filter prodi: id_prodi atau potongan nama (kosong = semuanya)')
        parser.add_argument('--kurikulum', help='id_kurikulum Feeder target (kosong = pilih manual)')
        parser.add_argument('--jenjang', required=True, help='Jenjang Kurikulum di DB, mis. S1 / D3')
        parser.add_argument('--angkatan', required=True, type=int, help='Tahun angkatan Kurikulum di DB')
        parser.add_argument('--replace', action='store_true',
                            help='Hapus MK lama yang tidak ada di hasil Feeder & urutkan ulang')

    def handle(self, *args, **opts):
        ok, msg = feeder.check_connection()
        if not ok:
            raise CommandError(f'Neo Feeder tidak dapat dihubungi: {msg}')

        # 1. Pilih prodi
        prodi = None
        if opts['prodi']:
            daftar_prodi = feeder.get_daftar_prodi(opts['prodi'])
            if not daftar_prodi:
                raise CommandError(f"Tidak ada prodi yang cocok dengan '{opts['prodi']}'.")
            prodi = _pilih(daftar_prodi, 'Pilih program studi',
                           label=lambda r: f"{r.get('nama_program_studi')} ({r.get('id_prodi')})")
        else:
            daftar_prodi = feeder.get_daftar_prodi()
            prodi = _pilih(daftar_prodi, 'Pilih program studi',
                           label=lambda r: f"{r.get('nama_program_studi')} ({r.get('id_prodi')})")

        prodi_id = prodi.get('id_prodi') or prodi.get('id_program_studi')
        nama_prodi = (prodi.get('nama_program_studi') or '').upper()
        prodi_key = 'PROFESI_NERS' if 'PROFESI' in nama_prodi or 'NERS' in nama_prodi else ''
        if not prodi_key:
            for key, label in Kurikulum.PRODI_CHOICES:
                if label.upper() in nama_prodi:
                    prodi_key = key
                    break
        self.stdout.write(f"Prodi terpilih: {prodi.get('nama_program_studi')} ({prodi_id})")

        jenjang_db = opts['jenjang'].upper()
        if jenjang_db in ('PROFESI', 'PROFESI NERS', 'NERS'):
            jenjang_db = 'PROF'
        kurikulum_qs = Kurikulum.objects.filter(
            jenjang=jenjang_db, angkatan=opts['angkatan']
        )
        kurikulum_db = kurikulum_qs.filter(prodi=prodi_key).first() if prodi_key else None
        if kurikulum_db is None:
            kurikulum_db = kurikulum_qs.filter(prodi='').first()
        if kurikulum_db is None and kurikulum_qs.count() == 1:
            kurikulum_db = kurikulum_qs.first()
        if kurikulum_db is None:
            raise CommandError(
                f"Kurikulum DB '{jenjang_db} {opts['angkatan']} "
                f"{prodi_key or nama_prodi}' belum ada. Buat dulu via Admin → Kurikulum."
            )

        # 2. Pilih kurikulum
        if opts['kurikulum']:
            id_kurikulum = opts['kurikulum']
            self.stdout.write(f"Kurikulum Feeder: {id_kurikulum} (dari --kurikulum)")
        else:
            daftar_kurikulum = feeder.get_daftar_kurikulum(prodi_id or '')
            if not daftar_kurikulum:
                raise CommandError(f"Tidak ada kurikulum untuk prodi {prodi_id}.")
            id_kurikulum = _pilih(
                daftar_kurikulum,
                'Pilih kurikulum Feeder',
                label=lambda k: (
                    f"{k.get('id_kurikulum')} | {k.get('nama_kurikulum')} | "
                    f"mulai {k.get('tahun_mulai_berlaku') or k.get('tahun_mulai') or '-'}"
                    f"{' [AKTIF]' if str(k.get('status')) == '1' else ''}"
                ),
            ).get('id_kurikulum')
        self.stdout.write(f"Menarik MK kurikulum {id_kurikulum} dari Feeder...")

        # 3. Tarik MK
        daftar_mk_feeder = feeder.get_mk_kurikulum(id_kurikulum)
        if not daftar_mk_feeder:
            raise CommandError(f"Feeder mengembalikan 0 mata kuliah untuk kurikulum {id_kurikulum}.")

        # 4. Upsert
        with transaction.atomic():
            existing = {mk.kode_mk.strip().lower(): mk
                        for mk in kurikulum_db.matakuliah.all()}

            ditambah = diperbarui = 0
            for i, item in enumerate(daftar_mk_feeder):
                kode = (item['kode_mk'] or '').strip().lower()
                nama = item['nama_mk']
                sks = item['sks']
                if not kode or not nama:
                    continue

                if kode in existing:
                    mk = existing[kode]
                    if mk.nama_mk != nama or mk.sks != sks:
                        mk.nama_mk = nama
                        mk.sks = sks
                        mk.save(update_fields=['nama_mk', 'sks'])
                        diperbarui += 1
                    del existing[kode]  # masih ada → bukan 'hilang'
                else:
                    urutan = kurikulum_db.matakuliah.count() + ditambah + 1
                    MataKuliahKurikulum.objects.create(
                        kurikulum=kurikulum_db,
                        kode_mk=item['kode_mk'],
                        nama_mk=nama,
                        sks=sks if sks > 0 else 0,
                        urutan_alfabet=urutan,
                    )
                    ditambah += 1

            dihapus = 0
            if opts['replace']:
                hapus_pk = [mk.pk for mk in existing.values()]
                if hapus_pk:
                    dihapus = len(hapus_pk)
                    MataKuliahKurikulum.objects.filter(pk__in=hapus_pk).delete()
                # urutkan ulang semua MK berdasarkan urutan saat ini
                for i, mk in enumerate(
                    kurikulum_db.matakuliah.all().order_by('urutan_alfabet', 'kode_mk')
                ):
                    if mk.urutan_alfabet != i:
                        mk.urutan_alfabet = i
                        mk.save(update_fields=['urutan_alfabet'])

        self.stdout.write(self.style.SUCCESS(
            f"Selesai: {len(daftar_mk_feeder)} MK dari Feeder. "
            f"+{ditambah} baru, ~{diperbarui} diperbarui, {dihapus} dihapus "
            f"(total di DB: {kurikulum_db.matakuliah.count()} MK)."
        ))