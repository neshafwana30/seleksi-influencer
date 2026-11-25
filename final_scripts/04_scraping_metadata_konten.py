"""
04 - Scraping Metadata Profil + Video, lalu Cleanup
======================================================
Tahap 3.1.2.3 (Pengumpulan Metadata Konten) pada proposal TA.

Alur:
1. scrape_metadata()
   Untuk tiap influencer di video_ids_master.csv (yang needs_comment_scrape
   = True), scrape metadata profil (followers, bio, dll) + metadata tiap
   video (like/comment/share/save count, description). Strategi dipilih
   otomatis per influencer:
     - pending >= 100 video -> scroll grid + swipe (arrow-key chain),
       fallback ke direct-navigate kalau grid mentok.
     - pending < 100 video  -> direct-navigate langsung.
   Metadata diprioritaskan dari network response JSON (lebih cepat &
   akurat), fallback ke DOM scraping kalau network timeout.
2. cleanup_metadata_profil()
   Perbaiki bug scraper lama: kolom followers/following kebawa jadi satu
   string gabungan ("153Following6.4MFollowers177.8MLikes") -> dipisah
   jadi 3 kolom angka penuh (following, followers, likes). Bio dirapikan
   jadi satu baris.
3. cleanup_metadata_video()
   Buang baris yang ke-4 kolom stats-nya nol semua (indikasi extract gagal
   diam-diam, bukan video dengan engagement 0 beneran) -- baris yang
   dibuang dicatat ke _removed_zero_rows.csv dan otomatis ke-retry di run
   scraping berikutnya.

Konsolidasi dari (dipilih versi FINAL/paling matang lewat metode cek
output + evolusi docstring tiap file, semuanya append ke metadata_profil.csv
& metadata_video.csv yang SAMA secara resumable):
- 01_pengumpulan_data/04_1_mendapatkan_metadata_akun/01_2_metadata_akun_tanpa_login.py  <- dipakai sebagai basis
- 01_pengumpulan_data/04_1_mendapatkan_metadata_akun/02_cleanup_profil.py
- 01_pengumpulan_data/04_1_mendapatkan_metadata_akun/03_cleanup_video.py

Riwayat generasi scraper yang DIGANTIKAN oleh 01_2 (disimpan sebagai
catatan historis, TIDAK diikutkan di sini):
- 01_1_videoid_with_audio.py (folder 03_videoid_perakun, generasi TERTUA,
  9/5) -- human-like arrow-key chain lewat seluruh grid.
- 01_metadata_akun_video.py (9/7) -- direct-link navigation saja (v2-v4).
- 01_1_metadata_akun_konten.py (9/8 siang) -- mixed strategy versi awal
  (scroll+klik tiap video, tanpa swipe chain).
- 00_tambah_cek_foto.py -- migrasi SEKALI JALAN buat nambah kolom
  'is_photo' ke baris lama (sebelum scraper bisa deteksi post foto).
  Tidak relevan lagi karena 01_2 sudah menulis kolom ini langsung.
Dicek lewat timestamp file + isi docstring tiap file: tiap generasi
menulis ke metadata_profil.csv/metadata_video.csv yang sama (resumable),
jadi bukan 4 percobaan paralel yang gagal, melainkan 4 iterasi perbaikan
berurutan pada data yang sama. Versi terakhir (01_2) dipakai sebagai kode
acuan karena strategi & deteksi captcha/error-nya paling lengkap.

Prasyarat: credentials.py (TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID) di root
project untuk notifikasi captcha; opsional, otomatis nonaktif kalau tidak ada.

CATATAN EKSEKUSI: scrape_metadata() adalah scraping PALING BERAT di seluruh
pipeline (bisa berjam-jam, ribuan video). SKIP otomatis kalau
final_scripts/output/04_metadata_video_konten.csv sudah ada. cleanup_* juga
skip otomatis kalau sudah pernah dijalankan (dicek dari nama kolom).
"""

import asyncio
import csv
import random
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

import pygame
import requests
from playwright.async_api import async_playwright

# -------------------------------------------------------------------------
# KONFIGURASI PATH
# -------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "01_pengumpulan_data"
FINAL_OUTPUT_ROOT = Path(__file__).resolve().parent / "output"
OUTPUT_DIR = FINAL_OUTPUT_ROOT / "04_scraping_metadata_konten"

MASTER_CSV = FINAL_OUTPUT_ROOT / "03_scraping_video_id" / "video_ids_master.csv"  # dari 03_scraping_video_id.py
PROFILE_DIR = DATA_DIR / "00_akun_playwright" / "tiktok_browser_profile"
WARNING_MP3 = DATA_DIR / "03_videoid_perakun" / "warning.mp3"  # file audio, bukan data -- tetap di lokasi lama

