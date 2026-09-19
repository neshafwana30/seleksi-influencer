"""
07_SCRAPE_COMMENTS.py
=====================
Scrape komentar dari TikTok videos (dari health_videos.csv hasil klasifikasi)

Strategi:
1. Load health_videos.csv (hasil klasifikasi step sebelumnya)
2. Navigate ke video, buka comment section
3. Scroll comments secara natural (mirip user asli membaca)
4. Extract dari network JSON (bukan DOM) - sama seperti metadata scraping
5. Save ke CSV dengan resume capability

Aturan human-like:
- Delay random antar video (30-60 detik)
- Delay random saat scroll comments (2-4 detik per scroll)
- Jangan load semua comments, cukup top N (misal 100-200)
- Deteksi captcha, tunggu user solve
- Telegram alert kalau ada issue
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

# Input: health videos dari klasifikasi
INPUT_HEALTH_CSV = DATA_DIR / "health_videos.csv"

# Output: comments — di folder 05_scrap_komentar (BEDA dari folder input)
OUTPUT_BASE_DIR = Path(r"01_pengumpulan_data/05_scrap_komentar")
OUTPUT_DIR = OUTPUT_BASE_DIR / "hasil_07_comments"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_COMMENTS = OUTPUT_DIR / "comments.csv"
OUTPUT_COMMENTS_STATS = OUTPUT_DIR / "comments_stats.csv"

# Browser profile & cookies (dari apply_cookies.py)
PROFILE_DIR = DATA_DIR / "tiktok_browser_profile"
COOKIES_FILE = DATA_DIR.parent / "00_akun_playwright" / "tiktok_cookies.json"  # Path: 01_pengumpulan_data/00_akun_playwright/tiktok_cookies.json
WARNING_MP3 = DATA_DIR.parent / "03_videoid_perakun" / "warning.mp3"  # Path: 01_pengumpulan_data/03_videoid_perakun/warning.mp3

print(f"📁 Path configuration:")
print(f"   Input CSV: {INPUT_HEALTH_CSV}")
print(f"   Output: {OUTPUT_DIR}")
print(f"   Profile: {PROFILE_DIR}")
print(f"   Cookies: {COOKIES_FILE}")

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
# KONFIGURASI SCRAPING (HUMAN-LIKE)
# ============================================================
DELAY_BETWEEN_VIDEO = (30, 60)           # Delay antar video
DELAY_BETWEEN_SCROLL = (2, 4)            # ⭐ DIKEMBALIKAN ke 2-4s (1-2s kemarin kena WAF block!)
DELAY_EVERY_N_VIDEOS = 25                # ⭐ TURUN dari 50 → 25 (cooldown lebih sering)
DELAY_AFTER_N_VIDEOS = (5 * 60, 10 * 60) # Cooldown duration (5-10 menit)
MAX_COMMENTS_PER_VIDEO = 200             # Max comments per video (jangan greedy)
MAX_SCROLL_ATTEMPTS = 120                # ⭐ INCREASED: 60 → 120 (perlu lebih banyak buat capai target + expand replies)
COMMENTS_REQUEST_TIMEOUT = 15000

NETWORK_STATS_WAIT_TIMEOUT = 8.0
NETWORK_STATS_POLL_INTERVAL = 0.25

REALISTIC_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)

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


# ============================================================
# AUDIO ALERT
# ============================================================
def play_warning():
    if not os.path.exists(WARNING_MP3):
        print(f"   ⚠️ File {WARNING_MP3} gak ketemu")
        return
    try:
        pygame.mixer.init()
        pygame.mixer.music.load(str(WARNING_MP3))
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
# HELPER: LOAD COOKIES
# ============================================================
def load_cookies_from_json():
    """Load cookies dari tiktok_cookies.json"""
    cookies_file = DATA_DIR.parent / "00_akun_playwright" / "tiktok_cookies.json"
    
    if not cookies_file.exists():
        print(f"⚠️  Cookies file not found: {cookies_file}")
        print(f"   Harusnya ada di: 01_pengumpulan_data/00_akun_playwright/tiktok_cookies.json")
        print(f"   Run: python apply_cookies_script.py dulu!")
        return None
    
    try:
        with open(cookies_file, "r", encoding="utf-8") as f:
            cookies = json.load(f)
        print(f"✅ Loaded {len(cookies)} cookies")
        return cookies
    except Exception as e:
        print(f"❌ Error loading cookies: {e}")
        return None

# ============================================================
# HELPER: LOAD DATA
# ============================================================
def load_health_videos():
    """Load health videos dari klasifikasi results"""
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
    """Load existing comments (untuk skip yang sudah scraped)"""
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
    """Load video stats (tahu sudah discrape atau belum)"""
    if not OUTPUT_COMMENTS_STATS.exists():
        return {}
    
    stats = {}
    with open(OUTPUT_COMMENTS_STATS, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            key = (row["username"], row["video_id"])
            stats[key] = row
    
    return stats


def append_comment_rows(rows):
    """Append comments ke CSV"""
    if not rows:
        return
    
    file_exists = OUTPUT_COMMENTS.exists()
    with open(OUTPUT_COMMENTS, "a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=COMMENT_FIELDS)
        if not file_exists:
            writer.writeheader()
        writer.writerows(rows)
        f.flush()
        os.fsync(f.fileno())


def append_stats_row(row):
    """Append video stats ke CSV"""
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
    """Recursive search untuk nemuin blok comments di JSON"""
    results = []
    if _depth > 15:
        return results

    if isinstance(obj, dict):
        # TikTok comments biasanya ada di: comments, item, comments.data, dll
        # Termasuk endpoint reply (/api/comment/list/reply/) yang juga pakai key "comments"
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
                            "is_reply": False,  # ← Mark as top-level
                        })
                except Exception:
                    continue
        
        for value in obj.values():
            results.extend(find_comments_blocks(value, _depth + 1))

    elif isinstance(obj, list):
        for item in obj:
            results.extend(find_comments_blocks(item, _depth + 1))

    return results


# ============================================================
# EXPECTED COMMENT COUNT (target buat tau kapan scroll cukup)
# ============================================================
def count_unique_comments(comments_cache):
    """
    Hitung comment UNIQUE dari cache berdasarkan comment_id.
    PENTING: jangan pakai len(comments_cache) langsung! Network response yang
    ke-fetch berkali-kali bisa include comment yang sama berulang, jadi raw list
    length bisa jauh lebih besar dari jumlah comment yang beneran unique.
    """
    seen = set()
    for c in comments_cache:
        cid = c.get("comment_id")
        if cid:
            seen.add(cid)
    return len(seen)


def parse_count_string(text):
    """
    Parse angka TikTok style ke int: '59' -> 59, '15.2K' -> 15200, '1.2M' -> 1200000
    """
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
    """
    Ambil jumlah komentar sebenarnya dari video (sama selector kayak di metadata scraping).
    Dipakai sebagai TARGET biar tau kapan scroll udah "cukup" (bukan cuma nebak dari
    "no new comments for N scrolls" doang).
    """
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
    """
    Match SPESIFIK tombol "View X replies" / "Lihat X balasan" (buat EXPAND
    balasan yang udah ada) — BUKAN tombol "Reply" polos (buat COMPOSE balasan
    baru, yang buka text box kosong). Dua-duanya sama-sama ngandung substring
    "repl", makanya harus di-bedain dengan hati-hati!

    Contoh yang HARUS match: "View 2 replies", "View 15 replies", "Lihat 3 balasan"
    Contoh yang HARUS DI-SKIP: "Reply", "Balas" (tombol compose, single word doang,
    gak ada angka atau kata "view"/"lihat" di depannya)
    """
    if not text:
        return False
    t = text.strip().lower()

    # Exclude eksplisit: tombol compose reply biasanya CUMA "reply" atau "balas"
    # doang (persis, tanpa embel-embel lain)
    if t in ("reply", "balas", "membalas", "respond"):
        return False
    if "hide" in t or "sembunyikan" in t:
        return False

    # Harus ada ANGKA yang nempel sama kata repl/balasan (paling reliable indicator
    # dari "View X replies"), ATAU eksplisit ada kata "view"/"lihat" bareng repl/balasan
    has_number_and_repl = bool(re.search(r'\d+\s*(repl|balasan)', t))
    has_view_repl = ("view" in t and "repl" in t)
    has_lihat_balasan = ("lihat" in t and "balasan" in t)

    return has_number_and_repl or has_view_repl or has_lihat_balasan


async def expand_visible_reply_buttons(page, verbose=True, max_clicks=3):
    """
    Klik tombol "View X replies" / "Lihat X balasan" yang lagi visible.
    Coba beberapa selector pattern (struktur DOM TikTok kadang beda-beda),
    dengan JS-click fallback kalau .click() biasa gagal (misal element
    ke-intercept sama elemen lain di atasnya).
    
    max_clicks: batasi berapa banyak klik per pemanggilan (default 3) — biar
    gak rapid-fire klik puluhan tombol sekaligus dalam waktu singkat, yang
    bisa kelihatan sangat bot-like dan berisiko kena flag WAF.
    
    PENTING: filter teks pakai is_view_replies_button_text() yang SPESIFIK,
    BUKAN cuma cek substring "repl" — soalnya tombol "Reply" (compose balasan
    baru) juga ngandung substring itu dan HARUS di-exclude!
    """
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
                
                # Hindari klik elemen yang sama 2x (kalau ke-match dari beberapa selector pattern)
                box = await elem.bounding_box()
                pos_key = (round(box["x"]), round(box["y"])) if box else None
                if pos_key and pos_key in seen_positions:
                    continue
                if pos_key:
                    seen_positions.add(pos_key)
                
                # Coba klik normal dulu
                click_success = False
                try:
                    await elem.scroll_into_view_if_needed(timeout=2000)
                    await elem.click(timeout=3000)
                    click_success = True
                except Exception as click_err:
                    if verbose:
                        print(f"        ⚠️ Normal click gagal pada '{text[:30]}': {str(click_err)[:60]}")
                    # Fallback: dispatch click event langsung via JS
                    try:
                        await elem.evaluate("el => el.click()")
                        click_success = True
                        if verbose:
                            print(f"        🖱️ JS-click fallback berhasil untuk '{text[:30]}'")
                    except Exception as js_err:
                        if verbose:
                            print(f"        ❌ JS-click fallback juga gagal: {str(js_err)[:60]}")
                
                if click_success:
                    clicked += 1
                    if verbose:
                        print(f"        ✅ Clicked: '{text[:40]}'")
                    await asyncio.sleep(random.uniform(0.5, 1))
                    
            except Exception as e:
                if verbose:
                    print(f"        ⚠️ Error processing candidate: {str(e)[:60]}")
                continue
    
    if verbose:
        print(f"      🔍 Reply-button scan: {candidates_checked} candidates checked, "
              f"{matched_texts} matched text filter, {clicked} berhasil di-klik")
    
    return clicked


def make_comments_response_listener(comments_cache):
    """Async function untuk capture comments dari network response"""
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
    """
    Extract visible replies dari DOM (yang sudah ter-render)
    Tidak klik "show replies" - hanya ambil yang kelihatan
    """
    try:
        # Tunggu sebentar biar DOM settled
        await asyncio.sleep(0.5)
        
        # Cari semua reply elements dalam comment container
        reply_selectors = [
            '[data-e2e="comment-item"][class*="reply"]',  # TikTok reply item
            'div[class*="ReplyItem"]',                     # Reply item component
            '[class*="CommentReply"]',                     # Comment reply div
        ]
        
        all_replies = []
        
        for selector in reply_selectors:
            try:
                reply_elems = await page.query_selector_all(selector)
                
                for reply_elem in reply_elems:
                    try:
                        # Extract reply text
                        reply_text_elem = await reply_elem.query_selector('[data-e2e="comment-content"]')
                        if not reply_text_elem:
                            reply_text_elem = await reply_elem.query_selector('[class*="content"]')
                        
                        if reply_text_elem:
                            reply_text = await reply_text_elem.text_content()
                            
                            # Extract username
                            username_elem = await reply_elem.query_selector('[data-e2e="comment-username"]')
                            username = ""
                            if username_elem:
                                username = await username_elem.text_content()
                            
                            # Extract likes
                            likes_elem = await reply_elem.query_selector('[data-e2e="comment-like-count"]')
                            likes = "0"
                            if likes_elem:
                                likes = await likes_elem.text_content()
                            
                            if reply_text and len(reply_text) > 3:  # Filter noise
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
    """
    `comment_container` yang ketemu dari selector generik ([class*="CommentList"] dll)
    bisa jadi cuma WRAPPER div, BUKAN elemen yang beneran overflow-y:auto/scroll.

    Cari ke DUA arah:
    - Descendant (anak): kalau list-nya virtualized dan scroll ada di div dalem
    - Ancestor (bapak): kalau CommentList cuma list polos, dan yang beneran scroll
      adalah PANEL pembungkusnya (paling sering kasusnya di TikTok's comment panel)

    Fallback ke container asli kalau gak ketemu.
    """
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
                // 1) Cek ancestor dulu (parent chain) - paling sering ini yang bener
                let ancestor = el.parentElement;
                let depth = 0;
                while (ancestor && depth < 8) {
                    if (isScrollable(ancestor)) return ancestor;
                    ancestor = ancestor.parentElement;
                    depth++;
                }
                // 2) Kalau gak ketemu, cek descendant
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
                // 3) Cek diri sendiri
                if (isScrollable(el)) return el;
                return el;  // fallback
            }
        """)
        elem = handle.as_element()
        return elem if elem else container
    except Exception:
        return container


