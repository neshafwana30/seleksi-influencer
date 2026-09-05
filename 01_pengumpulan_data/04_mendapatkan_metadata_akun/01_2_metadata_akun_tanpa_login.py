"""
TAHAP 2 — Scrape metadata profil + metadata video dari TikTok.

Sumber: video_ids_master.csv (hasil tahap 1)
Output:
  - metadata_profil.csv : followers, following, bio, nama, verification, avatar_url
  - metadata_video.csv : like_count, comment_count, share_count, save_count, description

⚡ VERSI INI: EXTRACT METADATA DARI NETWORK RESPONSE (JSON API), BUKAN DOM.

Kenapa: mode grid+klik (modal) TikTok gak nampilin elemen share-count di
DOM secara reliable. Tapi TikTok tetap FETCH data video itu di background
lewat internal API (buat render modal-nya) -- dan response JSON itu
ISINYA share_count/like_count/comment_count/save_count dalam bentuk ANGKA
MENTAH (bukan "1.2K" yang perlu di-parse). Jadi daripada scrape teks dari
DOM (gak reliable + perlu parsing "1.2K" -> 1200), kita dengerin response
network yang lewat pas video dibuka, ambil datanya langsung dari situ.

Ini SUDAH DIVERIFIKASI jalan lewat script diagnostik terpisah (nemuin
share_count muncul di response JSON pas video dibuka via klik grid).

Cara kerja:
1. Sebelum mulai scraping, pasang listener `page.on("response", ...)` yang
   nyaring SEMUA response JSON dari tiktok.com yang mengandung field stats
   video (diggCount/shareCount/dst), lalu simpan ke cache in-memory
   (dict: video_id -> stats) berdasarkan id yang ketemu di JSON-nya.
2. Sama seperti sebelumnya: grid -> klik video -> arrow-key ke video
   berikutnya (SPA navigation, BUKAN goto langsung -- ini yang bikin aman
   dari WAF/Akamai block, karena cuma trigger fetch API, bukan full page
   load per video).
3. Pas mau extract 1 video: TUNGGU SEBENTAR cache keisi buat video_id itu
   (network response biasanya nyusul beberapa ratus ms - 2 detik setelah
   video dibuka). Kalau ketemu di cache -> pakai itu (prioritas utama).
   Kalau TIDAK ketemu dalam waktu wajar -> fallback ke cara lama (scrape
   DOM), biar tetap ada hasil walau mungkin share_count-nya kosong/gak
   akurat -- daripada video-nya di-skip total.

Fitur lain (SEMUA SAMA PERSIS kayak versi sebelumnya):
- HUMAN-LIKE NAVIGATION: scroll grid -> klik video -> arrow-key.
- Lazy scraping, resume otomatis, TikTok Shop detection, Telegram alert,
  audio warning, dedup, write langsung per video ke CSV.
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

OUTPUT_DIR = os.path.join(DATA_DIR, "04_mendapatkan_metadata_akun")
OUTPUT_PROFIL = os.path.join(OUTPUT_DIR, "metadata_profil.csv")
OUTPUT_VIDEO = os.path.join(OUTPUT_DIR, "metadata_video.csv")

PROFIL_FIELDS = ["username", "followers", "following", "bio", "display_name",
                 "is_verified", "avatar_url", "scraped_at"]
VIDEO_FIELDS = ["username", "video_id", "video_url", "is_photo", "like_count", "comment_count",
                "share_count", "save_count", "description", "scraped_at"]

# ============================================================
# KONFIGURASI SCRAPING (LAZY/SLOW, HUMAN-LIKE)
# ============================================================
DELAY_BETWEEN_VIDEO = (10, 50)
DELAY_BETWEEN_INFLUENCER = (3 * 60, 5 * 60)
DELAY_EVERY_N_VIDEOS = 300
DELAY_AFTER_N_VIDEOS = (2 * 60, 3 * 60)
DELAY_SCROLL_STEP = (1.5, 3)
MAX_VIDEOS_PER_INFLUENCER = 500
MAX_SCROLL_ATTEMPTS_NO_NEW = 4
MAX_ARROW_STEPS_PER_CHAIN = 60
METADATA_READY_TIMEOUT = 10000

# ⬇️ BARU: berapa lama nunggu network response keisi cache sebelum
# fallback ke DOM scraping. Biasanya response nyampe cepat (<2s).
NETWORK_STATS_WAIT_TIMEOUT = 8.0   # detik
NETWORK_STATS_POLL_INTERVAL = 0.25  # detik, jeda antar cek cache

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
# ⬇️ BARU: NETWORK RESPONSE CAPTURE -- cari stats video di JSON response
# ============================================================
def find_video_stats_blocks(obj, _depth=0):
    """
    Recursive search di dalam JSON (dict/list apapun bentuknya) buat nemuin
    "blok" yang punya struktur kayak item video TikTok: ada field id-ish
    (id / itemId / awemeId) DAN ada sub-object "stats" yang isinya
    diggCount/shareCount/dst.

    Return: list of (video_id, stats_dict, desc_or_None)

    Kenapa recursive & fleksibel kayak gini (bukan langsung akses path
    JSON yang fixed): karena TikTok bisa naro item video ini di berbagai
    posisi struktur JSON tergantung endpoint-nya (kadang di "itemInfo.
    itemStruct", kadang di list "itemList", dll). Dengan nyari pola
    "punya stats dengan diggCount/shareCount" di manapun posisinya, kita
    gak perlu hardcode path yang gampang berubah.
    """
    results = []
    if _depth > 12:  # safety limit, jangan sampai infinite recursion
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
    """
    Return sebuah async function yang bisa dipasang ke page.on("response", ...).
    Tiap response JSON dari tiktok.com yang lewat, dicek -- kalau ketemu
    blok stats video, disimpan ke video_stats_cache (dict video_id -> dict).
    """
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
    """
    Polling video_stats_cache nunggu video_id ini keisi (dari network
    listener yang jalan di background). Return dict stats kalau ketemu
    dalam batas waktu, None kalau timeout.
    """
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
# HELPER: parse video_id dari URL
# ============================================================
def parse_video_id_from_url(url: str):
    """
    Ambil video_id dari URL TikTok. TikTok punya 2 tipe post yang formatnya
    mirip banget:
      - Video biasa : https://www.tiktok.com/@user/video/7123456789012345678
      - Photo/slide  : https://www.tiktok.com/@user/photo/7123456789012345678
    Dua-duanya PUNYA id numerik yang sama polanya, cuma segment path-nya
    beda ("video" vs "photo"). Sebelumnya cuma "/video/" yang dikenalin,
    jadi kalau arrow-key nyasar ke post foto, id-nya gagal keparse dan
    chain berhenti padahal itemnya valid -- cuma beda tipe konten.
    """
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
    """
    Return "photo" kalau URL-nya post foto/slide, "video" kalau video
    biasa, None kalau gak keduanya (misal lagi di halaman profil).
    Dipakai buat nentuin kolom is_photo di CSV & buat bikin video_url
    yang benar (path /photo/ vs /video/).
    """
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


# ============================================================
# TIKTOK SHOP / VIDEO UNPLAYABLE CHECK
# ============================================================
async def is_video_unplayable(page):
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
# GRID: cari video-video yang keliatan di profile grid
# ============================================================
async def get_grid_video_ids(page):
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


# ============================================================
# MODAL: extract metadata dari video yang lagi kebuka
# ⬇️ VERSI BARU: prioritas ambil dari network cache, fallback DOM
# ============================================================
async def extract_video_metadata_current(page, username, video_id, video_stats_cache):
    """
    video_id: video_id yang LAGI DIBUKA (dari page.url, dikasih caller).
    video_stats_cache: dict shared, diisi otomatis oleh network listener
                        yang jalan di background (lihat make_network_response_listener).

    Prioritas 1: cek video_stats_cache -- kalau ada, pakai ini (dari JSON
    API asli, angka mentah & akurat termasuk share_count).
    Prioritas 2 (fallback): kalau network response gak nangkep data ini
    dalam waktu wajar, balik ke cara lama -- scrape teks dari DOM (biar
    tetap ada hasil walau mungkin share_count kosong).
    """
    # --- PRIORITAS 1: network cache ---
    stats_from_network = await wait_for_network_stats(video_stats_cache, video_id)
    if stats_from_network:
        print(f"      📡 Metadata didapat dari network response (JSON API)")
        # Description dari network kadang kosong (endpoint tertentu gak
        # nyertain desc) -- kalau kosong, coba ambil dari DOM sebagai pelengkap.
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

    # --- PRIORITAS 2: fallback DOM scraping (cara lama) ---
    print(f"      ⚠️ Network response gak nangkep data video ini dalam "
          f"{NETWORK_STATS_WAIT_TIMEOUT:.0f}s -- fallback ke DOM scraping.")

    try:
        await page.wait_for_selector('[data-e2e="like-count"]', timeout=METADATA_READY_TIMEOUT)
    except Exception:
        print(f"      ⚠️ Elemen metadata (like-count) juga gak muncul -- "
              "video kemungkinan invalid. Skip, bakal ke-retry di run berikutnya.")
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
        print(f"      ❌ Error extract metadata (fallback DOM): {e}")
        return None


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


# ============================================================
# CORE: scrape semua video 1 influencer, human-like
# ⬇️ diupdate: passing video_stats_cache ke extract_video_metadata_current
# ============================================================
async def scrape_videos_human_like(page, username, target_video_ids, scraped_videos,
                                    csv_writer_callback, video_stats_cache):
    profile_url = f"https://www.tiktok.com/@{username}"
    videos_scraped_count = 0
    clicked_ids_this_session = set()

    no_new_scroll_streak = 0

    while True:
        grid_videos = await get_grid_video_ids(page)

        pending = [
            (vid, elem) for (vid, elem) in grid_videos
            if vid in target_video_ids
            and (username, vid) not in scraped_videos
            and vid not in clicked_ids_this_session
        ]

        if not pending:
            before_count = len(grid_videos)
            await scroll_grid_step(page)
            after_videos = await get_grid_video_ids(page)
            after_count = len(after_videos)

            if after_count <= before_count:
                no_new_scroll_streak += 1
            else:
                no_new_scroll_streak = 0

            if no_new_scroll_streak >= MAX_SCROLL_ATTEMPTS_NO_NEW:
                print(f"   🔚 Grid @{username} udah mentok (gak ada video baru muncul), stop scroll.")
                break

            all_target_seen = target_video_ids.issubset(
                {vid for (uname, vid) in scraped_videos if uname == username}
            )
            if all_target_seen:
                print(f"   ✅ Semua video target @{username} sudah ke-cover.")
                break

            continue

        vid, elem = pending[0]
        print(f"   👆 Klik video {vid[:10]}... dari grid")
        try:
            await elem.click(timeout=15000)
        except Exception as e:
            print(f"      ⚠️ Gagal klik elemen grid: {str(e)[:150]}")
            print("      🔍 Cek kemungkinan ada captcha yang nutupin elemen...")
            await wait_for_login_wall_clear(page, timeout=600, context_label=f"@{username} (klik grid)")
            try:
                await elem.scroll_into_view_if_needed()
                await asyncio.sleep(random.uniform(0.5, 1))
                await elem.click(timeout=15000)
            except Exception as e2:
                print(f"      ❌ Tetap gagal klik setelah retry: {str(e2)[:150]}, skip video ini.")
                clicked_ids_this_session.add(vid)
                continue

        opened = await wait_url_change(page, profile_url, timeout_ms=8000)
        if not opened:
            print("      ⚠️ Modal gak kebuka (url gak berubah), skip.")
            clicked_ids_this_session.add(vid)
            continue

        await asyncio.sleep(random.uniform(2, 3))

        steps = 0
        while steps < MAX_ARROW_STEPS_PER_CHAIN:
            steps += 1
            current_url = page.url
            current_vid = parse_video_id_from_url(current_url)
            current_uname = parse_username_from_url(current_url)
            # ⬇️ BARU: deteksi tipe konten (video biasa vs photo/slide).
            # current_vid udah handle 2 tipe URL ini di parse_video_id_from_url,
            # tapi kita perlu tau tipe-nya juga buat nulis kolom is_photo &
            # bikin video_url yang path-nya benar.
            current_content_type = get_content_type_from_url(current_url)

            if current_uname and current_uname != username:
                print(f"      🔀 Arrow key nyasar ke akun lain (@{current_uname}), balik ke profil @{username}.")
                break

            if not current_vid:
                print("      ⚠️ Gak bisa parse video_id dari URL (bukan /video/ maupun /photo/), stop chain ini.")
                break

            clicked_ids_this_session.add(current_vid)

            if await is_video_unplayable(page):
                print(f"      🛍️  Video {current_vid[:10]} adalah TikTok Shop video "
                      f"(cuma bisa diliat di app TikTok) -- skip, BUKAN captcha.")
            else:
                await wait_for_login_wall_clear(
                    page, timeout=600, context_label=f"@{username} (video {current_vid[:10]})"
                )

                if (username, current_vid) in scraped_videos:
                    print(f"      ⏭️  Video {current_vid[:10]} sudah ada di CSV, skip extract, lanjut arrow.")
                elif current_vid not in target_video_ids:
                    print(f"      ⏭️  Video {current_vid[:10]} di luar rentang tanggal target, skip extract.")
                else:
                    meta = await extract_video_metadata_current(
                        page, username, current_vid, video_stats_cache
                    )
                    if meta:
                        is_photo_flag = (current_content_type == "photo")
                        content_path = current_content_type or "video"  # fallback aman
                        row = {
                            "username": username,
                            "video_id": current_vid,
                            "video_url": f"https://www.tiktok.com/@{username}/{content_path}/{current_vid}",
                            "is_photo": str(is_photo_flag),
                            **meta,
                            "scraped_at": datetime.now().isoformat(),
                        }
                        csv_writer_callback([row])
                        print(f"      💾 Tersimpan ke CSV: video {current_vid[:10]}"
                              f"{' (photo)' if is_photo_flag else ''}")
                        scraped_videos.add((username, current_vid))
                        videos_scraped_count += 1
                        print(f"      ✅ Video {current_vid[:10]}: like={meta['like_count']}, "
                              f"comment={meta['comment_count']}, share={meta['share_count']}, "
                              f"save={meta['save_count']}")

                        if videos_scraped_count % DELAY_EVERY_N_VIDEOS == 0:
                            long_delay = random.uniform(*DELAY_AFTER_N_VIDEOS)
                            print(f"\n      🌙 Jeda PANJANG {long_delay / 60:.1f} menit "
                                  f"(anti-captcha, setelah {videos_scraped_count} video total)...")
                            await asyncio.sleep(long_delay)

                        delay = random.uniform(*DELAY_BETWEEN_VIDEO)
                        print(f"      ⏳ Jeda {delay:.0f}s...")
                        await asyncio.sleep(delay)
                    else:
                        print(f"      ⏭️  Video {current_vid[:10]} di-skip (metadata gagal di-load), "
                              f"belum ditandai scraped -- bakal ke-retry di run berikutnya.")

            already_done_ids = {v for (u, v) in scraped_videos if u == username}
            if target_video_ids.issubset(already_done_ids):
                print(f"   ✅ Semua video target @{username} sudah selesai (dalam chain).")
                break

            moved = await navigate_next_video_arrow(page)
            if not moved:
                print("      🔚 Arrow key mentok (gak ada video berikutnya), balik ke profil.")
                break

            await asyncio.sleep(random.uniform(1, 2))

        await close_video_modal(page, profile_url)
        await asyncio.sleep(random.uniform(1, 2))

        already_done_ids = {v for (u, v) in scraped_videos if u == username}
        if target_video_ids.issubset(already_done_ids):
            break

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
        f"🚀 Scraping dimulai (network-capture mode).\n"
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

        # ⬇️ BARU: pasang network listener SEKALI di awal, sebelum loop.
        # video_stats_cache diisi otomatis di background tiap ada response
        # yang lewat -- dipakai nanti sama extract_video_metadata_current().
        video_stats_cache = {}
        page.on("response", make_network_response_listener(video_stats_cache))
        print("📡 Network response listener aktif (buat nangkep stats video, termasuk share_count)")

        try:
            for idx, username in enumerate(to_process, start=1):
                print(f"\n{'=' * 70}")
                print(f"[{idx}/{len(to_process)}] @{username}")
                print(f"{'=' * 70}")

                target_video_ids = {v["video_id"] for v in videos_by_user[username]}

                if username in scraped_profil:
                    print(f"   ⏭️  Profil @{username} udah pernah discrape, skip.")
                else:
                    profil_row = await scrape_profil_metadata(page, username)
                    if profil_row:
                        append_profil_row(profil_row)
                        scraped_profil.add(username)

                already_done_ids = {vid for (uname, vid) in scraped_videos if uname == username}
                if target_video_ids.issubset(already_done_ids):
                    print(f"   ⏭️  Semua video @{username} udah ada di CSV, skip video scraping.")
                else:
                    if page.url != f"https://www.tiktok.com/@{username}":
                        try:
                            await page.goto(f"https://www.tiktok.com/@{username}",
                                             wait_until="load", timeout=60000)
                            await asyncio.sleep(random.uniform(2, 4))
                            await wait_for_login_wall_clear(page, timeout=600, context_label=f"@{username} (buka profil)")
                        except Exception as e:
                            print(f"   ❌ Gagal buka profil buat video scraping: {e}")
                            continue

                    n_new = await scrape_videos_human_like(
                        page, username, target_video_ids, scraped_videos,
                        append_video_rows, video_stats_cache,
                    )
                    print(f"   📊 Total video baru di-scrape untuk @{username}: {n_new}")

                # ⬇️ Bersihin cache tiap ganti influencer, biar gak numpuk
                # terus di memory selama scraping ratusan/ribuan video.
                video_stats_cache.clear()

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