OUTPUT_PROFIL = OUTPUT_DIR / "metadata_profil_influencer.csv"
OUTPUT_VIDEO = OUTPUT_DIR / "metadata_video_konten.csv"
REMOVED_LOG_FILE = OUTPUT_DIR / "video_baris_dibuang.csv"

PROFIL_FIELDS = ["username", "followers", "following", "bio", "display_name",
                  "is_verified", "avatar_url", "scraped_at"]
VIDEO_FIELDS = ["username", "video_id", "video_url", "is_photo", "like_count", "comment_count",
                "share_count", "save_count", "description", "scraped_at"]
PROFIL_CLEAN_FIELDS = ["username", "following", "followers", "likes", "bio",
                        "display_name", "is_verified", "avatar_url", "scraped_at"]
STAT_COLUMNS = ["like_count", "comment_count", "share_count", "save_count"]

# Konfigurasi scraping (lazy/slow, human-like)
DELAY_BETWEEN_VIDEO = (10, 30)
DELAY_BETWEEN_INFLUENCER = (30, 100)
DELAY_EVERY_N_VIDEOS = 300
DELAY_AFTER_N_VIDEOS = (2 * 60, 3 * 60)
DELAY_SCROLL_STEP = (1.5, 2)
MAX_SCROLL_ATTEMPTS_NO_NEW = 5
MAX_ARROW_STEPS_PER_CHAIN = 60
METADATA_READY_TIMEOUT = 10000
NETWORK_STATS_WAIT_TIMEOUT = 8.0
NETWORK_STATS_POLL_INTERVAL = 0.25
DIRECT_NAVIGATE_MAX_PENDING = 100  # pending < ini -> direct navigate, >= ini -> scroll grid

REALISTIC_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)


# -------------------------------------------------------------------------
# TELEGRAM ALERT (opsional, lewat credentials.py di root project)
# -------------------------------------------------------------------------
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

try:
    from credentials import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
    TELEGRAM_ENABLED = bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)
except ImportError:
    TELEGRAM_BOT_TOKEN = None
    TELEGRAM_CHAT_ID = None
    TELEGRAM_ENABLED = False


def _send_telegram_alert(message: str):
    if not TELEGRAM_ENABLED:
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": message}, timeout=10)
    except Exception as e:
        print(f"   Gagal kirim Telegram alert: {e}")


class CaptchaTimeoutError(Exception):
    """Captcha/login wall gak di-solve dalam batas waktu."""


# -------------------------------------------------------------------------
# AUDIO ALERT
# -------------------------------------------------------------------------
def _play_warning():
    if not WARNING_MP3.exists():
        return
    try:
        pygame.mixer.init()
        pygame.mixer.music.load(str(WARNING_MP3))
        pygame.mixer.music.play()
    except Exception as e:
        print(f"   Gagal jalanin audio: {e}")


def _stop_warning():
    try:
        if pygame.mixer.get_init():
            pygame.mixer.music.stop()
            pygame.mixer.quit()
    except Exception:
        pass