async def wheel_scroll_element(page, elem, delta_y=650):
    """
    Scroll pakai REAL mouse wheel event di atas posisi elemen — lebih reliable
    dibanding manipulasi scrollTop via JS, karena virtualized list (kayak yang
    TikTok pakai) sering cuma dengerin wheel/scroll event ASLI dari browser,
    bukan perubahan scrollTop programmatic (yang keliatan dari evidence:
    scrollTop stuck di 0 meski scrollHeight udah 2668px).

    Return True kalau berhasil dispatch wheel event.
    """
    try:
        box = await elem.bounding_box()
        if not box or box["width"] <= 0 or box["height"] <= 0:
            return False
        # Posisi mouse di tengah elemen (dengan sedikit variasi biar natural)
        x = box["x"] + box["width"] / 2 + random.uniform(-20, 20)
        y = box["y"] + box["height"] / 2 + random.uniform(-20, 20)
        await page.mouse.move(x, y)
        await asyncio.sleep(random.uniform(0.1, 0.3))
        await page.mouse.wheel(0, delta_y)
        return True
    except Exception:
        return False


async def get_scroll_diagnostics(elem):
    """Ambil scrollTop/scrollHeight buat logging/debug, gak critical kalau gagal."""
    try:
        info = await elem.evaluate("el => ({top: el.scrollTop, sh: el.scrollHeight, ch: el.clientHeight})")
        return info.get("top", 0), info.get("sh", 0)
    except Exception:
        return 0, 0




