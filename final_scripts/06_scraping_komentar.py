"""
06 - Scraping Komentar + Views
=================================
Tahap 3.1.2.3 lanjutan & 3.1.2.5 (data performa: views) pada proposal TA.
Mengambil sample video per influencer (stratified by bulan+engagement),
scrape komentarnya, lalu -- SEKALIAN selagi udah ada datanya -- scrape
jumlah views tiap video. Digabung jadi SATU file biar gak scraping TikTok
dua kali buat 2 kebutuhan berbeda.

Alur:
1. generate_scrape_list()
   Join health_videos.csv + metadata_video_konten.csv, stratified sampling
   per influencer (50% HIGH engagement / 50% MEDIUM, disebar per bulan),
   cap 20 video/influencer -> to_scrape.csv
2. scrape_comments(to_scrape.csv)
   2 akun TikTok paralel, tiap video dibuka (page.goto), komentar diambil
   dari network response JSON + reply dari DOM. Resume lewat kolom
   scraped=True/False di to_scrape.csv itu sendiri.
3. generate_replacement_list()
   Video yang ternyata 0 komentar valid (post dihapus/private/dll) diganti
   dengan video kesehatan lain dari influencer yang sama, jaga stratifikasi
   tier & bulan -> to_scrape_pengganti.csv
4. scrape_comments(to_scrape_pengganti.csv)  -- scrape ronde kedua
5. scrape_video_views()
   Dari SEMUA video_id unik yang akhirnya ada di comments.csv: buka profil
   tiap influencer SEKALI, scroll grid, baca views langsung dari kartu
   video (data-e2e="video-views") -- BUKAN buka tiap video satu-satu lagi.

Konsolidasi dari:
- 01_pengumpulan_data/05_scrap_komentar/01_00_scraping_list.py -> generate_scrape_list()
- 01_pengumpulan_data/05_scrap_komentar/01_03_scrap_2akun.py   -> scrape_comments()
- 01_pengumpulan_data/05_scrap_komentar/04_tambahan_scrap.py   -> generate_replacement_list()
- 01_pengumpulan_data/08_scrape_viewers_tambahan/scrape_views.ipynb -> scrape_video_views()

SENGAJA TIDAK diikutkan:
- 01_01_scraping.py, 01_02_scraping.py -- generasi awal scraper komentar,
  digantikan 01_03 ("FINAL VERSION", 2-worker + proper resume tracking).
- 02_cek_struktur.py, 03_test_code.py -- diagnostic/catatan patch manual,
  bukan bagian pipeline (isi patch-nya soal SPA-routing klik-grid yang
  gak relevan lagi karena versi final selalu page.goto() langsung).

CATATAN PENTING soal penggabungan views: awalnya scraping views dibikin
terpisah (folder 08_scrape_viewers_tambahan) karena baru kepikiran belakangan.
Di sini digabung jadi 1 file (bukan 1 tahap terpisah) sesuai permintaan --
TAPI tetap pakai teknik scroll-grid (bukan buka ulang tiap video), karena
video sudah selesai di-scrape komentarnya lewat page.goto() individual
(mahal buat diulang), sedangkan scroll grid 1x per profil jauh lebih murah
untuk ambil views banyak video sekaligus.

CATATAN EKSEKUSI: scrape_comments() & scrape_video_views() scraping berat
(Playwright, 2 akun paralel). SKIP otomatis kalau target sudah 100% selesai.
Prasyarat: 2 akun TikTok dengan cookies terpisah di
01_pengumpulan_data/00_akun_playwright/final_cookie/
(tiktok_cookies_akun1.json, tiktok_cookies_akun2.json).
"""

import asyncio
import csv
import os
import random
import re
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

import pandas as pd
import pygame
import requests
from playwright.async_api import async_playwright

# -------------------------------------------------------------------------
# KONFIGURASI PATH
# -------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "01_pengumpulan_data"
FINAL_OUTPUT_ROOT = Path(__file__).resolve().parent / "output"
OUTPUT_DIR = FINAL_OUTPUT_ROOT / "06_scraping_komentar"
DETAIL_DIR = OUTPUT_DIR / "detail_tambahan"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
DETAIL_DIR.mkdir(parents=True, exist_ok=True)

HEALTH_VIDEOS = FINAL_OUTPUT_ROOT / "05_klasifikasi_konten_kesehatan" / "health_videos.csv"
METADATA_VIDEO = FINAL_OUTPUT_ROOT / "04_scraping_metadata_konten" / "metadata_video_konten.csv"

TO_SCRAPE = OUTPUT_DIR / "to_scrape.csv"
TO_SCRAPE_PENGGANTI = OUTPUT_DIR / "to_scrape_pengganti.csv"
COMMENTS_CSV = OUTPUT_DIR / "comments.csv"
COMMENTS_STATS_CSV = OUTPUT_DIR / "comments_stats.csv"
VIDEO_VIEWS_CSV = OUTPUT_DIR / "video_views.csv"
VIDEO_TIDAK_ADA_CSV = OUTPUT_DIR / "video_tidak_ada.csv"
VIDEO_GAGAL_CSV = OUTPUT_DIR / "video_gagal_cek_manual.csv"

# 2 akun TikTok (dipakai paralel) -- tetap di lokasi lama (kredensial, bukan data)
FINAL_COOKIE_DIR = DATA_DIR / "00_akun_playwright" / "final_cookie"
PROFILE_DIR_1 = DATA_DIR / "00_akun_playwright" / "tiktok_browser_profile_akun1"
PROFILE_DIR_2 = DATA_DIR / "00_akun_playwright" / "tiktok_browser_profile_akun2"
COOKIES_FILE_1 = FINAL_COOKIE_DIR / "tiktok_cookies_akun1.json"
COOKIES_FILE_2 = FINAL_COOKIE_DIR / "tiktok_cookies_akun2.json"
WARNING_MP3 = DATA_DIR / "03_videoid_perakun" / "warning.mp3"

EXCLUDE_USERNAMES = ["drrichardlee"]  # konsisten sama tahap-tahap sebelumnya

MAX_VIDEOS_PER_INFLUENCER = 20
RANDOM_SEED = 42
SCRAPE_LIST_FIELDS = ["username", "video_id", "month", "engagement_tier", "scraped", "scraped_at"]

