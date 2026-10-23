"""CLI utama: login, cek sesi, petakan menu, ekstrak tabel.

Pakai:
  python -m ecampuz.cli login
  python -m ecampuz.cli check
  python -m ecampuz.cli discover [--out FILE]
  python -m ecampuz.cli extract --url "?act=mahasiswa" --name mahasiswa
  python -m ecampuz.cli extract-auto --path "index.php" --name beranda
  python -m ecampuz.cli audit
"""

from __future__ import annotations

import argparse
import getpass
import json
import sys
import threading
import webbrowser
from pathlib import Path
from urllib.parse import urljoin

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ecampuz import (  # noqa: E402
    SessionExpired,
    discover_links,
    extract_personal_info,
    extract_tables,
    find_pagination,
    parse_transcript,
    transcript_candidates,
    validate_transcript,
    write_csv,
    eCampuzClient,
)
from ecampuz.yudisium_browser import run_yudisium  # noqa: E402
from ecampuz.audit import load_config  # noqa: E402
from ecampuz.batch import (  # noqa: E402
    BatchRunner,
    format_progress,
    read_nim_file,
    verify_bulk_export,
)
from ecampuz.browser_login import login_via_browser  # noqa: E402
from ecampuz.credentials import CredentialStore  # noqa: E402
from ecampuz.dhs_runner import read_nims, run_dhs  # noqa: E402
from ecampuz.niu_filter import build_url as build_niu_url  # noqa: E402
from ecampuz.niu_filter import summarize as niu_summarize  # noqa: E402


def _client(args) -> eCampuzClient:
    cfg = load_config()
    if getattr(args, "base_url", None):
        cfg.base_url = args.base_url
    return eCampuzClient(cfg, audit_enabled=not getattr(args, "no_audit", False))


def _ensure_session(client: eCampuzClient) -> None:
    if client.authenticated:
        try:
            client.ensure_alive()
            print("[ok] Sesi aktif")
            return
        except SessionExpired as exc:
            print(f"[warn] {exc}")
    raise SystemExit(
        "Belum login. Jalankan:  python -m ecampuz.cli login"
    )


# ---------- commands ----------


def cmd_login(args) -> int:
    client = _client(args)

    store = CredentialStore(client.cfg.credential_path)
    if getattr(args, "reset_creds", False):
        store.clear()
        print("[ok] Kredensial tersimpan dihapus")

    if store.exists():
        creds = store.load()
        if creds:
            print(f"[i] Kredensial tersimpan (DPAPI) untuk: {creds[0]}")
        else:
            print("[!] File kredensial ada tapi tidak bisa dibuka - masukkan ulang")
    else:
        print("[i] Belum ada kredensial tersimpan - akan disimpan terenkripsi (DPAPI)")

    if not args.force and client.authenticated:
        ok, detail = client.verify_session()
        if ok:
            print(f"[ok] Sesi sudah aktif, tidak perlu login ulang")
            print(f"     {detail}")
            return 0
        print(f"[i] Sesi lama tidak valid ({detail}), login ulang...")

    result = client.login()
    print(f"\n[ok] {result.message} (percobaan {result.attempts})")

    ok, detail = client.verify_session()
    if not ok:
        print(f"[!] PERINGATAN: login dilaporkan sukses tapi verifikasi gagal: {detail}")
        print("    Sesi tidak disimpan. Periksa kredensial.")
        return 3

    print(f"     verifikasi: {detail}")
    print(f"     cookies   : {client.cfg.session_path}")
    print(f"     kredensial: {client.cfg.credential_path} (terenkripsi DPAPI)")
    print(f"     konfigurasi: {client.cfg.save()}")
    return 0


def cmd_creds(args) -> int:
    client = _client(args)
    store = CredentialStore(client.cfg.credential_path)
    if args.action == "clear":
        existed = store.exists()
        store.clear()
        print("[ok] Kredensial dihapus" if existed else "[i] Tidak ada kredensial tersimpan")
        return 0
    if args.action == "status":
        if not store.exists():
            print("[i] Belum ada kredensial tersimpan")
            return 1
        creds = store.load()
        if not creds:
            print("[x] File rusak atau tidak bisa didekripsi (DPAPI)")
            return 1
        pw = creds[1]
        print(f"[ok] username : {creds[0]}")
        print(f"     password : tersimpan, {len(pw)} karakter (DPAPI terenkripsi)")
        return 0
    return 0