async def wait_for_comments_loaded(page, comments_cache, timeout=NETWORK_STATS_WAIT_TIMEOUT,
                                    min_expected=5):
    """Wait sampai comments ter-load dari network"""
    initial_count = len(comments_cache)
    elapsed = 0.0
    
    while elapsed < timeout:
        if len(comments_cache) > initial_count + min_expected:
            await asyncio.sleep(0.5)
            return True
        await asyncio.sleep(NETWORK_STATS_POLL_INTERVAL)
        elapsed += NETWORK_STATS_POLL_INTERVAL
    
    return len(comments_cache) > initial_count


# ============================================================
# LOGIN WALL / CAPTCHA CHECK (IMPROVED)
# ============================================================
async def wait_for_login_wall_clear(page, timeout=600, context_label="", require_manual_confirm=False):
    """
    Wait untuk captcha/login hilang - IMPROVED VERSION dengan better detection
    
    Args:
        page: Playwright page object
        timeout: Berapa lama tunggu (default 10 menit)
        context_label: Context untuk alert message
        require_manual_confirm: Kalau True, tunggu user confirm manual (button click) sebelum lanjut
    
    Return: True jika clear, False jika timeout
    """
    
    # Lebih comprehensive captcha selectors
    blocker_selectors = [
        # Login/Captcha modals
        'text="Log in to TikTok"',
        '[data-e2e="login-modal"]',
        'div[role="dialog"]:has-text("Log in")',
        'div[role="dialog"]:has-text("Continue with")',
        
        # Captcha specific
        'text="Drag the slider"',
        'text="Drag the slider to fit the puzzle"',
        'text="puzzle"',
        'text=/verify.*puzzle/i',
        '[id*="captcha"]',
        '[class*="captcha"]',
        '[class*="Captcha"]',
        '[class*="puzzle"]',
        'iframe[src*="captcha"]',
        'img[alt="Captcha" i]',
        'img[alt*="captcha" i]',
        '.TUXModal:has-text("captcha")',
        '.TUXModal:has-text("Captcha")',
        '[class*="captcha-verify"]',
        '[class*="captcha_verify"]',
        'div[class*="TUXModal"] iframe',
        
        # ⭐ NEW: TikTok's newer captcha uses "cap-" prefixed class names
        # (e.g. class="cap-flex cap-flex-col cap-justify-center cap-items-center")
        'div[class^="cap-"]',
        'div[class*=" cap-"]',
        
        # Generic verify/check
        r'text=/verify\s*to\s*continue/i',
        r'text=/select\s*2\s*similar\s*images/i',
        r'text=/tap\s*the\s*objects/i',
        r'text=/rotate\s*the\s*image/i',
        r'text=/verify\s*you\s*are\s*human/i',
        'button:has-text("Verify")',
        'button:has-text("verify")',
    ]
    
    ALERT_REPEAT_EVERY = 30
    start = asyncio.get_event_loop().time()
    was_found = False
    alert_sent = False
    last_alert_at = None

    while asyncio.get_event_loop().time() - start < timeout:
        found = False
        matched_selector = None
        
        # Check each selector
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
                print("   ✅ Captcha/login wall hilang, lanjut.")
                stop_warning()
                if alert_sent:
                    send_telegram_alert(f"✅ Captcha solved! Lanjut scraping! 🚀\n{context_label}".strip())
                await asyncio.sleep(random.uniform(2, 4))
            return True

        now = asyncio.get_event_loop().time()
        elapsed = now - start
        remaining = int(timeout - elapsed)

        if not was_found:
            print("\n" + "=" * 70)
            print("   🔒 🔊 CAPTCHA / LOGIN WALL TERDETEKSI !!!")
            print(f"   📍 Selector matched: {matched_selector}")
            print(f"   ⏰ Timeout dalam {remaining} detik ({remaining / 60:.1f} menit)")
            print(f"   📱 **PLEASE SOLVE MANUALLY DI BROWSER WINDOW!**")
            print("=" * 70)
            play_warning()

        if last_alert_at is None or (now - last_alert_at) >= ALERT_REPEAT_EVERY:
            send_telegram_alert(
                "⚠️ CAPTCHA DETECTED!\n"
                f"{context_label}\n"
                f"Waktu: {datetime.now().strftime('%H:%M:%S')}\n"
                f"⏳ Sisa waktu: {remaining}s ({remaining / 60:.1f} menit)\n\n"
                "📱 Buka Chrome Remote Desktop di HP buat solve.\n"
                "Script sedang PAUSE & menunggu Anda solve!"
            )
            alert_sent = True
            last_alert_at = now

        was_found = True
        print(f"   ⏳ Waiting untuk solve... ({remaining}s remaining)", end="\r")
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
    raise Exception("Captcha/login wall gak di-solve dalam 10 menit. Program dihentikan.")


