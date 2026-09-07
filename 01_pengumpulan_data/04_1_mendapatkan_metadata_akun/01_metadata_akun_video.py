"""
TAHAP 2 — Scrape metadata profil + metadata video dari TikTok.

Sumber: video_ids_master.csv (hasil tahap 1)
Output:
  - metadata_profil.csv : followers, following, bio, nama, verification, avatar_url
  - metadata_video.csv : like_count, comment_count, share_count, save_count, description

Fitur:
- DIRECT LINK NAVIGATION (v2 -> v4): Langsung goto ke video link @username/video/videoid
  (gak perlu scroll grid, gak perlu klik, gak perlu arrow key). Lebih cepat & reliable.
- Video ID selalu diambil dari master CSV, dipakai buat bikin link video.
- Lazy scraping: delay antar video (10-50s) + jeda panjang tiap 300 video
  (2-3 menit) + delay antar influencer (3-5 menit)
- Resume otomatis (v3): urutan proses TETAP SESUAI urutan asli di master
  CSV, cuma yang sudah profil+video lengkap yang di-skip.
- TIKTOK SHOP VIDEO DETECTION: video TikTok Shop (warning "can only be viewed
  in the TikTok app") DIBEDAIN dari captcha -> langsung skip, gak nunggu.
- EXTRACT METADATA (FIXED): nunggu elemen like-count BENERAN muncul + ada
  isinya sebelum extract (bukan cuma nunggu URL berubah). Kalau gagal load
  dalam waktu wajar, video di-skip (bukan nulis nol palsu) -> otomatis ke-retry.
- Write LANGSUNG per video ke CSV -> minimize data loss.
- Audio warning saat captcha (pygame)
- TELEGRAM ALERT saat captcha kedeteksi / solved / timeout
- Dedup: gak ulang video yang sudah ada di CSV
"""

import asyncio
import csv
import os
import sys
import random
import pygame
import requests
from datetime import datetime

from playwright.async_api import async_playwright


class CaptchaTimeoutError(Exception):
    """Dilempar saat captcha/login wall gak di-solve dalam batas waktu (10 menit)."""
    pass


# ============================================================
# KONFIGURASI PATH
# ============================================================
ROOT_DIR = r"C:\Users\nesha\Tugas_akhir\seleksi-influencer"
DATA_DIR = os.path.join(ROOT_DIR, "01_pengumpulan_data")

MASTER_CSV = os.path.join(DATA_DIR, "03_videoid_perakun", "video_ids_master.csv")
PROFILE_DIR = os.path.join(DATA_DIR, "00_akun_playwright", "tiktok_browser_profile")
WARNING_MP3 = os.path.join(DATA_DIR, "03_videoid_perakun", "warning.mp3")

OUTPUT_DIR = os.path.join(DATA_DIR, "04_1_mendapatkan_metadata_akun")
OUTPUT_PROFIL = os.path.join(OUTPUT_DIR, "metadata_profil.csv")
OUTPUT_VIDEO = os.path.join(OUTPUT_DIR, "metadata_video.csv")

PROFIL_FIELDS = ["username", "followers", "following", "bio", "display_name",
                 "is_verified", "avatar_url", "scraped_at"]
VIDEO_FIELDS = ["username", "video_id", "video_url", "like_count", "comment_count",
                "share_count", "save_count", "description", "scraped_at"]

# ============================================================
# KONFIGURASI SCRAPING (LAZY/SLOW, HUMAN-LIKE)
# ============================================================
DELAY_BETWEEN_VIDEO = (10, 40)             # jeda antar video
DELAY_BETWEEN_INFLUENCER = (30, 120)  # 3-5 menit antar influencer
DELAY_EVERY_N_VIDEOS = 300                  # tiap N video, jeda panjang
DELAY_AFTER_N_VIDEOS = (2 * 60, 3 * 60)    # 2-3 menit jeda panjang (anti-captcha)
METADATA_READY_TIMEOUT = 10000             # ms -- max nunggu elemen like-count

REALISTIC_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)


# ============================================================
# TELEGRAM ALERT
# ============================================================
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

try:
    from credentials import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
    TELEGRAM_ENABLED = bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)
    if TELEGRAM_ENABLED:
        print(f"✅ credentials.py ketemu di {ROOT_DIR} -> Telegram alert ENABLED.")
