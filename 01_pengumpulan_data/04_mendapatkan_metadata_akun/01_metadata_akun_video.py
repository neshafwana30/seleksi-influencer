"""
TAHAP 2 — Scrape metadata profil + metadata video dari TikTok.

Sumber: video_ids_master.csv (hasil tahap 1)
Output:
  - metadata_profil.csv : followers, following, bio, nama, verification, avatar_url
  - metadata_video.csv : like_count, comment_count, share_count, save_count, description

Fitur:
- Lazy scraping: delay panjang antar video (30-60s) + antar influencer (10-15 min)
- Resume otomatis: skip influencer yang sudah di-scrape profil-nya
- Batch write per 10 video buat minimize data loss
- Audio warning saat captcha (pygame)
- Dedup: gak ulang video yang sudah ada
"""

import asyncio
import csv
import os
import random
import pygame
from datetime import datetime, timedelta, timezone

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

OUTPUT_DIR = os.path.join(DATA_DIR, "04_mendapatkan_metadata_akun")
OUTPUT_PROFIL = os.path.join(OUTPUT_DIR, "metadata_profil.csv")
OUTPUT_VIDEO = os.path.join(OUTPUT_DIR, "metadata_video.csv")

PROFIL_FIELDS = ["username", "followers", "following", "bio", "display_name",
                 "is_verified", "avatar_url", "scraped_at"]
VIDEO_FIELDS = ["username", "video_id", "video_url", "like_count", "comment_count",
                "share_count", "save_count", "description", "scraped_at"]

# ============================================================
# KONFIGURASI SCRAPING (LAZY/SLOW)
# ============================================================
DELAY_BETWEEN_VIDEO = (30, 60)        # 30-60 detik antar video
DELAY_BETWEEN_INFLUENCER = (10*60, 15*60)  # 10-15 menit antar influencer
DELAY_EVERY_N_VIDEOS = 15             # Setelah 15 video, jeda panjang
DELAY_AFTER_N_VIDEOS = (3*60, 5*60)   # 3-5 menit jeda panjang (anti-captcha)
BATCH_SIZE_VIDEO = 10
MAX_VIDEOS_PER_INFLUENCER = 200  # safety cap

