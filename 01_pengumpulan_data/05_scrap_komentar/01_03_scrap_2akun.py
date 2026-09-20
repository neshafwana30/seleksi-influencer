"""
07_SCRAPE_COMMENTS_2AKUN.py
============================
Scrape komentar TikTok pakai 2 AKUN & 2 BROWSER PARALEL biar 2x lebih cepat.

Perubahan dari versi single-browser:
1. MAX_COMMENTS_PER_VIDEO = 100, MIN target = 50 (dulu sampai 200+)
2. MAX_VIDEOS_PER_INFLUENCER = 20 (skip video influencer yang sama kalau udah 20)
3. 2 WORKER PARALEL — masing-masing pakai akun/cookies & profile browser SENDIRI,
   dan masing-masing pegang SET INFLUENCER YANG BEDA (gak overlap sama sekali,
   jadi gak ada race condition di level video/influencer)
4. Shared file-write lock biar CSV gak korup pas 2 worker nulis bersamaan

SETUP YANG DIBUTUHKAN:
- 2 file cookies terpisah (dari 2 akun TikTok BEDA):
    01_pengumpulan_data/00_akun_playwright/tiktok_cookies_akun1.json
    01_pengumpulan_data/00_akun_playwright/tiktok_cookies_akun2.json
- 2 folder browser profile terpisah (akan dibuat otomatis kalau belum ada):
    01_pengumpulan_data/04_2_Klasifikasikan_konten_scraping/tiktok_browser_profile_akun1
    01_pengumpulan_data/04_2_Klasifikasikan_konten_scraping/tiktok_browser_profile_akun2

⚠️  CATATAN RISIKO: 2 akun jalan barengan itu jauh lebih aman daripada 1 akun
    dipakai di 2 browser sekaligus (yang bisa keliatan mencurigakan buat TikTok).
    Tetap disarankan akun-akun ini bukan akun utama/pribadi.
"""

import asyncio
import csv
import json
import os
import sys
import random
import re
import pygame
import requests
from datetime import datetime
from pathlib import Path
from collections import defaultdict

from playwright.async_api import async_playwright

# ============================================================
# KONFIGURASI PATH
# ============================================================
ROOT_DIR = r"01_pengumpulan_data/05_scrap_komentar"
DATA_DIR = Path(ROOT_DIR)

INPUT_HEALTH_CSV = DATA_DIR / "health_videos.csv"

OUTPUT_BASE_DIR = Path(r"01_pengumpulan_data/05_scrap_komentar")
OUTPUT_DIR = OUTPUT_BASE_DIR / "hasil_07_comments"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_COMMENTS = OUTPUT_DIR / "comments.csv"
OUTPUT_COMMENTS_STATS = OUTPUT_DIR / "comments_stats.csv"

# ⭐ 2 SET profile & cookies — satu per akun
AKUN_DIR = DATA_DIR.parent / "00_akun_playwright"
FINAL_COOKIE_DIR = AKUN_DIR / "final_cookie"   # ⭐ hasil dari convert_and_apply_cookies.py
PROFILE_DIR_1 = DATA_DIR / "tiktok_browser_profile_akun1"
PROFILE_DIR_2 = DATA_DIR / "tiktok_browser_profile_akun2"
COOKIES_FILE_1 = FINAL_COOKIE_DIR / "tiktok_cookies_akun1.json"
COOKIES_FILE_2 = FINAL_COOKIE_DIR / "tiktok_cookies_akun2.json"

WARNING_MP3 = DATA_DIR.parent / "03_videoid_perakun" / "warning.mp3"

print(f"📁 Path configuration:")
print(f"   Input CSV: {INPUT_HEALTH_CSV}")
print(f"   Output: {OUTPUT_DIR}")
print(f"   Profile akun 1: {PROFILE_DIR_1}")
print(f"   Profile akun 2: {PROFILE_DIR_2}")
print(f"   Cookies akun 1: {COOKIES_FILE_1}")
print(f"   Cookies akun 2: {COOKIES_FILE_2}")

COMMENT_FIELDS = [
    "username", "video_id", "comment_id", "commenter_username",
    "comment_text", "like_count", "reply_count", "timestamp",
    "scraped_at"
]

STATS_FIELDS = [
    "username", "video_id", "total_comments_loaded", "total_comments_available",
    "scraped_comments_count", "expected_comment_count", "coverage_pct", "scraped_at"
]

# ============================================================
# KONFIGURASI SCRAPING
# ============================================================
DELAY_BETWEEN_VIDEO = (10, 30)
DELAY_BETWEEN_SCROLL = (2, 4)
DELAY_EVERY_N_VIDEOS = 25
DELAY_AFTER_N_VIDEOS = (20, 60)

