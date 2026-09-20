"""
convert_and_apply_cookies.py
=============================
Convert cookies raw (hasil export Cookie-Editor) ke format Playwright,
untuk 2 akun sekaligus, terus verify login-nya (apply + test).

⭐ FIX: browser verify sekarang pakai stealth measures yang sama kayak
   script scraper utama (disable automation flag + webdriver override),
   soalnya browser polos gampang di-flag TikTok jadi bot -> captcha/lambat
   -> keliatan kayak "gagal login" padahal cookies-nya valid.
   Juga nambah beberapa selector alternatif + screenshot kalau gagal,
   biar kamu bisa cek sendiri apa yang beneran muncul di halaman.

Input (raw, hasil export Cookie-Editor):
    01_pengumpulan_data/00_akun_playwright/raw_cookie/tiktok_cookies_raw_1.json
    01_pengumpulan_data/00_akun_playwright/raw_cookie/tiktok_cookies_raw_2.json

Output (final, siap dipakai scraper):
    01_pengumpulan_data/00_akun_playwright/final_cookie/tiktok_cookies_akun1.json
    01_pengumpulan_data/00_akun_playwright/final_cookie/tiktok_cookies_akun2.json

Cara pakai:
1. Install extension "Cookie-Editor" di Chrome
2. Login TikTok akun 1 di Chrome (browser/profile manapun, bukan incognito)
3. Di tiktok.com, buka Cookie-Editor -> Export -> pilih JSON
4. Paste hasilnya ke file 'tiktok_cookies_raw_1.json' di folder raw_cookie/
5. Ulangi utk akun 2 -> simpan sebagai 'tiktok_cookies_raw_2.json'
6. Run script ini -> otomatis convert KEDUA akun + test login masing-masing
"""

import asyncio
import json
import os
from pathlib import Path

from playwright.async_api import async_playwright

# ============================================================
# PATH
# ============================================================
BASE_DIR = Path(r"01_pengumpulan_data/00_akun_playwright")
RAW_DIR = BASE_DIR / "raw_cookie"
FINAL_DIR = BASE_DIR / "final_cookie"
FINAL_DIR.mkdir(parents=True, exist_ok=True)

DEBUG_DIR = BASE_DIR / "verify_debug_screenshots"
DEBUG_DIR.mkdir(parents=True, exist_ok=True)

AKUN_LIST = [
    {
        "label": "akun1",
        "raw_file": RAW_DIR / "tiktok_cookies_raw_1.json",
        "final_file": FINAL_DIR / "tiktok_cookies_akun1.json",
    },
    {
        "label": "akun2",
        "raw_file": RAW_DIR / "tiktok_cookies_raw_2.json",
        "final_file": FINAL_DIR / "tiktok_cookies_akun2.json",
    },
]

SAME_SITE_MAP = {
    "no_restriction": "None",
    "unspecified": "Lax",
    "lax": "Lax",
    "strict": "Strict",
    "none": "None",
}

REALISTIC_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)

# ⭐ Beberapa indikator "logged in" — gak cuma andelin 1 selector doang
LOGGED_IN_SELECTORS = [
    '[data-e2e="header-profile"]',
    '[data-e2e="profile-icon"]',
    'a[href="/upload"]',                 # Tombol upload cuma muncul kalau login
    '[data-e2e="nav-upload"]',
    'div[class*="avatar"][class*="header"]',
]

LOGGED_OUT_SELECTORS = [
    'button:has-text("Log in")',
    '[data-e2e="top-login-button"]',
]


# ============================================================
# STEP 1: CONVERT
# ============================================================
def convert_cookies(raw_file: Path, final_file: Path, label: str):
    if not raw_file.exists():
        print(f"❌ [{label}] File raw gak ketemu: {raw_file}")
        return 0

    with open(raw_file, "r", encoding="utf-8") as f:
        raw_cookies = json.load(f)

    converted = []
    for c in raw_cookies:
        if "tiktok.com" not in c.get("domain", ""):
            continue

        cookie = {
            "name": c["name"],
            "value": c["value"],
            "domain": c["domain"],
            "path": c.get("path", "/"),
            "secure": bool(c.get("secure", False)),
            "httpOnly": bool(c.get("httpOnly", False)),
        }

        same_site_raw = str(c.get("sameSite", "unspecified")).lower()
        cookie["sameSite"] = SAME_SITE_MAP.get(same_site_raw, "Lax")

        if not c.get("session", False) and c.get("expirationDate"):
            cookie["expires"] = c["expirationDate"]

        converted.append(cookie)

    with open(final_file, "w", encoding="utf-8") as f:
        json.dump(converted, f, indent=2)

    print(f"✅ [{label}] Berhasil convert {len(converted)} cookies TikTok")
    print(f"   Disimpan ke: {final_file}")

    cookie_names = {c["name"] for c in converted}
    important = {"sessionid", "sid_tt", "sid_guard"}
    missing = important - cookie_names
    if missing:
        print(f"   ⚠️ [{label}] Cookie penting yang gak ketemu: {missing}")
        print(f"      (mungkin export-nya gak lengkap, atau nama cookie beda versi TikTok)")

    return len(converted)


