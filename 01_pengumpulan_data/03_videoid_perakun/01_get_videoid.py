"""
TAHAP 1 — Kumpulin video_id + tanggal post dari profile TikTok tiap
influencer. TIDAK buka video satu-satu (cuma scroll grid profile),
karena video_id + tanggal bisa dihitung langsung dari video_id-nya.

Output: CSV gabungan (semua influencer) dengan kolom:
    username, video_id, video_url, post_date_wib, needs_comment_scrape

Fitur:
- Resume otomatis: influencer yang udah 100% selesai di run
  sebelumnya di-skip. Influencer TERAKHIR yang tercatat di CSV bakal
  di-scrape ULANG (dedup otomatis) buat mastiin gak ada video yang
  kelewat kalau crash di tengah batch.
- Dedup: video_id yang udah ada di CSV gak akan ditulis dobel.
- Batch write per 20 video (atau kurang kalau distop lebih awal),
  biar minim resiko kehilangan data kalau script gagal di tengah jalan.
- Stop scraping utk 1 influencer begitu ketemu video yang LEBIH LAMA
  dari batas bawah rentang (1 Jan 2026 WIB) -- video di grid profile
  urut dari terbaru ke terlama, jadi kalau udah ketemu yang lebih
  lama dari itu, sisanya pasti lebih lama lagi -> gak perlu diteruskan.
- Video yang lebih BARU dari batas atas (31 Agu 2026 WIB) tetap
  dicatat (needs_comment_scrape=False) tapi TIDAK menghentikan
  scraping, karena video yang lebih lama masih mungkin masuk rentang.
- Delay 3-7 menit (random) antar influencer, 5-10 detik antar scroll.
- 🔊 AUDIO ALERT: Jalanin warning sound saat captcha/login-wall
  terdeteksi. Kamu bisa langsung tahu ada yang perlu di-handle manual.
"""

import asyncio
import csv
import os
import random
import subprocess
from datetime import datetime, timedelta, timezone

from playwright.async_api import async_playwright

# ============================================================
# KONFIGURASI PATH
# ============================================================
ROOT_DIR = r"C:\Users\nesha\Tugas_akhir\seleksi-influencer"
DATA_DIR = os.path.join(ROOT_DIR, "01_pengumpulan_data")

USERNAMES_FILE = os.path.join(DATA_DIR, "02_filter_akun_folls_like_bio", "usernames_only.txt")
PROFILE_DIR = os.path.join(DATA_DIR, "00_akun_playwright", "tiktok_browser_profile")
OUTPUT_DIR = os.path.join(DATA_DIR, "03_videoid_perakun")
OUTPUT_CSV = os.path.join(OUTPUT_DIR, "video_ids_master.csv")
WARNING_MP3 = os.path.join(OUTPUT_DIR, "warning.mp3")  # gak benar2 mp3, tapi placeholder

CSV_FIELDS = ["username", "video_id", "video_url", "post_date_wib", "needs_comment_scrape"]

# ============================================================
# KONFIGURASI RENTANG TANGGAL (WIB, konsisten)
# ============================================================
WIB = timezone(timedelta(hours=7))
RANGE_START = datetime(2026, 1, 1, 0, 0, 0, tzinfo=WIB)
RANGE_END = datetime(2026, 8, 31, 23, 59, 59, tzinfo=WIB)

# ============================================================
# KONFIGURASI SCRAPING
# ============================================================
BATCH_SIZE = 20
DELAY_BETWEEN_INFLUENCER = (3 * 60, 7 * 60)   # 3-7 menit (detik)
DELAY_BETWEEN_SCROLL = (20, 30)                 # detik
MAX_SCROLL_PER_INFLUENCER = 250                # safety cap
SAME_COUNT_STREAK_LIMIT = 20                   # anggap "habis" kalau gak nambah 20x scroll berturut2

REALISTIC_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)