def cmd_neo_feeder(args) -> int:
    """Configure or inspect the read-only Neo Feeder integration."""
    import getpass

    from ecampuz.neo_feeder import NeoFeederClient, NeoFeederError

    cfg = load_config()
    store = CredentialStore(cfg.neo_feeder_credential_path)
    if args.action == "configure":
        endpoint = args.url.strip()
        if endpoint.startswith("http://") and not args.allow_insecure_http:
            print("[x] HTTP tidak terenkripsi. Gunakan HTTPS/VPN atau konfirmasi --allow-insecure-http.")
            return 1
        if endpoint.startswith("http://"):
            print("[PERINGATAN] Username, password, dan token dapat terlihat di jaringan HTTP.")
            if input("Ketik 'SAYA PAHAM RISIKO HTTP' untuk lanjut: ").strip() != "SAYA PAHAM RISIKO HTTP":
                print("[batal] Endpoint belum diubah.")
                return 1
        username = input("Username Neo Feeder: ").strip()
        password = getpass.getpass("Password Neo Feeder: ")
        if not username or not password:
            print("[x] Username dan password wajib diisi.")
            return 1
        try:
            store.save(username, password)
        except (OSError, RuntimeError, ValueError) as exc:
            print(f"[x] Kredensial tidak dapat disimpan dengan DPAPI: {exc}")
            return 1
        cfg.neo_feeder_url = endpoint
        cfg.neo_feeder_allow_insecure_http = bool(args.allow_insecure_http)
        cfg.neo_feeder_verify_tls = not args.no_verify_tls
        cfg.save()
        print("[ok] Endpoint disimpan di config; password disimpan terenkripsi DPAPI.")
        print("[i] Belum ada request API yang dikirim. Jalankan `neo-feeder inspect` untuk cek token/dictionary.")
        return 0

    if args.action == "status":
        print(f"URL       : {cfg.neo_feeder_url or '(belum dikonfigurasi)'}")
        print(f"TLS verify: {cfg.neo_feeder_verify_tls}")
        print(f"HTTP opt-in: {cfg.neo_feeder_allow_insecure_http}")
        print(f"Credential: {'tersimpan DPAPI' if store.load() else 'belum tersedia'}")
        return 0

    try:
        feeder = NeoFeederClient.from_config(cfg)
        dictionary = feeder.get_dictionary()
    except (NeoFeederError, OSError, ValueError) as exc:
        print(f"[x] Inspeksi Neo Feeder gagal: {exc}")
        return 1
    rendered = json.dumps(dictionary, ensure_ascii=False, indent=2, default=str)
    print("[ok] GetToken dan GetDictionary berhasil.")
    print("[i] Cuplikan schema (maksimal 12000 karakter, metadata saja):")
    print(rendered[:12000])
    if len(rendered) > 12000:
        print("... schema dipotong; simpan response dictionary lewat kanal administrasi aman untuk inspeksi penuh.")
    return 0


def cmd_login_browser(args) -> int:
    """Login lewat browser; autofill akun tersimpan, CAPTCHA tetap manual."""
    cfg = load_config()
    if getattr(args, "base_url", None):
        cfg.base_url = args.base_url

    store = CredentialStore(cfg.credential_path)
    if getattr(args, "reset_creds", False):
        store.clear()
        print("[i] Kredensial tersimpan dihapus")
    creds = store.load()
    if creds:
        username, password = creds
        print(f"[i] Menggunakan kredensial DPAPI untuk: {username}")
    else:
        username = input("Username eAkademik: ").strip()
        password = getpass.getpass("Password eAkademik: ")
        if not username or not password:
            print("[x] Username dan password wajib diisi")
            return 1
        try:
            store.save(username, password)
        except (OSError, RuntimeError, ValueError) as exc:
            print(f"[x] Kredensial tidak bisa disimpan aman: {exc}")
            return 1
        print("[ok] Kredensial disimpan terenkripsi dengan Windows DPAPI")

    path = cfg.session_path
    if getattr(args, "reset", False) and path.exists():
        from ecampuz.credentials import _unhide

        _unhide(path)
        path.unlink()
        print("[ok] Sesi lama dihapus")

    print("[i] Membuka jendela browser...")
    result = login_via_browser(
        cfg,
        headless=args.headless,
        timeout_ms=args.timeout * 1000,
        username=username,
        password=password,
    )

    if not result.ok:
        print(f"\n[x] Gagal: {result.message}")
        print("\n    Kemungkinan:")
        print("      - kredensial salah (cek caps lock, spasi di akhir)")
        print("      - akun terkunci / belum aktif")
        print("      - perlu reset password lewat IT kampus")
        return 1

    print(f"\n[ok] {result.message}")
    print(f"     cookies : {path}")

    client = eCampuzClient(cfg, audit_enabled=not args.no_audit)
    ok, detail = client.verify_session()
    if not ok:
        print(f"[!] Login sukses tapi verifikasi gagal: {detail}")
        return 3
    print(f"     verifikasi: {detail}")
    print("\n     Sekarang bisa lanjut:")
    print('       .\\run.bat check')
    print('       .\\run.bat discover')
    return 0