# ============================================================
# STEP 2: APPLY + VERIFY LOGIN (dengan stealth measures)
# ============================================================
async def apply_and_verify(final_file: Path, label: str):
    if not final_file.exists():
        print(f"⏭️  [{label}] Skip verify, file final gak ada")
        return False

    with open(final_file, "r", encoding="utf-8") as f:
        cookies = json.load(f)

    if not cookies:
        print(f"⏭️  [{label}] Skip verify, cookies kosong")
        return False

    print(f"\n🔍 [{label}] Verifying login pakai browser sementara (stealth mode)...")

    async with async_playwright() as p:
        # ⭐ FIX: pakai launch args yang sama kayak scraper utama, biar gak
        # kedetect sebagai automation dan malah ke-block/challenge
        browser = await p.chromium.launch(
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = await browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent=REALISTIC_UA,
        )
        # ⭐ FIX: sembunyiin navigator.webdriver flag
        await context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        )
        await context.add_cookies(cookies)
        page = await context.new_page()

        is_logged_in = False
        try:
            # ⭐ FIX: pakai domcontentloaded (lebih cepat, gak nunggu semua
            # resource kayak video/ads yang bisa bikin "load" event ngaret/timeout)
            await page.goto("https://www.tiktok.com", wait_until="domcontentloaded", timeout=45000)
            await asyncio.sleep(4)  # kasih waktu React render & cookies ke-apply

            # Cek beberapa indikator logged-in
            for sel in LOGGED_IN_SELECTORS:
                try:
                    elem = await page.query_selector(sel)
                    if elem and await elem.is_visible():
                        is_logged_in = True
                        print(f"      ✅ Ketemu indikator login: {sel}")
                        break
                except Exception:
                    continue

            # Kalau gak ketemu, cek juga apakah tombol "Log in" ADA (kalau ada,
            # berarti beneran belum login, bukan cuma selector-nya yang salah)
            if not is_logged_in:
                for sel in LOGGED_OUT_SELECTORS:
                    try:
                        elem = await page.query_selector(sel)
                        if elem and await elem.is_visible():
                            print(f"      ℹ️ Ketemu tombol 'Log in' — beneran belum login")
                            break
                    except Exception:
                        continue

            if is_logged_in:
                print(f"✅ [{label}] LOGIN BERHASIL — cookies valid!")
            else:
                # Screenshot buat debug manual
                screenshot_path = DEBUG_DIR / f"{label}_failed_verify.png"
                try:
                    await page.screenshot(path=str(screenshot_path))
                    print(f"❌ [{label}] Gak ketemu indikator login.")
                    print(f"   📸 Screenshot disimpan: {screenshot_path}")
                    print(f"   👉 Cek screenshot itu manual — kalau ternyata KELIATAN login")
                    print(f"      di situ, berarti cookies-nya VALID, cuma selector detection")
                    print(f"      aku yang kurang pas. Kasih tau aku apa yang keliatan di screenshot.")
                except Exception:
                    pass

            await asyncio.sleep(2)
        except Exception as e:
            print(f"❌ [{label}] Error saat verify: {e}")
            try:
                screenshot_path = DEBUG_DIR / f"{label}_error_verify.png"
                await page.screenshot(path=str(screenshot_path))
                print(f"   📸 Screenshot disimpan: {screenshot_path}")
            except Exception:
                pass
            is_logged_in = False
        finally:
            await browser.close()

    return is_logged_in


# ============================================================
# MAIN
# ============================================================
async def main():
    print("=" * 70)
    print("STEP 1: CONVERT COOKIES (raw -> final)")
    print("=" * 70)

    converted_counts = {}
    for akun in AKUN_LIST:
        n = convert_cookies(akun["raw_file"], akun["final_file"], akun["label"])
        converted_counts[akun["label"]] = n

    print("\n" + "=" * 70)
    print("STEP 2: APPLY & VERIFY LOGIN")
    print("=" * 70)

    verify_results = {}
    for akun in AKUN_LIST:
        if converted_counts.get(akun["label"], 0) == 0:
            print(f"\n⏭️  [{akun['label']}] Skip verify (convert gagal/kosong)")
            verify_results[akun["label"]] = False
            continue

        ok = await apply_and_verify(akun["final_file"], akun["label"])
        verify_results[akun["label"]] = ok

    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    for akun in AKUN_LIST:
        label = akun["label"]
        status = "✅ SIAP DIPAKAI" if verify_results.get(label) else "❌ PERLU DICEK (lihat screenshot)"
        print(f"   {label}: {status} ({converted_counts.get(label, 0)} cookies)")

    if all(verify_results.values()):
        print("\n🎉 Kedua akun siap! Lanjut ke script scraping.")
    else:
        print(f"\n⚠️  Ada yang gagal auto-verify. CEK SCREENSHOT dulu di:")
        print(f"   {DEBUG_DIR}")
        print(f"   Kalau di screenshot KELIATAN udah login, cookies-nya sebenarnya OKE")
        print(f"   dan aman lanjut ke scraping — verify-nya doang yang meleset.")


if __name__ == "__main__":
    asyncio.run(main())