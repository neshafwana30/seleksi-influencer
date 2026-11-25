"""
07_SCRAPE_COMMENTS_2AKUN_FINAL.py
==================================
✅ FINAL VERSION dengan proper tracking:
1. Baca dari to_scrape.csv (generated dari generate_scrape_list.py)
2. Filter video yang belum di-scrape (scraped=False)
3. Setelah selesai process video, update to_scrape.csv: scraped=True + timestamp
4. 2 worker paralel, shared lock buat tulis CSV

Resume logic:
- Load to_scrape.csv di awal -> ambil rows yang scraped=False
- Pas selesai scrape, langsung update row di to_scrape.csv -> scraped=True
- Run berikutnya, load ulang to_scrape.csv -> rows yang True sudah ter-skip
- Semua status tracking jelas di 1 file CSV
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
ROOT_DIR = r"01_pengumpulan_data/04_2_Klasifikasikan_konten_scraping"
DATA_DIR = Path(ROOT_DIR)

# ⭐ NEW: Baca dari to_scrape.csv (bukan health_videos.csv)
TO_SCRAPE_CSV = Path(r"01_pengumpulan_data/05_scrap_komentar/to_scrape_pengganti.csv")

OUTPUT_BASE_DIR = Path(r"01_pengumpulan_data/05_scrap_komentar")
OUTPUT_DIR = OUTPUT_BASE_DIR / "hasil_07_comments"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_COMMENTS = OUTPUT_DIR / "comments.csv"
OUTPUT_COMMENTS_STATS = OUTPUT_DIR / "comments_stats.csv"

FINAL_COOKIE_DIR = DATA_DIR.parent / "00_akun_playwright" / "final_cookie"
PROFILE_DIR_1 = DATA_DIR / "tiktok_browser_profile_akun1"
PROFILE_DIR_2 = DATA_DIR / "tiktok_browser_profile_akun2"
COOKIES_FILE_1 = FINAL_COOKIE_DIR / "tiktok_cookies_akun1.json"
COOKIES_FILE_2 = FINAL_COOKIE_DIR / "tiktok_cookies_akun2.json"

WARNING_MP3 = DATA_DIR.parent / "03_videoid_perakun" / "warning.mp3"

print(f"📁 Path configuration:")
print(f"   Input CSV: {TO_SCRAPE_CSV}")
print(f"   Output: {OUTPUT_DIR}")

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
MAX_COMMENTS_PER_VIDEO = 100
MIN_COMMENTS_TARGET = 50
MAX_VIDEOS_PER_INFLUENCER = 20
MAX_SCROLL_ATTEMPTS = 40

NETWORK_STATS_WAIT_TIMEOUT = 8.0
NETWORK_STATS_POLL_INTERVAL = 0.25

REALISTIC_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)

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
    TELEGRAM_ENABLED = False
    print(f"⚠️  Telegram alert DISABLED")


def send_telegram_alert(message: str):
    if not TELEGRAM_ENABLED:
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message}
        requests.post(url, json=payload, timeout=10)
    except Exception:
        pass


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
# HELPER: LOAD DATA
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


def load_to_scrape_videos():
    """Load to_scrape.csv, filter hanya yang scraped=False."""
    videos = []
    if not TO_SCRAPE_CSV.exists():
        print(f"❌ {TO_SCRAPE_CSV} tidak ditemukan!")
        print(f"   Run: python generate_scrape_list.py dulu")
        return videos

    with open(TO_SCRAPE_CSV, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            # Filter: ambil cuma yang belum scraped
            if row.get("scraped", "False").lower() != "false":
                continue
            videos.append(row)

    print(f"✅ Loaded {len(videos)} pending videos dari to_scrape.csv")
    return videos


async def append_comment_rows_locked(rows):
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


async def write_stats_row_locked(row):
    async with FILE_LOCK:
        file_exists = OUTPUT_COMMENTS_STATS.exists()
        with open(OUTPUT_COMMENTS_STATS, "a", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=STATS_FIELDS)
            if not file_exists:
                writer.writeheader()
            writer.writerow(row)
            f.flush()
            os.fsync(f.fileno())


async def update_to_scrape_csv_locked(username: str, video_id: str, now_timestamp: str):
    """Update row di to_scrape.csv: set scraped=True dan timestamp."""
    async with FILE_LOCK:
        # Baca semua rows
        rows = []
        with open(TO_SCRAPE_CSV, "r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                rows.append(row)

        # Update row yang match
        for row in rows:
            if row.get("username") == username and row.get("video_id") == video_id:
                row["scraped"] = "True"
                row["scraped_at"] = now_timestamp
                break

        # Tulis ulang semua rows
        with open(TO_SCRAPE_CSV, "w", encoding="utf-8", newline="") as f:
            if rows:
                writer = csv.DictWriter(f, fieldnames=rows[0].keys())
                writer.writeheader()
                writer.writerows(rows)
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
    selectors = ['[data-e2e="comment-count"]', 'strong[data-e2e="comment-count"]']
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
    selector_patterns = [
        '[class*="TUXButton-label"]',
        'p[class*="TUXButton-label"]',
        'span[class*="TUXButton-label"]',
        'div[class*="TUXButton-label"]',
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
            try:
                if not await elem.is_visible():
                    continue
                text = (await elem.text_content() or "").strip()
                if not text or not is_view_replies_button_text(text):
                    continue
                box = await elem.bounding_box()
                pos_key = (round(box["x"]), round(box["y"])) if box else None
                if pos_key and pos_key in seen_positions:
                    continue
                if pos_key:
                    seen_positions.add(pos_key)
                try:
                    await elem.scroll_into_view_if_needed(timeout=2000)
                    await elem.click(timeout=3000)
                    clicked += 1
                except Exception:
                    try:
                        await elem.evaluate("el => el.click()")
                        clicked += 1
                    except Exception:
                        pass
                if clicked > 0:
                    await asyncio.sleep(random.uniform(0.5, 1))
            except Exception:
                continue
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
    except Exception:
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
        'text="Drag the slider"',
        'text="Drag the slider to fit the puzzle"',
        '[id*="captcha"]',
        '[class*="captcha"]',
        'iframe[src*="captcha"]',
        'img[alt="Captcha" i]',
        'div[class^="cap-"]',
        'div[class*=" cap-"]',
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
                print(f"   ✅ [{worker_label}] Captcha/login wall hilang")
                stop_warning()
                if alert_sent:
                    send_telegram_alert(f"✅ [{worker_label}] Captcha solved!\n{context_label}")
                await asyncio.sleep(random.uniform(2, 4))
            return True

        now = asyncio.get_event_loop().time()
        remaining = int(timeout - (now - start))

        if not was_found:
            print(f"\n   🔒 [{worker_label}] CAPTCHA TERDETEKSI !!!")
            play_warning()

        if last_alert_at is None or (now - last_alert_at) >= ALERT_REPEAT_EVERY:
            send_telegram_alert(f"⚠️ [{worker_label}] CAPTCHA DETECTED!\n{context_label}")
            alert_sent = True
            last_alert_at = now

        was_found = True
        print(f"   ⏳ [{worker_label}] Waiting... ({remaining}s)", end="\r")
        await asyncio.sleep(3)

    print(f"\n   ❌ [{worker_label}] TIMEOUT!")
    stop_warning()
    raise Exception(f"[{worker_label}] Captcha timeout")


async def pause_for_manual_captcha_solve(page, timeout=600, context_label="", worker_label=""):
    print(f"\n   🔒 [{worker_label}] CAPTCHA DI TENGAH PROSES!!!")
    play_warning()
    send_telegram_alert(f"⚠️ [{worker_label}] CAPTCHA (mid-process)!\n{context_label}")

    start = asyncio.get_event_loop().time()
    while asyncio.get_event_loop().time() - start < timeout:
        found, _ = await quick_captcha_check(page)
        if not found:
            print(f"   ✅ [{worker_label}] Captcha solved!")
            stop_warning()
            await asyncio.sleep(random.uniform(2, 4))
            return True
        remaining = int(timeout - (asyncio.get_event_loop().time() - start))
        print(f"   ⏳ [{worker_label}] Waiting... ({remaining}s)", end="\r")
        await asyncio.sleep(3)

    print(f"\n   ❌ [{worker_label}] TIMEOUT!")
    stop_warning()
    raise Exception(f"[{worker_label}] Captcha timeout")


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

    if expected_count > 0:
        target_count = min(MAX_COMMENTS_PER_VIDEO, max(MIN_COMMENTS_TARGET, int(expected_count * 0.5)))
        print(f"      🎯 [{worker_label}] Target: {target_count}")
    else:
        target_count = MIN_COMMENTS_TARGET

    comment_container = None
    container_selectors = [
        '[data-e2e="comment-list"]',
        '[data-testid="comment-list-container"]',
        '[class*="CommentList"]',
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
        return {"total_comments_loaded": 0, "total_comments_available": 0, "comments": [], "replies": [], "expected_comment_count": expected_count}

    print(f"      📜 [{worker_label}] Scrolling...")

    scroll_target = await find_actual_scroll_target(page, comment_container)

    no_new_streak = 0
    prev_count = 0

    for scroll_attempt in range(MAX_SCROLL_ATTEMPTS):
        wheel_ok = await wheel_scroll_element(page, scroll_target, delta_y=random.randint(500, 800))
        if not wheel_ok:
            await wheel_scroll_element(page, comment_container, delta_y=random.randint(500, 800))

        await asyncio.sleep(random.uniform(*DELAY_BETWEEN_SCROLL))

        if scroll_attempt % 3 == 0:
            is_captcha, _ = await quick_captcha_check(page)
            if is_captcha:
                try:
                    await pause_for_manual_captcha_solve(page, timeout=600, context_label=f"Video {video_id[:10]}", worker_label=worker_label)
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
            print(f"      ✅ [{worker_label}] Target: {current_count}/{target_count}")
            break

        if current_count >= MAX_COMMENTS_PER_VIDEO:
            print(f"      ✅ [{worker_label}] Max: {current_count}")
            break

        if no_new_streak >= 12 and scroll_attempt >= 8:
            print(f"      🔚 [{worker_label}] Stop at {current_count}")
            break

    comments_loaded = count_unique_comments(comments_cache)

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
async def process_video_comments(page, username, video_id, comments_cache, worker_label=""):
    result = await scrape_comments_from_video(page, username, video_id, comments_cache, worker_label=worker_label)

    if not result or not result["comments"]:
        print(f"      ⚠️ [{worker_label}] Gak ada comments")
        return 0

    # Save comments
    rows = []
    for comment_data in result["comments"][:MAX_COMMENTS_PER_VIDEO]:
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

    if rows:
        await append_comment_rows_locked(rows)
        print(f"      💾 [{worker_label}] Saved {len(rows)} comments")

    # Save replies
    reply_rows = []
    if result.get("replies"):
        for idx, reply_data in enumerate(result["replies"]):
            reply_id = f"reply_{video_id}_{idx}"
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

    if reply_rows:
        await append_comment_rows_locked(reply_rows)
        print(f"      💾 [{worker_label}] Saved {len(reply_rows)} replies")

    # Update stats
    expected = result.get("expected_comment_count", 0)
    total_scraped = len(rows) + len(reply_rows)
    coverage_pct = round((total_scraped / expected * 100), 1) if expected > 0 else ""

    await write_stats_row_locked({
        "username": username,
        "video_id": video_id,
        "total_comments_loaded": result["total_comments_loaded"],
        "total_comments_available": result["total_comments_available"],
        "scraped_comments_count": total_scraped,
        "expected_comment_count": expected,
        "coverage_pct": coverage_pct,
        "scraped_at": datetime.now().isoformat(),
    })

    # ⭐ UPDATE to_scrape.csv: mark as scraped
    now_timestamp = datetime.now().isoformat()
    await update_to_scrape_csv_locked(username, video_id, now_timestamp)
    print(f"      ✅ [{worker_label}] Marked as scraped in to_scrape.csv")

    return total_scraped


# ============================================================
# WORKER
# ============================================================
async def run_worker(p, worker_label, profile_dir, cookies_path, video_list):
    cookies = load_cookies_from_json(cookies_path, label=worker_label)
    if not cookies:
        print(f"❌ [{worker_label}] No cookies")
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
    except Exception:
        pass

    comments_cache = []
    page.on("response", make_comments_response_listener(comments_cache))

    total_scraped = 0

    try:
        for idx, video_data in enumerate(video_list, start=1):
            username = video_data.get("username")
            video_id = video_data.get("video_id")

            print(f"\n{'=' * 60}")
            print(f"[{worker_label}] [{idx}/{len(video_list)}] @{username}")
            print(f"{'=' * 60}")

            try:
                n_comments = await process_video_comments(
                    page, username, video_id, comments_cache, worker_label=worker_label
                )
                total_scraped += n_comments

                if idx % DELAY_EVERY_N_VIDEOS == 0:
                    long_delay = random.uniform(*DELAY_AFTER_N_VIDEOS)
                    print(f"\n   🌙 [{worker_label}] Cooldown {long_delay:.0f}s...")
                    await asyncio.sleep(long_delay)
                else:
                    delay = random.uniform(*DELAY_BETWEEN_VIDEO)
                    await asyncio.sleep(delay)

            except Exception as e:
                print(f"   ❌ [{worker_label}] Error: {e}")
                continue

    finally:
        await context.close()

    print(f"\n✅ [{worker_label}] SELESAI: {total_scraped} comments")
    return total_scraped


# ============================================================
# MAIN
# ============================================================
async def main():
    videos = load_to_scrape_videos()
    if not videos:
        print("❌ Gak ada video pending")
        return

    print(f"\n📊 Summary:")
    print(f"   Total pending: {len(videos)}")

    # Split ke 2 worker per influencer
    influencer_videos = defaultdict(list)
    for v in videos:
        influencer_videos[v.get("username")].append(v)

    influencer_list = list(influencer_videos.keys())
    random.shuffle(influencer_list)
    half = len(influencer_list) // 2
    influencers_worker1 = set(influencer_list[:half])
    influencers_worker2 = set(influencer_list[half:])

    videos_worker1 = [v for v in videos if v.get("username") in influencers_worker1]
    videos_worker2 = [v for v in videos if v.get("username") in influencers_worker2]

    print(f"   Worker 1: {len(videos_worker1)} videos")
    print(f"   Worker 2: {len(videos_worker2)} videos")

    send_telegram_alert(f"🚀 Scraping mulai\nWorker 1: {len(videos_worker1)}\nWorker 2: {len(videos_worker2)}")

    async with async_playwright() as p:
        results = await asyncio.gather(
            run_worker(p, "WORKER-1", PROFILE_DIR_1, COOKIES_FILE_1, videos_worker1),
            run_worker(p, "WORKER-2", PROFILE_DIR_2, COOKIES_FILE_2, videos_worker2),
            return_exceptions=True,
        )

    total = sum(r for r in results if not isinstance(r, Exception))

    print(f"\n✅ SELESAI: {total} total comments")
    send_telegram_alert(f"✅ Scraping selesai!\nTotal: {total} comments")


if __name__ == "__main__":
    asyncio.run(main())