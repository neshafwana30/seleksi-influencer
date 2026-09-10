"""
TAHAP 2 — Scrape metadata profil + metadata video dari TikTok.
⬇️ MIXED STRATEGY: scroll grid (pending banyak) + fallback direct-navigate

Sumber: video_ids_master.csv (hasil tahap 1)
Output:
  - metadata_profil.csv : followers, following, bio, nama, verification, avatar_url
  - metadata_video.csv : like_count, comment_count, share_count, save_count, description

⚡ EXTRACT METADATA DARI NETWORK RESPONSE (JSON API), BUKAN DOM.

🎯 STRATEGI (dipilih otomatis per-influencer, berdasarkan JUMLAH pending):

   1. Pending >= 100 video
      -> SCROLL GRID: buka profil, scroll cari thumbnail yang match target,
         klik LANGSUNG tiap video yang relevan (extract, tutup modal, lanjut
         scroll -- BUKAN arrow-key chain ngelewatin video yang gak relevan).
         Kalau 5x scroll berturut-turut GAK nemu video baru (grid mentok /
         video gak ke-load), otomatis FALLBACK ke direct-navigate buat
         nutup sisa video yang belum ke-cover.

   2. Pending < 100 video
      -> DIRECT NAVIGATE langsung dari awal: susun link video_id-nya,
         tembak satu-satu, gak usah buka grid & scroll sama sekali.

Kenapa: Scroll grid efisien buat nemuin BANYAK video sekaligus (natural
discovery). Direct-navigate efisien buat sisa dikit (gak buang waktu
scroll panjang cuma buat nemuin beberapa video doang).
"""

import asyncio
import csv
import json
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
VIDEO_FIELDS = ["username", "video_id", "video_url", "is_photo", "like_count", "comment_count",
                "share_count", "save_count", "description", "scraped_at"]

# ============================================================
# KONFIGURASI SCRAPING (LAZY/SLOW, HUMAN-LIKE)
# ============================================================
DELAY_BETWEEN_VIDEO = (10, 30)
DELAY_BETWEEN_INFLUENCER = (30, 100)
DELAY_EVERY_N_VIDEOS = 300
DELAY_AFTER_N_VIDEOS = (2 * 60, 3 * 60)
DELAY_SCROLL_STEP = (1.5, 2)
MAX_VIDEOS_PER_INFLUENCER = 500
MAX_SCROLL_ATTEMPTS_NO_NEW = 5
MAX_ARROW_STEPS_PER_CHAIN = 60
CHAIN_SAVE_LIMIT = 5  # ⬇️ BARU: setelah 5 video berhasil ke-save dalam 1 "chain"
                       # swipe, balik ke grid & scroll lagi (biar gak swipe kejauhan
                       # ngelewatin banyak video yang gak relevan)
METADATA_READY_TIMEOUT = 10000

NETWORK_STATS_WAIT_TIMEOUT = 8.0
NETWORK_STATS_POLL_INTERVAL = 0.25