REALISTIC_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)


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
    """Load video_ids_master.csv, group by username."""
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
async def wait_for_login_wall_clear(page, timeout=600):
    """
    Check & wait untuk login wall/captcha hilang.
    Default timeout: 600 detik = 10 menit (sesuai request user).
    Kalo 10 menit gak di-solve, return False & exit program.
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
                print("   ✅ Captcha/login wall hilang, lanjut.")
                stop_warning()
                await asyncio.sleep(random.uniform(2, 4))
            return True

        if not was_found:
            elapsed = asyncio.get_event_loop().time() - start
            print("\n" + "="*70)
            print("   🔒 🔊 CAPTCHA / LOGIN WALL TERDETEKSI !!!")
            print(f"   ⏰ Timeout dalam {timeout - int(elapsed)} detik ({(timeout - int(elapsed))/60:.1f} menit)")
            print("="*70)
            play_warning()
        was_found = True
        elapsed = asyncio.get_event_loop().time() - start
        remaining = int(timeout - elapsed)
        print(f"   ⏳ Solve dalam {remaining}s ({remaining/60:.1f} menit)...", end="\r")
        await asyncio.sleep(3)

    print("\n" + "="*70)
    print("   ❌ TIMEOUT 10 MENIT - CAPTCHA GAK DI-SOLVE!")
    print("   Program akan ditutup...")
    print("="*70)
    stop_warning()
    raise CaptchaTimeoutError(
        "Captcha/login wall gak di-solve dalam 10 menit. Program dihentikan."
    )


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
    await wait_for_login_wall_clear(page, timeout=600)

    try:
        # Extract followers/following dari text "123.4K Followers"
        followers_text = ""
        following_text = ""
        bio_text = ""
        display_name = ""
        is_verified = False
        avatar_url = ""

        # Followers count (biasanya di h3 dengan text "Followers")
        try:
            followers_elem = await page.query_selector('h3:has-text("Followers")')
            if followers_elem:
                followers_text = await followers_elem.text_content()
        except:
            pass

        # Following count
        try:
            following_elem = await page.query_selector('h3:has-text("Following")')
            if following_elem:
                following_text = await following_elem.text_content()
        except:
            pass

        # Bio / signature
        try:
            bio_elem = await page.query_selector('[data-e2e="user-bio"]')
            if bio_elem:
                bio_text = await bio_elem.text_content()
        except:
            pass

        # Display name
        try:
            name_elem = await page.query_selector('h1')
            if name_elem:
                display_name = await name_elem.text_content()
        except:
            pass

        # Verification badge
        try:
            verified_badge = await page.query_selector('svg[data-e2e="icon-verified"]')
            is_verified = verified_badge is not None
        except:
            is_verified = False

        # Avatar URL
        try:
            avatar_elem = await page.query_selector('img[alt*="avatar"]')
            if avatar_elem:
                avatar_url = await avatar_elem.get_attribute("src")
        except:
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
# SCRAPE VIDEO METADATA
# ============================================================
async def scrape_video_metadata(page, username, video_url, video_id):
    """
    Buka video page, extract: like_count, comment_count, share_count,
    save_count, description.
    """
    print(f"   🎬 Buka video {video_id[:8]}...")

    try:
        await page.goto(video_url, wait_until="load", timeout=60000)
    except Exception as e:
        print(f"      ❌ Gagal buka video: {e}")
        return None

    await asyncio.sleep(random.uniform(2, 3))
    await wait_for_login_wall_clear(page, timeout=600)

    try:
        like_count = ""
        comment_count = ""
        share_count = ""
        save_count = ""
        description = ""

        # Like count
        try:
            like_elem = await page.query_selector('[data-e2e="like-count"]')
            if like_elem:
                like_count = await like_elem.text_content()
        except:
            pass

        # Comment count
        try:
            comment_elem = await page.query_selector('[data-e2e="comment-count"]')
            if comment_elem:
                comment_count = await comment_elem.text_content()
        except:
            pass

        # Share count
        try:
            share_elem = await page.query_selector('[data-e2e="share-count"]')
            if share_elem:
                share_count = await share_elem.text_content()
        except:
            pass

        # Save count (bookmark)
        try:
            save_elem = await page.query_selector('strong[data-e2e$="-count"]')
            if save_elem:
                # Ambil dari urutan ke-4 (biasanya: like, comment, save, share)
                all_counts = await page.query_selector_all('strong[data-e2e$="-count"]')
                if len(all_counts) >= 3:
                    save_count = await all_counts[2].text_content()
        except:
            pass

        # Description
        try:
            desc_elem = await page.query_selector('[data-e2e="video-desc"]')
            if desc_elem:
                description = await desc_elem.text_content()
        except:
            pass

        row = {
            "username": username,
            "video_id": video_id,
            "video_url": video_url,
            "like_count": like_count.strip() if like_count else "0",
            "comment_count": comment_count.strip() if comment_count else "0",
            "share_count": share_count.strip() if share_count else "0",
            "save_count": save_count.strip() if save_count else "0",
            "description": description.strip() if description else "",
            "scraped_at": datetime.now().isoformat(),
        }

        print(f"      ✅ Video {video_id[:8]}: "
              f"like={row['like_count']}, comment={row['comment_count']}, "
              f"share={row['share_count']}, save={row['save_count']}")
        return row

    except Exception as e:
        print(f"      ❌ Error scrape video: {e}")
        return None


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

    # ⚡ FIX RESUME: seorang influencer di-skip TOTAL cuma kalau profil-nya
    # SUDAH discrape DAN semua video-nya juga udah ada di CSV. Kalau
    # sebelumnya berhenti di tengah jalan (misal captcha timeout) padahal
    # profil udah sempat ke-scrape, video yang tersisa harus tetap
    # dilanjutkan -- bukan di-skip cuma karena profil udah ada.
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

        try:
            for idx, username in enumerate(to_process, start=1):
                print(f"\n{'='*70}")
                print(f"[{idx}/{len(to_process)}] @{username}")
                print(f"{'='*70}")

                # Scrape profil metadata -- SKIP kalau udah pernah discrape
                # (resume case: profil udah ada, tapi video belum kelar semua)
                if username in scraped_profil:
                    print(f"   ⏭️  Profil @{username} udah pernah discrape, skip.")
                else:
                    profil_row = await scrape_profil_metadata(page, username)
                    if profil_row:
                        append_profil_row(profil_row)
                        scraped_profil.add(username)

                # Scrape video metadata
                user_videos = videos_by_user[username]
                video_batch = []
                videos_scraped_this_influencer = 0  # Counter untuk long break setiap 15 video

                for v_idx, video_row in enumerate(user_videos[:MAX_VIDEOS_PER_INFLUENCER], 1):
                    video_id = video_row["video_id"]
                    video_url = video_row["video_url"]

                    # ⚡ Cek dulu di CSV (in-memory set) SEBELUM buka video --
                    # kalau udah ada, langsung skip tanpa buka page/nunggu apapun.
                    # Ini yang bikin resume jadi cepat, gak perlu buka video
                    # satu-satu buat video yang udah pasti ada datanya.
                    if (username, video_id) in scraped_videos:
                        print(f"   ⏭️  Video {video_id[:8]} sudah ada di CSV, skip (gak dibuka).")
                        continue

                    # Scrape video
                    v_meta = await scrape_video_metadata(page, username, video_url, video_id)
                    if v_meta:
                        video_batch.append(v_meta)
                        scraped_videos.add((username, video_id))
                        videos_scraped_this_influencer += 1

                    # Batch write
                    if len(video_batch) >= BATCH_SIZE_VIDEO:
                        append_video_rows(video_batch)
                        print(f"   💾 Tersimpan {len(video_batch)} video metadata")
                        video_batch = []

                    # LONG BREAK setiap 15 video (anti-captcha strategy)
                    if videos_scraped_this_influencer % DELAY_EVERY_N_VIDEOS == 0:
                        long_delay = random.uniform(*DELAY_AFTER_N_VIDEOS)
                        long_mins = long_delay / 60
                        print(f"\n   🌙 Jeda PANJANG {long_mins:.1f} menit (anti-captcha, setelah {videos_scraped_this_influencer} video)...")
                        await asyncio.sleep(long_delay)

                    # Delay antar video (30-60s, LAZY/SLOW)
                    if v_idx < len(user_videos):
                        delay = random.uniform(*DELAY_BETWEEN_VIDEO)
                        print(f"   ⏳ Jeda {delay:.0f}s sebelum video berikutnya...")
                        await asyncio.sleep(delay)

                # Flush sisa batch
                if video_batch:
                    append_video_rows(video_batch)
                    print(f"   💾 Tersimpan sisa {len(video_batch)} video metadata")

                # Delay antar influencer (10-15 min, SANGAT SLOW)
                if idx < len(to_process):
                    delay = random.uniform(*DELAY_BETWEEN_INFLUENCER)
                    mins = delay / 60
                    print(f"\n😴 Jeda {mins:.1f} menit sebelum influencer berikutnya...")
                    await asyncio.sleep(delay)

            print("\n\n✅✅✅ SEMUA PROFIL & VIDEO METADATA SELESAI DI-SCRAPE ✅✅✅")
            print(f"Metadata profil: {OUTPUT_PROFIL}")
            print(f"Metadata video : {OUTPUT_VIDEO}")

        except CaptchaTimeoutError as e:
            # Sisa video/profil yang belum sempat diproses aman -- yang
            # sudah ke-scrape sudah tersimpan lewat batch write & append
            # per-row, jadi run berikutnya bisa langsung resume dari sini.
            print(f"\n\n🛑 PROGRAM DIHENTIKAN: {e}")
            print("   Data yang udah sempat ke-scrape aman tersimpan di CSV.")
            print("   Jalanin ulang script ini kapan aja, otomatis resume dari sini.")

        finally:
            print("\n🔒 Menutup browser...")
            await context.close()


if __name__ == "__main__":
    asyncio.run(main())