MAX_COMMENTS_PER_VIDEO = 100        # ⭐ Cap atas
MIN_COMMENTS_TARGET = 50            # ⭐ Target minimal sebelum berhenti (kalau videonya punya banyak komentar)
MAX_VIDEOS_PER_INFLUENCER = 20      # ⭐ Max video per akun/influencer
MAX_SCROLL_ATTEMPTS = 40            # ⭐ Cukup buat capai ~100 komentar, gak perlu scroll ratusan kali

NETWORK_STATS_WAIT_TIMEOUT = 8.0
NETWORK_STATS_POLL_INTERVAL = 0.25

REALISTIC_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)

# ⭐ Lock global buat nulis file CSV — dibagi ke 2 worker biar gak race condition
FILE_LOCK = asyncio.Lock()

# ============================================================
# TELEGRAM ALERT
# ============================================================
try:
    sys.path.insert(0, str(Path(ROOT_DIR).parent.parent))
    from credentials import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
    TELEGRAM_ENABLED = bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)
    if TELEGRAM_ENABLED:
        print(f"✅ Telegram alert ENABLED")
except ImportError:
    TELEGRAM_BOT_TOKEN = None
    TELEGRAM_CHAT_ID = None
    TELEGRAM_ENABLED = False
    print(f"⚠️  Telegram alert DISABLED")


def send_telegram_alert(message: str):
    if not TELEGRAM_ENABLED:
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message}
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"   ⚠️ Telegram error: {e}")


def play_warning():
    if not os.path.exists(WARNING_MP3):
        return
    try:
        pygame.mixer.init()
        pygame.mixer.music.load(str(WARNING_MP3))
        pygame.mixer.music.play()
    except Exception:
        pass


def stop_warning():
    try:
        if pygame.mixer.get_init():
            pygame.mixer.music.stop()
            pygame.mixer.quit()
    except Exception:
        pass


# ============================================================
# HELPER: LOAD COOKIES
# ============================================================
def load_cookies_from_json(cookies_path: Path, label=""):
    if not cookies_path.exists():
        print(f"⚠️  Cookies file not found ({label}): {cookies_path}")
        return None
    try:
        with open(cookies_path, "r", encoding="utf-8") as f:
            cookies = json.load(f)
        print(f"✅ [{label}] Loaded {len(cookies)} cookies")
        return cookies
    except Exception as e:
        print(f"❌ [{label}] Error loading cookies: {e}")
        return None