# ============================================================
# QUICK CAPTCHA CHECK (untuk dipanggil berkali-kali di tengah loop)
# ============================================================
CAPTCHA_QUICK_SELECTORS = [
    'img[alt="Captcha" i]',
    'img[alt*="captcha" i]',
    'text="Drag the slider to fit the puzzle"',
    'text="Drag the slider"',
    '[id*="captcha"]',
    '[class*="captcha"]',
    '[class*="Captcha"]',
    'div[class^="cap-"]',           # ⭐ NEW: TikTok captcha uses "cap-" prefix classes!
    'div[class*=" cap-"]',          # ⭐ NEW: catch classes like "cap-flex cap-absolute"
    'iframe[src*="captcha"]',
    '.TUXModal:has-text("captcha")',
    '.TUXModal:has-text("Captcha")',
]


async def quick_captcha_check(page):
    """
    Quick check (single pass, no waiting) - return True kalau captcha terdeteksi.
    Dipakai untuk check berkala DI TENGAH proses scroll/loop, bukan hanya di awal.
    """
    for sel in CAPTCHA_QUICK_SELECTORS:
        try:
            elem = await page.query_selector(sel)
            if elem and await elem.is_visible():
                return True, sel
        except Exception:
            continue
    return False, None


async def pause_for_manual_captcha_solve(page, timeout=600, context_label=""):
    """
    Dipanggil begitu quick_captcha_check() return True.
    Sama seperti wait_for_login_wall_clear tapi dipanggil dari tengah proses lain.
    """
    print("\n" + "=" * 70)
    print("   🔒 🔊 CAPTCHA TERDETEKSI DI TENGAH PROSES!!!")
    print(f"   ⏰ Timeout dalam {timeout} detik ({timeout / 60:.1f} menit)")
    print(f"   📱 **PLEASE SOLVE MANUALLY DI BROWSER WINDOW!**")
    print("=" * 70)
    play_warning()
    send_telegram_alert(
        "⚠️ CAPTCHA DETECTED (mid-process)!\n"
        f"{context_label}\n"
        f"Waktu: {datetime.now().strftime('%H:%M:%S')}\n"
        "📱 Buka browser & solve manual sekarang!\n"
        "Script PAUSE menunggu Anda."
    )

    start = asyncio.get_event_loop().time()
    while asyncio.get_event_loop().time() - start < timeout:
        found, _ = await quick_captcha_check(page)
        if not found:
            print("   ✅ Captcha solved! Melanjutkan proses...")
            stop_warning()
            send_telegram_alert(f"✅ Captcha solved! Lanjut scraping! 🚀\n{context_label}")
            await asyncio.sleep(random.uniform(2, 4))
            return True

        remaining = int(timeout - (asyncio.get_event_loop().time() - start))
        print(f"   ⏳ Waiting untuk solve... ({remaining}s remaining)", end="\r")
        await asyncio.sleep(3)

    print("\n   ❌ TIMEOUT - Captcha gak di-solve dalam waktu yang ditentukan!")
    stop_warning()
    send_telegram_alert(f"❌ CAPTCHA TIMEOUT (mid-process)!\n{context_label}\nProgram dihentikan.")
    raise Exception("Captcha timeout di tengah proses scraping")


# ============================================================
# IP / WAF BLOCK DETECTION (BEDA DARI CAPTCHA — jauh lebih serius!)
# ============================================================
async def check_ip_blocked(page):
    """
    Detect kalau kena block level WAF/server (HTTP 403 "Access denied"),
    BUKAN captcha. Ini artinya IP/session ke-flag sebagai bot oleh Akamai
    (WAF yang dipakai TikTok), dan butuh cooldown jauh lebih lama dari captcha.
    """
    try:
        # Cek dari page title / content, bukan cuma selector — soalnya ini
        # halaman error generik dari WAF, bukan komponen React TikTok
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