def cmd_dhs(args) -> int:
    """Ambil transkrip nilai (Detail Hasil Studi) satu atau banyak NIM."""
    cfg = load_config()
    client = eCampuzClient(cfg)
    client.ensure_alive()

    if args.file:
        nims = read_nims(Path(args.file))
        if not nims:
            print(f"[!] Tidak ada NIM terbaca dari {args.file}")
            return 1
    elif args.nim:
        nims = [args.nim]
    else:
        print("[!] Beri --nim <NIM> atau --file <file>")
        return 1

    out_dir = Path(args.out) if args.out else cfg.out_dir
    grade_fallback = None
    if args.neo_feeder_fallback:
        from ecampuz.neo_feeder import NeoFeederClient, NeoFeederError

        try:
            grade_fallback = NeoFeederClient.from_config(cfg)
        except (NeoFeederError, ValueError) as exc:
            print(f"[x] Neo Feeder fallback tidak siap: {exc}")
            print("    Konfigurasi: .\\run.bat neo-feeder configure --url <HTTPS_URL>")
            return 1
        print("[i] Neo Feeder fallback aktif; hanya nilai eCampus yang kosong yang akan dicari.")
    run_dhs(
        client,
        nims,
        angkatan=args.angkatan,
        prodi=args.prodi,
        out_dir=out_dir,
        per_mahasiswa=args.per_mahasiswa,
        limit=args.limit,
        resume=not args.reset,
        retry_failed=args.retry_failed,
        verbose=not args.quiet,
        grade_fallback=grade_fallback,
    )
    return 0


def cmd_web(args) -> int:
    """Jalankan web dashboard yang terintegrasi dengan bot scraping."""
    from webapp.app import app as web_app

    port = args.port
    host = args.host
    if args.open:
        threading.Timer(1.0, webbrowser.open, args=(f"http://{host}:{port}/",)).start()
    print(f"[web] Dashboard: http://{host}:{port}/  (Ctrl+C untuk berhenti)")
    web_app.run(
        host=host,
        port=port,
        debug=args.debug,
        threaded=True,
        use_reloader=False,
    )
    return 0


def cmd_niu(args) -> int:
    """Filter daftar mahasiswa via form 'niu' eCampuz (read-only, GET saja)."""
    cfg = load_config()
    client = eCampuzClient(cfg)
    client.ensure_alive()

    if not args.act:
        print("[!] --act wajib diisi (nilai act dari halaman list)")
        return 1

    url = build_niu_url(
        args.path,
        act=args.act,
        nim=args.nim,
        nim_from=args.nim_from,
        nim_to=args.nim_to,
        prodi=args.prodi,
        ang_from=args.ang_from,
        ang_to=args.ang_to,
        nama=args.nama,
    )
    print(f"[i] GET {url}")
    resp = client.get(url, note="niu_filter")
    html = resp.text
    soup = client.soup(html)

    info = niu_summarize(soup)
    if not info["found"]:
        print("[!] Tabel hasil tidak ditemukan di halaman ini.")
        return 1

    print(f"[ok] Header : {info['headers']}")
    print(f"[ok] Jumlah : {info['count']} baris")
    if info["count"] == 0:
        print("     Filter tidak menghasilkan data. Cek NIM/angkatan/prodi.")
        return 0

    for row in info["rows"][: args.show]:
        nim = row.get("nim") or "?"
        nama = row.get("Nama") or (row["cells"][3] if len(row["cells"]) > 3 else "")
        print(f"     - {nim:<16} {nama}")

    link = info.get("sample_detail_link")
    if link:
        print(f"\n[i] Link detail contoh:")
        print(f"    {link['href']}")
        print(f"    teks: {link.get('text', '')}")
        print("\n     Untuk membuka halaman detail itu:")
        print(f'     .\\run.bat extract --url "{link["href"]}" --name nilai')
    return 0