COMMENT_FIELDS = ["username", "video_id", "comment_id", "commenter_username",
                   "comment_text", "like_count", "reply_count", "timestamp", "scraped_at"]
STATS_FIELDS = ["username", "video_id", "total_comments_loaded", "total_comments_available",
                 "scraped_comments_count", "expected_comment_count", "coverage_pct", "scraped_at"]
VIEWS_FIELDS = ["username", "video_id", "video_url", "views", "scraped_at"]
TIDAK_ADA_FIELDS = ["username", "video_id", "video_url", "alasan", "checked_at"]

DELAY_BETWEEN_VIDEO = (10, 30)
DELAY_BETWEEN_SCROLL_COMMENT = (2, 4)
DELAY_EVERY_N_VIDEOS = 25
DELAY_AFTER_N_VIDEOS = (20, 60)
MAX_COMMENTS_PER_VIDEO = 100
MIN_COMMENTS_TARGET = 50
MAX_SCROLL_ATTEMPTS_COMMENT = 40

DELAY_BETWEEN_SCROLL_VIEWS = (1.5, 3.0)
DELAY_WHEN_NO_NEW_VIEWS = (3.0, 6.0)
DELAY_BETWEEN_INFLUENCER_VIEWS = (20, 60)
NO_NEW_CARD_LIMIT = 20
MAX_SCROLL_PER_USER_VIEWS = 400

REALISTIC_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)

FILE_LOCK = asyncio.Lock()


# -------------------------------------------------------------------------
# TELEGRAM & AUDIO ALERT (sama dengan tahap 04)
# -------------------------------------------------------------------------
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))
try:
    from credentials import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
    TELEGRAM_ENABLED = bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)
except ImportError:
    TELEGRAM_BOT_TOKEN = TELEGRAM_CHAT_ID = None
    TELEGRAM_ENABLED = False


def _send_telegram_alert(message: str):
    if not TELEGRAM_ENABLED:
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": message}, timeout=10)
    except Exception:
        pass


class CaptchaTimeoutError(Exception):
    pass


def _play_warning():
    if not WARNING_MP3.exists():
        return
    try:
        pygame.mixer.init()
        pygame.mixer.music.load(str(WARNING_MP3))
        pygame.mixer.music.play()
    except Exception:
        pass


def _stop_warning():
    try:
        if pygame.mixer.get_init():
            pygame.mixer.music.stop()
            pygame.mixer.quit()
    except Exception:
        pass