# ============================================================
# AUDIO ALERT
# ============================================================
def play_warning_sound():
    """
    Jalanin file warning.mp3 dari folder 03_videoid_perakun saat captcha ada.
    """
    warning_file = os.path.join(OUTPUT_DIR, "warning.mp3")
    if not os.path.exists(warning_file):
        print(f"   ⚠️ File {warning_file} gak ketemu, skip audio alert")
        return
    try:
        # Windows: pakai 'start' command buat jalanin mp3 di player default
        # atau pakai powershell
        subprocess.Popen(
            ["powershell", "-c", f"(New-Object Media.SoundPlayer '{warning_file}').PlaySync()"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
    except Exception as e:
        print(f"   ⚠️ Gagal jalanin audio: {e}")


# ============================================================
# HELPER: video_id -> tanggal WIB
# ============================================================
def video_id_to_wib(video_id: str):
    vid_int = int(video_id)
    timestamp = vid_int >> 32
    return datetime.fromtimestamp(timestamp, tz=WIB)


def needs_scrape(dt_wib: datetime) -> bool:
    return RANGE_START <= dt_wib <= RANGE_END


def is_older_than_range(dt_wib: datetime) -> bool:
    return dt_wib < RANGE_START


# ============================================================
# HELPER: baca daftar username
# ============================================================
def read_usernames():
    with open(USERNAMES_FILE, "r", encoding="utf-8") as f:
        return [line.strip().lstrip("@") for line in f if line.strip()]


# ============================================================
# HELPER: baca CSV existing, hitung rencana resume
# ============================================================
def load_existing_data():
    """
    Return:
        existing_video_ids_by_user: dict[username] -> set(video_id)
        all_usernames_in_csv: set of username yang punya >=1 baris
    """
    existing_video_ids_by_user = {}
    if not os.path.exists(OUTPUT_CSV):
        return existing_video_ids_by_user

    with open(OUTPUT_CSV, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            u = row["username"]
            existing_video_ids_by_user.setdefault(u, set()).add(row["video_id"])

    return existing_video_ids_by_user


def build_resume_plan(username_list, existing_video_ids_by_user):
    """
    Tentukan: siapa yang di-skip (udah selesai), siapa yang di-scrape
    ulang (influencer terakhir yang punya data), siapa yang scrape normal.

    Return: list of (username, mode) dengan mode in {"skip", "resume", "fresh"}
    """
    usernames_with_data = set(existing_video_ids_by_user.keys())

    if not usernames_with_data:
        return [(u, "fresh") for u in username_list]

    # cari username TERAKHIR (menurut urutan di file list) yang punya data di CSV
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


# ============================================================
# HELPER: tulis batch ke CSV (append, auto-buat header kalau baru)
# ============================================================
def append_rows_to_csv(rows):
    if not rows:
        return
    file_exists = os.path.exists(OUTPUT_CSV)
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    with open(OUTPUT_CSV, "a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        if not file_exists:
            writer.writeheader()
        writer.writerows(rows)
        f.flush()
        os.fsync(f.fileno())


# ============================================================
# LOGIN WALL / CAPTCHA GUARD dengan AUDIO ALERT
# ============================================================
async def wait_for_login_wall_clear(page, timeout=180):
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
                print("   ✅ Login wall/captcha hilang, lanjut.")
                await asyncio.sleep(random.uniform(2, 4))
            return True
        if not was_found:
            print("\n" + "="*70)
            print("   🔒 🔊 LOGIN WALL / CAPTCHA TERDETEKSI !!!")
            print("   ⚠️  Playing warning.mp3...")
            print("="*70)
            play_warning_sound()
        was_found = True
        print("   ⏳ Solve captcha / login manual, script nunggu...", end="\r")
        await asyncio.sleep(3)
    print("\n   ⚠️ Timeout nunggu login wall, lanjut dengan hati-hati.")
    return False


# ============================================================
# SCRAPE 1 INFLUENCER
# ============================================================
async def scrape_influencer(page, username, already_known_ids):
    """
    already_known_ids: set video_id yang udah ada di CSV buat username
    ini (dipakai kalau mode == "resume", supaya gak dobel nulis).

    Return: total_new_rows_written (int)
    """
    profile_url = f"https://www.tiktok.com/@{username}"
    print(f"\n{'='*70}")
    print(f"👤 @{username}  -> {profile_url}")
    print(f"{'='*70}")

    try:
        await page.goto(profile_url, wait_until="load", timeout=60000)
    except Exception as e:
        print(f"   ❌ Gagal buka profile: {e}")
        return 0

    await asyncio.sleep(random.uniform(3, 5))

    # Cek captcha/login-wall DULUAN -- ini bisa muncul sebelum grid
    # video sempat ke-render, jadi harus di-clear dulu sebelum nunggu
    # video links (yang bisa timeout kalau ketutup captcha duluan).
    await wait_for_login_wall_clear(page, timeout=180)

    try:
        await page.wait_for_selector('a[href*="/video/"]', timeout=60000)
    except Exception:
        print("   ⚠️ Gak ada video ditemukan / grid gak muncul (akun kosong / private / gak ada?)")
        return 0

    # Cek sekali lagi jaga-jaga captcha muncul pas grid baru selesai load
    await wait_for_login_wall_clear(page, timeout=120)

    session_seen_ids = set(already_known_ids)  # dedup gabungan (existing + session ini)
    batch = []
    total_written = 0
    same_count_streak = 0
    scroll_count = 0
    stop_influencer = False
    video_index = 0  # urutan video ke berapa yang ketemu di sesi ini
    PINNED_BUFFER = 5  # video ke-1 s/d ke-5 gak boleh trigger stop (bisa pinned)

    while scroll_count < MAX_SCROLL_PER_INFLUENCER and not stop_influencer:
        links = await page.query_selector_all('a[href*="/video/"]')

        new_this_pass = 0
        for link in links:
            href = await link.get_attribute("href")
            if not href or "/video/" not in href:
                continue

            try:
                vid = href.rstrip("/").split("/video/")[-1].split("?")[0]
                int(vid)  # pastikan numeric
            except Exception:
                continue

            if vid in session_seen_ids:
                continue

            session_seen_ids.add(vid)
            new_this_pass += 1
            video_index += 1

            dt_wib = video_id_to_wib(vid)
            row = {
                "username": username,
                "video_id": vid,
                "video_url": href if href.startswith("http") else f"https://www.tiktok.com{href}",
                "post_date_wib": dt_wib.strftime("%Y-%m-%d %H:%M:%S"),
                "needs_comment_scrape": needs_scrape(dt_wib),
            }
            batch.append(row)

            if video_index <= PINNED_BUFFER:
                if is_older_than_range(dt_wib):
                    print(f"   📌 Video {vid} ({dt_wib.date()}) lebih lama dari batas, TAPI ini "
                          f"video ke-{video_index} (kemungkinan pinned) -- gak trigger stop.")
            elif is_older_than_range(dt_wib):
                print(f"   🛑 Video {vid} ({dt_wib.date()}) lebih lama dari batas (1 Jan 2026)."
                      f" Stop scraping influencer ini setelah batch ini ditulis.")
                stop_influencer = True

            if len(batch) >= BATCH_SIZE:
                append_rows_to_csv(batch)
                total_written += len(batch)
                print(f"   💾 Tersimpan {len(batch)} video (total sejauh ini: {total_written})")
                batch = []

            if stop_influencer:
                break

        if stop_influencer:
            break

        if new_this_pass == 0:
            same_count_streak += 1
            print(f"   🔄 Scroll #{scroll_count} -- 0 video baru "
                  f"(streak gak nambah: {same_count_streak}/{SAME_COUNT_STREAK_LIMIT})")
        else:
            same_count_streak = 0
            print(f"   🔄 Scroll #{scroll_count} -- {new_this_pass} video baru "
                  f"(total terkumpul sesi ini: {len(session_seen_ids) - len(already_known_ids)})")

        if same_count_streak >= SAME_COUNT_STREAK_LIMIT:
            print("   ✅ Udah sampai video paling lama di profile ini (gak ada video baru lagi).")
            break

        # Scroll pakai JS (window.scrollBy), BUKAN mouse.wheel --
        # mouse.wheel bergantung posisi kursor, kalau kursor kebeturan
        # di area sidebar (fixed, gak ikut scroll), scroll jadi gak
        # ngefek ke grid video sama sekali.
        try:
            await page.evaluate("window.scrollBy(0, 1400)")
        except Exception:
            pass

        # Setiap kelipatan 4 scroll gagal berturut2, coba "lompat"
        # jauh ke bawah -- kadang grid butuh dorongan lebih kuat
        # buat trigger lazy-load berikutnya
        if same_count_streak > 0 and same_count_streak % 4 == 0:
            try:
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            except Exception:
                pass

        scroll_count += 1
        await asyncio.sleep(random.uniform(*DELAY_BETWEEN_SCROLL))

    # flush sisa batch yang belum 20 (akhir dari influencer ini)
    if batch:
        append_rows_to_csv(batch)
        total_written += len(batch)
        print(f"   💾 Tersimpan sisa {len(batch)} video (total: {total_written})")

    print(f"   🏁 Selesai @{username}: {total_written} video baru tercatat.")
    return total_written


# ============================================================
# MAIN
# ============================================================
async def main():
    username_list = read_usernames()
    print(f"📋 Total influencer di daftar: {len(username_list)}")

    existing_by_user = load_existing_data()
    plan = build_resume_plan(username_list, existing_by_user)

    n_skip = sum(1 for _, m in plan if m == "skip")
    n_resume = sum(1 for _, m in plan if m == "resume")
    n_fresh = sum(1 for _, m in plan if m == "fresh")
    print(f"   -> Skip (udah selesai)  : {n_skip}")
    print(f"   -> Resume (cek ulang)   : {n_resume}")
    print(f"   -> Fresh (belum mulai)  : {n_fresh}")

    to_process = [(u, m) for u, m in plan if m != "skip"]
    if not to_process:
        print("✅ Semua influencer sudah selesai diproses. Gak ada yang perlu di-scrape.")
        return

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            PROFILE_DIR,
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
                await scrape_influencer(page, username, known_ids)
            except Exception as e:
                print(f"   ❌ ERROR gak terduga buat @{username}: {e}")
                import traceback
                traceback.print_exc()
                print("   -> Lanjut ke influencer berikutnya.")

            if idx < len(to_process):
                delay = random.uniform(*DELAY_BETWEEN_INFLUENCER)
                mins = delay / 60
                print(f"\n😴 Jeda {mins:.1f} menit sebelum influencer berikutnya...")
                await asyncio.sleep(delay)

        print("\n\n✅✅✅ SEMUA INFLUENCER SELESAI DIPROSES ✅✅✅")
        print(f"Hasil tersimpan di: {OUTPUT_CSV}")

        await context.close()


if __name__ == "__main__":
    asyncio.run(main())