# ============================================================
# HELPER: LOAD DATA
# ============================================================
def load_health_videos():
    videos = []
    if not INPUT_HEALTH_CSV.exists():
        print(f"❌ {INPUT_HEALTH_CSV} tidak ditemukan!")
        return videos

    with open(INPUT_HEALTH_CSV, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            videos.append(row)

    print(f"✅ Loaded {len(videos)} health videos")
    return videos


def load_existing_comments():
    if not OUTPUT_COMMENTS.exists():
        return set()
    scraped = set()
    with open(OUTPUT_COMMENTS, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            key = (row["username"], row["video_id"], row["comment_id"])
            scraped.add(key)
    return scraped


def load_existing_stats():
    if not OUTPUT_COMMENTS_STATS.exists():
        return {}
    stats = {}
    with open(OUTPUT_COMMENTS_STATS, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            key = (row["username"], row["video_id"])
            stats[key] = row
    return stats


async def append_comment_rows_locked(rows):
    """Append comments ke CSV, dilindungi lock biar aman dipanggil dari 2 worker."""
    if not rows:
        return
    async with FILE_LOCK:
        file_exists = OUTPUT_COMMENTS.exists()
        with open(OUTPUT_COMMENTS, "a", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=COMMENT_FIELDS)
            if not file_exists:
                writer.writeheader()
            writer.writerows(rows)
            f.flush()
            os.fsync(f.fileno())


async def append_stats_row_locked(row):
    async with FILE_LOCK:
        file_exists = OUTPUT_COMMENTS_STATS.exists()
        with open(OUTPUT_COMMENTS_STATS, "a", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=STATS_FIELDS)
            if not file_exists:
                writer.writeheader()
            writer.writerow(row)
            f.flush()
            os.fsync(f.fileno())


# ============================================================
# NETWORK RESPONSE CAPTURE - COMMENTS
# ============================================================
def find_comments_blocks(obj, _depth=0):
    results = []
    if _depth > 15:
        return results

    if isinstance(obj, dict):
        comments_list = (
            obj.get("comments") or obj.get("Items") or obj.get("items")
            or obj.get("reply_comment") or obj.get("replies")
        )
        if isinstance(comments_list, list):
            for comment in comments_list:
                try:
                    comment_id = comment.get("id") or comment.get("cid")
                    text = comment.get("text") or ""
                    user_data = comment.get("user") or {}
                    username = user_data.get("uniqueId") or user_data.get("id") or ""
                    stats = comment.get("stats") or {}
                    likes = stats.get("diggCount") or 0
                    replies = stats.get("replyCount") or 0
                    create_time = comment.get("createTime") or ""

                    if comment_id and text:
                        results.append({
                            "comment_id": str(comment_id),
                            "username": str(username),
                            "text": str(text),
                            "likes": str(likes),
                            "replies": str(replies),
                            "create_time": str(create_time),
                            "is_reply": False,
                        })
                except Exception:
                    continue

        for value in obj.values():
            results.extend(find_comments_blocks(value, _depth + 1))

    elif isinstance(obj, list):
        for item in obj:
            results.extend(find_comments_blocks(item, _depth + 1))

    return results


def count_unique_comments(comments_cache):
    seen = set()
    for c in comments_cache:
        cid = c.get("comment_id")
        if cid:
            seen.add(cid)
    return len(seen)


def parse_count_string(text):
    if not text:
        return 0
    text = text.strip().upper().replace(",", "")
    try:
        if "K" in text:
            return int(float(text.replace("K", "")) * 1_000)
        elif "M" in text:
            return int(float(text.replace("M", "")) * 1_000_000)
        elif "B" in text:
            return int(float(text.replace("B", "")) * 1_000_000_000)
        else:
            return int(float(text))
    except Exception:
        return 0


async def get_expected_comment_count(page):
    selectors = [
        '[data-e2e="comment-count"]',
        'strong[data-e2e="comment-count"]',
    ]
    for sel in selectors:
        try:
            elem = await page.query_selector(sel)
            if elem:
                text = await elem.text_content()
                count = parse_count_string(text)
                if count > 0:
                    return count
        except Exception:
            continue
    return 0


# ============================================================
# EXPAND "VIEW X REPLIES" BUTTONS
# ============================================================
def is_view_replies_button_text(text):
    if not text:
        return False
    t = text.strip().lower()

    if t in ("reply", "balas", "membalas", "respond"):
        return False
    if "hide" in t or "sembunyikan" in t:
        return False

    has_number_and_repl = bool(re.search(r'\d+\s*(repl|balasan)', t))
    has_view_repl = ("view" in t and "repl" in t)
    has_lihat_balasan = ("lihat" in t and "balasan" in t)

    return has_number_and_repl or has_view_repl or has_lihat_balasan


async def expand_visible_reply_buttons(page, verbose=True, max_clicks=3):
    clicked = 0
    candidates_checked = 0
    matched_texts = 0

    selector_patterns = [
        '[class*="TUXButton-label"]',
        'p[class*="TUXButton-label"]',
        'span[class*="TUXButton-label"]',
        'div[class*="TUXButton-label"]',
        'button:has-text("repl")',
        'button:has-text("balasan")',
        '[role="button"]:has-text("repl")',
    ]

    seen_positions = set()

    for pattern in selector_patterns:
        if clicked >= max_clicks:
            break
        try:
            elems = await page.query_selector_all(pattern)
        except Exception:
            continue

        for elem in elems:
            if clicked >= max_clicks:
                break
            candidates_checked += 1
            try:
                if not await elem.is_visible():
                    continue

                text = (await elem.text_content() or "").strip()
                if not text:
                    continue

                if not is_view_replies_button_text(text):
                    continue

                matched_texts += 1

                box = await elem.bounding_box()
                pos_key = (round(box["x"]), round(box["y"])) if box else None
                if pos_key and pos_key in seen_positions:
                    continue
                if pos_key:
                    seen_positions.add(pos_key)

                click_success = False
                try:
                    await elem.scroll_into_view_if_needed(timeout=2000)
                    await elem.click(timeout=3000)
                    click_success = True
                except Exception:
                    try:
                        await elem.evaluate("el => el.click()")
                        click_success = True
                    except Exception:
                        pass

                if click_success:
                    clicked += 1
                    await asyncio.sleep(random.uniform(0.5, 1))

            except Exception:
                continue

    if verbose:
        print(f"      🔍 Reply-button scan: {candidates_checked} candidates, "
              f"{matched_texts} matched, {clicked} di-klik")

    return clicked


def make_comments_response_listener(comments_cache):
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
            blocks = find_comments_blocks(body)
        except Exception:
            return
        for block in blocks:
            comments_cache.append(block)
    return on_response


# ============================================================
# EXTRACT REPLIES FROM DOM
# ============================================================
async def extract_replies_from_dom_simple(page, comment_container):
    try:
        await asyncio.sleep(0.5)
        reply_selectors = [
            '[data-e2e="comment-item"][class*="reply"]',
            'div[class*="ReplyItem"]',
            '[class*="CommentReply"]',
        ]
        all_replies = []
        for selector in reply_selectors:
            try:
                reply_elems = await page.query_selector_all(selector)
                for reply_elem in reply_elems:
                    try:
                        reply_text_elem = await reply_elem.query_selector('[data-e2e="comment-content"]')
                        if not reply_text_elem:
                            reply_text_elem = await reply_elem.query_selector('[class*="content"]')
                        if reply_text_elem:
                            reply_text = await reply_text_elem.text_content()
                            username_elem = await reply_elem.query_selector('[data-e2e="comment-username"]')
                            username = await username_elem.text_content() if username_elem else ""
                            likes_elem = await reply_elem.query_selector('[data-e2e="comment-like-count"]')
                            likes = await likes_elem.text_content() if likes_elem else "0"

                            if reply_text and len(reply_text) > 3:
                                all_replies.append({
                                    "text": reply_text.strip(),
                                    "username": username.strip() if username else "unknown",
                                    "likes": likes.strip() if likes else "0"
                                })
                    except Exception:
                        continue
            except Exception:
                continue
        return all_replies
    except Exception as e:
        print(f"      ⚠️ Error extracting replies: {str(e)[:50]}")
        return []


async def find_actual_scroll_target(page, container):
    try:
        handle = await container.evaluate_handle("""
            (el) => {
                function isScrollable(node) {
                    if (!(node instanceof HTMLElement)) return false;
                    const style = window.getComputedStyle(node);
                    const overflowY = style.overflowY;
                    return (overflowY === 'auto' || overflowY === 'scroll')
                           && node.scrollHeight > node.clientHeight + 10;
                }
                let ancestor = el.parentElement;
                let depth = 0;
                while (ancestor && depth < 8) {
                    if (isScrollable(ancestor)) return ancestor;
                    ancestor = ancestor.parentElement;
                    depth++;
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
                const desc = findScrollable(el, 0);
                if (desc) return desc;
                if (isScrollable(el)) return el;
                return el;
            }
        """)
        elem = handle.as_element()
        return elem if elem else container
    except Exception:
        return container


async def wheel_scroll_element(page, elem, delta_y=650):
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


async def get_scroll_diagnostics(elem):
    try:
        info = await elem.evaluate("el => ({top: el.scrollTop, sh: el.scrollHeight, ch: el.clientHeight})")
        return info.get("top", 0), info.get("sh", 0)
    except Exception:
        return 0, 0


# ============================================================
# CAPTCHA CHECK — SELECTOR-ONLY (no content scanning — no false positives!)
# ============================================================
CAPTCHA_QUICK_SELECTORS = [
    'img[alt="Captcha" i]',
    'img[alt*="captcha" i]',
    'text="Drag the slider to fit the puzzle"',
    'text="Drag the slider"',
    '[id*="captcha"]',
    '[class*="captcha"]',
    '[class*="Captcha"]',
    'div[class^="cap-"]',
    'div[class*=" cap-"]',
    'iframe[src*="captcha"]',
    '.TUXModal:has-text("captcha")',
    '.TUXModal:has-text("Captcha")',
]


async def quick_captcha_check(page):
    for sel in CAPTCHA_QUICK_SELECTORS:
        try:
            elem = await page.query_selector(sel)
            if elem and await elem.is_visible():
                return True, sel
        except Exception:
            continue
    return False, None


async def wait_for_login_wall_clear(page, timeout=600, context_label="", worker_label=""):
    blocker_selectors = [
        'text="Log in to TikTok"',
        '[data-e2e="login-modal"]',
        'div[role="dialog"]:has-text("Log in")',
        'div[role="dialog"]:has-text("Continue with")',
        'text="Drag the slider"',
        'text="Drag the slider to fit the puzzle"',
        '[id*="captcha"]',
        '[class*="captcha"]',
        '[class*="Captcha"]',
        'iframe[src*="captcha"]',
        'img[alt="Captcha" i]',
        'img[alt*="captcha" i]',
        '.TUXModal:has-text("captcha")',
        '.TUXModal:has-text("Captcha")',
        'div[class^="cap-"]',
        'div[class*=" cap-"]',
        'button:has-text("Verify")',
    ]

    ALERT_REPEAT_EVERY = 30
    start = asyncio.get_event_loop().time()
    was_found = False
    alert_sent = False
    last_alert_at = None

    while asyncio.get_event_loop().time() - start < timeout:
        found = False
        matched_selector = None

        for sel in blocker_selectors:
            try:
                elem = await page.query_selector(sel)
                if elem and await elem.is_visible():
                    found = True
                    matched_selector = sel
                    break
            except Exception:
                continue

        if not found:
            if was_found:
                print(f"   ✅ [{worker_label}] Captcha/login wall hilang, lanjut.")
                stop_warning()
                if alert_sent:
                    send_telegram_alert(f"✅ [{worker_label}] Captcha solved! Lanjut scraping! 🚀\n{context_label}")
                await asyncio.sleep(random.uniform(2, 4))
            return True

        now = asyncio.get_event_loop().time()
        remaining = int(timeout - (now - start))

        if not was_found:
            print(f"\n{'=' * 70}")
            print(f"   🔒 [{worker_label}] CAPTCHA / LOGIN WALL TERDETEKSI !!!")
            print(f"   📍 Selector matched: {matched_selector}")
            print(f"   ⏰ Timeout dalam {remaining} detik")
            print(f"{'=' * 70}")
            play_warning()

        if last_alert_at is None or (now - last_alert_at) >= ALERT_REPEAT_EVERY:
            send_telegram_alert(
                f"⚠️ [{worker_label}] CAPTCHA DETECTED!\n{context_label}\n"
                f"⏳ Sisa waktu: {remaining}s\nSolve manual di browser worker ini!"
            )
            alert_sent = True
            last_alert_at = now

        was_found = True
        print(f"   ⏳ [{worker_label}] Waiting untuk solve... ({remaining}s)", end="\r")
        await asyncio.sleep(3)

    print(f"\n   ❌ [{worker_label}] TIMEOUT - Captcha gak di-solve!")
    stop_warning()
    send_telegram_alert(f"❌ [{worker_label}] CAPTCHA TIMEOUT!\n{context_label}\nWorker ini dihentikan.")
    raise Exception(f"[{worker_label}] Captcha/login wall gak di-solve dalam {timeout}s.")


async def pause_for_manual_captcha_solve(page, timeout=600, context_label="", worker_label=""):
    print(f"\n{'=' * 70}")
    print(f"   🔒 [{worker_label}] CAPTCHA TERDETEKSI DI TENGAH PROSES!!!")
    print(f"{'=' * 70}")
    play_warning()
    send_telegram_alert(f"⚠️ [{worker_label}] CAPTCHA (mid-process)!\n{context_label}\nSolve manual sekarang!")

    start = asyncio.get_event_loop().time()
    while asyncio.get_event_loop().time() - start < timeout:
        found, _ = await quick_captcha_check(page)
        if not found:
            print(f"   ✅ [{worker_label}] Captcha solved! Lanjut...")
            stop_warning()
            send_telegram_alert(f"✅ [{worker_label}] Captcha solved!\n{context_label}")
            await asyncio.sleep(random.uniform(2, 4))
            return True
        remaining = int(timeout - (asyncio.get_event_loop().time() - start))
        print(f"   ⏳ [{worker_label}] Waiting... ({remaining}s)", end="\r")
        await asyncio.sleep(3)

    print(f"\n   ❌ [{worker_label}] TIMEOUT!")
    stop_warning()
    raise Exception(f"[{worker_label}] Captcha timeout di tengah proses")


# ============================================================
# IP / WAF BLOCK DETECTION
# ============================================================
async def check_ip_blocked(page):
    try:
        content = await page.content()
        indicators = [
            "Access to www.tiktok.com was denied",
            "You don't have authorization to view this page",
            "HTTP ERROR 403",
        ]
        for indicator in indicators:
            if indicator in content:
                return True
    except Exception:
        pass
    return False


async def handle_ip_block(page, context_label="", cooldown_minutes=45, worker_label=""):
    print(f"\n{'=' * 70}")
    print(f"   🚫 [{worker_label}] IP/SESSION KE-BLOCK (HTTP 403)!")
    print(f"   😴 PAUSE {cooldown_minutes} menit...")
    print(f"{'=' * 70}")
    play_warning()
    send_telegram_alert(
        f"🚫 [{worker_label}] IP/SESSION BLOCKED (HTTP 403)!\n{context_label}\n"
        f"Worker ini PAUSE {cooldown_minutes} menit."
    )
    stop_warning()
    await asyncio.sleep(cooldown_minutes * 60)
    print(f"\n   ⏰ [{worker_label}] Cooldown selesai, mencoba lagi...")


# ============================================================
# SCRAPE COMMENTS DARI VIDEO
# ============================================================
async def scrape_comments_from_video(page, username, video_id, comments_cache, worker_label=""):
    video_url = f"https://www.tiktok.com/@{username}/video/{video_id}"

    print(f"\n   🎥 [{worker_label}] Scrape: {video_id[:10]}...")

    try:
        await page.goto(video_url, wait_until="load", timeout=30000)
        await asyncio.sleep(random.uniform(2, 3))
    except Exception as e:
        print(f"      ❌ [{worker_label}] Gagal navigate: {e}")
        return None

    max_block_retries = 3
    for retry in range(max_block_retries):
        is_blocked = await check_ip_blocked(page)
        if not is_blocked:
            break
        await handle_ip_block(page, context_label=f"Video {video_id[:10]}", worker_label=worker_label)
        try:
            await page.goto(video_url, wait_until="load", timeout=30000)
            await asyncio.sleep(random.uniform(2, 3))
        except Exception as e:
            print(f"      ❌ [{worker_label}] Gagal navigate setelah cooldown: {e}")
            return None
    else:
        raise Exception(f"[{worker_label}] Masih ke-block setelah {max_block_retries}x retry")

    comments_cache.clear()

    try:
        await wait_for_login_wall_clear(page, timeout=600, context_label=f"Video {video_id}", worker_label=worker_label)
    except Exception:
        return None

    await asyncio.sleep(random.uniform(2, 3))

    print(f"      👉 [{worker_label}] Clicking Comments tab...")
    comment_clicked = False
    comment_selectors = [
        '[data-e2e="comment-icon"]',
        '[aria-label="Comments"]',
        'button[aria-label*="comment" i]',
        'button:has-text("Comments")',
    ]

    for selector in comment_selectors:
        try:
            elem = await page.query_selector(selector)
            if elem and await elem.is_visible():
                await elem.click(timeout=5000)
                comment_clicked = True
                await asyncio.sleep(random.uniform(1, 2))
                break
        except Exception:
            continue

    if not comment_clicked:
        try:
            await page.evaluate("window.scrollBy(0, 300)")
            await asyncio.sleep(1)
        except Exception:
            pass

    await asyncio.sleep(random.uniform(1, 2))

    expected_count = await get_expected_comment_count(page)

    # ⭐ Target: minimal MIN_COMMENTS_TARGET (50), maksimal MAX_COMMENTS_PER_VIDEO (100)
    if expected_count > 0:
        target_count = min(MAX_COMMENTS_PER_VIDEO, max(MIN_COMMENTS_TARGET, int(expected_count * 0.5)))
        print(f"      🎯 [{worker_label}] Target: {target_count} (video punya {expected_count} total)")
    else:
        target_count = MIN_COMMENTS_TARGET
        print(f"      ⚠️ [{worker_label}] Gak bisa detect count, target default: {target_count}")

    comment_container = None
    container_selectors = [
        '[data-e2e="comment-list"]',
        '[data-testid="comment-list-container"]',
        '[class*="CommentList"]',
        'div[class*="DraftCommentPanel"]',
    ]

    for sel in container_selectors:
        try:
            container = await page.query_selector(sel)
            if container and await container.is_visible():
                comment_container = container
                break
        except Exception:
            continue

    if not comment_container:
        is_captcha, matched_sel = await quick_captcha_check(page)
        if is_captcha:
            try:
                await pause_for_manual_captcha_solve(
                    page, timeout=600, context_label=f"Video {video_id[:10]}", worker_label=worker_label
                )
            except Exception:
                return None
            for sel in container_selectors:
                try:
                    container = await page.query_selector(sel)
                    if container and await container.is_visible():
                        comment_container = container
                        break
                except Exception:
                    continue

    if not comment_container:
        await page.evaluate("window.scrollBy(0, 500)")
        await asyncio.sleep(1)
        for sel in container_selectors:
            try:
                container = await page.query_selector(sel)
                if container and await container.is_visible():
                    comment_container = container
                    break
            except Exception:
                continue

        if not comment_container:
            return {"total_comments_loaded": 0, "total_comments_available": 0, "comments": [], "replies": [], "expected_comment_count": expected_count}

    print(f"      📜 [{worker_label}] Scrolling comments...")

    scroll_target = await find_actual_scroll_target(page, comment_container)

    no_new_streak = 0
    prev_count = 0
    no_new_streak_limit = 12
    min_scroll_before_stop = 8

    for scroll_attempt in range(MAX_SCROLL_ATTEMPTS):
        wheel_ok = await wheel_scroll_element(page, scroll_target, delta_y=random.randint(500, 800))
        if not wheel_ok:
            await wheel_scroll_element(page, comment_container, delta_y=random.randint(500, 800))

        await asyncio.sleep(random.uniform(*DELAY_BETWEEN_SCROLL))

        if scroll_attempt % 3 == 0:
            is_captcha, matched_sel = await quick_captcha_check(page)
            if is_captcha:
                print(f"\n      🔒 [{worker_label}] CAPTCHA saat scroll ke-{scroll_attempt + 1}!")
                try:
                    await pause_for_manual_captcha_solve(
                        page, timeout=600,
                        context_label=f"Video {video_id[:10]} (scroll {scroll_attempt + 1})",
                        worker_label=worker_label
                    )
                except Exception:
                    break
                no_new_streak = 0

        if scroll_attempt % 6 == 0:
            n_expanded = await expand_visible_reply_buttons(page, verbose=False)
            if n_expanded > 0:
                await asyncio.sleep(random.uniform(1, 1.5))

        current_count = count_unique_comments(comments_cache)

        if current_count > prev_count:
            no_new_streak = 0
            prev_count = current_count
        else:
            no_new_streak += 1

        if scroll_attempt % 5 == 4:
            print(f"      ↻ [{worker_label}] {scroll_attempt + 1}x scroll, {current_count} comments...")

        if current_count >= target_count:
            print(f"      ✅ [{worker_label}] Target tercapai: {current_count}/{target_count}")
            break

        if current_count >= MAX_COMMENTS_PER_VIDEO:
            print(f"      ✅ [{worker_label}] Max reached: {current_count}")
            break

        if no_new_streak >= no_new_streak_limit and scroll_attempt >= min_scroll_before_stop:
            print(f"      🔚 [{worker_label}] No new comments x{no_new_streak}, stop di {current_count}")
            break

    comments_loaded = count_unique_comments(comments_cache)
    print(f"      ✅ [{worker_label}] Total {comments_loaded} unique comments")

    unique_comments = {}
    for comment in comments_cache:
        cid = comment.get("comment_id")
        if cid and cid not in unique_comments:
            unique_comments[cid] = comment

    final_comments = list(unique_comments.values())[:MAX_COMMENTS_PER_VIDEO]

    dom_replies = []
    try:
        dom_replies = await extract_replies_from_dom_simple(page, comment_container)
    except Exception:
        pass

    return {
        "total_comments_loaded": len(final_comments),
        "total_comments_available": comments_loaded,
        "comments": final_comments,
        "replies": dom_replies,
        "expected_comment_count": expected_count,
    }


# ============================================================
# PROCESS VIDEO DAN SAVE COMMENTS
# ============================================================
async def process_video_comments(page, username, video_id, existing_comments, comments_cache, worker_label=""):
    video_comments = [c for c in existing_comments if c[0] == username and c[1] == video_id]
    if video_comments:
        print(f"      ⏭️  [{worker_label}] Video {video_id[:10]} sudah ada {len(video_comments)} comments")
        return 0

    result = await scrape_comments_from_video(page, username, video_id, comments_cache, worker_label=worker_label)

    if not result or not result["comments"]:
        print(f"      ⚠️ [{worker_label}] Gak ada comments ter-load")
        return 0

    rows = []
    for comment_data in result["comments"][:MAX_COMMENTS_PER_VIDEO]:
        key = (username, video_id, comment_data.get("comment_id"))
        if key in existing_comments:
            continue
        rows.append({
            "username": username,
            "video_id": video_id,
            "comment_id": comment_data.get("comment_id", ""),
            "commenter_username": comment_data.get("username", ""),
            "comment_text": comment_data.get("text", ""),
            "like_count": comment_data.get("likes", "0"),
            "reply_count": comment_data.get("replies", "0"),
            "timestamp": comment_data.get("create_time", ""),
            "scraped_at": datetime.now().isoformat(),
        })
        existing_comments.add(key)

    if rows:
        await append_comment_rows_locked(rows)
        print(f"      💾 [{worker_label}] Saved {len(rows)} comments")

    reply_rows = []
    if result.get("replies"):
        for idx, reply_data in enumerate(result["replies"]):
            reply_id = f"reply_{video_id}_{idx}"
            key = (username, video_id, reply_id)
            if key in existing_comments:
                continue
            reply_rows.append({
                "username": username,
                "video_id": video_id,
                "comment_id": reply_id,
                "commenter_username": reply_data.get("username", ""),
                "comment_text": reply_data.get("text", ""),
                "like_count": reply_data.get("likes", "0"),
                "reply_count": "0",
                "timestamp": "",
                "scraped_at": datetime.now().isoformat(),
            })
            existing_comments.add(key)

    if reply_rows:
        await append_comment_rows_locked(reply_rows)
        print(f"      💾 [{worker_label}] Saved {len(reply_rows)} replies")

    expected = result.get("expected_comment_count", 0)
    total_scraped = len(rows) + len(reply_rows)
    coverage_pct = round((total_scraped / expected * 100), 1) if expected > 0 else ""

    await append_stats_row_locked({
        "username": username,
        "video_id": video_id,
        "total_comments_loaded": result["total_comments_loaded"],
        "total_comments_available": result["total_comments_available"],
        "scraped_comments_count": total_scraped,
        "expected_comment_count": expected,
        "coverage_pct": coverage_pct,
        "scraped_at": datetime.now().isoformat(),
    })

    return total_scraped


# ============================================================
# WORKER — 1 BROWSER, 1 AKUN, LIST VIDEO SENDIRI
# ============================================================
async def run_worker(p, worker_label, profile_dir, cookies_path, video_list, existing_comments):
    cookies = load_cookies_from_json(cookies_path, label=worker_label)
    if not cookies:
        print(f"❌ [{worker_label}] No cookies, worker ini SKIP.")
        return 0

    profile_dir.mkdir(parents=True, exist_ok=True)

    context = await p.chromium.launch_persistent_context(
        str(profile_dir),
        headless=False,
        viewport={"width": 1400, "height": 900},
        user_agent=REALISTIC_UA,
        args=["--disable-blink-features=AutomationControlled"],
    )
    await context.add_init_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
    )

    await context.add_cookies(cookies)
    print(f"✅ [{worker_label}] {len(cookies)} cookies applied")

    page = context.pages[0] if context.pages else await context.new_page()

    try:
        await page.goto("https://www.tiktok.com", wait_until="load", timeout=30000)
        await asyncio.sleep(2)
        profile_button = await page.query_selector('[data-e2e="header-profile"]')
        if profile_button:
            print(f"✅ [{worker_label}] LOGGED IN")
        else:
            print(f"⚠️  [{worker_label}] Might not be logged in, continuing...")
        await asyncio.sleep(2)
    except Exception as e:
        print(f"⚠️  [{worker_label}] Error verifying login: {e}")

    comments_cache = []
    page.on("response", make_comments_response_listener(comments_cache))

    total_scraped = 0

    try:
        for idx, video_data in enumerate(video_list, start=1):
            username = video_data.get("username")
            video_id = video_data.get("video_id")

            print(f"\n{'=' * 60}")
            print(f"[{worker_label}] [{idx}/{len(video_list)}] @{username} - {video_id[:10]}")
            print(f"{'=' * 60}")

            try:
                n_comments = await process_video_comments(
                    page, username, video_id, existing_comments, comments_cache, worker_label=worker_label
                )
                total_scraped += n_comments

                if idx % DELAY_EVERY_N_VIDEOS == 0:
                    long_delay = random.uniform(*DELAY_AFTER_N_VIDEOS)
                    print(f"\n   🌙 [{worker_label}] Jeda {long_delay:.0f}s (cooldown)...")
                    await asyncio.sleep(long_delay)
                else:
                    delay = random.uniform(*DELAY_BETWEEN_VIDEO)
                    print(f"   ⏳ [{worker_label}] Jeda {delay:.0f}s")
                    await asyncio.sleep(delay)

            except Exception as e:
                print(f"   ❌ [{worker_label}] Error: {e}")
                continue

    finally:
        await context.close()

    print(f"\n✅ [{worker_label}] SELESAI. Total scraped: {total_scraped}")
    return total_scraped


# ============================================================
# MAIN
# ============================================================
async def main():
    health_videos = load_health_videos()
    if not health_videos:
        print("❌ Gak ada health videos untuk di-scrape")
        return

    existing_comments = load_existing_comments()
    existing_stats = load_existing_stats()

    # Filter videos yang belum discrape
    pending = []
    for video_data in health_videos:
        username = video_data.get("username")
        video_id = video_data.get("video_id")
        key = (username, video_id)
        if key not in existing_stats:
            pending.append(video_data)

    # ⭐ CAP max 20 video per influencer
    count_per_influencer = defaultdict(int)
    to_process = []
    for video_data in pending:
        username = video_data.get("username")
        if count_per_influencer[username] >= MAX_VIDEOS_PER_INFLUENCER:
            continue
        count_per_influencer[username] += 1
        to_process.append(video_data)

    print(f"\n📊 Summary:")
    print(f"   Total health videos: {len(health_videos)}")
    print(f"   Pending (belum discrape): {len(pending)}")
    print(f"   Setelah cap {MAX_VIDEOS_PER_INFLUENCER}/influencer: {len(to_process)}")
    print(f"   Jumlah influencer terlibat: {len(count_per_influencer)}")

    if not to_process:
        print("✅ Semua videos (dalam cap) sudah di-scrape")
        return

    # ⭐ Split video list JADI 2, per-influencer (gak ada influencer yang sama
    # dikerjain 2 worker sekaligus — biar gak ada race condition & gak keliatan
    # aneh 1 influencer diakses dari 2 sesi/akun barengan)
    influencer_list = list(count_per_influencer.keys())
    random.shuffle(influencer_list)
    half = len(influencer_list) // 2
    influencers_worker1 = set(influencer_list[:half])
    influencers_worker2 = set(influencer_list[half:])

    videos_worker1 = [v for v in to_process if v.get("username") in influencers_worker1]
    videos_worker2 = [v for v in to_process if v.get("username") in influencers_worker2]

    print(f"\n📦 Split:")
    print(f"   Worker 1 (akun 1): {len(videos_worker1)} video dari {len(influencers_worker1)} influencer")
    print(f"   Worker 2 (akun 2): {len(videos_worker2)} video dari {len(influencers_worker2)} influencer")

    send_telegram_alert(
        f"🚀 Mulai scrape 2 WORKER PARALEL\n"
        f"Worker 1: {len(videos_worker1)} video\n"
        f"Worker 2: {len(videos_worker2)} video\n"
        f"Waktu: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )

    async with async_playwright() as p:
        results = await asyncio.gather(
            run_worker(p, "WORKER-1", PROFILE_DIR_1, COOKIES_FILE_1, videos_worker1, existing_comments),
            run_worker(p, "WORKER-2", PROFILE_DIR_2, COOKIES_FILE_2, videos_worker2, existing_comments),
            return_exceptions=True,
        )

    total = 0
    for i, r in enumerate(results, start=1):
        if isinstance(r, Exception):
            print(f"❌ Worker-{i} error: {r}")
        else:
            total += r

    print(f"\n\n✅ SEMUA WORKER SELESAI")
    print(f"   Total comments scraped: {total}")
    print(f"   Output: {OUTPUT_COMMENTS}")

    send_telegram_alert(
        f"✅ Scrape comments selesai (2 worker)!\n"
        f"Total comments: {total}\n"
        f"Waktu: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )


if __name__ == "__main__":
    asyncio.run(main())