async def handle_ip_block(page, context_label="", cooldown_minutes=45):
    """
    Dipanggil begitu check_ip_blocked() return True. Ini BUKAN captcha yang
    bisa langsung di-solve — ini block dari WAF (Akamai) yang biasanya perlu
    waktu buat "reda" (cooldown), bukan interaksi manual.

    Kasih PAUSE PANJANG (default 45 menit) sebelum retry, karena kalau langsung
    di-retry terus-terusan pas lagi ke-block, kemungkinan malah bikin block-nya
    makin lama / makin parah.
    """
    print("\n" + "=" * 70)
    print("   🚫🚫🚫 IP/SESSION KE-BLOCK OLEH TIKTOK (HTTP 403)! 🚫🚫🚫")
    print(f"   ⚠️  Ini BUKAN captcha biasa — ini block level WAF (Akamai).")
    print(f"   ⚠️  Kemungkinan besar karena request terlalu agresif/cepat.")
    print(f"   😴 PAUSE {cooldown_minutes} menit sebelum retry...")
    print(f"   💡 Kalau masih ke-block setelah pause ini, STOP script manual")
    print(f"      dan tunggu lebih lama (beberapa jam) sebelum coba lagi.")
    print("=" * 70)
    play_warning()
    send_telegram_alert(
        "🚫 IP/SESSION BLOCKED (HTTP 403)!\n"
        f"{context_label}\n"
        f"Waktu: {datetime.now().strftime('%H:%M:%S')}\n"
        f"Ini block WAF (Akamai), BUKAN captcha biasa.\n"
        f"Script PAUSE {cooldown_minutes} menit.\n"
        "Kalau masih kena block setelah ini, sebaiknya STOP manual\n"
        "dan tunggu beberapa jam sebelum lanjut lagi."
    )
    stop_warning()
    await asyncio.sleep(cooldown_minutes * 60)
    print(f"\n   ⏰ Cooldown {cooldown_minutes} menit selesai, mencoba lagi...")