def cmd_check(args) -> int:
    client = _client(args)
    if not client.authenticated:
        print("[x] Tidak ada sesi tersimpan")
        return 1
    ok, detail = client.verify_session()
    if not ok:
        print(f"[x] {detail}")
        return 1
    print(f"[ok] {detail}")

    html = client.last_html or ""
    soup = client.soup(html)
    for i, table in enumerate(extract_tables(html), 1):
        cols = len(table[0]) if table else 0
        print(f"     tabel {i}: {len(table)} baris x {cols} kolom")

    try:
        from ecampuz import discover_links

        links = [l for l in discover_links(html, "") if not l["href"].startswith("http")]
        print(f"     link internal: {len(links)}")
    except Exception:
        pass
    return 0


def cmd_discover(args) -> int:
    client = _client(args)
    _ensure_session(client)

    start = args.path or ""
    html = client.get_text(start, note="discover_root")
    client.save_raw("discover_root", html)

    links = discover_links(html, start)
    internal = [l for l in links if not l["href"].startswith("http")]

    payload = {
        "base_url": client.base,
        "root_path": start,
        "total_links": len(links),
        "internal": len(internal),
        "links": internal,
    }
    out = Path(args.out) if args.out else client.cfg.out_dir / "discover.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"[ok] {len(internal)} link internal ditemukan -> {out}")
    for link in internal[: args.limit]:
        label = link["text"][:44] or "-"
        print(f"  {link['href']:<58} {label}  ({link['source']})")
    if len(internal) > args.limit:
        print(f"  ... {len(internal) - args.limit} lainnya (lihat {out})")
    return 0


def cmd_extract(args) -> int:
    client = _client(args)
    _ensure_session(client)

    html = client.get_text(args.url, note=f"extract:{args.name}")
    raw = client.save_raw(f"extract_{args.name}", html)

    tables = extract_tables(html, min_rows=args.min_rows)
    outdir = Path(args.out_dir) if args.out_dir else client.cfg.out_dir
    written: list[str] = []

    for idx, records in enumerate(tables, 1):
        suffix = "" if len(tables) == 1 else f"_t{idx}"
        path = outdir / f"{args.name}{suffix}.csv"
        write_csv(records, path)
        written.append(str(path))
        print(f"[ok] tabel {idx}: {len(records)} baris x {len(records[0])} kolom -> {path}")
        if args.print_head:
            for rec in records[: args.print_head]:
                print("    ", json.dumps(rec, ensure_ascii=False)[:200])

    if not tables:
        print("[!] Tidak ada tabel terdeteksi")
        if args.profile:
            info = extract_personal_info(html)
            if info:
                print(f"[ok] profil personal: {len(info)} field")
                print(json.dumps(info, indent=2, ensure_ascii=False)[:2000])
        print(f"    HTML mentah: {raw}")

    pag = find_pagination(html)
    print(f"[i] paginasi: {pag}")
    return 0


def cmd_extract_auto(args) -> int:
    client = _client(args)
    _ensure_session(client)
    html = client.get_text(args.path, note=f"auto:{args.name}")
    raw = client.save_raw(f"auto_{args.name}", html)
    tables = extract_tables(html, min_rows=args.min_rows)
    if not tables:
        print(f"[!] {args.name}: tidak ada tabel. HTML: {raw}")
        return 1
    records = [r for t in tables for r in t]
    path = client.cfg.out_dir / f"{args.name}.csv"
    write_csv(records, path)
    print(f"[ok] {args.name}: {len(records)} baris -> {path}")
    return 0


