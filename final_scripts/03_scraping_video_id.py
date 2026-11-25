"""
03 - Scraping Video ID per Akun Influencer
=============================================
Bagian dari tahap 3.1.2.3 (Pengumpulan Metadata Konten) pada proposal TA --
mengumpulkan daftar video_id tiap influencer sebelum metadata lengkapnya
diambil di tahap berikutnya (04_scraping_metadata_konten.py).

Alur:
1. scrape_video_ids()
   Membuka profil tiap username (Playwright, pakai cookies hasil
   00_setup_tiktok_login.py), scroll grid profile, kumpulkan video_id +
   tanggal upload (dihitung langsung dari video_id, tanpa buka video
   satu-satu). Resume-aware per influencer + dedup per video_id.
   Video lebih lama dari batas bawah rentang tanggal menghentikan
   scraping untuk influencer itu (grid terurut baru->lama).
2. rekap_jumlah_video()
   Hitung jumlah video per influencer yang masuk rentang tanggal target
   (needs_comment_scrape=True) -- buat spot check sebelum lanjut ke tahap
   metadata/komentar.

Konsolidasi dari:
- 01_pengumpulan_data/03_videoid_perakun/01_get_videoid.py
- 01_pengumpulan_data/03_videoid_perakun/02_rekap_jumlah_vid_perinfluencer.py

Dicek & SENGAJA TIDAK diikutkan:
- 01_1_videoid_with_audio.py -- walau namanya mirip, isinya scraping
  METADATA PROFIL+VIDEO (bukan video_id), ditulis ke folder
  04_mendapatkan_metadata_akun. Ini salah tempat/salah nama, jadi
  dikonsolidasikan sebagai bagian dari 04_scraping_metadata_konten.py,
  bukan di sini.
- test.py -- script debug cek login playwright, bukan bagian pipeline data.

CATATAN EKSEKUSI: scrape_video_ids() scraping berat (Playwright, delay
3-7 menit per influencer) -- SKIP otomatis kalau final_scripts/output/
03_video_ids_master.csv sudah ada. Hapus manual kalau memang mau scraping
ulang / nambah periode tanggal baru.
"""

import asyncio
import csv
import os
import random
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from collections import Counter

from playwright.async_api import async_playwright

# -------------------------------------------------------------------------
# KONFIGURASI PATH
# -------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "01_pengumpulan_data"
FINAL_OUTPUT_ROOT = Path(__file__).resolve().parent / "output"
OUTPUT_DIR = FINAL_OUTPUT_ROOT / "03_scraping_video_id"

USERNAMES_FILE = FINAL_OUTPUT_ROOT / "02_filter_akun_influencer" / "username_influencer_terpilih.txt"
PROFILE_DIR = DATA_DIR / "00_akun_playwright" / "tiktok_browser_profile"
WARNING_MP3 = DATA_DIR / "03_videoid_perakun" / "warning.mp3"  # file audio, bukan data -- tetap di lokasi lama
OUTPUT_CSV = OUTPUT_DIR / "video_ids_master.csv"
REKAP_CSV = OUTPUT_DIR / "rekap_video_per_influencer.csv"

CSV_FIELDS = ["username", "video_id", "video_url", "post_date_wib", "needs_comment_scrape"]

# Rentang tanggal video yang relevan untuk penelitian (WIB)
WIB = timezone(timedelta(hours=7))
RANGE_START = datetime(2026, 1, 1, 0, 0, 0, tzinfo=WIB)
RANGE_END = datetime(2026, 8, 31, 23, 59, 59, tzinfo=WIB)

BATCH_SIZE = 20
DELAY_BETWEEN_INFLUENCER = (3 * 60, 7 * 60)  # detik
DELAY_BETWEEN_SCROLL = (20, 30)              # detik
MAX_SCROLL_PER_INFLUENCER = 250
SAME_COUNT_STREAK_LIMIT = 20
PINNED_BUFFER = 5  # video ke-1..5 gak trigger stop (kemungkinan pinned)

REALISTIC_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)