except ImportError:
    TELEGRAM_BOT_TOKEN = None
    TELEGRAM_CHAT_ID = None
    TELEGRAM_ENABLED = False
    print(f"⚠️  credentials.py gak ketemu di {ROOT_DIR} -> Telegram alert DISABLED.")


def send_telegram_alert(message: str):
    """Kirim notifikasi ke Telegram. Silent-fail kalau gagal."""
    if not TELEGRAM_ENABLED:
        print(f"   📵 [Telegram disabled] pesan gak dikirim: {message[:60]}...")
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message}
        resp = requests.post(url, json=payload, timeout=10)
        data = resp.json()
        if not data.get("ok"):
            print(f"   ⚠️ Telegram API nolak pesan: {data}")
        else:
            print(f"   📤 Telegram terkirim: {message[:60]}...")
    except Exception as e:
        print(f"   ⚠️ Gagal kirim Telegram alert: {e}")


# ============================================================
# AUDIO ALERT (PYGAME)
# ============================================================
def play_warning():
    """Play warning.mp3 saat captcha/login wall terdeteksi."""
    if not os.path.exists(WARNING_MP3):
        print(f"   ⚠️ File {WARNING_MP3} gak ketemu")
        return
    try:
        pygame.mixer.init()
        pygame.mixer.music.load(WARNING_MP3)
        pygame.mixer.music.play()
        print("   🔊 warning.mp3 sedang diputar!")
    except Exception as e:
        print(f"   ⚠️ Gagal jalanin audio: {e}")


def stop_warning():
    """Stop audio."""
    try:
        if pygame.mixer.get_init():
            pygame.mixer.music.stop()
            pygame.mixer.quit()
    except Exception:
        pass