def cmd_transcript_find(args) -> int:
    client = _client(args)
    _ensure_session(client)

    html = client.get_text(args.path, note="transcript_find")
    client.save_raw("transcript_find", html)
    cands = transcript_candidates(html)

    print(f"[i] {len(cands)} kandidat endpoint transkrip di {args.path or 'beranda'}")
    if not cands:
        print("\n[!] Tidak ada kandidat dari teks menu.")
        print("    Cari manual: buka menu Transkrip Nilai di browser, salin URL-nya,")
        print("    lalu jalankan:")
        print('      .\\run.bat transcript --url "<URL_YANG_DISALIN>" --nim <NIM>')
        print(f"\n    HTML mentah: {client.cfg.raw_dir / 'transcript_find.html'}")
        return 1

    for c in cands:
        print(f"  {c['href']:<52} {c['text'][:38]:<40} ({c['source']})")

    if args.url:
        print(f"\n[i] Mencoba kandidat pertama: {cands[0]['href']}")
        return _transcript_run(client, cands[0]["href"], args)

    print(f"\n[i] Uji salah satu dengan:  .\\run.bat transcript --url \"<href>\" --nim <NIM>")
    return 0


def _transcript_run(client, url: str, args) -> int:
    if args.nim:
        joiner = "&" if "?" in url else "?"
        url = f"{url}{joiner}nim={args.nim}"

    html = client.get_text(url, note=f"transcript:{args.nim or 'self'}")
    raw = client.save_raw(f"transcript_{args.nim or 'self'}", html)

    if client._looks_login_page(html):
        print("[x] Server mengembalikan halaman login - sesi tidak valid")
        return 2

    result = parse_transcript(html)
    ident = result["identity"]
    summary = result["summary"]
    courses = result["courses"]

    print(f"\n[ok] URL  : {url}")
    print(f"     HTML : {raw}")
    print("\n--- Identitas ---")
    if ident:
        for k, v in ident.items():
            print(f"  {k:<10} {v}")
    else:
        print("  (tidak terdeteksi)")

    print("\n--- Ringkasan ---")
    print(f"  jumlah MK   : {summary['jumlah_mk_diambil']}")
    print(f"  total SKS   : {summary['total_sks']}")
    print(f"  IPS (hitung): {summary['ips']}")
    if summary.get("ips_dokumen"):
        print(f"  IPS (dokumen): {summary['ips_dokumen']}")
    if summary.get("ipk"):
        print(f"  IPK (dokumen): {summary['ipk']}")

    if not courses:
        print("\n[!] Tidak ada mata kuliah terbaca.")
        print(f"    Periksa {raw} lalu sesuaikan transcript.py bila perlu.")
        return 1

    outdir = client.cfg.out_dir
    nim = args.nim or ident.get("nim") or "unknown"
    csv_path = outdir / f"transkrip_{nim}.csv"
    write_csv(courses, csv_path)
    print(f"\n[ok] {len(courses)} mata kuliah -> {csv_path}")

    if args.print_head:
        print(f"\n--- Contoh {args.print_head} baris ---")
        for c in courses[: args.print_head]:
            sks = c["sks"] if c["sks"] is not None else "-"
            mutu = c["mutu"] if c["mutu"] is not None else "-"
            print(f"  {c['kode']:<12} {c['mata_kuliah'][:38]:<40} {sks:>4} {c['nilai']:>4} {mutu:>5}")

    problems = validate_transcript(result)
    if problems:
        print("\n[!] Perlu diperiksa manual:")
        for p in problems[:8]:
            print(f"    - {p}")

    if args.json:
        json_path = outdir / f"transkrip_{nim}.json"
        json_path.write_text(
            json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(f"[ok] JSON -> {json_path}")
    return 0


def cmd_transcript(args) -> int:
    client = _client(args)
    _ensure_session(client)

    if not args.url:
        return cmd_transcript_find(
            argparse.Namespace(
                path=args.path,
                url=None,
                nim=args.nim,
                print_head=args.print_head,
                json=args.json,
            )
        )
    return _transcript_run(client, args.url, args)


def cmd_transcript_batch(args) -> int:
    client = _client(args)
    _ensure_session(client)

    nim_file = Path(args.file)
    if not nim_file.exists():
        print(f"[x] File NIM tidak ditemukan: {nim_file}")
        return 1

    nims = read_nim_file(nim_file)
    if not nims:
        print(f"[x] Tidak ada NIM terbaca dari {nim_file}")
        print("    Format: satu NIM per baris, atau kolom pertama CSV.")
        return 1

    if args.delay:
        delay = max(args.delay, 0.5)
        client.cfg.rate.min_delay = delay
        client.cfg.rate.max_delay = delay + 1.0
        client.limiter.min_delay = delay
        client.limiter.max_delay = delay + 1.0
        print(f"[i] Jeda antar request: {delay}-{delay + 1.0} detik")

    state_name = "batch_state.json"
    for p in client.cfg.out_dir.glob("batch_state*.json"):
        state_name = p.name
        break

    runner = BatchRunner(
        client,
        url_template=args.url,
        out_dir=client.cfg.out_dir,
        state_path=client.cfg.out_dir / state_name,
        keep_html=not args.no_html,
        per_student_files=args.per_mahasiswa,
        on_event=lambda kind, data: print(format_progress({"kind": kind, **data}), flush=True),
    )

    if args.reset:
        runner.reset_state()
        print("[i] Checkpoint dihapus, mulai dari awal")

    if args.limit:
        print(f"[i] Batas proses ini: {args.limit} NIM")

    summary = runner.run(
        nims,
        limit=args.limit,
        resume=not args.no_resume,
        retry_failed=args.retry_failed,
    )

    return 0 if summary["gagal"] == 0 and summary["pending"] == 0 else 1


def cmd_bulk_check(args) -> int:
    client = _client(args)
    _ensure_session(client)

    html = client.get_text(args.url, note="bulk_check")
    client.save_raw("bulk_check", html)
    info = verify_bulk_export(html)

    print(f"[i] Halaman: {args.url or 'beranda'}")
    if info["count"] == 0:
        print("\n[!] Tidak ditemukan tombol/link ekspor pada halaman ini.")
        print("    Kalau tidak ada fitur ekspor, pakai transcript-batch")
        print("    (request berurutan dengan jeda, aman untuk server).")
        return 1

    print(f"\n[ok] {info['count']} kandidat fitur ekspor ditemukan:")
    for item in info["found"]:
        print(f"  {item['href'] or '(tombol/POST)':<48} {item['label'][:36]}")
    print("\nFitur ekspor server jauh lebih cepat dan lebih ramah server")
    print("daripada iterasi per NIM. Periksa dulu di browser.")
    return 0


def cmd_audit(args) -> int:
    client = _client(args)
    print(json.dumps(client.audit.summary(), indent=2, ensure_ascii=False))
    return 0


def cmd_yudisium(args) -> int:
    cfg = load_config()
    if args.base_url:
        cfg.base_url = args.base_url
        cfg.validate()
    try:
        return run_yudisium(args, cfg)
    except Exception as exc:  # noqa: BLE001
        print(f"[x] {exc}")
        return 1


# ---------- parser ----------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ecampuz", description=__doc__)
    p.add_argument("--base-url", help="override base URL")
    p.add_argument("--no-audit", action="store_true", help="matikan audit log")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("login", help="login interaktif (captcha diisi manual)").set_defaults(func=cmd_login)
    lp = p.add_argument_group("login")
    lp.add_argument("--force", action="store_true", help="paksa login ulang walau sesi valid")
    lp.add_argument(
        "--reset-creds",
        action="store_true",
        help="hapus kredensial tersimpan sebelum login",
    )

    lb = sub.add_parser(
        "login-browser",
        help="login lewat browser sungguhan (cadangan kalau login biasa ditolak)",
    )
    lb.add_argument("--headless", action="store_true", help="tanpa jendela terlihat")
    lb.add_argument("--reset", action="store_true", help="hapus sesi lama dulu")
    lb.add_argument("--reset-creds", action="store_true", help="hapus kredensial dan minta akun baru")
    lb.add_argument("--timeout", type=int, default=180, help="batas tunggu detik")
    lb.set_defaults(func=cmd_login_browser)

    dhs = sub.add_parser("dhs", help="transkrip nilai (Detail Hasil Studi)")
    dhs.add_argument("--nim", help="satu NIM")
    dhs.add_argument("--file", help="file NIM (satu per baris / kolom CSV)")
    dhs.add_argument("--angkatan", required=True, help="tahun angkatan, mis. 2024")
    dhs.add_argument("--prodi", default="all", help="'all' atau kode prodi")
    dhs.add_argument("--out", help="folder output")
    dhs.add_argument("--limit", type=int, help="maksimal NIM proses ini")
    dhs.add_argument("--per-mahasiswa", action="store_true", help="1 CSV+JSON per NIM")
    dhs.add_argument("--reset", action="store_true", help="ulang dari awal")
    dhs.add_argument("--retry-failed", action="store_true", help="coba lagi yang gagal")
    dhs.add_argument("--quiet", action="store_true", help="tanpa cetak detail")
    dhs.add_argument(
        "--neo-feeder-fallback",
        action="store_true",
        help="isi nilai yang kosong dari Neo Feeder jika cocok unik; butuh konfigurasi neo-feeder",
    )
    dhs.set_defaults(func=cmd_dhs)

    nf = sub.add_parser("neo-feeder", help="konfigurasi/inspeksi sumber nilai Neo Feeder")
    nf_sub = nf.add_subparsers(dest="action", required=True)
    nf_config = nf_sub.add_parser("configure", help="simpan endpoint dan kredensial DPAPI")
    nf_config.add_argument("--url", required=True, help="URL web service Neo Feeder")
    nf_config.add_argument("--allow-insecure-http", action="store_true", help="opt-in eksplisit HTTP tanpa enkripsi")
    nf_config.add_argument("--no-verify-tls", action="store_true", help="nonaktifkan verifikasi sertifikat HTTPS")
    nf_config.set_defaults(func=cmd_neo_feeder)
    nf_status = nf_sub.add_parser("status", help="lihat status konfigurasi tanpa request jaringan")
    nf_status.set_defaults(func=cmd_neo_feeder)
    nf_inspect = nf_sub.add_parser("inspect", help="ambil GetToken/GetDictionary untuk membaca schema API")
    nf_inspect.set_defaults(func=cmd_neo_feeder)

    niu = sub.add_parser(
        "niu",
        help="filter daftar mahasiswa via form NIM (read-only)",
    )
    niu.add_argument("--path", default="nilai/", help="path halaman list, mis. 'nilai/'")
    niu.add_argument("--act", required=True, help="nilai act dari halaman list")
    niu.add_argument("--nim", help="satu NIM")
    niu.add_argument("--nim-from", help="NIM awal (rentang)")
    niu.add_argument("--nim-to", help="NIM akhir (rentang)")
    niu.add_argument("--prodi", default="all", help="'all' atau kode prodi")
    niu.add_argument("--ang-from", help="angkatan awal")
    niu.add_argument("--ang-to", help="angkatan akhir")
    niu.add_argument("--nama", help="filter nama")
    niu.add_argument("--show", type=int, default=10, help="jumlah baris ditampilkan")
    niu.set_defaults(func=cmd_niu)

    cr = sub.add_parser("creds", help="kelola kredensial tersimpan (DPAPI)")
    cr.add_argument("action", choices=["status", "clear"], nargs="?", default="status")
    cr.set_defaults(func=cmd_creds)

    sub.add_parser("check", help="cek sesi masih hidup").set_defaults(func=cmd_check)

    d = sub.add_parser("discover", help="petakan seluruh link menu")
    d.add_argument("--path", default="", help="path awal relatif, default beranda")
    d.add_argument("--out", help="tujuan JSON")
    d.add_argument("--limit", type=int, default=60, help="maks link ditampilkan")
    d.set_defaults(func=cmd_discover)

    e = sub.add_parser("extract", help="ambil tabel dari satu URL")
    e.add_argument("--url", required=True)
    e.add_argument("--name", required=True)
    e.add_argument("--out-dir")
    e.add_argument("--min-rows", type=int, default=1)
    e.add_argument("--print-head", type=int, default=3)
    e.add_argument("--profile", action="store_true", help="dump data pribadi jika tanpa tabel")
    e.set_defaults(func=cmd_extract)

    a = sub.add_parser("extract-auto", help="ambil tabel dan langsung simpan CSV")
    a.add_argument("--path", required=True)
    a.add_argument("--name", required=True)
    a.add_argument("--min-rows", type=int, default=1)
    a.set_defaults(func=cmd_extract_auto)

    tb = sub.add_parser(
        "transcript-batch",
        help="ambil transkrip banyak NIM (dari file), bisa dilanjutkan &mdash;",
    )
    tb.add_argument("--file", required=True, help="file daftar NIM (satu per baris / kolom CSV)")
    tb.add_argument("--url", required=True, help="endpoint transkrip; boleh pakai {nim}")
    tb.add_argument("--limit", type=int, help="maksimal NIM pada proses ini")
    tb.add_argument("--no-resume", action="store_true", help="ulang dari awal, abaikan checkpoint")
    tb.add_argument(
        "--retry-failed",
        action="store_true",
        help="coba lagi NIM yang sebelumnya gagal",
    )
    tb.add_argument("--per-mahasiswa", action="store_true", help="buat 1 CSV + 1 JSON per NIM")
    tb.add_argument("--no-html", action="store_true", help="simpan HTML mentah (default: simpan)")
    tb.add_argument("--delay", type=float, help="jeda minimum detik antar request")
    tb.add_argument("--reset", action="store_true", help="hapus checkpoint lalu mulai baru")
    tb.set_defaults(func=cmd_transcript_batch)

    yu = sub.add_parser(
        "yudisium",
        help="inspeksi atau isi form pendaftaran yudisium secara berurutan",
    )
    yu.add_argument("--inspect", action="store_true", help="buka halaman dan tampilkan struktur form/tabel/aksi")
    yu.add_argument("--file", help="file input .csv/.xlsx/.xlsm")
    yu.add_argument("--nim-column", default="NIM", help="nama kolom NIM pada file input")
    yu.add_argument("--sheet", help="nama sheet Excel; default sheet aktif")
    yu.add_argument("--url", help="URL halaman tambah mahasiswa yang aktif pada sesi eCampuz ini")
    yu.add_argument("--campaign-id", help="ID sesi yudisium untuk checkpoint; default diambil dari URL target")
    yu.add_argument("--limit", type=int, help="batas jumlah mahasiswa; wajib untuk submit")
    yu.add_argument("--submit", action="store_true", help="izinkan submit setelah konfirmasi teks eksplisit")
    yu.add_argument("--retry-failed", action="store_true", help="coba ulang baris yang gagal sebelum submit")
    yu.add_argument("--state-file", help="lokasi checkpoint; default data/state/yudisium_<file>_state.json")
    yu.add_argument("--log-file", help="CSV log hasil; default data/out/yudisium_<file>_log.csv")
    yu.add_argument("--user-data-dir", help="direktori profil browser persisten; default data/state/yudisium_browser_profile")
    yu.add_argument("--no-reset-filter", action="store_true", help="jangan klik RESET sebelum tiap NIM")
    yu.add_argument("--delay", type=float, default=3.0, help="jeda antar pendaftaran sukses, minimum 1.5 detik")
    yu.add_argument("--timeout", type=int, default=45, help="batas tunggu penanda sukses dalam detik")
    yu.set_defaults(func=cmd_yudisium)

    tc = sub.add_parser(
        "bulk-check",
        help="cek apakah server punya fitur ekspor massal (lebih cepat dari iterasi)",
    )
    tc.add_argument("--url", default="", help="halaman yang mungkin punya tombol ekspor")
    tc.set_defaults(func=cmd_bulk_check)

    tr = sub.add_parser(
        "transcript",
        help="ambil transkrip nilai (deteksi endpoint otomatis bila --url kosong)",
    )
    tr.add_argument("--url", help="endpoint transkrip; kosongkan untuk auto-detect")
    tr.add_argument("--path", default="", help="path untuk auto-detect, default beranda")
    tr.add_argument("--nim", help="NIM mahasiswa; kosongkan untuk transkrip akun sendiri")
    tr.add_argument("--print-head", type=int, default=8)
    tr.add_argument("--json", action="store_true", help="tulis juga JSON")
    tr.set_defaults(func=cmd_transcript)

    sub.add_parser("audit", help="ringkasan audit log").set_defaults(func=cmd_audit)

    web = sub.add_parser("web", help="jalankan web dashboard (terintegrasi bot)")
    web.add_argument("--host", default="127.0.0.1")
    web.add_argument("--port", type=int, default=8000)
    web.add_argument("--debug", action="store_true")
    web.add_argument("--open", action="store_true", help="buka browser otomatis")
    web.set_defaults(func=cmd_web)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except SessionExpired as exc:
        print(f"[x] Sesi habis: {exc}")
        print("    Jalankan ulang: python -m ecampuz.cli login")
        return 2
    except KeyboardInterrupt:
        print("\n[x] Dibatalkan")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