# ============================================================
# SCRAPE COMMENTS DARI VIDEO
# ============================================================
async def scrape_comments_from_video(page, username, video_id, existing_comments, 
                                      comments_cache):
    """
    Navigate ke video, KLIK COMMENTS TAB, scroll comments section, extract dari network JSON
    """
    video_url = f"https://www.tiktok.com/@{username}/video/{video_id}"
    
    print(f"\n   🎥 Scrape comments: {video_id[:10]}...")
    
    # Navigate ke video
    try:
        await page.goto(video_url, wait_until="load", timeout=30000)
        await asyncio.sleep(random.uniform(2, 3))
    except Exception as e:
        print(f"      ❌ Gagal navigate: {e}")
        return None
    
    # ⭐⭐⭐ CHECK IP/WAF BLOCK DULUAN — sebelum apapun lain! ⭐⭐⭐
    # Ini beda dari captcha, jadi harus di-cek terpisah dan paling awal,
    # soalnya kalau ke-block, semua selector lain juga gak bakal ketemu apa-apa.
    max_block_retries = 3
    for retry in range(max_block_retries):
        is_blocked = await check_ip_blocked(page)
        if not is_blocked:
            break
        await handle_ip_block(page, context_label=f"Video {video_id[:10]} (retry {retry + 1}/{max_block_retries})")
        # Retry navigate setelah cooldown
        try:
            await page.goto(video_url, wait_until="load", timeout=30000)
            await asyncio.sleep(random.uniform(2, 3))
        except Exception as e:
            print(f"      ❌ Gagal navigate setelah cooldown: {e}")
            return None
    else:
        # Loop habis (3x retry) dan masih ke-block juga
        print(f"      🛑 Masih ke-block setelah {max_block_retries}x retry. "
              f"Menghentikan script — sebaiknya tunggu lebih lama sebelum lanjut.")
        send_telegram_alert(
            f"🛑 STOPPING: Masih ke-block setelah {max_block_retries}x retry cooldown.\n"
            "Sebaiknya tunggu beberapa jam sebelum jalanin script lagi."
        )
        raise Exception(f"Masih ke-block (HTTP 403) setelah {max_block_retries}x retry cooldown")

    # Clear cache
    comments_cache.clear()
    
    # Wait for login wall
    try:
        await wait_for_login_wall_clear(page, timeout=600, context_label=f"Video {video_id}")
    except Exception:
        return None

    # Tunggu video siap
    await asyncio.sleep(random.uniform(2, 3))
    
    # ⭐ CHECK CAPTCHA SEBELUM SCRAPE! (IMPORTANT!)
    print(f"      🔍 Checking untuk captcha...")
    try:
        await wait_for_login_wall_clear(page, timeout=600, context_label=f"Video {video_id[:10]}")
    except Exception as e:
        print(f"      ❌ Captcha error: {e}")
        return None
    
    print(f"      ✅ No captcha detected, safe to proceed")
    
    # ⭐ KLIK COMMENTS TAB/BUTTON DULU! (INI YANG PENTING!)
    print(f"      👉 Clicking Comments tab...")
    comment_clicked = False
    
    comment_selectors = [
        '[data-e2e="comment-icon"]',      # Comment icon button
        '[aria-label="Comments"]',        # Aria label
        'button[aria-label*="comment" i]',  # Comment button
        'div[role="button"]:has-text("Comments")',  # Comments text button
        '[data-testid="comment"]',        # Data testid
        'svg[data-e2e="icon-comment"]',  # Comment SVG icon
        'button:has-text("Comments")',   # Button with Comments text
    ]
    
    for selector in comment_selectors:
        try:
            elem = await page.query_selector(selector)
            if elem and await elem.is_visible():
                print(f"      ✅ Found comments button: {selector}")
                await elem.click(timeout=5000)
                comment_clicked = True
                await asyncio.sleep(random.uniform(1, 2))
                break
        except Exception:
            continue
    
    if not comment_clicked:
        print(f"      ⚠️ Could not find/click comments button, trying alternative method...")
        # Try keyboard shortcut atau scroll ke comment section
        try:
            await page.evaluate("window.scrollBy(0, 300)")
            await asyncio.sleep(1)
        except Exception:
            pass
    
    # Tunggu comments section ter-load
    await asyncio.sleep(random.uniform(1, 2))
    
    # ⭐ AMBIL TARGET JUMLAH KOMENTAR (dari comment-count badge, sama kayak metadata scraping)
    expected_count = await get_expected_comment_count(page)
    if expected_count > 0:
        target_count = max(1, int(expected_count * 0.9))  # Toleransi 10%
        print(f"      🎯 Target: {expected_count} comments (min acceptable: {target_count} / 90%)")
    else:
        target_count = 0
        print(f"      ⚠️ Gak bisa detect expected comment count, pakai stop-logic biasa")
    
    # Cari comment container SETELAH comments di-klik
    print(f"      📜 Looking for comment container...")

    # Scroll comments section untuk load lebih banyak
    comments_loaded = 0
    scroll_attempts = 0

    # Cari comment container (SETELAH comments di-klik)
    comment_container = None
    container_selectors = [
        '[data-e2e="comment-list"]',      # TikTok comment list
        '[data-testid="comment-list-container"]',
        '[class*="CommentList"]',
        'div[class*="DraftCommentPanel"]',
        'div:has(> div[class*="comment-item"])',  # Container with comment items
        '[role="region"]:has-text("Comment")',  # Region with Comment text
        'div[class*="comment-section"]',
    ]

    for sel in container_selectors:
        try:
            container = await page.query_selector(sel)
            if container and await container.is_visible():
                print(f"      ✅ Found comment container: {sel}")
                comment_container = container
                break
        except Exception:
            continue

    if not comment_container:
        # ⭐ Comment container gak ketemu - kemungkinan besar karena CAPTCHA!
        is_captcha, matched_sel = await quick_captcha_check(page)
        if is_captcha:
            print(f"      🔒 CAPTCHA terdeteksi! (selector: {matched_sel})")
            try:
                await pause_for_manual_captcha_solve(
                    page, timeout=600, context_label=f"Video {video_id[:10]} (comment container search)"
                )
            except Exception as e:
                print(f"      ❌ {e}")
                return None
            
            # Setelah captcha solved, coba cari container lagi
            for sel in container_selectors:
                try:
                    container = await page.query_selector(sel)
                    if container and await container.is_visible():
                        print(f"      ✅ Found comment container after captcha solve: {sel}")
                        comment_container = container
                        break
                except Exception:
                    continue

    if not comment_container:
        print(f"      ⚠️ Comment section gak ketemu - mungkin ada captcha atau comments disabled")
        print(f"      💡 Trying to scroll page to find comments...")
        
        # Try scroll page dan look again
        await page.evaluate("window.scrollBy(0, 500)")
        await asyncio.sleep(1)
        
        for sel in container_selectors:
            try:
                container = await page.query_selector(sel)
                if container and await container.is_visible():
                    print(f"      ✅ Found after scroll: {sel}")
                    comment_container = container
                    break
            except Exception:
                continue
        
        if not comment_container:
            return {
                "total_comments_loaded": 0,
                "total_comments_available": 0,
                "comments": []
            }

    # Scroll comments dengan aggressive strategy
    print(f"      📜 Scrolling comments (aggressive mode, real mouse wheel)...")
    
    # ⭐ Cari elemen scroll yang BENERAN scrollable (cek ancestor DAN descendant)
    # Dipakai buat hover posisi mouse & diagnostic logging, wheel event yang
    # actually scroll-nya (lebih reliable daripada manipulasi scrollTop).
    scroll_target = await find_actual_scroll_target(page, comment_container)
    
    no_new_streak = 0  # Counter untuk consecutive scrolls dengan no new comments
    prev_count = 0
    total_replies_expanded = 0
    scroll_ineffective_streak = 0  # Counter kalau scrollTop gak berubah sama sekali (diagnostic)
    
    # Kalau ada target, kasih toleransi lebih banyak scroll (soalnya perlu expand replies juga)
    # Kalau gak ada target (gagal detect count), pakai logic lama yang lebih konservatif
    no_new_streak_limit = 25 if target_count > 0 else 15
    min_scroll_before_stop = 30 if target_count > 0 else 20
    
    for scroll_attempt in range(MAX_SCROLL_ATTEMPTS):
        # ⭐ REAL MOUSE WHEEL EVENT (bukan manipulasi scrollTop via JS)
        # Ini lebih reliable karena TikTok's virtualized list kemungkinan cuma
        # dengerin wheel/scroll event ASLI, bukan perubahan scrollTop programmatic.
        top_before, height_before = await get_scroll_diagnostics(scroll_target)
        wheel_ok = await wheel_scroll_element(page, scroll_target, delta_y=random.randint(500, 800))
        
        if not wheel_ok:
            # Fallback: coba wheel di comment_container asli kalau scroll_target gagal
            await wheel_scroll_element(page, comment_container, delta_y=random.randint(500, 800))
        
        await asyncio.sleep(random.uniform(*DELAY_BETWEEN_SCROLL))
        
        top_after, height_after = await get_scroll_diagnostics(scroll_target)
        moved = (top_after > top_before) or (height_after > height_before)
        
        if not moved:
            scroll_ineffective_streak += 1
            if scroll_ineffective_streak == 5:
                print(f"      ⚠️ Scroll diagnostic gak berubah 5x (scrollTop={top_after}, "
                      f"scrollHeight={height_after}) — tapi lanjut monitor via comment count growth")
            
            # ⭐ RESCUE: kalau tetap gak efektif sampai 10x, coba strategi lain sekali
            # (window-level scroll + keyboard End) — buat jaga-jaga kalau ternyata
            # comment panel-nya ikut scroll flow halaman utama, bukan div sendiri.
            if scroll_ineffective_streak == 10:
                print(f"      🔧 Mencoba scroll strategy alternatif (window scroll + keyboard End)...")
                try:
                    await page.mouse.wheel(0, 600)
                except Exception:
                    pass
                try:
                    await page.keyboard.press("End")
                except Exception:
                    pass
                await asyncio.sleep(random.uniform(0.5, 1))
        else:
            scroll_ineffective_streak = 0
        
        # ⭐⭐⭐ KEY FIX: CHECK CAPTCHA SETIAP 3 SCROLL! ⭐⭐⭐
        # Captcha bisa muncul KAPAN SAJA saat scroll (triggered by TikTok's bot detection),
        # bukan cuma di awal. Kalau gak dicek di sini, script bakal terus "scroll ke kosong"
        # tanpa sadar ada captcha yang menghalangi (kayak yang terjadi sebelumnya).
        if scroll_attempt % 3 == 0:
            is_captcha, matched_sel = await quick_captcha_check(page)
            if is_captcha:
                print(f"\n      🔒 CAPTCHA terdeteksi saat scroll ke-{scroll_attempt + 1}! (selector: {matched_sel})")
                try:
                    await pause_for_manual_captcha_solve(
                        page, timeout=600, 
                        context_label=f"Video {video_id[:10]} (scroll attempt {scroll_attempt + 1})"
                    )
                except Exception as e:
                    print(f"      ❌ {e}")
                    # Return apa yang udah ke-load sejauh ini, jangan buang semua
                    break
                # Reset streak counter setelah captcha solved, kasih kesempatan scroll lagi
                no_new_streak = 0
        
        # ⭐ EXPAND "VIEW X REPLIES" BUTTONS SETIAP 4 SCROLL
        # Ini yang bikin nested replies ke-load (baik ke network cache atau ke DOM)
        if scroll_attempt % 6 == 0:
            count_before_expand = count_unique_comments(comments_cache)
            n_expanded = await expand_visible_reply_buttons(page, verbose=True)
            if n_expanded > 0:
                total_replies_expanded += n_expanded
                # Extra wait biar network request buat fetch replies sempat selesai & ke-capture
                await asyncio.sleep(random.uniform(1, 1.5))
                count_after_expand = count_unique_comments(comments_cache)
                delta = count_after_expand - count_before_expand
                print(f"      💬 Clicked {n_expanded} reply button(s) — comment count: "
                      f"{count_before_expand} → {count_after_expand} (+{delta}) "
                      f"(total buttons clicked: {total_replies_expanded})")
                if delta == 0:
                    print(f"      ⚠️ Klik berhasil tapi comment count gak nambah — "
                          f"kemungkinan network response reply gak ke-capture, atau replies "
                          f"udah ke-hitung sebelumnya di top-level fetch")
        
        # Check current count (UNIQUE, bukan raw list yang bisa kena duplikat!)
        current_count = count_unique_comments(comments_cache)
        
        # Check kalau ada komentar baru
        if current_count > prev_count:
            no_new_streak = 0  # Reset streak
            prev_count = current_count
        else:
            no_new_streak += 1
        
        # Progress report
        if scroll_attempt % 10 == 9:
            target_info = f" (target: {target_count})" if target_count > 0 else ""
            print(f"      ↻ Scrolled {scroll_attempt + 1}x, loaded {current_count} comments{target_info}...")
        
        # ⭐ STOP CONDITION #1: Target tercapai (>= 90% dari expected count)
        if target_count > 0 and current_count >= target_count:
            print(f"      ✅ Target reached! Loaded {current_count}/{expected_count} comments ({current_count/expected_count*100:.0f}%)")
            break
        
        # Stop condition #2: Hard cap
        if current_count >= MAX_COMMENTS_PER_VIDEO:
            print(f"      ✅ Loaded {current_count} comments (max reached)")
            break
        
        # Stop condition #3: Udah mentok (gak ada comment baru dalam waktu lama)
        if no_new_streak >= no_new_streak_limit and scroll_attempt >= min_scroll_before_stop:
            if target_count > 0 and current_count < target_count:
                print(f"      ⚠️ Mentok di {current_count}/{expected_count} comments "
                      f"({current_count/expected_count*100:.0f}%, target 90% gak tercapai) "
                      f"setelah {no_new_streak} scrolls tanpa comment baru")
            else:
                print(f"      🔚 No new comments for {no_new_streak} scrolls (loaded: {current_count})")
            break

    comments_loaded = count_unique_comments(comments_cache)
    print(f"      ✅ Total {comments_loaded} unique comments loaded (raw responses: {len(comments_cache)})")

    # Parse comments dari cache
    unique_comments = {}
    for comment in comments_cache:
        cid = comment.get("comment_id")
        if cid and cid not in unique_comments:
            unique_comments[cid] = comment

    final_comments = list(unique_comments.values())

    # ⭐ EXTRACT REPLIES FROM DOM (juga ambil visible replies)
    print(f"      💬 Extracting visible replies from DOM...")
    dom_replies = []
    try:
        dom_replies = await extract_replies_from_dom_simple(page, comment_container)
        print(f"      ✅ Found {len(dom_replies)} visible replies")
    except Exception as e:
        print(f"      ⚠️ Error extracting replies: {str(e)[:40]}")

    return {
        "total_comments_loaded": len(final_comments),
        "total_comments_available": comments_loaded,
        "comments": final_comments,
        "replies": dom_replies,  # ← Include replies
        "expected_comment_count": expected_count,  # ⭐ NEW: target dari comment-count badge
    }