# -------------------------------------------------------------------------
# HELPER: baca master CSV & existing output (resume)
# -------------------------------------------------------------------------
def _load_master_csv() -> dict[str, list]:
    videos_by_user = {}
    with open(MASTER_CSV, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if str(row.get("needs_comment_scrape", "")).strip().lower() == "true":
                videos_by_user.setdefault(row["username"], []).append(row)
    return videos_by_user


def _load_existing_profil() -> set:
    if not OUTPUT_PROFIL.exists():
        return set()
    with open(OUTPUT_PROFIL, "r", encoding="utf-8", newline="") as f:
        return {row["username"] for row in csv.DictReader(f)}


def _load_existing_videos() -> set:
    if not OUTPUT_VIDEO.exists():
        return set()
    with open(OUTPUT_VIDEO, "r", encoding="utf-8", newline="") as f:
        return {(row["username"], row["video_id"]) for row in csv.DictReader(f)}


def _append_profil_row(row):
    file_exists = OUTPUT_PROFIL.exists()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PROFIL, "a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=PROFIL_FIELDS)
        if not file_exists:
            writer.writeheader()
        writer.writerow(row)


def _append_video_rows(rows):
    if not rows:
        return
    file_exists = OUTPUT_VIDEO.exists()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_VIDEO, "a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=VIDEO_FIELDS)
        if not file_exists:
            writer.writeheader()
        writer.writerows(rows)


# -------------------------------------------------------------------------
# NETWORK RESPONSE CAPTURE (sumber metadata utama, lebih cepat dari DOM)
# -------------------------------------------------------------------------
def _find_video_stats_blocks(obj, _depth=0):
    results = []
    if _depth > 12:
        return results
    if isinstance(obj, dict):
        stats = obj.get("stats") or obj.get("statsV2")
        if isinstance(stats, dict) and (
            "diggCount" in stats or "shareCount" in stats or "playCount" in stats
        ):
            vid = obj.get("id") or obj.get("itemId") or obj.get("awemeId")
            if vid:
                results.append((str(vid), stats, obj.get("desc")))
        for value in obj.values():
            results.extend(_find_video_stats_blocks(value, _depth + 1))
    elif isinstance(obj, list):
        for item in obj:
            results.extend(_find_video_stats_blocks(item, _depth + 1))
    return results


def _make_network_response_listener(video_stats_cache):
    async def on_response(response):
        try:
            if "tiktok.com" not in response.url:
                return
            if "json" not in response.headers.get("content-type", ""):
                return
            body = await response.json()
        except Exception:
            return
        try:
            blocks = _find_video_stats_blocks(body)
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


async def _wait_for_network_stats(video_stats_cache, video_id,
                                   timeout=NETWORK_STATS_WAIT_TIMEOUT,
                                   poll_interval=NETWORK_STATS_POLL_INTERVAL):
    elapsed = 0.0
    while elapsed < timeout:
        if video_id in video_stats_cache:
            return video_stats_cache[video_id]
        await asyncio.sleep(poll_interval)
        elapsed += poll_interval
    return None


# -------------------------------------------------------------------------
# LOGIN WALL / CAPTCHA CHECK
# -------------------------------------------------------------------------
async def _wait_for_login_wall_clear(page, timeout=600, context_label="") -> bool:
    blocker_selectors = [
        'text="Log in to TikTok"', '[data-e2e="login-modal"]',
        'div[role="dialog"]:has-text("Log in")', 'div[role="dialog"]:has-text("Continue with")',
        'text="Drag the slider to fit the puzzle"', '[id*="captcha"]', '[class*="captcha"]',
        'iframe[src*="captcha"]', 'img[alt="Captcha" i]', 'img[alt*="captcha" i]',
        '.TUXModal:has-text("captcha")', '.TUXModal:has-text("Captcha")',
        '[class*="captcha-verify"]', '[class*="captcha_verify"]', 'div[class*="TUXModal"] iframe',
        'text=/verify\\s*to\\s*continue/i', 'text=/select\\s*2\\s*similar\\s*images/i',
        'text=/tap\\s*the\\s*objects/i', 'text=/rotate\\s*the\\s*image/i',
        'text=/drag\\s*the\\s*puzzle/i', 'text=/verify\\s*you\\s*are\\s*human/i',
    ]
    ALERT_REPEAT_EVERY = 30
    start = asyncio.get_event_loop().time()
    was_found = alert_sent = False
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
                _stop_warning()
                if alert_sent:
                    _send_telegram_alert(f"Captcha solved, lanjut scraping.\n{context_label}".strip())
                await asyncio.sleep(random.uniform(2, 4))
            return True

        now = asyncio.get_event_loop().time()
        remaining = int(timeout - (now - start))

        if not was_found:
            print(f"\n   CAPTCHA / LOGIN WALL TERDETEKSI, timeout {remaining}s")
            _play_warning()

        if last_alert_at is None or (now - last_alert_at) >= ALERT_REPEAT_EVERY:
            _send_telegram_alert(
                f"Captcha terdeteksi!\n{context_label}\n"
                f"Waktu: {datetime.now().strftime('%H:%M:%S')} | Sisa: {remaining}s"
            )
            alert_sent, last_alert_at = True, now

        was_found = True
        await asyncio.sleep(3)

    _stop_warning()
    _send_telegram_alert(f"Captcha timeout!\n{context_label}\nData aman di CSV, jalanin ulang kapan saja.")
    raise CaptchaTimeoutError("Captcha/login wall gak di-solve dalam batas waktu.")


# -------------------------------------------------------------------------
# HELPER: parse video_id & URL
# -------------------------------------------------------------------------
def _parse_video_id_from_url(url: str):
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


def _get_content_type_from_url(url: str):
    if not url:
        return None
    if "/photo/" in url:
        return "photo"
    if "/video/" in url:
        return "video"
    return None


def _parse_username_from_url(url: str):
    if not url or "/@" not in url:
        return None
    return url.split("/@", 1)[1].split("/", 1)[0] or None


async def _wait_url_change(page, old_url, timeout_ms=8000) -> bool:
    try:
        await page.wait_for_function(
            "oldUrl => window.location.href !== oldUrl", arg=old_url, timeout=timeout_ms,
        )
        return True
    except Exception:
        return False


async def _is_video_unavailable(page) -> bool:
    for sel in ['text="Video currently unavailable"', 'text="This video is unavailable"',
                'text=/video.*unavailable/i', 'text=/tidak.*tersedia/i',
                '[data-e2e="video-unavailable-placeholder"]']:
        try:
            elem = await page.query_selector(sel)
            if elem and await elem.is_visible():
                return True
        except Exception:
            continue
    return False


async def _is_video_shop(page) -> bool:
    for sel in ['text=/can\\s*only\\s*be\\s*viewed\\s*in\\s*the\\s*tiktok\\s*app/i',
                'text=/only\\s*available\\s*(on|in)\\s*the\\s*tiktok\\s*app/i',
                'text=/hanya\\s*(dapat|bisa)\\s*dilihat\\s*di\\s*aplikasi\\s*tiktok/i']:
        try:
            elem = await page.query_selector(sel)
            if elem and await elem.is_visible():
                return True
        except Exception:
            continue
    return False


# -------------------------------------------------------------------------
# SCRAPE PROFILE METADATA
# -------------------------------------------------------------------------
async def _scrape_profil_metadata(page, username):
    profile_url = f"https://www.tiktok.com/@{username}"
    try:
        await page.goto(profile_url, wait_until="load", timeout=60000)
    except Exception as e:
        print(f"   Gagal buka profil: {e}")
        return None

    await asyncio.sleep(random.uniform(2, 4))
    await _wait_for_login_wall_clear(page, timeout=600, context_label=f"@{username} (profil)")

    async def _text(sel):
        try:
            el = await page.query_selector(sel)
            return (await el.text_content() or "").strip() if el else ""
        except Exception:
            return ""

    followers_text = await _text('h3:has-text("Followers")')
    following_text = await _text('h3:has-text("Following")')
    bio_text = await _text('[data-e2e="user-bio"]')
    display_name = await _text('h1')

    try:
        is_verified = (await page.query_selector('svg[data-e2e="icon-verified"]')) is not None
    except Exception:
        is_verified = False

    try:
        avatar_elem = await page.query_selector('img[alt*="avatar"]')
        avatar_url = (await avatar_elem.get_attribute("src")) if avatar_elem else ""
    except Exception:
        avatar_url = ""

    return {
        "username": username,
        "followers": followers_text,
        "following": following_text,
        "bio": bio_text,
        "display_name": display_name or username,
        "is_verified": str(is_verified),
        "avatar_url": avatar_url or "",
        "scraped_at": datetime.now().isoformat(),
    }


# -------------------------------------------------------------------------
# EXTRACT METADATA VIDEO (network cache > fallback DOM)
# -------------------------------------------------------------------------
async def _extract_video_metadata(page, username, video_id, video_stats_cache):
    stats_from_network = await _wait_for_network_stats(video_stats_cache, video_id)
    if stats_from_network:
        result = dict(stats_from_network)
        if not result.get("description"):
            try:
                desc_elem = await page.query_selector('[data-e2e="video-desc"]')
                if desc_elem:
                    result["description"] = (await desc_elem.text_content() or "").strip()
            except Exception:
                pass
        return result

    try:
        await page.wait_for_selector('[data-e2e="like-count"]', timeout=METADATA_READY_TIMEOUT)
    except Exception:
        return None

    async def _text(sel):
        try:
            el = await page.query_selector(sel)
            return (await el.text_content() or "").strip() if el else ""
        except Exception:
            return ""

    like_count = await _text('[data-e2e="like-count"]')
    if not like_count:
        await asyncio.sleep(2)
        like_count = await _text('[data-e2e="like-count"]')
        if not like_count:
            return None

    return {
        "like_count": like_count or "0",
        "comment_count": (await _text('[data-e2e="comment-count"]')) or "0",
        "share_count": (await _text('[data-e2e="share-count"]')) or "0",
        "save_count": (await _text('[data-e2e="favorite-count"]')) or "0",
        "description": await _text('[data-e2e="video-desc"]'),
    }


def _build_video_row(username, video_id, content_type, meta):
    content_path = content_type or "video"
    return {
        "username": username,
        "video_id": video_id,
        "video_url": f"https://www.tiktok.com/@{username}/{content_path}/{video_id}",
        "is_photo": str(content_type == "photo"),
        **meta,
        "scraped_at": datetime.now().isoformat(),
    }


def _build_unavailable_row(username, video_id, content_type, reason):
    content_path = content_type or "video"
    return {
        "username": username,
        "video_id": video_id,
        "video_url": f"https://www.tiktok.com/@{username}/{content_path}/{video_id}",
        "is_photo": str(content_type == "photo"),
        "like_count": "N/A", "comment_count": "N/A", "share_count": "N/A", "save_count": "N/A",
        "description": reason,
        "scraped_at": datetime.now().isoformat(),
    }


# -------------------------------------------------------------------------
# STRATEGI 1: DIRECT NAVIGATE (pending < 100 video)
# -------------------------------------------------------------------------
async def _process_video_by_direct_navigate(page, username, video_id, video_stats_cache,
                                             scraped_videos, csv_writer_callback):
    video_url = f"https://www.tiktok.com/@{username}/video/{video_id}"
    try:
        await page.goto(video_url, wait_until="load", timeout=30000)
        await asyncio.sleep(random.uniform(1, 2))
    except Exception:
        return False

    current_url = page.url
    content_type = _get_content_type_from_url(current_url)
    current_vid = _parse_video_id_from_url(current_url)
    current_uname = _parse_username_from_url(current_url)

    if not current_vid or current_vid != video_id or current_uname != username:
        return False

    if await _is_video_unavailable(page):
        csv_writer_callback([_build_unavailable_row(username, current_vid, content_type,
                                                      "Video currently unavailable (deleted/private/etc)")])
        scraped_videos.add((username, current_vid))
        return True

    if await _is_video_shop(page):
        csv_writer_callback([_build_unavailable_row(username, current_vid, content_type,
                                                      "TikTok Shop video (app-only)")])
        scraped_videos.add((username, current_vid))
        return True

    await _wait_for_login_wall_clear(page, timeout=600, context_label=f"@{username} (direct nav {video_id[:10]})")

    meta = await _extract_video_metadata(page, username, current_vid, video_stats_cache)
    if not meta:
        return False

    csv_writer_callback([_build_video_row(username, current_vid, content_type, meta)])
    scraped_videos.add((username, current_vid))
    return True


async def _scrape_videos_direct_navigate(page, username, pending_ids, scraped_videos,
                                          csv_writer_callback, video_stats_cache):
    videos_scraped_count = 0
    for idx, vid in enumerate(sorted(pending_ids), start=1):
        if (username, vid) in scraped_videos:
            continue
        success = await _process_video_by_direct_navigate(
            page, username, vid, video_stats_cache, scraped_videos, csv_writer_callback
        )
        if success:
            videos_scraped_count += 1
            if videos_scraped_count % DELAY_EVERY_N_VIDEOS == 0:
                await asyncio.sleep(random.uniform(*DELAY_AFTER_N_VIDEOS))
            else:
                await asyncio.sleep(random.uniform(*DELAY_BETWEEN_VIDEO))
    print(f"   Direct navigate: {videos_scraped_count} video baru")
    return videos_scraped_count


# -------------------------------------------------------------------------
# STRATEGI 2: SCROLL GRID + SWIPE (pending >= 100 video)
# -------------------------------------------------------------------------
async def _get_grid_video_ids(page):
    anchors = await page.query_selector_all('div[data-e2e="user-post-item"] a')
    if not anchors:
        anchors = await page.query_selector_all('a[href*="/video/"]')
    results, seen = [], set()
    for a in anchors:
        try:
            href = await a.get_attribute("href")
        except Exception:
            continue
        vid = _parse_video_id_from_url(href) if href else None
        if vid and vid not in seen:
            seen.add(vid)
            results.append((vid, a))
    return results


async def _scroll_grid_step(page):
    await page.evaluate("window.scrollBy(0, window.innerHeight * (0.6 + Math.random()*0.4))")
    await asyncio.sleep(random.uniform(*DELAY_SCROLL_STEP))


async def _close_video_modal(page, profile_url):
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


async def _navigate_next_video_arrow(page) -> bool:
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
    return await _wait_url_change(page, old_url, timeout_ms=8000)


async def _scrape_videos_grid(page, username, target_video_ids, scraped_videos,
                               csv_writer_callback, video_stats_cache):
    """
    Scroll grid cari thumbnail pending -> klik -> swipe (arrow-key) chain
    selama video berikutnya masih relevan (target & belum discrape).
    Begitu ketemu video gak relevan, chain stop, balik ke grid cari batch
    berikutnya. Kalau grid mentok (5x scroll gak ada video baru) tapi
    masih ada sisa target, fallback ke direct-navigate.
    """
    profile_url = f"https://www.tiktok.com/@{username}"
    videos_scraped_count = 0
    no_new_scroll_streak = 0

    while True:
        grid_videos = await _get_grid_video_ids(page)
        pending = [(vid, elem) for (vid, elem) in grid_videos
                   if vid in target_video_ids and (username, vid) not in scraped_videos]

        if not pending:
            before_count = len(grid_videos)
            await _scroll_grid_step(page)
            await _wait_for_login_wall_clear(page, timeout=600, context_label=f"@{username} (scroll grid)")
            after_count = len(await _get_grid_video_ids(page))

            no_new_scroll_streak = 0 if after_count > before_count else no_new_scroll_streak + 1
            if no_new_scroll_streak >= MAX_SCROLL_ATTEMPTS_NO_NEW:
                break

            already_done = {v for (u, v) in scraped_videos if u == username}
            if target_video_ids.issubset(already_done):
                break
            continue

        vid, elem = pending[0]
        await _wait_for_login_wall_clear(page, timeout=600, context_label=f"@{username} (sebelum klik grid)")

        try:
            await elem.scroll_into_view_if_needed()
            await asyncio.sleep(random.uniform(0.3, 0.8))
            await elem.click(timeout=15000)
        except Exception:
            await _wait_for_login_wall_clear(page, timeout=600, context_label=f"@{username} (klik grid)")
            try:
                await elem.scroll_into_view_if_needed()
                await asyncio.sleep(random.uniform(0.5, 1))
                await elem.click(timeout=15000)
            except Exception:
                await asyncio.sleep(random.uniform(2, 4))
                continue

        opened = await _wait_url_change(page, profile_url, timeout_ms=8000)
        if not opened:
            await _close_video_modal(page, profile_url)
            continue

        await asyncio.sleep(random.uniform(2, 3))

        # Chain swipe: lanjut selama video berikutnya masih relevan
        steps = 0
        while steps < MAX_ARROW_STEPS_PER_CHAIN:
            steps += 1
            current_url = page.url
            current_vid = _parse_video_id_from_url(current_url)
            current_uname = _parse_username_from_url(current_url)
            content_type = _get_content_type_from_url(current_url)

            if current_uname and current_uname != username:
                break
            if not current_vid:
                break
            if (username, current_vid) in scraped_videos or current_vid not in target_video_ids:
                break

            if await _is_video_shop(page):
                csv_writer_callback([_build_unavailable_row(username, current_vid, content_type,
                                                              "TikTok Shop video (app-only)")])
                scraped_videos.add((username, current_vid))
            elif await _is_video_unavailable(page):
                csv_writer_callback([_build_unavailable_row(username, current_vid, content_type,
                                                              "Video currently unavailable (deleted/private/etc)")])
                scraped_videos.add((username, current_vid))
            else:
                await _wait_for_login_wall_clear(page, timeout=600,
                                                  context_label=f"@{username} (video {current_vid[:10]})")
                meta = await _extract_video_metadata(page, username, current_vid, video_stats_cache)
                if meta:
                    csv_writer_callback([_build_video_row(username, current_vid, content_type, meta)])
                    scraped_videos.add((username, current_vid))
                    videos_scraped_count += 1
                    if videos_scraped_count % DELAY_EVERY_N_VIDEOS == 0:
                        await asyncio.sleep(random.uniform(*DELAY_AFTER_N_VIDEOS))
                    else:
                        await asyncio.sleep(random.uniform(*DELAY_BETWEEN_VIDEO))
                else:
                    break  # metadata gagal -> stop chain, belum ditandai scraped (retry nanti)

            already_done = {v for (u, v) in scraped_videos if u == username}
            if target_video_ids.issubset(already_done):
                break

            if not await _navigate_next_video_arrow(page):
                break
            await asyncio.sleep(random.uniform(1, 2))

        await _close_video_modal(page, profile_url)
        await asyncio.sleep(random.uniform(1, 2))

        already_done = {v for (u, v) in scraped_videos if u == username}
        if target_video_ids.issubset(already_done):
            break

    already_done = {v for (u, v) in scraped_videos if u == username}
    still_pending = target_video_ids - already_done
    if still_pending:
        n_fallback = await _scrape_videos_direct_navigate(
            page, username, still_pending, scraped_videos, csv_writer_callback, video_stats_cache
        )
        videos_scraped_count += n_fallback

    print(f"   Scroll+swipe grid: {videos_scraped_count} video baru")
    return videos_scraped_count


async def _scrape_videos_choose_strategy(page, username, target_video_ids, scraped_videos,
                                          csv_writer_callback, video_stats_cache):
    already_done = {vid for (uname, vid) in scraped_videos if uname == username}
    pending_ids = target_video_ids - already_done
    if not pending_ids:
        return 0

    if len(pending_ids) >= DIRECT_NAVIGATE_MAX_PENDING:
        return await _scrape_videos_grid(
            page, username, target_video_ids, scraped_videos, csv_writer_callback, video_stats_cache
        )
    return await _scrape_videos_direct_navigate(
        page, username, pending_ids, scraped_videos, csv_writer_callback, video_stats_cache
    )


# -------------------------------------------------------------------------
# STEP 1 - MAIN SCRAPING LOOP
# -------------------------------------------------------------------------
async def scrape_metadata() -> None:
    if OUTPUT_VIDEO.exists() and OUTPUT_PROFIL.exists():
        print(f"[Step 1] Sudah ada -> {OUTPUT_PROFIL.name} & {OUTPUT_VIDEO.name} "
              f"(skip scraping terberat di pipeline, data lama dipakai)")
        return

    videos_by_user = _load_master_csv()
    scraped_profil = _load_existing_profil()
    scraped_videos = _load_existing_videos()

    to_process = []
    for u, vids in videos_by_user.items():
        user_video_ids = {v["video_id"] for v in vids}
        already_done = {vid for (uname, vid) in scraped_videos if uname == u}
        if u in scraped_profil and user_video_ids.issubset(already_done):
            continue
        to_process.append(u)

    print(f"Total influencer: {len(videos_by_user)} | selesai: {len(videos_by_user) - len(to_process)} | perlu diproses: {len(to_process)}")

    if not to_process:
        print("Semua profil & video sudah scraped.")
        return

    _send_telegram_alert(f"Scraping metadata dimulai. Influencer diproses: {len(to_process)}")

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            str(PROFILE_DIR), headless=False, viewport={"width": 1400, "height": 900},
            user_agent=REALISTIC_UA, args=["--disable-blink-features=AutomationControlled"],
        )
        await context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        )
        page = context.pages[0] if context.pages else await context.new_page()

        video_stats_cache = {}
        page.on("response", _make_network_response_listener(video_stats_cache))

        try:
            for idx, username in enumerate(to_process, start=1):
                print(f"\n[{idx}/{len(to_process)}] @{username}")
                target_video_ids = {v["video_id"] for v in videos_by_user[username]}

                if username not in scraped_profil:
                    profil_row = await _scrape_profil_metadata(page, username)
                    if profil_row:
                        _append_profil_row(profil_row)
                        scraped_profil.add(username)

                already_done = {vid for (uname, vid) in scraped_videos if uname == username}
                if not target_video_ids.issubset(already_done):
                    if page.url != f"https://www.tiktok.com/@{username}":
                        try:
                            await page.goto(f"https://www.tiktok.com/@{username}", wait_until="load", timeout=60000)
                            await asyncio.sleep(random.uniform(2, 4))
                            await _wait_for_login_wall_clear(page, timeout=600, context_label=f"@{username} (profil)")
                        except Exception as e:
                            print(f"   Gagal buka profil: {e}")
                            continue

                    await _scrape_videos_choose_strategy(
                        page, username, target_video_ids, scraped_videos, _append_video_rows, video_stats_cache,
                    )

                video_stats_cache.clear()
                if idx < len(to_process):
                    await asyncio.sleep(random.uniform(*DELAY_BETWEEN_INFLUENCER))

            print("\nSelesai semua influencer.")
            _send_telegram_alert(f"Scraping metadata selesai. Waktu: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

        except CaptchaTimeoutError as e:
            print(f"\n{e}\nData tersimpan aman di CSV, jalanin ulang kapan saja.")
        finally:
            await context.close()


# -------------------------------------------------------------------------
# STEP 2 - CLEANUP metadata_profil.csv
# -------------------------------------------------------------------------
_COMBINED_PATTERN = re.compile(
    r'([\d]*\.?[\d]+\s*[KMB]?)\s*Following'
    r'([\d]*\.?[\d]+\s*[KMB]?)\s*Followers'
    r'([\d]*\.?[\d]+\s*[KMB]?)\s*Likes',
    re.IGNORECASE,
)


def _is_pure_number(text: str) -> bool:
    return bool(text) and text.strip().isdigit()


def _convert_shorthand_to_number(text: str):
    if not text or not (text := text.strip()):
        return None
    match = re.match(r'^([\d]*\.?[\d]+)\s*([KMB])?$', text, re.IGNORECASE)
    if not match:
        return None
    number_part = float(match.group(1))
    multiplier = {"": 1, "K": 1_000, "M": 1_000_000, "B": 1_000_000_000}
    return int(round(number_part * multiplier.get((match.group(2) or "").upper(), 1)))


def _parse_combined_stats(raw_text: str):
    if not raw_text:
        return None, None, None
    match = _COMBINED_PATTERN.search(raw_text)
    if not match:
        return None, None, None
    following_raw, followers_raw, likes_raw = match.groups()
    return (_convert_shorthand_to_number(following_raw),
            _convert_shorthand_to_number(followers_raw),
            _convert_shorthand_to_number(likes_raw))


def _clean_bio(raw_bio: str) -> str:
    if not raw_bio:
        return ""
    flattened = raw_bio.replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
    return re.sub(r'\s+', ' ', flattened).strip()


def cleanup_metadata_profil(input_file: Path = OUTPUT_PROFIL) -> None:
    """Pisahkan kolom followers/following/likes yang kebawa gabungan jadi satu string, rapikan bio."""
    if not input_file.exists():
        raise FileNotFoundError(f"File tidak ditemukan: {input_file}")

    with open(input_file, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        original_fields = reader.fieldnames or []
        rows = list(reader)

    if not rows:
        print("[Cleanup Profil] File kosong, tidak ada yang di-cleanup.")
        return

    if "likes" in original_fields and all(_is_pure_number(r.get("followers", "")) or not r.get("followers") for r in rows):
        print(f"[Cleanup Profil] {input_file.name} sudah bersih (kolom 'likes' sudah terpisah), skip.")
        return

    backup_path = input_file.with_name(input_file.name + f".bak_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
    shutil.copy2(input_file, backup_path)

    already_migrated = "likes" in original_fields
    cleaned_rows = []
    n_parsed_ok = n_parse_failed = n_empty = n_already_clean = 0

    for row in rows:
        raw_followers = row.get("followers", "")

        if not raw_followers:
            following = followers = likes = None
            n_empty += 1
        elif _is_pure_number(raw_followers):
            following = row.get("following", "").strip() or None
            followers = raw_followers.strip()
            likes = (row.get("likes", "").strip() or None) if already_migrated else None
            n_already_clean += 1
        else:
            following, followers, likes = _parse_combined_stats(raw_followers)
            if following is None and followers is None and likes is None:
                n_parse_failed += 1
            else:
                n_parsed_ok += 1

        cleaned_rows.append({
            "username": row.get("username", ""),
            "following": following if following is not None else "",
            "followers": followers if followers is not None else "",
            "likes": likes if likes is not None else "",
            "bio": _clean_bio(row.get("bio", "")),
            "display_name": row.get("display_name", ""),
            "is_verified": row.get("is_verified", ""),
            "avatar_url": row.get("avatar_url", ""),
            "scraped_at": row.get("scraped_at", ""),
        })

    with open(input_file, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=PROFIL_CLEAN_FIELDS)
        writer.writeheader()
        writer.writerows(cleaned_rows)

    print(f"[Cleanup Profil] sudah bersih: {n_already_clean} | diparse: {n_parsed_ok} | "
          f"gagal parse: {n_parse_failed} | kosong: {n_empty} | backup: {backup_path.name}")


# -------------------------------------------------------------------------
# STEP 3 - CLEANUP metadata_video.csv
# -------------------------------------------------------------------------
def _clean_text_field(raw_text: str) -> str:
    if not raw_text:
        return ""
    flattened = raw_text.replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
    return re.sub(r'\s+', ' ', flattened).strip()


def _is_all_zero(row: dict) -> bool:
    return all(str(row.get(col, "")).strip() in ("0", "") for col in STAT_COLUMNS)


def cleanup_metadata_video(input_file: Path = OUTPUT_VIDEO,
                            removed_log_file: Path = REMOVED_LOG_FILE) -> None:
    """Rapikan description, buang baris stats all-zero (indikasi extract gagal diam-diam)."""
    if not input_file.exists():
        raise FileNotFoundError(f"File tidak ditemukan: {input_file}")

    with open(input_file, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []
        rows = list(reader)

    if not rows:
        print("[Cleanup Video] File kosong, tidak ada yang di-cleanup.")
        return

    missing_cols = [c for c in STAT_COLUMNS if c not in fieldnames]
    if missing_cols:
        raise ValueError(f"Kolom stats tidak lengkap, hilang: {missing_cols}")

    if not any(_is_all_zero(r) for r in rows) and not any("\n" in r.get("description", "") for r in rows):
        print(f"[Cleanup Video] {input_file.name} sudah bersih (tidak ada baris all-zero/newline), skip.")
        return

    backup_path = input_file.with_name(input_file.name + f".bak_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
    shutil.copy2(input_file, backup_path)

    kept_rows, removed_rows = [], []
    for row in rows:
        if _is_all_zero(row):
            removed_rows.append(row)
            continue
        new_row = dict(row)
        new_row["description"] = _clean_text_field(row.get("description", ""))
        kept_rows.append(new_row)

    if removed_rows:
        with open(removed_log_file, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(removed_rows)

    with open(input_file, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(kept_rows)

    print(f"[Cleanup Video] dihapus (all-zero): {len(removed_rows)} | valid: {len(kept_rows)} | "
          f"backup: {backup_path.name}")
    if removed_rows:
        print(f"[Cleanup Video] video yang dihapus otomatis di-retry di run scraping berikutnya "
              f"(dicatat di {removed_log_file.name})")


# -------------------------------------------------------------------------
# MAIN
# -------------------------------------------------------------------------
async def main():
    await scrape_metadata()
    cleanup_metadata_profil()
    cleanup_metadata_video()


if __name__ == "__main__":
    asyncio.run(main())