# -------------------------------------------------------------------------
# AUDIO ALERT (captcha/login wall)
# -------------------------------------------------------------------------
def _play_warning_sound():
    if not WARNING_MP3.exists():
        return
    try:
        subprocess.Popen(
            ["powershell", "-c", f"(New-Object Media.SoundPlayer '{WARNING_MP3}').PlaySync()"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception as e:
        print(f"   Gagal jalanin audio alert: {e}")


# -------------------------------------------------------------------------
# HELPERS
# -------------------------------------------------------------------------
def _video_id_to_wib(video_id: str) -> datetime:
    vid_int = int(video_id)
    timestamp = vid_int >> 32
    return datetime.fromtimestamp(timestamp, tz=WIB)


def _needs_scrape(dt_wib: datetime) -> bool:
    return RANGE_START <= dt_wib <= RANGE_END


def _is_older_than_range(dt_wib: datetime) -> bool:
    return dt_wib < RANGE_START


def _read_usernames() -> list[str]:
    with open(USERNAMES_FILE, "r", encoding="utf-8") as f:
        return [line.strip().lstrip("@") for line in f if line.strip()]


def _load_existing_data() -> dict[str, set]:
    existing = {}
    if not OUTPUT_CSV.exists():
        return existing
    with open(OUTPUT_CSV, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            existing.setdefault(row["username"], set()).add(row["video_id"])
    return existing


def _build_resume_plan(username_list, existing_by_user) -> list[tuple[str, str]]:
    """Mode per username: 'skip' (udah selesai), 'resume' (cek ulang terakhir), 'fresh'."""
    usernames_with_data = set(existing_by_user.keys())
    if not usernames_with_data:
        return [(u, "fresh") for u in username_list]

    last_with_data_idx = -1
    for i, u in enumerate(username_list):
        if u in usernames_with_data:
            last_with_data_idx = i

    plan = []
    for i, u in enumerate(username_list):
        if i < last_with_data_idx:
            plan.append((u, "skip"))
        elif i == last_with_data_idx:
            plan.append((u, "resume"))
        else:
            plan.append((u, "fresh"))
    return plan


def _append_rows_to_csv(rows):
    if not rows:
        return
    file_exists = OUTPUT_CSV.exists()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_CSV, "a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        if not file_exists:
            writer.writeheader()
        writer.writerows(rows)
        f.flush()
        os.fsync(f.fileno())


async def _wait_for_login_wall_clear(page, timeout=180) -> bool:
    blocker_selectors = [
        'text="Log in to TikTok"',
        '[data-e2e="login-modal"]',
        'div[role="dialog"]:has-text("Log in")',
        'div[role="dialog"]:has-text("Continue with")',
        'text="Drag the slider to fit the puzzle"',
        '[id*="captcha"]',
        '[class*="captcha"]',
        'iframe[src*="captcha"]',
    ]
    start = asyncio.get_event_loop().time()
    was_found = False
    while asyncio.get_event_loop().time() - start < timeout:
        found = False
        for sel in blocker_selectors:
            try:
                elem = await page.query_selector(sel)
                if elem and await elem.is_visible():
                    found = True
                    break
            except Exception:
                continue
        if not found:
            if was_found:
                print("   Login wall/captcha hilang, lanjut.")
                await asyncio.sleep(random.uniform(2, 4))
            return True
        if not was_found:
            print("\n" + "=" * 70)
            print("   LOGIN WALL / CAPTCHA TERDETEKSI - solve manual di browser.")
            print("=" * 70)
            _play_warning_sound()
        was_found = True
        await asyncio.sleep(3)
    print("\n   Timeout nunggu login wall, lanjut dengan hati-hati.")
    return False


# -------------------------------------------------------------------------
# STEP 1 - Scrape video_id per influencer
# -------------------------------------------------------------------------
async def _scrape_influencer(page, username: str, already_known_ids: set) -> int:
    profile_url = f"https://www.tiktok.com/@{username}"
    print(f"\n{'=' * 70}\n@{username} -> {profile_url}\n{'=' * 70}")

    try:
        await page.goto(profile_url, wait_until="load", timeout=60000)
    except Exception as e:
        print(f"   Gagal buka profile: {e}")
        return 0

    await asyncio.sleep(random.uniform(3, 5))
    await _wait_for_login_wall_clear(page, timeout=180)

    try:
        await page.wait_for_selector('a[href*="/video/"]', timeout=60000)
    except Exception:
        print("   Grid video gak muncul (akun kosong / private / gak ada?)")
        return 0

    await _wait_for_login_wall_clear(page, timeout=120)

    session_seen_ids = set(already_known_ids)
    batch, total_written, same_count_streak, scroll_count = [], 0, 0, 0
    stop_influencer, video_index = False, 0

    while scroll_count < MAX_SCROLL_PER_INFLUENCER and not stop_influencer:
        links = await page.query_selector_all('a[href*="/video/"]')
        new_this_pass = 0

        for link in links:
            href = await link.get_attribute("href")
            if not href or "/video/" not in href:
                continue
            try:
                vid = href.rstrip("/").split("/video/")[-1].split("?")[0]
                int(vid)
            except Exception:
                continue
            if vid in session_seen_ids:
                continue

            session_seen_ids.add(vid)
            new_this_pass += 1
            video_index += 1

            dt_wib = _video_id_to_wib(vid)
            batch.append({
                "username": username,
                "video_id": vid,
                "video_url": href if href.startswith("http") else f"https://www.tiktok.com{href}",
                "post_date_wib": dt_wib.strftime("%Y-%m-%d %H:%M:%S"),
                "needs_comment_scrape": _needs_scrape(dt_wib),
            })

            if video_index > PINNED_BUFFER and _is_older_than_range(dt_wib):
                print(f"   Video {vid} ({dt_wib.date()}) lebih lama dari batas bawah, stop setelah batch ini.")
                stop_influencer = True

            if len(batch) >= BATCH_SIZE:
                _append_rows_to_csv(batch)
                total_written += len(batch)
                print(f"   Tersimpan {len(batch)} video (total: {total_written})")
                batch = []

            if stop_influencer:
                break

        if stop_influencer:
            break

        if new_this_pass == 0:
            same_count_streak += 1
            print(f"   Scroll #{scroll_count} - 0 video baru (streak: {same_count_streak}/{SAME_COUNT_STREAK_LIMIT})")
        else:
            same_count_streak = 0
            print(f"   Scroll #{scroll_count} - {new_this_pass} video baru")

        if same_count_streak >= SAME_COUNT_STREAK_LIMIT:
            print("   Sudah sampai video paling lama di profil ini.")
            break

        try:
            await page.evaluate("window.scrollBy(0, 1400)")
        except Exception:
            pass

        if same_count_streak > 0 and same_count_streak % 4 == 0:
            try:
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            except Exception:
                pass

        scroll_count += 1
        await asyncio.sleep(random.uniform(*DELAY_BETWEEN_SCROLL))

    if batch:
        _append_rows_to_csv(batch)
        total_written += len(batch)

    print(f"   Selesai @{username}: {total_written} video baru tercatat.")
    return total_written


async def scrape_video_ids() -> None:
    if OUTPUT_CSV.exists():
        print(f"[Step 1] Sudah ada -> {OUTPUT_CSV} (skip scraping berat, data lama dipakai)")
        return

    username_list = _read_usernames()
    print(f"Total influencer di daftar: {len(username_list)}")

    existing_by_user = _load_existing_data()
    plan = _build_resume_plan(username_list, existing_by_user)

    n_skip = sum(1 for _, m in plan if m == "skip")
    n_resume = sum(1 for _, m in plan if m == "resume")
    n_fresh = sum(1 for _, m in plan if m == "fresh")
    print(f"  Skip (selesai)  : {n_skip}")
    print(f"  Resume (cek ulang): {n_resume}")
    print(f"  Fresh (baru)    : {n_fresh}")

    to_process = [(u, m) for u, m in plan if m != "skip"]
    if not to_process:
        print("Semua influencer sudah selesai diproses.")
        return

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            str(PROFILE_DIR),
            headless=False,
            viewport={"width": 1400, "height": 900},
            user_agent=REALISTIC_UA,
            args=["--disable-blink-features=AutomationControlled"],
        )
        await context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        )
        page = context.pages[0] if context.pages else await context.new_page()

        for idx, (username, mode) in enumerate(to_process, start=1):
            known_ids = existing_by_user.get(username, set()) if mode == "resume" else set()
            print(f"\n### [{idx}/{len(to_process)}] mode={mode} ###")
            try:
                await _scrape_influencer(page, username, known_ids)
            except Exception as e:
                print(f"   ERROR tak terduga buat @{username}: {e}")

            if idx < len(to_process):
                delay = random.uniform(*DELAY_BETWEEN_INFLUENCER)
                print(f"\nJeda {delay / 60:.1f} menit sebelum influencer berikutnya...")
                await asyncio.sleep(delay)

        print(f"\nSelesai semua influencer. Hasil: {OUTPUT_CSV}")
        await context.close()


# -------------------------------------------------------------------------
# STEP 2 - Rekap jumlah video per influencer (dalam rentang tanggal target)
# -------------------------------------------------------------------------
def _parse_needs_scrape(value: str) -> bool:
    return str(value).strip().lower() == "true"


def rekap_jumlah_video(input_csv: Path = OUTPUT_CSV, output_csv: Path = REKAP_CSV) -> None:
    if output_csv.exists():
        print(f"[Rekap] Sudah ada -> {output_csv} (skip, tidak diproses ulang)")
        return

    if not input_csv.exists():
        raise FileNotFoundError(f"File tidak ditemukan: {input_csv}")

    counter = Counter()
    total_rows = total_true = 0

    with open(input_csv, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            total_rows += 1
            if _parse_needs_scrape(row.get("needs_comment_scrape", "")):
                counter[row["username"]] += 1
                total_true += 1

    if not counter:
        print("[Rekap] Tidak ada video dengan needs_comment_scrape=True.")
        return

    sorted_counts = counter.most_common()
    with open(output_csv, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["username", "jumlah_video"])
        writer.writerows(sorted_counts)

    print(f"[Rekap] Total baris master: {total_rows} | needs_scrape=True: {total_true} | influencer: {len(counter)}")
    print(f"[Rekap] Tersimpan -> {output_csv}")


# -------------------------------------------------------------------------
# MAIN
# -------------------------------------------------------------------------
async def main():
    await scrape_video_ids()
    rekap_jumlah_video()


if __name__ == "__main__":
    asyncio.run(main())