# ============================================================
# PROCESS VIDEO DAN SAVE COMMENTS
# ============================================================
async def process_video_comments(page, username, video_id, existing_comments,
                                 comments_cache):
    """Process satu video: scrape comments, save ke CSV"""
    
    # Check kalau sudah ada comments
    video_comments = [
        c for c in existing_comments
        if c[0] == username and c[1] == video_id
    ]
    
    if video_comments:
        print(f"      ⏭️  Video {video_id[:10]} sudah ada {len(video_comments)} comments")
        return 0

    # Scrape comments
    result = await scrape_comments_from_video(page, username, video_id, 
                                              existing_comments, comments_cache)
    
    if not result or not result["comments"]:
        print(f"      ⚠️ Gak ada comments ter-load")
        return 0

    # Build rows
    rows = []
    for comment_data in result["comments"][:MAX_COMMENTS_PER_VIDEO]:
        key = (username, video_id, comment_data.get("comment_id"))
        
        if key in existing_comments:
            continue
        
        row = {
            "username": username,
            "video_id": video_id,
            "comment_id": comment_data.get("comment_id", ""),
            "commenter_username": comment_data.get("username", ""),
            "comment_text": comment_data.get("text", ""),
            "like_count": comment_data.get("likes", "0"),
            "reply_count": comment_data.get("replies", "0"),
            "timestamp": comment_data.get("create_time", ""),
            "scraped_at": datetime.now().isoformat(),
        }
        
        rows.append(row)
        existing_comments.add(key)

    # Save
    if rows:
        append_comment_rows(rows)
        print(f"      💾 Saved {len(rows)} new comments")
    
    # ⭐ SAVE REPLIES (dari DOM extraction)
    reply_rows = []
    if result.get("replies"):
        print(f"      💬 Processing {len(result['replies'])} visible replies...")
        for idx, reply_data in enumerate(result["replies"]):
            # Generate unique ID untuk reply (pakai hash atau counter)
            reply_id = f"reply_{video_id}_{idx}"
            key = (username, video_id, reply_id)
            
            if key in existing_comments:
                continue
            
            reply_row = {
                "username": username,
                "video_id": video_id,
                "comment_id": reply_id,  # Mark as reply
                "commenter_username": reply_data.get("username", ""),
                "comment_text": reply_data.get("text", ""),
                "like_count": reply_data.get("likes", "0"),
                "reply_count": "0",  # Replies don't have sub-replies
                "timestamp": "",  # Tidak ada timestamp dari DOM extraction
                "scraped_at": datetime.now().isoformat(),
            }
            
            reply_rows.append(reply_row)
            existing_comments.add(key)
    
    # Save replies
    if reply_rows:
        append_comment_rows(reply_rows)
        print(f"      💾 Saved {len(reply_rows)} visible replies")
    
    # Save stats
    expected = result.get("expected_comment_count", 0)
    total_scraped = len(rows) + len(reply_rows)
    coverage_pct = round((total_scraped / expected * 100), 1) if expected > 0 else ""
    
    stats_row = {
        "username": username,
        "video_id": video_id,
        "total_comments_loaded": result["total_comments_loaded"],
        "total_comments_available": result["total_comments_available"],
        "scraped_comments_count": total_scraped,  # Include replies in count
        "expected_comment_count": expected,
        "coverage_pct": coverage_pct,
        "scraped_at": datetime.now().isoformat(),
    }
    append_stats_row(stats_row)
    
    if expected > 0 and coverage_pct != "" and coverage_pct < 90:
        print(f"      ⚠️ Coverage rendah: {total_scraped}/{expected} ({coverage_pct}%) - di bawah target 90%")

    return total_scraped  # Return total (comments + replies)