# ⬇️ BARU: Threshold buat milih strategi. Sisa video < ini -> direct navigate.
DIRECT_NAVIGATE_MAX_PENDING = 100

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
    videos_by_user = {}
    with open(MASTER_CSV, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if str(row.get("needs_comment_scrape", "")).strip().lower() == "true":
                username = row["username"]
                videos_by_user.setdefault(username, []).append(row)
    return videos_by_user


# ============================================================
# HELPER: baca existing data
# ============================================================
def load_existing_profil():
    if not os.path.exists(OUTPUT_PROFIL):
        return set()
    scraped_users = set()
    with open(OUTPUT_PROFIL, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            scraped_users.add(row["username"])
    return scraped_users


def load_existing_videos():
    if not os.path.exists(OUTPUT_VIDEO):
        return set()
    scraped_videos = set()
    with open(OUTPUT_VIDEO, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            scraped_videos.add((row["username"], row["video_id"]))
    return scraped_videos


def append_profil_row(row):
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
# NETWORK RESPONSE CAPTURE
# ============================================================
def find_video_stats_blocks(obj, _depth=0):
    """Recursive search di dalam JSON buat nemuin blok stats video."""
    results = []
    if _depth > 12:
        return results

    if isinstance(obj, dict):
        stats = obj.get("stats") or obj.get("statsV2")
        if isinstance(stats, dict) and (
            "diggCount" in stats or "shareCount" in stats or "playCount" in stats
        ):
            vid = obj.get("id") or obj.get("itemId") or obj.get("awemeId")
            desc = obj.get("desc")
            if vid:
                results.append((str(vid), stats, desc))

        for value in obj.values():
            results.extend(find_video_stats_blocks(value, _depth + 1))

    elif isinstance(obj, list):
        for item in obj:
            results.extend(find_video_stats_blocks(item, _depth + 1))

    return results


def make_network_response_listener(video_stats_cache):
    """Async function untuk page.on("response", ...) -- nangkep stats video dari JSON."""
    async def on_response(response):
        try:
            url = response.url
            if "tiktok.com" not in url:
                return
            content_type = response.headers.get("content-type", "")
            if "json" not in content_type:
                return

            body = await response.json()
        except Exception:
            return

        try:
            blocks = find_video_stats_blocks(body)
        except Exception:
            return

        for vid, stats, desc in blocks:
            video_stats_cache[vid] = {
                "like_count": str(stats.get("diggCount", "")),
                "comment_count": str(stats.get("commentCount", "")),
                "share_count": str(stats.get("shareCount", "")),
                "save_count": str(stats.get("collectCount", "")),
                "description": (desc or "").strip(),
            }

    return on_response


async def wait_for_network_stats(video_stats_cache, video_id,
                                  timeout=NETWORK_STATS_WAIT_TIMEOUT,
                                  poll_interval=NETWORK_STATS_POLL_INTERVAL):
    """Polling nunggu video_id ini keisi dari network listener."""
    elapsed = 0.0
    while elapsed < timeout:
        if video_id in video_stats_cache:
            return video_stats_cache[video_id]
        await asyncio.sleep(poll_interval)
        elapsed += poll_interval
    return None


# ============================================================
# LOGIN WALL / CAPTCHA CHECK
# ============================================================
async def wait_for_login_wall_clear(page, timeout=600, context_label=""):
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
        '.TUXModal:has-text("captcha")',
        '.TUXModal:has-text("Captcha")',
        '[class*="captcha-verify"]',
        '[class*="captcha_verify"]',
        'div[class*="TUXModal"] iframe',
        'text=/verify\\s*to\\s*continue/i',
        'text=/select\\s*2\\s*similar\\s*images/i',
        'text=/tap\\s*the\\s*objects/i',
        'text=/rotate\\s*the\\s*image/i',
        'text=/drag\\s*the\\s*puzzle/i',
        'text=/verify\\s*you\\s*are\\s*human/i',
    ]
    ALERT_REPEAT_EVERY = 30

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
# HELPER: parse video_id & URL
# ============================================================
def parse_video_id_from_url(url: str):
    """Ambil video_id dari URL TikTok (/video/ atau /photo/)."""
    if not url:
        return None
    if "/video/" in url:
        tail = url.split("/video/", 1)[1]
    elif "/photo/" in url:
        tail = url.split("/photo/", 1)[1]
    else:
        return None
    vid = tail.split("?", 1)[0].strip("/")
    return vid if vid.isdigit() else None


def get_content_type_from_url(url: str):
    """Return "photo" atau "video", None kalau gak keduanya."""
    if not url:
        return None
    if "/photo/" in url:
        return "photo"
    if "/video/" in url:
        return "video"
    return None


def parse_username_from_url(url: str):
    if not url or "/@" not in url:
        return None
    tail = url.split("/@", 1)[1]
    uname = tail.split("/", 1)[0]
    return uname if uname else None


async def wait_url_change(page, old_url, timeout_ms=8000):
    try:
        await page.wait_for_function(
            "oldUrl => window.location.href !== oldUrl",
            arg=old_url,
            timeout=timeout_ms,
        )
        return True
    except Exception:
        return False


async def is_video_unavailable(page):
    """Check kalau video tidak tersedia (dihapus, diprivat, etc)."""
    selectors = [
        'text="Video currently unavailable"',
        'text="This video is unavailable"',
        'text=/video.*unavailable/i',
        'text=/tidak.*tersedia/i',
        '[data-e2e="video-unavailable-placeholder"]',
    ]
    for sel in selectors:
        try:
            elem = await page.query_selector(sel)
            if elem and await elem.is_visible():
                return True
        except Exception:
            continue
    return False


async def is_video_shop(page):
    """Check kalau video adalah TikTok Shop (app-only)."""
    selectors = [
        'text=/can\\s*only\\s*be\\s*viewed\\s*in\\s*the\\s*tiktok\\s*app/i',
        'text=/only\\s*available\\s*(on|in)\\s*the\\s*tiktok\\s*app/i',
        'text=/hanya\\s*(dapat|bisa)\\s*dilihat\\s*di\\s*aplikasi\\s*tiktok/i',
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
# EXTRACT METADATA (network cache > fallback DOM) -- dipakai KEDUA strategi
# ============================================================
async def extract_video_metadata_current(page, username, video_id, video_stats_cache):
    """Extract metadata dengan prioritas: network cache > fallback DOM."""
    stats_from_network = await wait_for_network_stats(video_stats_cache, video_id)
    if stats_from_network:
        print(f"      📡 Metadata dari network response (JSON)")
        result = dict(stats_from_network)
        if not result.get("description"):
            try:
                desc_elem = await page.query_selector('[data-e2e="video-desc"]')
                if desc_elem:
                    dom_desc = await desc_elem.text_content()
                    result["description"] = dom_desc.strip() if dom_desc else ""
            except Exception:
                pass
        return result

    print(f"      ⚠️ Network response timeout -- fallback DOM scraping")

    try:
        await page.wait_for_selector('[data-e2e="like-count"]', timeout=METADATA_READY_TIMEOUT)
    except Exception:
        print(f"      ⚠️ Elemen metadata gak muncul -- skip, bakal di-retry nanti")
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

        if not (like_count or "").strip():
            await asyncio.sleep(2)
            try:
                like_elem = await page.query_selector('[data-e2e="like-count"]')
                if like_elem:
                    like_count = await like_elem.text_content()
            except Exception:
                pass
            if not (like_count or "").strip():
                print("      ⚠️ Masih kosong. Skip video ini")
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


def build_video_row(username, video_id, content_type, meta):
    """Helper: bikin row dict siap masuk CSV, dipakai kedua strategi biar konsisten."""
    is_photo_flag = (content_type == "photo")
    content_path = content_type or "video"
    return {
        "username": username,
        "video_id": video_id,
        "video_url": f"https://www.tiktok.com/@{username}/{content_path}/{video_id}",
        "is_photo": str(is_photo_flag),
        **meta,
        "scraped_at": datetime.now().isoformat(),
    }


def build_unavailable_row(username, video_id, content_type, reason):
    is_photo_flag = (content_type == "photo")
    content_path = content_type or "video"
    return {
        "username": username,
        "video_id": video_id,
        "video_url": f"https://www.tiktok.com/@{username}/{content_path}/{video_id}",
        "is_photo": str(is_photo_flag),
        "like_count": "N/A",
        "comment_count": "N/A",
        "share_count": "N/A",
        "save_count": "N/A",
        "description": reason,
        "scraped_at": datetime.now().isoformat(),
    }


# ============================================================
# STRATEGI 1: DIRECT NAVIGATE (buat sisa video sedikit, <100)
# ============================================================
async def process_video_by_direct_navigate(page, username, video_id,
                                           video_stats_cache, scraped_videos,
                                           csv_writer_callback):
    """
    Navigate langsung ke video dengan URL.
    Handle 3 cases: success / unavailable / TikTok Shop.
    """
    video_url = f"https://www.tiktok.com/@{username}/video/{video_id}"

    print(f"\n   🎯 Direct navigate: {video_id[:10]}... (/video/)")
    try:
        await page.goto(video_url, wait_until="load", timeout=30000)
        await asyncio.sleep(random.uniform(1, 2))
    except Exception as e:
        print(f"      ⚠️ Gagal navigate /video/: {e}, skip")
        return False

    current_url = page.url
    current_content_type = get_content_type_from_url(current_url)
    current_vid = parse_video_id_from_url(current_url)
    current_uname = parse_username_from_url(current_url)

    if not current_vid or current_vid != video_id:
        print(f"      ⚠️ URL gak sesuai (redirect gak terduga), skip")
        return False

    if current_uname != username:
        print(f"      ⚠️ Navigated ke akun lain (@{current_uname}), skip")
        return False

    if await is_video_unavailable(page):
        print(f"      ⚠️ Video tidak tersedia (dihapus/diprivat/dll)")
        row = build_unavailable_row(username, current_vid, current_content_type,
                                     "Video currently unavailable (deleted/private/etc)")
        csv_writer_callback([row])
        scraped_videos.add((username, current_vid))
        print(f"      💾 Saved sebagai unavailable: {current_vid[:10]}")
        return True

    if await is_video_shop(page):
        print(f"      🛍️  TikTok Shop video (app-only)")
        row = build_unavailable_row(username, current_vid, current_content_type,
                                     "TikTok Shop video (app-only)")
        csv_writer_callback([row])
        scraped_videos.add((username, current_vid))
        print(f"      💾 Saved sebagai TikTok Shop: {current_vid[:10]}")
        return True

    await wait_for_login_wall_clear(page, timeout=600, context_label=f"@{username} (direct nav {video_id[:10]})")

    meta = await extract_video_metadata_current(page, username, current_vid, video_stats_cache)
    if not meta:
        print(f"      ⏭️  Metadata gagal, skip")
        return False

    row = build_video_row(username, current_vid, current_content_type, meta)
    csv_writer_callback([row])
    scraped_videos.add((username, current_vid))

    print(f"      💾 Saved: {current_vid[:10]}{' (photo)' if current_content_type == 'photo' else ''}")
    print(f"      ✅ Like:{meta['like_count']}, Comment:{meta['comment_count']}, Share:{meta['share_count']}, Save:{meta['save_count']}")

    return True


async def scrape_videos_direct_navigate(page, username, pending_ids, scraped_videos,
                                         csv_writer_callback, video_stats_cache):
    """Strategi DIRECT: tembak URL satu-satu buat semua pending video."""
    videos_scraped_count = 0
    pending_list = sorted(pending_ids)

    print(f"   🎯 Strategy: DIRECT NAVIGATE ke {len(pending_list)} video")

    for idx, vid in enumerate(pending_list, start=1):
        if (username, vid) in scraped_videos:
            continue

        print(f"      [{idx}/{len(pending_list)}] Video {vid[:10]}...")

        success = await process_video_by_direct_navigate(
            page, username, vid, video_stats_cache, scraped_videos, csv_writer_callback
        )

        if success:
            videos_scraped_count += 1

            if videos_scraped_count % DELAY_EVERY_N_VIDEOS == 0:
                long_delay = random.uniform(*DELAY_AFTER_N_VIDEOS)
                print(f"\n      🌙 Jeda panjang {long_delay / 60:.1f} menit (anti-captcha)...\n")
                await asyncio.sleep(long_delay)
            else:
                delay = random.uniform(*DELAY_BETWEEN_VIDEO)
                print(f"      ⏳ Jeda {delay:.0f}s")
                await asyncio.sleep(delay)

    print(f"   📊 Total video baru di-scrape (direct navigate): {videos_scraped_count}")
    return videos_scraped_count


# ============================================================
# STRATEGI 2: SCROLL GRID (buat influencer yang BELUM PERNAH discrape)
# ============================================================
async def get_grid_video_ids(page):
    """Cari video-video yang keliatan di profile grid saat ini."""
    anchors = await page.query_selector_all('div[data-e2e="user-post-item"] a')
    if not anchors:
        anchors = await page.query_selector_all('a[href*="/video/"]')

    results = []
    seen = set()
    for a in anchors:
        try:
            href = await a.get_attribute("href")
        except Exception:
            continue
        vid = parse_video_id_from_url(href) if href else None
        if vid and vid not in seen:
            seen.add(vid)
            results.append((vid, a))
    return results


async def scroll_grid_step(page):
    await page.evaluate("window.scrollBy(0, window.innerHeight * (0.6 + Math.random()*0.4))")
    await asyncio.sleep(random.uniform(*DELAY_SCROLL_STEP))


async def close_video_modal(page, profile_url):
    old_url = page.url
    try:
        await page.keyboard.press("Escape")
        await asyncio.sleep(random.uniform(1, 2))
        if page.url != old_url and "/video/" not in page.url:
            return
    except Exception:
        pass

    try:
        await page.go_back(wait_until="load", timeout=15000)
        await asyncio.sleep(random.uniform(1, 2))
        if "/video/" not in page.url:
            return
    except Exception:
        pass

    try:
        await page.goto(profile_url, wait_until="load", timeout=30000)
    except Exception:
        pass


async def navigate_next_video_arrow(page):
    old_url = page.url
    moved = False

    for sel in ['[data-e2e="arrow-right"]', 'button[aria-label*="Next video" i]']:
        try:
            btn = await page.query_selector(sel)
            if btn and await btn.is_visible():
                await btn.click()
                moved = True
                break
        except Exception:
            continue

    if not moved:
        try:
            await page.keyboard.press("ArrowDown")
        except Exception:
            pass

    return await wait_url_change(page, old_url, timeout_ms=8000)


async def scrape_videos_grid(page, username, target_video_ids, scraped_videos,
                              csv_writer_callback, video_stats_cache):
    """
    Strategi SCROLL+SWIPE (hybrid): buat influencer dengan pending >= 100 video.

    Cara kerja:
    1. Scroll grid cari thumbnail yang match target (belum discrape).
    2. Klik LANGSUNG thumbnail pending pertama yang ketemu.
    3. Begitu masuk ke video itu, lanjut SWIPE (arrow-key) ke video-video
       berikutnya secara berurutan -- ini natural karena TikTok emang didesain
       buat di-swipe terus, urutan di dalam player ngikutin urutan grid juga.
       Video yang bukan target / udah discrape di-skip cepat (tetap swipe
       terus), yang target & belum discrape di-extract & disimpan.
    4. Chain swipe ini berhenti kalau salah satu tercapai duluan:
       - Udah berhasil SAVE 5 video baru (CHAIN_SAVE_LIMIT)
       - Udah nyampe batas step (MAX_ARROW_STEPS_PER_CHAIN)
       - Nyasar ke akun lain / arrow mentok (gak ada video berikutnya)
    5. Chain berhenti -> tutup modal, balik ke grid, scroll cari batch
       pending berikutnya, klik lagi, swipe lagi (ulang dari langkah 2).
    6. Kalau scroll grid udah mentok (5x scroll berturut-turut gak nemu video
       baru) tapi masih ada sisa target yang belum ke-cover -> fallback ke
       DIRECT NAVIGATE buat nutup sisanya.
    """
    profile_url = f"https://www.tiktok.com/@{username}"
    videos_scraped_count = 0
    no_new_scroll_streak = 0

    print(f"   🔄 Strategy: SCROLL GRID + SWIPE (klik lalu arrow-key beberapa video, batch {CHAIN_SAVE_LIMIT})")

    while True:
        grid_videos = await get_grid_video_ids(page)

        pending = [
            (vid, elem) for (vid, elem) in grid_videos
            if vid in target_video_ids
            and (username, vid) not in scraped_videos
        ]

        if not pending:
            before_count = len(grid_videos)
            await scroll_grid_step(page)

            await wait_for_login_wall_clear(page, timeout=600, context_label=f"@{username} (scroll grid)")

            after_videos = await get_grid_video_ids(page)
            after_count = len(after_videos)

            if after_count <= before_count:
                no_new_scroll_streak += 1
            else:
                no_new_scroll_streak = 0

            if no_new_scroll_streak >= MAX_SCROLL_ATTEMPTS_NO_NEW:
                print(f"   🔚 Grid @{username} udah mentok ({MAX_SCROLL_ATTEMPTS_NO_NEW}x scroll gak ada video baru), stop scroll.")
                break

            all_target_seen = target_video_ids.issubset(
                {vid for (uname, vid) in scraped_videos if uname == username}
            )
            if all_target_seen:
                print(f"   ✅ Semua video target @{username} sudah ke-cover.")
                break

            continue

        # ⬇️ Klik thumbnail pending pertama yang ketemu di grid
        vid, elem = pending[0]

        await wait_for_login_wall_clear(page, timeout=600, context_label=f"@{username} (sebelum klik grid)")

        print(f"   👆 Klik video {vid[:10]}... dari grid (buka chain swipe baru)")
        try:
            await elem.scroll_into_view_if_needed()
            await asyncio.sleep(random.uniform(0.3, 0.8))
            await elem.click(timeout=15000)
        except Exception as e:
            print(f"      ⚠️ Gagal klik elemen grid: {str(e)[:150]}")
            await wait_for_login_wall_clear(page, timeout=600, context_label=f"@{username} (klik grid)")
            try:
                await elem.scroll_into_view_if_needed()
                await asyncio.sleep(random.uniform(0.5, 1))
                await elem.click(timeout=15000)
            except Exception as e2:
                print(f"      ❌ Tetap gagal klik setelah retry: {str(e2)[:150]}, skip video ini sesi ini.")
                await asyncio.sleep(random.uniform(2, 4))
                continue

        opened = await wait_url_change(page, profile_url, timeout_ms=8000)
        if not opened:
            print("      ⚠️ Modal gak kebuka (url gak berubah), skip.")
            await close_video_modal(page, profile_url)
            continue

        await asyncio.sleep(random.uniform(2, 3))

        # ⬇️ CHAIN SWIPE: mulai dari video yang baru dibuka, lanjut arrow-key
        saved_this_chain = 0
        steps = 0

        while steps < MAX_ARROW_STEPS_PER_CHAIN and saved_this_chain < CHAIN_SAVE_LIMIT:
            steps += 1
            current_url = page.url
            current_vid = parse_video_id_from_url(current_url)
            current_uname = parse_username_from_url(current_url)
            current_content_type = get_content_type_from_url(current_url)

            if current_uname and current_uname != username:
                print(f"      🔀 Swipe nyasar ke akun lain (@{current_uname}), balik ke grid @{username}.")
                break

            if not current_vid:
                print("      ⚠️ Gak bisa parse video_id dari URL, stop chain ini.")
                break

            if await is_video_shop(page):
                print(f"      🛍️  Video {current_vid[:10]} TikTok Shop -- save & skip extract, lanjut swipe.")
                row = build_unavailable_row(username, current_vid, current_content_type,
                                             "TikTok Shop video (app-only)")
                csv_writer_callback([row])
                scraped_videos.add((username, current_vid))
            elif await is_video_unavailable(page):
                print(f"      ⚠️ Video {current_vid[:10]} tidak tersedia -- save & skip extract, lanjut swipe.")
                row = build_unavailable_row(username, current_vid, current_content_type,
                                             "Video currently unavailable (deleted/private/etc)")
                csv_writer_callback([row])
                scraped_videos.add((username, current_vid))
            else:
                await wait_for_login_wall_clear(
                    page, timeout=600, context_label=f"@{username} (video {current_vid[:10]})"
                )

                if (username, current_vid) in scraped_videos:
                    print(f"      ⏭️  Video {current_vid[:10]} sudah ada di CSV, lanjut swipe.")
                elif current_vid not in target_video_ids:
                    print(f"      ⏭️  Video {current_vid[:10]} di luar rentang target, lanjut swipe.")
                else:
                    meta = await extract_video_metadata_current(
                        page, username, current_vid, video_stats_cache
                    )
                    if meta:
                        row = build_video_row(username, current_vid, current_content_type, meta)
                        csv_writer_callback([row])
                        print(f"      💾 Tersimpan ke CSV: video {current_vid[:10]}"
                              f"{' (photo)' if current_content_type == 'photo' else ''} "
                              f"({saved_this_chain + 1}/{CHAIN_SAVE_LIMIT} di chain ini)")
                        scraped_videos.add((username, current_vid))
                        videos_scraped_count += 1
                        saved_this_chain += 1
                        print(f"      ✅ Video {current_vid[:10]}: like={meta['like_count']}, "
                              f"comment={meta['comment_count']}, share={meta['share_count']}, "
                              f"save={meta['save_count']}")

                        if videos_scraped_count % DELAY_EVERY_N_VIDEOS == 0:
                            long_delay = random.uniform(*DELAY_AFTER_N_VIDEOS)
                            print(f"\n      🌙 Jeda PANJANG {long_delay / 60:.1f} menit "
                                  f"(anti-captcha, setelah {videos_scraped_count} video total)...")
                            await asyncio.sleep(long_delay)
                        else:
                            delay = random.uniform(*DELAY_BETWEEN_VIDEO)
                            print(f"      ⏳ Jeda {delay:.0f}s...")
                            await asyncio.sleep(delay)
                    else:
                        print(f"      ⏭️  Video {current_vid[:10]} di-skip (metadata gagal), "
                              f"belum ditandai scraped -- bakal ke-retry di run berikutnya.")

            already_done_ids = {v for (u, v) in scraped_videos if u == username}
            if target_video_ids.issubset(already_done_ids):
                print(f"   ✅ Semua video target @{username} sudah selesai (dalam chain swipe).")
                break

            if saved_this_chain >= CHAIN_SAVE_LIMIT:
                print(f"      🔁 Udah {CHAIN_SAVE_LIMIT} video ke-save di chain ini, balik ke grid buat cari batch berikutnya.")
                break

            moved = await navigate_next_video_arrow(page)
            if not moved:
                print("      🔚 Swipe mentok (gak ada video berikutnya), balik ke grid.")
                break

            await asyncio.sleep(random.uniform(1, 2))

        # Tutup modal, balik ke grid buat scroll & cari batch pending berikutnya
        await close_video_modal(page, profile_url)
        await asyncio.sleep(random.uniform(1, 2))

        already_done_ids = {v for (u, v) in scraped_videos if u == username}
        if target_video_ids.issubset(already_done_ids):
            print(f"   ✅ Semua video target @{username} sudah selesai.")
            break

    # ⬇️ FALLBACK: kalau scroll udah mentok (5x gak nemu video baru) TAPI masih
    # ada sisa target yang belum ke-cover, jangan nyerah -- lanjut DIRECT NAVIGATE
    # buat nutup sisanya. Ini bisa kejadian kalau video-nya somehow gak muncul
    # di grid (misal ke-filter TikTok, urutan lazy-load beda, dll).
    already_done_ids = {v for (u, v) in scraped_videos if u == username}
    still_pending = target_video_ids - already_done_ids

    if still_pending:
        print(f"\n   ℹ️  Scroll grid mentok tapi masih sisa {len(still_pending)} video "
              f"yang gak ketemu di grid -> lanjut DIRECT NAVIGATE buat nutup sisanya")
        n_fallback = await scrape_videos_direct_navigate(
            page, username, still_pending, scraped_videos, csv_writer_callback, video_stats_cache
        )
        videos_scraped_count += n_fallback

    print(f"   📊 Total video baru di-scrape (scroll grid): {videos_scraped_count}")
    return videos_scraped_count


# ============================================================
# ⬇️ PEMILIH STRATEGI (per-influencer)
# ============================================================
async def scrape_videos_choose_strategy(page, username, target_video_ids, scraped_videos,
                                          csv_writer_callback, video_stats_cache):
    """
    Pilih strategi berdasarkan JUMLAH video yang masih pending (belum discrape):

    - Pending >= DIRECT_NAVIGATE_MAX_PENDING (100)
        -> SCROLL GRID: buka profil, scroll terus nyari thumbnail yang match
           target. Kalau 5x scroll berturut-turut GAK nemu video baru di grid
           (lazy-load mentok / video gak ke-load di grid), otomatis fallback
           ke DIRECT NAVIGATE buat nutup sisa yang belum ke-cover.

    - Pending < DIRECT_NAVIGATE_MAX_PENDING (100)
        -> DIRECT NAVIGATE aja dari awal, gak usah buka grid & scroll sama
           sekali -- susun link video_id-nya langsung, lebih cepat buat
           jumlah kecil kayak gini.
    """
    already_done_ids = {vid for (uname, vid) in scraped_videos if uname == username}
    pending_ids = target_video_ids - already_done_ids

    if not pending_ids:
        print(f"   ✅ Semua video target @{username} sudah ke-cover")
        return 0

    n_pending = len(pending_ids)
    print(f"   📊 Video status: {n_pending}/{len(target_video_ids)} belum scraped")

    if n_pending >= DIRECT_NAVIGATE_MAX_PENDING:
        print(f"   ℹ️  Pending {n_pending} video (>={DIRECT_NAVIGATE_MAX_PENDING}) -> SCROLL GRID "
              f"(fallback ke direct-navigate kalau scroll mentok)")
        return await scrape_videos_grid(
            page, username, target_video_ids, scraped_videos, csv_writer_callback, video_stats_cache
        )
    else:
        print(f"   ℹ️  Pending {n_pending} video (<{DIRECT_NAVIGATE_MAX_PENDING}) -> DIRECT NAVIGATE langsung")
        return await scrape_videos_direct_navigate(
            page, username, pending_ids, scraped_videos, csv_writer_callback, video_stats_cache
        )


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

    print(f"   Influencer selesai total (profil+video): {n_fully_done}")
    print(f"   Influencer perlu diproses (baru/lanjut) : {len(to_process)}")

    if not to_process:
        print("✅ Semua profil & video sudah scraped.")
        send_telegram_alert("✅ Semua profil & video sudah scraped. Gak ada yang perlu dijalankan.")
        return

    send_telegram_alert(
        f"🚀 Scraping dimulai (mixed strategy: scroll-grid untuk influencer baru, "
        f"direct-nav untuk sisa <{DIRECT_NAVIGATE_MAX_PENDING}).\n"
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

        video_stats_cache = {}
        page.on("response", make_network_response_listener(video_stats_cache))
        print("📡 Network response listener aktif")

        try:
            for idx, username in enumerate(to_process, start=1):
                print(f"\n{'=' * 70}")
                print(f"[{idx}/{len(to_process)}] @{username}")
                print(f"{'=' * 70}")

                target_video_ids = {v["video_id"] for v in videos_by_user[username]}

                if username not in scraped_profil:
                    profil_row = await scrape_profil_metadata(page, username)
                    if profil_row:
                        append_profil_row(profil_row)
                        scraped_profil.add(username)

                already_done_ids = {vid for (uname, vid) in scraped_videos if uname == username}
                if not target_video_ids.issubset(already_done_ids):
                    if page.url != f"https://www.tiktok.com/@{username}":
                        try:
                            await page.goto(f"https://www.tiktok.com/@{username}",
                                             wait_until="load", timeout=60000)
                            await asyncio.sleep(random.uniform(2, 4))
                            await wait_for_login_wall_clear(page, timeout=600, context_label=f"@{username} (profil)")
                        except Exception as e:
                            print(f"   ❌ Gagal buka profil: {e}")
                            continue

                    n_new = await scrape_videos_choose_strategy(
                        page, username, target_video_ids, scraped_videos,
                        append_video_rows, video_stats_cache,
                    )
                    print(f"   📊 Video baru di-scrape: {n_new}")

                video_stats_cache.clear()

                if idx < len(to_process):
                    delay = random.uniform(*DELAY_BETWEEN_INFLUENCER)
                    mins = delay / 60
                    print(f"\n😴 Jeda {mins:.1f} menit sebelum influencer berikutnya...")
                    await asyncio.sleep(delay)

            print("\n\n✅✅✅ SELESAI ✅✅✅")
            send_telegram_alert(
                "✅✅✅ SCRAPING SELESAI ✅✅✅\n"
                f"Waktu selesai: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            )

        except CaptchaTimeoutError as e:
            print(f"\n\n🛑 {e}")
            print("   Data tersimpan aman di CSV. Jalanin ulang script kapan aja.")

        finally:
            print("\n🔒 Menutup browser...")
            await context.close()


if __name__ == "__main__":
    asyncio.run(main())