# ============================================================
# HELPER: baca master CSV
# ============================================================
def load_master_csv():
    """Load video_ids_master.csv, group by username (urutan sesuai urutan di CSV)."""
    videos_by_user = {}
    with open(MASTER_CSV, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            # Filter: hanya yang needs_comment_scrape == True
            if str(row.get("needs_comment_scrape", "")).strip().lower() == "true":
                username = row["username"]
                videos_by_user.setdefault(username, []).append(row)
    return videos_by_user


# ============================================================
# HELPER: baca existing data
# ============================================================
def load_existing_profil():
    """Load metadata_profil.csv, return set of username yang sudah scraped."""
    if not os.path.exists(OUTPUT_PROFIL):
        return set()
    scraped_users = set()
    with open(OUTPUT_PROFIL, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            scraped_users.add(row["username"])
    return scraped_users


def load_existing_videos():
    """Load metadata_video.csv, return set of (username, video_id) yang sudah scraped."""
    if not os.path.exists(OUTPUT_VIDEO):
        return set()
    scraped_videos = set()
    with open(OUTPUT_VIDEO, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            scraped_videos.add((row["username"], row["video_id"]))
    return scraped_videos


def append_profil_row(row):
    """Append profil metadata ke CSV."""
    file_exists = os.path.exists(OUTPUT_PROFIL)
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    with open(OUTPUT_PROFIL, "a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=PROFIL_FIELDS)
        if not file_exists:
            writer.writeheader()
        writer.writerow(row)
        f.flush()
        os.fsync(f.fileno())


def append_video_rows(rows):
    """Append video metadata ke CSV (batch)."""
    if not rows:
        return
    file_exists = os.path.exists(OUTPUT_VIDEO)
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    with open(OUTPUT_VIDEO, "a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=VIDEO_FIELDS)
        if not file_exists:
            writer.writeheader()
        writer.writerows(rows)
        f.flush()
        os.fsync(f.fileno())


# ============================================================
# LOGIN WALL / CAPTCHA CHECK (timeout 10 menit)
# ============================================================
async def wait_for_login_wall_clear(page, timeout=600, context_label=""):
    """
    Check & wait untuk login wall/captcha hilang.
    Default timeout: 600 detik = 10 menit.
    """
    blocker_selectors = [
        'text="Log in to TikTok"',
        '[data-e2e="login-modal"]',
        'div[role="dialog"]:has-text("Log in")',
        'div[role="dialog"]:has-text("Continue with")',
        'text="Drag the slider to fit the puzzle"',
        '[id*="captcha"]',
        '[class*="captcha"]',
        'iframe[src*="captcha"]',
        'img[alt="Captcha" i]',
        'img[alt*="captcha" i]',
    ]
    ALERT_REPEAT_EVERY = 30  # detik -- kirim ulang reminder Telegram tiap segini

    start = asyncio.get_event_loop().time()
    was_found = False
    alert_sent = False
    last_alert_at = None

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
                print("   ✅ Captcha/login wall hilang, lanjut.")
                stop_warning()
                if alert_sent:
                    send_telegram_alert(
                        f"✅ Captcha solved! Lanjut Scraping! 🚀\n{context_label}".strip()
                    )
                await asyncio.sleep(random.uniform(2, 4))
            return True

        now = asyncio.get_event_loop().time()
        elapsed = now - start
        remaining = int(timeout - elapsed)

        if not was_found:
            print("\n" + "=" * 70)
            print("   🔒 🔊 CAPTCHA / LOGIN WALL TERDETEKSI !!!")
            print(f"   ⏰ Timeout dalam {remaining} detik ({remaining / 60:.1f} menit)")
            print("=" * 70)
            play_warning()

        if last_alert_at is None or (now - last_alert_at) >= ALERT_REPEAT_EVERY:
            send_telegram_alert(
                "⚠️ CAPTCHA DETECTED!\n"
                f"{context_label}\n"
                f"Waktu: {datetime.now().strftime('%H:%M:%S')}\n"
                f"⏳ Sisa waktu: {remaining}s ({remaining / 60:.1f} menit)\n\n"
                "📱 Buka Chrome Remote Desktop di HP buat solve."
            )
            alert_sent = True
            last_alert_at = now

        was_found = True
        print(f"   ⏳ Solve dalam {remaining}s ({remaining / 60:.1f} menit)...", end="\r")
        await asyncio.sleep(3)

    print("\n" + "=" * 70)
    print("   ❌ TIMEOUT 10 MENIT - CAPTCHA GAK DI-SOLVE!")
    print("   Program akan ditutup...")
    print("=" * 70)
    stop_warning()
    send_telegram_alert(
        "❌ CAPTCHA TIMEOUT (10 menit)!\n"
        f"{context_label}\n"
        "Program dihentikan. Data yang sempat ke-scrape aman di CSV.\n"
        "Jalanin ulang script kapan aja, otomatis resume."
    )
    raise CaptchaTimeoutError(
        "Captcha/login wall gak di-solve dalam 10 menit. Program dihentikan."
    )


# ============================================================
# TIKTOK SHOP / VIDEO UNPLAYABLE CHECK
# ============================================================
async def is_video_unplayable(page):
    """
    Deteksi video yang MEMANG gak bisa diputar di browser -- paling sering
    video TikTok Shop yang mucul warning "can only be viewed in the TikTok
    app". Ini BUKAN captcha!
    """
    selectors = [
        'text=/can\\s*only\\s*be\\s*viewed\\s*in\\s*the\\s*tiktok\\s*app/i',
        'text=/only\\s*available\\s*(on|in)\\s*the\\s*tiktok\\s*app/i',
        'text=/hanya\\s*(dapat|bisa)\\s*dilihat\\s*di\\s*aplikasi\\s*tiktok/i',
        'text=/video\\s*(is\\s*)?not\\s*available/i',
        'text=/video\\s*ini\\s*tidak\\s*tersedia/i',
        'text=/this\\s*video\\s*is\\s*unavailable/i',
    ]
    for sel in selectors:
        try:
            elem = await page.query_selector(sel)
            if elem and await elem.is_visible():
                return True
        except Exception:
            continue
    return False


# ============================================================
# SCRAPE PROFILE METADATA
# ============================================================
async def scrape_profil_metadata(page, username):
    """
    Buka profile page, extract: followers, following, bio, display_name,
    is_verified, avatar_url.
    """
    profile_url = f"https://www.tiktok.com/@{username}"
    print(f"\n   🔍 Buka profil @{username}...")

    try:
        await page.goto(profile_url, wait_until="load", timeout=60000)
    except Exception as e:
        print(f"   ❌ Gagal buka profil: {e}")
        return None

    await asyncio.sleep(random.uniform(2, 4))
    await wait_for_login_wall_clear(page, timeout=600, context_label=f"@{username} (profil)")

    try:
        followers_text = ""
        following_text = ""
        bio_text = ""
        display_name = ""
        is_verified = False
        avatar_url = ""

        try:
            followers_elem = await page.query_selector('h3:has-text("Followers")')
            if followers_elem:
                followers_text = await followers_elem.text_content()
        except Exception:
            pass

        try:
            following_elem = await page.query_selector('h3:has-text("Following")')
            if following_elem:
                following_text = await following_elem.text_content()
        except Exception:
            pass

        try:
            bio_elem = await page.query_selector('[data-e2e="user-bio"]')
            if bio_elem:
                bio_text = await bio_elem.text_content()
        except Exception:
            pass

        try:
            name_elem = await page.query_selector('h1')
            if name_elem:
                display_name = await name_elem.text_content()
        except Exception:
            pass

        try:
            verified_badge = await page.query_selector('svg[data-e2e="icon-verified"]')
            is_verified = verified_badge is not None
        except Exception:
            is_verified = False

        try:
            avatar_elem = await page.query_selector('img[alt*="avatar"]')
            if avatar_elem:
                avatar_url = await avatar_elem.get_attribute("src")
        except Exception:
            pass

        row = {
            "username": username,
            "followers": followers_text.strip() if followers_text else "",
            "following": following_text.strip() if following_text else "",
            "bio": bio_text.strip() if bio_text else "",
            "display_name": display_name.strip() if display_name else username,
            "is_verified": str(is_verified),
            "avatar_url": avatar_url if avatar_url else "",
            "scraped_at": datetime.now().isoformat(),
        }

        print(f"   ✅ Profil @{username} berhasil di-scrape")
        return row

    except Exception as e:
        print(f"   ❌ Error scrape profil: {e}")
        return None


# ============================================================
# MODAL: extract metadata dari video yang lagi kebuka
# ============================================================
async def extract_video_metadata_current(page, username):
    """
    Extract like/comment/share/save/description dari video yang LAGI TERBUKA.
    
    PENTING (fix race condition): TikTok itu SPA -- URL video bisa berubah
    DULUAN sebelum konten detail video (like-count, share-count, dll)
    beneran ke-render. Kalau extract langsung abis URL berubah, ada
    kemungkinan semua elemen masih kosong -> ke-extract semua "0"/kosong
    padahal videonya valid. Makanya di sini WAJIB nunggu elemen like-count
    beneran muncul (dan ada isinya) dulu sebelum extract beneran.
    """
    try:
        await page.wait_for_selector('[data-e2e="like-count"]', timeout=METADATA_READY_TIMEOUT)
    except Exception:
        print(f"      ⚠️ Elemen metadata (like-count) gak muncul dalam "
              f"{METADATA_READY_TIMEOUT / 1000:.0f}s -- render kemungkinan lambat / "
              "video gak valid. Skip dulu, bakal ke-retry otomatis di run berikutnya.")
        return None

    try:
        like_count = ""
        comment_count = ""
        share_count = ""
        save_count = ""
        description = ""

        try:
            like_elem = await page.query_selector('[data-e2e="like-count"]')
            if like_elem:
                like_count = await like_elem.text_content()
        except Exception:
            pass

        try:
            comment_elem = await page.query_selector('[data-e2e="comment-count"]')
            if comment_elem:
                comment_count = await comment_elem.text_content()
        except Exception:
            pass

        try:
            share_elem = await page.query_selector('[data-e2e="share-count"]')
            if share_elem:
                share_count = await share_elem.text_content()
        except Exception:
            pass

        try:
            save_elem = await page.query_selector('[data-e2e="favorite-count"]')
            if save_elem:
                save_count = await save_elem.text_content()
        except Exception:
            pass

        try:
            desc_elem = await page.query_selector('[data-e2e="video-desc"]')
            if desc_elem:
                description = await desc_elem.text_content()
        except Exception:
            pass

        # Sanity check tambahan: elemen like-count udah ADA di DOM (lolos
        # wait_for_selector di atas), tapi kalau TEKS-nya masih kosong itu
        # artinya render-nya belum selesai (elemen nempel duluan, isinya
        # nyusul beberapa saat kemudian). Kasih 1x kesempatan retry.
        if not (like_count or "").strip():
            print("      ⚠️ like-count elemen ada tapi teksnya masih kosong -- "
                  "kemungkinan render belum selesai, tunggu & retry sekali...")
            await asyncio.sleep(2)
            try:
                like_elem = await page.query_selector('[data-e2e="like-count"]')
                if like_elem:
                    like_count = await like_elem.text_content()
            except Exception:
                pass
            if not (like_count or "").strip():
                print("      ⚠️ Masih kosong setelah retry. Skip video ini dulu.")
                return None

        return {
            "like_count": like_count.strip() if like_count else "0",
            "comment_count": comment_count.strip() if comment_count else "0",
            "share_count": share_count.strip() if share_count else "0",
            "save_count": save_count.strip() if save_count else "0",
            "description": description.strip() if description else "",
        }
    except Exception as e:
        print(f"      ❌ Error extract metadata: {e}")
        return None


# ============================================================
# CORE: scrape semua video 1 influencer (langsung goto link)
# ============================================================
async def scrape_videos_human_like(page, username, target_video_ids, scraped_videos,
                                    csv_writer_callback):
    """
    target_video_ids : set of video_id yang KITA MAU (dari master.csv, sudah
                        difilter needs_comment_scrape==True) untuk username ini.
    scraped_videos   : set (username, video_id) yang sudah ada di CSV (in-memory,
                        dipakai buat skip + di-update tiap dapet video baru).
    csv_writer_callback : dipanggil tiap video selesai, buat nulis ke CSV.

    Return: jumlah video baru yang berhasil di-extract.
    
    Flow: LANGSUNG goto ke video link @username/video/videoid (gak perlu scroll
    grid). Login/captcha check tetap sama sebelumnya.
    """
    videos_scraped_count = 0

    # Filter: hanya video yang belum discrape & ada di target list
    pending_videos = [
        vid for vid in target_video_ids
        if (username, vid) not in scraped_videos
    ]

    if not pending_videos:
        print(f"   ⏭️  Semua video @{username} sudah ada di CSV, gak ada yang diproses.")
        return 0

    print(f"   📋 Total video yang perlu discrape: {len(pending_videos)}")

    for idx, vid in enumerate(pending_videos, start=1):
        video_url = f"https://www.tiktok.com/@{username}/video/{vid}"
        print(f"\n   [{idx}/{len(pending_videos)}] Buka: {video_url}")

        try:
            await page.goto(video_url, wait_until="load", timeout=60000)
        except Exception as e:
            print(f"      ⚠️ Gagal goto video: {e}")
            continue

        await asyncio.sleep(random.uniform(2, 3))

        # ⚡ Cek TikTok Shop / video unplayable DULU, SEBELUM cek captcha
        if await is_video_unplayable(page):
            print(f"      🛍️  Video {vid[:10]} adalah TikTok Shop video "
                  f"(cuma bisa dilihat di app) -- skip.")
            continue

        # Cek & tunggu captcha/login wall hilang
        await wait_for_login_wall_clear(
            page, timeout=600, context_label=f"@{username} (video {vid[:10]})"
        )

        # Extract metadata
        meta = await extract_video_metadata_current(page, username)
        if meta:
            row = {
                "username": username,
                "video_id": vid,
                "video_url": video_url,
                **meta,
                "scraped_at": datetime.now().isoformat(),
            }
            csv_writer_callback([row])
            print(f"      💾 Tersimpan ke CSV: video {vid[:10]}")
            scraped_videos.add((username, vid))
            videos_scraped_count += 1
            print(f"      ✅ Video {vid[:10]}: like={meta['like_count']}, "
                  f"comment={meta['comment_count']}, share={meta['share_count']}, "
                  f"save={meta['save_count']}")
        else:
            # Extract gagal (timeout/kosong), video gak ditandai scraped
            # -> otomatis ke-retry di run berikutnya
            print(f"      ⏭️  Video {vid[:10]} di-skip (metadata gagal di-load), "
                  f"belum ditandai scraped -- bakal ke-retry di run berikutnya.")

        # Jeda antar video
        if videos_scraped_count % DELAY_EVERY_N_VIDEOS == 0 and videos_scraped_count > 0:
            long_delay = random.uniform(*DELAY_AFTER_N_VIDEOS)
            print(f"\n      🌙 Jeda PANJANG {long_delay / 60:.1f} menit "
                  f"(anti-captcha, setelah {videos_scraped_count} video total)...")
            await asyncio.sleep(long_delay)
        else:
            delay = random.uniform(*DELAY_BETWEEN_VIDEO)
            print(f"      ⏳ Jeda {delay:.0f}s...")
            await asyncio.sleep(delay)

    return videos_scraped_count


# ============================================================
# MAIN
# ============================================================
async def main():
    videos_by_user = load_master_csv()
    scraped_profil = load_existing_profil()
    scraped_videos = load_existing_videos()

    print(f"📋 Total influencer dengan video: {len(videos_by_user)}")
    print(f"   Profil sudah scraped: {len(scraped_profil)}")
    print(f"   Video sudah scraped: {len(scraped_videos)}")

    # ====== RESUME LOGIC (v3 -- urutan asli master CSV) ======
    to_process = []
    n_fully_done = 0
    for u in videos_by_user.keys():
        user_video_ids = {v["video_id"] for v in videos_by_user[u]}
        already_done_ids = {vid for (uname, vid) in scraped_videos if uname == u}
        profil_done = u in scraped_profil
        videos_done = user_video_ids.issubset(already_done_ids)

        if profil_done and videos_done:
            n_fully_done += 1
            continue
        to_process.append(u)

    print(f"   Influencer selesai total (profil+video) : {n_fully_done}")
    print(f"   Influencer perlu diproses (baru/lanjut)  : {len(to_process)}")

    if not to_process:
        print("✅ Semua profil & video sudah scraped.")
        send_telegram_alert("✅ Semua profil & video sudah scraped. Gak ada yang perlu dijalankan.")
        return

    send_telegram_alert(
        f"🚀 Scraping dimulai.\n"
        f"Influencer yang akan diproses: {len(to_process)}\n"
        f"Waktu mulai: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )

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

        try:
            for idx, username in enumerate(to_process, start=1):
                print(f"\n{'=' * 70}")
                print(f"[{idx}/{len(to_process)}] @{username}")
                print(f"{'=' * 70}")

                target_video_ids = {v["video_id"] for v in videos_by_user[username]}

                # --- Profil (goto langsung profile page tetap OK) ---
                if username in scraped_profil:
                    print(f"   ⏭️  Profil @{username} udah pernah discrape, skip.")
                else:
                    profil_row = await scrape_profil_metadata(page, username)
                    if profil_row:
                        append_profil_row(profil_row)
                        scraped_profil.add(username)

                # --- Video (langsung goto link) ---
                already_done_ids = {vid for (uname, vid) in scraped_videos if uname == username}
                if target_video_ids.issubset(already_done_ids):
                    print(f"   ⏭️  Semua video @{username} udah ada di CSV, skip video scraping.")
                else:
                    n_new = await scrape_videos_human_like(
                        page, username, target_video_ids, scraped_videos,
                        append_video_rows,
                    )
                    print(f"   📊 Total video baru di-scrape untuk @{username}: {n_new}")

                if idx < len(to_process):
                    delay = random.uniform(*DELAY_BETWEEN_INFLUENCER)
                    mins = delay / 60
                    print(f"\n😴 Jeda {mins:.1f} menit sebelum influencer berikutnya...")
                    await asyncio.sleep(delay)

            print("\n\n✅✅✅ SEMUA PROFIL & VIDEO METADATA SELESAI DI-SCRAPE ✅✅✅")
            print(f"Metadata profil: {OUTPUT_PROFIL}")
            print(f"Metadata video : {OUTPUT_VIDEO}")
            send_telegram_alert(
                "✅✅✅ SEMUA PROFIL & VIDEO METADATA SELESAI DI-SCRAPE ✅✅✅\n"
                f"Waktu selesai: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            )

        except CaptchaTimeoutError as e:
            print(f"\n\n🛑 PROGRAM DIHENTIKAN: {e}")
            print("   Data yang udah sempat ke-scrape aman tersimpan di CSV.")
            print("   Jalanin ulang script ini kapan aja, otomatis resume dari sini.")

        finally:
            print("\n🔒 Menutup browser...")
            await context.close()


if __name__ == "__main__":
    asyncio.run(main())