# ============================================================
# MAIN
# ============================================================
async def main():
    # Load data
    health_videos = load_health_videos()
    if not health_videos:
        print("❌ Gak ada health videos untuk di-scrape")
        return

    existing_comments = load_existing_comments()
    existing_stats = load_existing_stats()

    # Filter videos yang belum discrape
    to_process = []
    for video_data in health_videos:
        username = video_data.get("username")
        video_id = video_data.get("video_id")
        key = (username, video_id)
        
        if key not in existing_stats:
            to_process.append(video_data)

    print(f"\n📊 Summary:")
    print(f"   Total health videos: {len(health_videos)}")
    print(f"   Existing comments: {len(existing_comments)}")
    print(f"   Videos to process: {len(to_process)}")

    if not to_process:
        print("✅ Semua videos sudah di-scrape comments-nya")
        send_telegram_alert("✅ Semua videos sudah di-scrape comments")
        return

    send_telegram_alert(
        f"🚀 Mulai scrape comments dari {len(to_process)} health videos\n"
        f"Waktu: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )

    # Load cookies SEBELUM launch browser
    print(f"\n📋 Loading cookies...")
    cookies = load_cookies_from_json()
    
    if not cookies:
        print(f"\n❌ No cookies loaded - cannot continue!")
        print(f"   Please run: python apply_cookies.py (atau script sejenis) dulu")
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
        
        # ⭐ INJECT COOKIES seperti apply_cookies.py
        print(f"📋 Applying cookies ke browser context...")
        await context.add_cookies(cookies)
        print(f"✅ {len(cookies)} cookies applied")
        
        page = context.pages[0] if context.pages else await context.new_page()

        # Test login state dulu dengan navigate home
        print(f"\n📋 Verifying login state...")
        try:
            await page.goto("https://www.tiktok.com", wait_until="load", timeout=30000)
            await asyncio.sleep(2)
            
            # Check profile button (indicator user sudah login)
            profile_button = await page.query_selector('[data-e2e="header-profile"]')
            if profile_button:
                print(f"✅ Profile button found - LOGGED IN ✅")
            else:
                print(f"⚠️  Profile button not found - might not be logged in")
                print(f"   Continuing anyway...")
            
            await asyncio.sleep(2)
        except Exception as e:
            print(f"⚠️  Error verifying login: {e}")

        comments_cache = []
        page.on("response", make_comments_response_listener(comments_cache))
        print("📡 Network listener untuk comments aktif")

        try:
            total_comments_scraped = 0
            
            for idx, video_data in enumerate(to_process, start=1):
                username = video_data.get("username")
                video_id = video_data.get("video_id")
                
                print(f"\n{'=' * 70}")
                print(f"[{idx}/{len(to_process)}] @{username} - Video {video_id[:10]}...")
                print(f"{'=' * 70}")

                try:
                    n_comments = await process_video_comments(
                        page, username, video_id, existing_comments, comments_cache
                    )
                    total_comments_scraped += n_comments

                    if total_comments_scraped % DELAY_EVERY_N_VIDEOS == 0:
                        long_delay = random.uniform(*DELAY_AFTER_N_VIDEOS)
                        print(f"\n   🌙 Jeda {long_delay / 60:.1f} menit (anti-captcha)...")
                        await asyncio.sleep(long_delay)
                    else:
                        delay = random.uniform(*DELAY_BETWEEN_VIDEO)
                        print(f"   ⏳ Jeda {delay:.0f}s")
                        await asyncio.sleep(delay)

                except Exception as e:
                    print(f"   ❌ Error: {e}")
                    continue

            print(f"\n\n✅ SELESAI")
            print(f"   Total comments scraped: {total_comments_scraped}")
            print(f"   Output: {OUTPUT_COMMENTS}")
            print(f"   Stats: {OUTPUT_COMMENTS_STATS}")
            
            send_telegram_alert(
                f"✅ Scrape comments selesai!\n"
                f"Total comments: {total_comments_scraped}\n"
                f"Waktu: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            )

        except Exception as e:
            print(f"\n❌ Error: {e}")
            send_telegram_alert(f"❌ Error saat scrape comments: {str(e)[:100]}")

        finally:
            await context.close()


if __name__ == "__main__":
    asyncio.run(main())