async def _wait_for_login_wall_clear(page, timeout=600, context_label="", worker_label="") -> bool:
    blocker_selectors = [
        'text="Log in to TikTok"', '[data-e2e="login-modal"]',
        'div[role="dialog"]:has-text("Log in")', 'div[role="dialog"]:has-text("Continue with")',
        'text="Drag the slider to fit the puzzle"', '[id*="captcha"]', '[class*="captcha"]',
        'iframe[src*="captcha"]', 'img[alt="Captcha" i]', 'img[alt*="captcha" i]',
        'div[class^="cap-"]', 'div[class*=" cap-"]',
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
                    _send_telegram_alert(f"[{worker_label}] Captcha solved!\n{context_label}")
                await asyncio.sleep(random.uniform(2, 4))
            return True

        now = asyncio.get_event_loop().time()
        remaining = int(timeout - (now - start))
        if not was_found:
            print(f"\n   [{worker_label}] CAPTCHA TERDETEKSI, timeout {remaining}s")
            _play_warning()
        if last_alert_at is None or (now - last_alert_at) >= ALERT_REPEAT_EVERY:
            _send_telegram_alert(f"[{worker_label}] CAPTCHA DETECTED!\n{context_label}\nSisa: {remaining}s")
            alert_sent, last_alert_at = True, now
        was_found = True
        await asyncio.sleep(3)

    _stop_warning()
    _send_telegram_alert(f"[{worker_label}] Captcha timeout!\n{context_label}")
    raise CaptchaTimeoutError(f"[{worker_label}] Captcha/login wall gak di-solve dalam batas waktu.")


def _load_cookies_from_json(cookies_path: Path, label=""):
    if not cookies_path.exists():
        print(f"   [{label}] Cookies tidak ditemukan: {cookies_path}")
        return None
    try:
        import json
        with open(cookies_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"   [{label}] Gagal load cookies: {e}")
        return None


# -------------------------------------------------------------------------
# STEP 1 - Generate daftar target scraping (stratified sampling)
# -------------------------------------------------------------------------
def _parse_date_to_month(date_str: str):
    if not date_str:
        return None
    date_str = str(date_str).strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S.%f",
                "%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(date_str, fmt).month
        except ValueError:
            continue
    return None


def _parse_count_string(count_str) -> int:
    if not count_str:
        return 0
    count_str = str(count_str).strip().upper().replace(",", "")
    try:
        if "K" in count_str:
            return int(float(count_str.replace("K", "")) * 1_000)
        if "M" in count_str:
            return int(float(count_str.replace("M", "")) * 1_000_000)
        if "B" in count_str:
            return int(float(count_str.replace("B", "")) * 1_000_000_000)
        return int(float(count_str))
    except Exception:
        return 0


def generate_scrape_list(output_csv: Path = TO_SCRAPE) -> None:
    """Stratified sampling (per influencer, per bulan, 50% HIGH/50% MEDIUM engagement), cap 20/influencer."""
    if output_csv.exists():
        print(f"[Sampling] Sudah ada -> {output_csv} (skip)")
        return

    if not HEALTH_VIDEOS.exists() or not METADATA_VIDEO.exists():
        raise FileNotFoundError("health_videos.csv atau metadata_video_konten.csv tidak ditemukan.")

    metadata = {}
    with open(METADATA_VIDEO, "r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            if row.get("video_id"):
                metadata[row["video_id"]] = row

    with open(HEALTH_VIDEOS, "r", encoding="utf-8", newline="") as f:
        health_videos = list(csv.DictReader(f))

    all_videos = []
    for hv in health_videos:
        meta = metadata.get(hv.get("video_id"))
        if not meta:
            continue
        month = _parse_date_to_month(meta.get("scraped_at", ""))
        if not month:
            continue
        like_count = _parse_count_string(meta.get("like_count", 0))
        comment_count = _parse_count_string(meta.get("comment_count", 0))
        engagement_rate = comment_count / like_count if like_count else comment_count
        all_videos.append({
            "username": hv.get("username"), "video_id": hv.get("video_id"),
            "month": month, "engagement_rate": engagement_rate,
        })

    print(f"[Sampling] {len(all_videos)} video ter-join dari {len(health_videos)} health videos")

    groups = defaultdict(lambda: defaultdict(list))
    for video in all_videos:
        groups[video["username"]][video["month"]].append(video)

    random.seed(RANDOM_SEED)
    to_scrape, influencer_count = [], defaultdict(int)

    for username, months_videos in groups.items():
        for month, videos in months_videos.items():
            if influencer_count[username] >= MAX_VIDEOS_PER_INFLUENCER:
                break
            videos.sort(key=lambda v: v["engagement_rate"], reverse=True)
            split_idx = len(videos) // 2
            high_engagement, medium_engagement = videos[:split_idx], videos[split_idx:]

            remaining = MAX_VIDEOS_PER_INFLUENCER - influencer_count[username]
            n_high = min(len(high_engagement), remaining // 2 + 1)
            n_medium = min(len(medium_engagement), remaining // 2)
            sampled_high = random.sample(high_engagement, n_high) if n_high else []
            sampled_medium = random.sample(medium_engagement, n_medium) if n_medium else []

            for video in sampled_high + sampled_medium:
                if influencer_count[username] >= MAX_VIDEOS_PER_INFLUENCER:
                    break
                to_scrape.append({
                    "username": username, "video_id": video["video_id"], "month": month,
                    "engagement_tier": "HIGH" if video in sampled_high else "MEDIUM",
                    "scraped": "False", "scraped_at": "",
                })
                influencer_count[username] += 1

    random.shuffle(to_scrape)
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    with open(output_csv, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=SCRAPE_LIST_FIELDS)
        writer.writeheader()
        writer.writerows(to_scrape)

    print(f"[Sampling] {len(to_scrape)} video target dari {len(influencer_count)} influencer -> {output_csv}")


# -------------------------------------------------------------------------
# STEP 2 - Scrape komentar (2 akun paralel)
# -------------------------------------------------------------------------
def _find_comments_blocks(obj, _depth=0):
    results = []
    if _depth > 15:
        return results
    if isinstance(obj, dict):
        comments_list = (obj.get("comments") or obj.get("Items") or obj.get("items")
                          or obj.get("reply_comment") or obj.get("replies"))
        if isinstance(comments_list, list):
            for comment in comments_list:
                try:
                    comment_id = comment.get("id") or comment.get("cid")
                    text = comment.get("text") or ""
                    user_data = comment.get("user") or {}
                    username = user_data.get("uniqueId") or user_data.get("id") or ""
                    stats = comment.get("stats") or {}
                    if comment_id and text:
                        results.append({
                            "comment_id": str(comment_id), "username": str(username), "text": str(text),
                            "likes": str(stats.get("diggCount") or 0), "replies": str(stats.get("replyCount") or 0),
                            "create_time": str(comment.get("createTime") or ""),
                        })
                except Exception:
                    continue
        for value in obj.values():
            results.extend(_find_comments_blocks(value, _depth + 1))
    elif isinstance(obj, list):
        for item in obj:
            results.extend(_find_comments_blocks(item, _depth + 1))
    return results


def _make_comments_response_listener(comments_cache):
    async def on_response(response):
        try:
            if "tiktok.com" not in response.url or "json" not in response.headers.get("content-type", ""):
                return
            body = await response.json()
        except Exception:
            return
        try:
            comments_cache.extend(_find_comments_blocks(body))
        except Exception:
            pass
    return on_response


def _count_unique_comments(comments_cache) -> int:
    return len({c["comment_id"] for c in comments_cache if c.get("comment_id")})


def _parse_views_count(text: str) -> int:
    return _parse_count_string(text)


async def _get_expected_comment_count(page) -> int:
    for sel in ['[data-e2e="comment-count"]', 'strong[data-e2e="comment-count"]']:
        try:
            elem = await page.query_selector(sel)
            if elem:
                count = _parse_views_count(await elem.text_content())
                if count > 0:
                    return count
        except Exception:
            continue
    return 0


def _is_view_replies_button_text(text: str) -> bool:
    if not text:
        return False
    t = text.strip().lower()
    if t in ("reply", "balas", "membalas", "respond") or "hide" in t or "sembunyikan" in t:
        return False
    return bool(re.search(r'\d+\s*(repl|balasan)', t)) or ("view" in t and "repl" in t) or ("lihat" in t and "balasan" in t)


async def _expand_visible_reply_buttons(page, max_clicks=3) -> int:
    clicked = 0
    for pattern in ['[class*="TUXButton-label"]', 'p[class*="TUXButton-label"]',
                     'span[class*="TUXButton-label"]', 'div[class*="TUXButton-label"]']:
        if clicked >= max_clicks:
            break
        try:
            elems = await page.query_selector_all(pattern)
        except Exception:
            continue
        for elem in elems:
            if clicked >= max_clicks:
                break
            try:
                if not await elem.is_visible():
                    continue
                text = (await elem.text_content() or "").strip()
                if not text or not _is_view_replies_button_text(text):
                    continue
                await elem.scroll_into_view_if_needed(timeout=2000)
                await elem.click(timeout=3000)
                clicked += 1
                await asyncio.sleep(random.uniform(0.5, 1))
            except Exception:
                continue
    return clicked


async def _extract_replies_from_dom(page) -> list:
    try:
        await asyncio.sleep(0.5)
        all_replies = []
        for selector in ['[data-e2e="comment-item"][class*="reply"]', 'div[class*="ReplyItem"]', '[class*="CommentReply"]']:
            try:
                for reply_elem in await page.query_selector_all(selector):
                    try:
                        text_elem = await reply_elem.query_selector('[data-e2e="comment-content"]') or await reply_elem.query_selector('[class*="content"]')
                        if not text_elem:
                            continue
                        reply_text = await text_elem.text_content()
                        if not reply_text or len(reply_text) <= 3:
                            continue
                        username_elem = await reply_elem.query_selector('[data-e2e="comment-username"]')
                        likes_elem = await reply_elem.query_selector('[data-e2e="comment-like-count"]')
                        all_replies.append({
                            "text": reply_text.strip(),
                            "username": (await username_elem.text_content() or "unknown").strip() if username_elem else "unknown",
                            "likes": (await likes_elem.text_content() or "0").strip() if likes_elem else "0",
                        })
                    except Exception:
                        continue
            except Exception:
                continue
        return all_replies
    except Exception:
        return []


async def _find_actual_scroll_target(container):
    try:
        handle = await container.evaluate_handle("""
            (el) => {
                function isScrollable(node) {
                    if (!(node instanceof HTMLElement)) return false;
                    const style = window.getComputedStyle(node);
                    return (style.overflowY === 'auto' || style.overflowY === 'scroll')
                           && node.scrollHeight > node.clientHeight + 10;
                }
                let ancestor = el.parentElement, depth = 0;
                while (ancestor && depth < 8) {
                    if (isScrollable(ancestor)) return ancestor;
                    ancestor = ancestor.parentElement; depth++;
                }
                function findScrollable(node, d) {
                    if (!node || d > 6) return null;
                    if (isScrollable(node)) return node;
                    for (const child of node.children) {
                        const found = findScrollable(child, d + 1);
                        if (found) return found;
                    }
                    return null;
                }
                return findScrollable(el, 0) || (isScrollable(el) ? el : el);
            }
        """)
        elem = handle.as_element()
        return elem if elem else container
    except Exception:
        return container


async def _wheel_scroll_element(page, elem, delta_y=650) -> bool:
    try:
        box = await elem.bounding_box()
        if not box or box["width"] <= 0 or box["height"] <= 0:
            return False
        x = box["x"] + box["width"] / 2 + random.uniform(-20, 20)
        y = box["y"] + box["height"] / 2 + random.uniform(-20, 20)
        await page.mouse.move(x, y)
        await asyncio.sleep(random.uniform(0.1, 0.3))
        await page.mouse.wheel(0, delta_y)
        return True
    except Exception:
        return False


CAPTCHA_QUICK_SELECTORS = ['img[alt="Captcha" i]', 'img[alt*="captcha" i]', 'text="Drag the slider to fit the puzzle"',
                            '[id*="captcha"]', '[class*="captcha"]', 'div[class^="cap-"]', 'iframe[src*="captcha"]']


async def _quick_captcha_check(page):
    for sel in CAPTCHA_QUICK_SELECTORS:
        try:
            elem = await page.query_selector(sel)
            if elem and await elem.is_visible():
                return True
        except Exception:
            continue
    return False


async def _pause_for_manual_captcha_solve(page, timeout=600, context_label="", worker_label=""):
    print(f"\n   [{worker_label}] CAPTCHA DI TENGAH PROSES!")
    _play_warning()
    _send_telegram_alert(f"[{worker_label}] CAPTCHA (mid-process)!\n{context_label}")
    start = asyncio.get_event_loop().time()
    while asyncio.get_event_loop().time() - start < timeout:
        if not await _quick_captcha_check(page):
            _stop_warning()
            await asyncio.sleep(random.uniform(2, 4))
            return True
        await asyncio.sleep(3)
    _stop_warning()
    raise CaptchaTimeoutError(f"[{worker_label}] Captcha timeout (mid-process)")


async def _scrape_comments_from_video(page, username, video_id, comments_cache, worker_label=""):
    video_url = f"https://www.tiktok.com/@{username}/video/{video_id}"
    try:
        await page.goto(video_url, wait_until="load", timeout=30000)
        await asyncio.sleep(random.uniform(2, 3))
    except Exception:
        return None

    comments_cache.clear()
    try:
        await _wait_for_login_wall_clear(page, timeout=600, context_label=f"Video {video_id}", worker_label=worker_label)
    except Exception:
        return None

    await asyncio.sleep(random.uniform(2, 3))
    for selector in ['[data-e2e="comment-icon"]', '[aria-label="Comments"]', 'button[aria-label*="comment" i]', 'button:has-text("Comments")']:
        try:
            elem = await page.query_selector(selector)
            if elem and await elem.is_visible():
                await elem.click(timeout=5000)
                await asyncio.sleep(random.uniform(1, 2))
                break
        except Exception:
            continue

    await asyncio.sleep(random.uniform(1, 2))
    expected_count = await _get_expected_comment_count(page)
    target_count = min(MAX_COMMENTS_PER_VIDEO, max(MIN_COMMENTS_TARGET, int(expected_count * 0.5))) if expected_count > 0 else MIN_COMMENTS_TARGET

    comment_container = None
    for sel in ['[data-e2e="comment-list"]', '[data-testid="comment-list-container"]', '[class*="CommentList"]']:
        try:
            container = await page.query_selector(sel)
            if container and await container.is_visible():
                comment_container = container
                break
        except Exception:
            continue

    if not comment_container:
        return {"total_comments_loaded": 0, "total_comments_available": 0, "comments": [], "replies": [], "expected_comment_count": expected_count}

    scroll_target = await _find_actual_scroll_target(comment_container)
    no_new_streak = prev_count = 0

    for scroll_attempt in range(MAX_SCROLL_ATTEMPTS_COMMENT):
        if not await _wheel_scroll_element(page, scroll_target, delta_y=random.randint(500, 800)):
            await _wheel_scroll_element(page, comment_container, delta_y=random.randint(500, 800))
        await asyncio.sleep(random.uniform(*DELAY_BETWEEN_SCROLL_COMMENT))

        if scroll_attempt % 3 == 0 and await _quick_captcha_check(page):
            try:
                await _pause_for_manual_captcha_solve(page, context_label=f"Video {video_id[:10]}", worker_label=worker_label)
            except Exception:
                break
            no_new_streak = 0

        if scroll_attempt % 6 == 0:
            if await _expand_visible_reply_buttons(page) > 0:
                await asyncio.sleep(random.uniform(1, 1.5))

        current_count = _count_unique_comments(comments_cache)
        no_new_streak = 0 if current_count > prev_count else no_new_streak + 1
        prev_count = current_count

        if current_count >= target_count or current_count >= MAX_COMMENTS_PER_VIDEO:
            break
        if no_new_streak >= 12 and scroll_attempt >= 8:
            break

    comments_loaded = _count_unique_comments(comments_cache)
    unique_comments = {}
    for comment in comments_cache:
        cid = comment.get("comment_id")
        if cid and cid not in unique_comments:
            unique_comments[cid] = comment
    final_comments = list(unique_comments.values())[:MAX_COMMENTS_PER_VIDEO]

    dom_replies = []
    try:
        dom_replies = await _extract_replies_from_dom(page)
    except Exception:
        pass

    return {"total_comments_loaded": len(final_comments), "total_comments_available": comments_loaded,
            "comments": final_comments, "replies": dom_replies, "expected_comment_count": expected_count}


async def _append_comment_rows_locked(rows):
    if not rows:
        return
    async with FILE_LOCK:
        file_exists = COMMENTS_CSV.exists()
        with open(COMMENTS_CSV, "a", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=COMMENT_FIELDS)
            if not file_exists:
                writer.writeheader()
            writer.writerows(rows)
            f.flush()
            os.fsync(f.fileno())


async def _write_stats_row_locked(row):
    async with FILE_LOCK:
        file_exists = COMMENTS_STATS_CSV.exists()
        with open(COMMENTS_STATS_CSV, "a", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=STATS_FIELDS)
            if not file_exists:
                writer.writeheader()
            writer.writerow(row)


async def _update_to_scrape_csv_locked(to_scrape_csv: Path, username, video_id, now_timestamp):
    async with FILE_LOCK:
        with open(to_scrape_csv, "r", encoding="utf-8", newline="") as f:
            rows = list(csv.DictReader(f))
        for row in rows:
            if row.get("username") == username and row.get("video_id") == video_id:
                row["scraped"], row["scraped_at"] = "True", now_timestamp
                break
        with open(to_scrape_csv, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=rows[0].keys())
            writer.writeheader()
            writer.writerows(rows)


async def _process_video_comments(page, to_scrape_csv, username, video_id, comments_cache, worker_label=""):
    result = await _scrape_comments_from_video(page, username, video_id, comments_cache, worker_label=worker_label)
    if not result or not result["comments"]:
        return 0

    rows = [{
        "username": username, "video_id": video_id, "comment_id": c.get("comment_id", ""),
        "commenter_username": c.get("username", ""), "comment_text": c.get("text", ""),
        "like_count": c.get("likes", "0"), "reply_count": c.get("replies", "0"),
        "timestamp": c.get("create_time", ""), "scraped_at": datetime.now().isoformat(),
    } for c in result["comments"][:MAX_COMMENTS_PER_VIDEO]]
    if rows:
        await _append_comment_rows_locked(rows)

    reply_rows = [{
        "username": username, "video_id": video_id, "comment_id": f"reply_{video_id}_{i}",
        "commenter_username": r.get("username", ""), "comment_text": r.get("text", ""),
        "like_count": r.get("likes", "0"), "reply_count": "0", "timestamp": "",
        "scraped_at": datetime.now().isoformat(),
    } for i, r in enumerate(result.get("replies", []))]
    if reply_rows:
        await _append_comment_rows_locked(reply_rows)

    expected = result.get("expected_comment_count", 0)
    total_scraped = len(rows) + len(reply_rows)
    await _write_stats_row_locked({
        "username": username, "video_id": video_id,
        "total_comments_loaded": result["total_comments_loaded"],
        "total_comments_available": result["total_comments_available"],
        "scraped_comments_count": total_scraped, "expected_comment_count": expected,
        "coverage_pct": round(total_scraped / expected * 100, 1) if expected > 0 else "",
        "scraped_at": datetime.now().isoformat(),
    })
    await _update_to_scrape_csv_locked(to_scrape_csv, username, video_id, datetime.now().isoformat())
    return total_scraped


async def _run_comment_worker(p, worker_label, profile_dir, cookies_path, to_scrape_csv, video_list):
    cookies = _load_cookies_from_json(cookies_path, label=worker_label)
    if not cookies:
        return 0

    profile_dir.mkdir(parents=True, exist_ok=True)
    context = await p.chromium.launch_persistent_context(
        str(profile_dir), headless=False, viewport={"width": 1400, "height": 900},
        user_agent=REALISTIC_UA, args=["--disable-blink-features=AutomationControlled"],
    )
    await context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")
    await context.add_cookies(cookies)
    page = context.pages[0] if context.pages else await context.new_page()

    try:
        await page.goto("https://www.tiktok.com", wait_until="load", timeout=30000)
        await asyncio.sleep(2)
    except Exception:
        pass

    comments_cache = []
    page.on("response", _make_comments_response_listener(comments_cache))
    total_scraped = 0

    try:
        for idx, video_data in enumerate(video_list, start=1):
            print(f"[{worker_label}] [{idx}/{len(video_list)}] @{video_data['username']}")
            try:
                total_scraped += await _process_video_comments(
                    page, to_scrape_csv, video_data["username"], video_data["video_id"], comments_cache, worker_label=worker_label
                )
                if idx % DELAY_EVERY_N_VIDEOS == 0:
                    await asyncio.sleep(random.uniform(*DELAY_AFTER_N_VIDEOS))
                else:
                    await asyncio.sleep(random.uniform(*DELAY_BETWEEN_VIDEO))
            except Exception as e:
                print(f"   [{worker_label}] Error: {e}")
                continue
    finally:
        await context.close()

    return total_scraped


async def scrape_comments(to_scrape_csv: Path = TO_SCRAPE) -> None:
    """Scrape komentar untuk semua baris scraped=False di to_scrape_csv, pakai 2 akun paralel."""
    if not to_scrape_csv.exists():
        raise FileNotFoundError(f"{to_scrape_csv} tidak ada. Jalankan generate_scrape_list() dulu.")

    with open(to_scrape_csv, "r", encoding="utf-8", newline="") as f:
        videos = [row for row in csv.DictReader(f) if row.get("scraped", "False").lower() == "false"]

    if not videos:
        print(f"[Komentar] Semua video di {to_scrape_csv.name} sudah di-scrape, skip.")
        return

    print(f"[Komentar] {len(videos)} video pending di {to_scrape_csv.name}")

    influencer_videos = defaultdict(list)
    for v in videos:
        influencer_videos[v["username"]].append(v)
    influencer_list = list(influencer_videos.keys())
    random.shuffle(influencer_list)
    half = len(influencer_list) // 2
    workers1, workers2 = set(influencer_list[:half]), set(influencer_list[half:])
    videos1 = [v for v in videos if v["username"] in workers1]
    videos2 = [v for v in videos if v["username"] in workers2]

    _send_telegram_alert(f"Scraping komentar mulai. Worker 1: {len(videos1)} | Worker 2: {len(videos2)}")

    async with async_playwright() as p:
        results = await asyncio.gather(
            _run_comment_worker(p, "WORKER-1", PROFILE_DIR_1, COOKIES_FILE_1, to_scrape_csv, videos1),
            _run_comment_worker(p, "WORKER-2", PROFILE_DIR_2, COOKIES_FILE_2, to_scrape_csv, videos2),
            return_exceptions=True,
        )
    total = sum(r for r in results if not isinstance(r, Exception))
    print(f"[Komentar] Selesai. Total baru: {total}")
    _send_telegram_alert(f"Scraping komentar selesai. Total baru: {total}")


# -------------------------------------------------------------------------
# STEP 3 - Generate daftar pengganti (video yang gagal diganti video lain)
# -------------------------------------------------------------------------
def _build_video_pool(health_df, meta_df):
    meta_idx = meta_df.drop_duplicates("video_id").set_index("video_id")
    rows = []
    for _, hv in health_df.drop_duplicates("video_id").iterrows():
        vid = hv["video_id"]
        if vid not in meta_idx.index:
            continue
        m = meta_idx.loc[vid]
        month = _parse_date_to_month(m.get("scraped_at", ""))
        if month is None:
            continue
        like = _parse_count_string(m.get("like_count", 0))
        com = _parse_count_string(m.get("comment_count", 0))
        rows.append({"username": hv["username"], "video_id": vid, "month": month,
                      "rate": com if like == 0 else com / like})
    pool = pd.DataFrame(rows).sort_values(["username", "month", "rate", "video_id"],
                                           ascending=[True, True, False, True]).reset_index(drop=True)
    tiers = []
    for _, g in pool.groupby(["username", "month"], sort=False):
        split = len(g) // 2
        tiers.extend(["HIGH" if i < split else "MEDIUM" for i in range(len(g))])
    pool["tier"] = tiers
    return pool


def _pilih_slot_pengganti(rng, avail, tier_count, month_count):
    tiers_order = sorted(["HIGH", "MEDIUM"], key=lambda t: (tier_count[t], t))
    for t in tiers_order:
        c = avail[avail.tier == t]
        if c.empty:
            continue
        low = min(month_count[m] for m in c.month.unique())
        c = c[c.month.map(lambda m: month_count[m] == low)]
        return rng.choice(sorted(c.video_id.tolist()))
    return None


def generate_replacement_list(target_per_influencer: int = MAX_VIDEOS_PER_INFLUENCER,
                               output_csv: Path = TO_SCRAPE_PENGGANTI) -> None:
    """Ganti video yang 0 komentar valid dengan video kesehatan lain, jaga stratifikasi tier & bulan."""
    if output_csv.exists():
        print(f"[Pengganti] Sudah ada -> {output_csv} (skip)")
        return

    comments = pd.read_csv(COMMENTS_CSV, dtype=str, keep_default_na=False)
    health = pd.read_csv(HEALTH_VIDEOS, dtype=str, keep_default_na=False)
    meta = pd.read_csv(METADATA_VIDEO, dtype=str, keep_default_na=False)
    to_scrape = pd.read_csv(TO_SCRAPE, dtype=str, keep_default_na=False)

    text_col = "comment_text" if "comment_text" in comments.columns else None
    valid_rows = comments[comments[text_col].str.strip() != ""] if text_col else comments
    vids_valid = set(valid_rows["video_id"])

    to_scrape = to_scrape.copy()
    to_scrape["_valid"] = to_scrape["video_id"].isin(vids_valid)
    hilang = to_scrape[~to_scrape._valid]
    hilang[SCRAPE_LIST_FIELDS].to_csv(DETAIL_DIR / "video_hilang.csv", index=False, encoding="utf-8")
    print(f"[Pengganti] Target {len(to_scrape)} | punya komentar {int(to_scrape._valid.sum())} | hilang {len(hilang)}")

    pool = _build_video_pool(health, meta)
    used = set(to_scrape["video_id"])
    cadangan = pool[~pool.video_id.isin(used)]

    rng = random.Random(RANDOM_SEED)
    pool_idx = pool.set_index("video_id")
    out_rows, ringkas = [], []

    for user in sorted(to_scrape.username.unique()):
        t = to_scrape[to_scrape.username == user]
        aktif = t[t._valid]
        kurang = max(0, target_per_influencer - len(aktif))

        tier_count = Counter({"HIGH": 0, "MEDIUM": 0})
        month_count = Counter()
        for _, r in aktif.iterrows():
            tier_count[r.engagement_tier] += 1
            month_count[int(r.month)] += 1
        avail = cadangan[cadangan.username == user]
        for m in pool[pool.username == user].month.unique():
            month_count.setdefault(int(m), 0)

        ditambah = 0
        for _ in range(kurang):
            vid = _pilih_slot_pengganti(rng, avail, tier_count, month_count)
            if vid is None:
                break
            r = pool_idx.loc[vid]
            avail = avail[avail.video_id != vid]
            tier_count[r.tier] += 1
            month_count[int(r.month)] += 1
            ditambah += 1
            out_rows.append({"username": user, "video_id": vid, "month": int(r.month),
                              "engagement_tier": r.tier, "scraped": "False", "scraped_at": ""})

        ringkas.append({"username": user, "target_to_scrape": len(t), "video_ada_komentar": len(aktif),
                         "kurang": kurang, "ditambah": ditambah, "masih_kurang": kurang - ditambah})

    pd.DataFrame(out_rows, columns=SCRAPE_LIST_FIELDS).to_csv(output_csv, index=False, encoding="utf-8")
    pd.DataFrame(ringkas).to_csv(DETAIL_DIR / "ringkasan_influencer.csv", index=False, encoding="utf-8")

    print(f"[Pengganti] {len(out_rows)} video tambahan dipilih -> {output_csv}")


# -------------------------------------------------------------------------
# STEP 4 - Scrape views (grid profil, bukan per-video)
# -------------------------------------------------------------------------
def _load_video_id_set(path: Path) -> set:
    if not path.exists():
        return set()
    with open(path, "r", encoding="utf-8", newline="") as f:
        return {row["video_id"] for row in csv.DictReader(f)}


async def _append_views_rows_locked(rows):
    if not rows:
        return
    async with FILE_LOCK:
        file_exists = VIDEO_VIEWS_CSV.exists()
        with open(VIDEO_VIEWS_CSV, "a", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=VIEWS_FIELDS)
            if not file_exists:
                writer.writeheader()
            writer.writerows(rows)


async def _append_tidak_ada_rows_locked(rows):
    if not rows:
        return
    async with FILE_LOCK:
        file_exists = VIDEO_TIDAK_ADA_CSV.exists()
        with open(VIDEO_TIDAK_ADA_CSV, "a", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=TIDAK_ADA_FIELDS)
            if not file_exists:
                writer.writeheader()
            writer.writerows(rows)


async def _extract_grid_pairs(page):
    pairs = {}
    anchors = await page.query_selector_all('a[href*="/video/"]')
    for a in anchors:
        try:
            href = await a.get_attribute("href")
            if not href:
                continue
            m = re.search(r'/video/(\d+)', href)
            if not m:
                continue
            views_elem = await a.query_selector('[data-e2e="video-views"]')
            if views_elem:
                views_text = (await views_elem.text_content() or "").strip()
                if views_text:
                    pairs[m.group(1)] = views_text
        except Exception:
            continue
    return pairs, anchors


async def _scroll_dan_kumpulkan_views(page, target_video_ids,
                                       max_scroll=MAX_SCROLL_PER_USER_VIEWS, no_new_limit=NO_NEW_CARD_LIMIT):
    kumpulan, no_new_count = {}, 0
    for i in range(max_scroll):
        pairs, anchors = await _extract_grid_pairs(page)
        ada_baru = False
        for vid, views in pairs.items():
            if vid not in kumpulan:
                kumpulan[vid] = views
                ada_baru = True

        if target_video_ids <= set(kumpulan.keys()):
            break
        if not ada_baru:
            no_new_count += 1
            if no_new_count >= no_new_limit:
                break
            await asyncio.sleep(random.uniform(*DELAY_WHEN_NO_NEW_VIEWS))
        else:
            no_new_count = 0

        if anchors:
            try:
                await anchors[-1].scroll_into_view_if_needed(timeout=5000)
            except Exception:
                pass
        try:
            await page.evaluate("window.scrollBy(0, 2200)")
        except Exception:
            pass
        try:
            await page.mouse.move(700, 450)
            await page.mouse.wheel(0, 2200)
        except Exception:
            pass
        await asyncio.sleep(random.uniform(*DELAY_BETWEEN_SCROLL_VIEWS))

    return kumpulan


async def _is_video_unavailable_text(page) -> bool:
    for sel in ['text=/can\\s*only\\s*be\\s*viewed\\s*in\\s*the\\s*tiktok\\s*app/i',
                'text=/video\\s*(is\\s*)?not\\s*available/i', 'text=/this\\s*video\\s*is\\s*unavailable/i',
                'text=/page\\s*not\\s*found/i', 'text=/couldn.?t\\s*find\\s*this\\s*account\\s*or\\s*video/i']:
        try:
            elem = await page.query_selector(sel)
            if elem and await elem.is_visible():
                return True
        except Exception:
            continue
    return False


async def _cek_video_exists(page, username, vid, worker_label=""):
    try:
        await page.goto(f"https://www.tiktok.com/@{username}/video/{vid}", wait_until="load", timeout=30000)
    except Exception as e:
        return "tidak_pasti", f"gagal_load: {e}"
    await asyncio.sleep(random.uniform(1.5, 2.5))
    await _wait_for_login_wall_clear(page, context_label=f"cek eksistensi @{username}/{vid[:10]}", worker_label=worker_label)
    if await _is_video_unavailable_text(page):
        return "tidak_ada", "pesan_video_unavailable"
    try:
        await page.wait_for_selector('[data-e2e="like-count"]', timeout=8000)
        return "ada", "valid_tapi_belum_ketemu_di_grid"
    except Exception:
        return "tidak_pasti", "timeout_nunggu_elemen_video"


async def _run_views_worker(p, worker_label, profile_dir, cookies_path, usernames_subset, target_per_user, scraped_views_set):
    cookies = _load_cookies_from_json(cookies_path, label=worker_label)
    if not cookies:
        return 0

    profile_dir.mkdir(parents=True, exist_ok=True)
    context = await p.chromium.launch_persistent_context(
        str(profile_dir), headless=False, viewport={"width": 1400, "height": 900},
        user_agent=REALISTIC_UA, args=["--disable-blink-features=AutomationControlled"],
    )
    await context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")
    await context.add_cookies(cookies)
    page = context.pages[0] if context.pages else await context.new_page()

    try:
        await page.goto("https://www.tiktok.com", wait_until="load", timeout=30000)
        await asyncio.sleep(2)
    except Exception:
        pass

    total_baru = 0
    try:
        for idx, username in enumerate(usernames_subset, start=1):
            sisa_target = target_per_user[username] - scraped_views_set
            if not sisa_target:
                continue

            print(f"[{worker_label}] [{idx}/{len(usernames_subset)}] @{username}: {len(sisa_target)} video tersisa")
            try:
                await page.goto(f"https://www.tiktok.com/@{username}", wait_until="load", timeout=60000)
            except Exception:
                continue
            await asyncio.sleep(random.uniform(2, 4))
            await _wait_for_login_wall_clear(page, context_label=f"@{username}", worker_label=worker_label)

            hasil = await _scroll_dan_kumpulkan_views(page, sisa_target)
            rows = [{"username": username, "video_id": vid,
                     "video_url": f"https://www.tiktok.com/@{username}/video/{vid}",
                     "views": views, "scraped_at": datetime.now().isoformat()}
                    for vid, views in hasil.items() if vid in sisa_target]
            if rows:
                await _append_views_rows_locked(rows)
                scraped_views_set.update(r["video_id"] for r in rows)
                total_baru += len(rows)

            tidak_ketemu = list(sisa_target - set(hasil.keys()))[:5]
            if tidak_ketemu:
                rows_tidak_ada = []
                gagal_beruntun = 0
                for vid in tidak_ketemu:
                    status, alasan = await _cek_video_exists(page, username, vid, worker_label=worker_label)
                    if status == "tidak_ada":
                        rows_tidak_ada.append({"username": username, "video_id": vid,
                                                "video_url": f"https://www.tiktok.com/@{username}/video/{vid}",
                                                "alasan": alasan, "checked_at": datetime.now().isoformat()})
                        gagal_beruntun = 0
                    elif status == "tidak_pasti":
                        gagal_beruntun += 1
                    else:
                        gagal_beruntun = 0
                    if gagal_beruntun >= 2:
                        break
                    await asyncio.sleep(random.uniform(10, 20))
                if rows_tidak_ada:
                    await _append_tidak_ada_rows_locked(rows_tidak_ada)

            if idx < len(usernames_subset):
                await asyncio.sleep(random.uniform(*DELAY_BETWEEN_INFLUENCER_VIEWS))
    except CaptchaTimeoutError as e:
        print(f"[{worker_label}] Dihentikan: {e}")
    finally:
        await context.close()

    return total_baru


async def scrape_video_views() -> None:
    """Ambil views tiap video unik di comments.csv lewat scroll grid profil (bukan buka per video)."""
    if not COMMENTS_CSV.exists():
        raise FileNotFoundError(f"{COMMENTS_CSV} tidak ada. Jalankan scrape_comments() dulu.")

    df_comments = pd.read_csv(COMMENTS_CSV, dtype=str, keep_default_na=False)
    if "commenter_username" in df_comments.columns:
        df_comments = df_comments[~df_comments["commenter_username"].isin(EXCLUDE_USERNAMES)]
    video_id_target = sorted(set(v for v in df_comments["video_id"].dropna().unique() if v.strip()))

    df_meta = pd.read_csv(METADATA_VIDEO, dtype=str, keep_default_na=False)
    df_meta = df_meta[~df_meta["username"].isin(EXCLUDE_USERNAMES)].drop_duplicates(subset="video_id", keep="last")
    lookup_username = dict(zip(df_meta["video_id"], df_meta["username"]))

    target_per_user = defaultdict(set)
    for vid in video_id_target:
        uname = lookup_username.get(vid)
        if uname:
            target_per_user[uname].add(vid)

    video_id_tidak_ada = _load_video_id_set(VIDEO_TIDAK_ADA_CSV)
    for uname in list(target_per_user.keys()):
        target_per_user[uname] -= video_id_tidak_ada
        if not target_per_user[uname]:
            del target_per_user[uname]

    scraped_views = _load_video_id_set(VIDEO_VIEWS_CSV)
    for uname in list(target_per_user.keys()):
        target_per_user[uname] -= scraped_views
        if not target_per_user[uname]:
            del target_per_user[uname]

    if not target_per_user:
        print(f"[Views] Semua {len(video_id_target)} video target sudah ada views-nya, skip.")
        return

    sisa_total = sum(len(v) for v in target_per_user.values())
    print(f"[Views] {sisa_total} video dari {len(target_per_user)} influencer masih perlu di-scrape views-nya")

    usernames_all = list(target_per_user.keys())
    random.shuffle(usernames_all)
    half = len(usernames_all) // 2
    _send_telegram_alert(f"Scraping views mulai. {len(usernames_all)} username, {sisa_total} video.")

    async with async_playwright() as p:
        results = await asyncio.gather(
            _run_views_worker(p, "WORKER-1", PROFILE_DIR_1, COOKIES_FILE_1, usernames_all[:half], target_per_user, scraped_views),
            _run_views_worker(p, "WORKER-2", PROFILE_DIR_2, COOKIES_FILE_2, usernames_all[half:], target_per_user, scraped_views),
            return_exceptions=True,
        )
    total = sum(r for r in results if isinstance(r, int))
    print(f"[Views] Selesai. Total video baru: {total}")
    _send_telegram_alert(f"Scraping views selesai. Total baru: {total}")

    _report_video_gagal(target_per_user)


def _report_video_gagal(target_per_user: dict) -> None:
    """Cek video target yang masih nyangkut (ga ketemu views-nya, ga juga dikonfirmasi
    tidak_ada) setelah 1 run scraping views -- disimpan buat dicek manual satu-satu."""
    video_id_ada = _load_video_id_set(VIDEO_VIEWS_CSV)
    video_id_tidak_ada = _load_video_id_set(VIDEO_TIDAK_ADA_CSV)

    rows_gagal = []
    for uname, vids in target_per_user.items():
        sisa = vids - video_id_ada - video_id_tidak_ada
        for vid in sisa:
            rows_gagal.append({"username": uname, "video_id": vid,
                                "video_url": f"https://www.tiktok.com/@{uname}/video/{vid}"})

    if not rows_gagal:
        print("[Views] Tidak ada video gagal -- semua target sudah ke-scrape atau dikonfirmasi tidak_ada.")
        if VIDEO_GAGAL_CSV.exists():
            VIDEO_GAGAL_CSV.unlink()
        return

    pd.DataFrame(rows_gagal).to_csv(VIDEO_GAGAL_CSV, index=False)
    print(f"[Views] {len(rows_gagal)} video masih gagal/belum ke-scrape -> {VIDEO_GAGAL_CSV} (cek manual satu-satu)")


# -------------------------------------------------------------------------
# MAIN
# -------------------------------------------------------------------------
async def main():
    generate_scrape_list()
    await scrape_comments(TO_SCRAPE)
    generate_replacement_list()
    await scrape_comments(TO_SCRAPE_PENGGANTI)
    await scrape_video_views()


if __name__ == "__main__":
    asyncio.run(main())
