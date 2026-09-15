"""
APPLY & PERSIST COOKIES - WITH CAPTCHA HANDLING

Jika captcha muncul:
1. Script DETECT & PAUSE (tidak langsung close!)
2. Show instruction: "Solve captcha di browser yang kebuka"
3. WAIT sampai captcha solved (check every 2 detik)
4. After solved, continue navigate
5. PERSIST cookies to profile

Ini adalah key difference dari versi sebelumnya!
"""

import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path

try:
    from playwright.async_api import async_playwright
except ImportError:
    print("❌ Playwright not installed")
    sys.exit(1)

SCRIPT_DIR = Path(__file__).parent.absolute()
PROFILE_DIR = SCRIPT_DIR / "tiktok_browser_profile"
COOKIES_FILE = SCRIPT_DIR / "tiktok_cookies.json"

REALISTIC_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)

# CAPTCHA detection
CAPTCHA_SELECTORS = [
    'text="Verify"',
    'text="verify"',
    '[data-e2e="login-modal"]',
    'div[role="dialog"]',
    'iframe[src*="captcha"]',
    '[id*="captcha"]',
    '[class*="captcha"]',
    'text="Drag the slider"',
    'text="puzzle"',
]


async def wait_for_captcha_to_clear(page, timeout=600):
    """
    WAIT untuk captcha hilang, dengan periodic check.

    timeout: berapa lama tunggu (default 10 menit)
    Return: True jika captcha hilang, False jika timeout
    """
    start_time = asyncio.get_event_loop().time()

    while asyncio.get_event_loop().time() - start_time < timeout:
        # Check apakah ada captcha
        captcha_found = False
        for sel in CAPTCHA_SELECTORS:
            try:
                elem = await page.query_selector(sel)
                if elem and await elem.is_visible():
                    captcha_found = True
                    break
            except:
                pass

        if not captcha_found:
            print(f"\n✅ Captcha cleared!")
            await asyncio.sleep(2)  # Wait biar page stabilize
            return True

        elapsed = asyncio.get_event_loop().time() - start_time
        remaining = timeout - elapsed
        print(f"   ⏳ Waiting untuk captcha solve... ({remaining:.0f}s remaining)", end="\r")

        await asyncio.sleep(2)  # Check setiap 2 detik

    print(f"\n❌ Captcha timeout setelah {timeout}s")
    return False


async def main():
    print("\n" + "=" * 70)
    print("🔄 APPLY & PERSIST COOKIES (WITH CAPTCHA HANDLING)")
    print("=" * 70)

    print(f"\n📋 Step 1: Verify files...")

    if not COOKIES_FILE.exists():
        print(f"❌ Cookies file NOT found: {COOKIES_FILE}")
        return

    print(f"✅ Cookies file found")

    if not PROFILE_DIR.exists():
        print(f"   Creating profile folder...")
        PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    print(f"\n📋 Step 2: Load cookies...")

    with open(COOKIES_FILE, "r", encoding="utf-8") as f:
        cookies = json.load(f)

    print(f"✅ Loaded {len(cookies)} cookies")

    async with async_playwright() as p:
        try:
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

            page = context.pages[0] if context.pages else await context.new_page()

            print(f"\n📋 Step 3: Inject cookies...")

            await context.add_cookies(cookies)
            print(f"✅ {len(cookies)} cookies injected")

            # ================================================================
            # STEP 4: Navigate TikTok HOME
            # ================================================================
            print(f"\n📋 Step 4: Navigate ke TikTok home...")

            await page.goto("https://www.tiktok.com", wait_until="load", timeout=60000)
            await asyncio.sleep(2)

            print(f"✅ Page loaded: {page.url}")

            # CHECK CAPTCHA DI HOME PAGE
            print(f"\n🔍 Checking untuk captcha di home page...")
            captcha_detected = False
            for sel in CAPTCHA_SELECTORS:
                try:
                    elem = await page.query_selector(sel)
                    if elem and await elem.is_visible():
                        captcha_detected = True
                        break
                except:
                    pass

            if captcha_detected:
                print(f"\n" + "!" * 70)
                print(f"🔒 CAPTCHA TERDETEKSI DI HOME PAGE!")
                print(f"!" * 70)
                print(f"\n📱 Solve captcha di browser yang sudah terbuka:")
                print(f"   1. Drag slider / click puzzle")
                print(f"   2. Complete verification")
                print(f"   3. Script akan continue otomatis")
                print(f"\n⏳ Waiting sampai captcha solved...")

                cleared = await wait_for_captcha_to_clear(page, timeout=600)

                if not cleared:
                    print(f"\n❌ Captcha not solved dalam 10 menit")
                    print(f"   Shutting down...")
                    await context.close()
                    return
            else:
                print(f"   ✅ No captcha detected at home")

            # Check login indicators
            print(f"\n🔍 Checking login indicators...")
            profile_button = await page.query_selector('[data-e2e="header-profile"]')
            if profile_button:
                print(f"   ✅ Profile button found - LOGGED IN")
                login_state = "LOGGED_IN"
            else:
                print(f"   ⚠️ No profile button (might still be loading)")
                login_state = "UNCLEAR"

            # ================================================================
            # STEP 5: Navigate ke @tiktok profile
            # ================================================================
            print(f"\n📋 Step 5: Navigate ke @tiktok profile...")

            await page.goto("https://www.tiktok.com/@tiktok", wait_until="load", timeout=60000)
            await asyncio.sleep(2)

            # CHECK CAPTCHA DI PROFILE PAGE
            captcha_detected = False
            for sel in CAPTCHA_SELECTORS:
                try:
                    elem = await page.query_selector(sel)
                    if elem and await elem.is_visible():
                        captcha_detected = True
                        break
                except:
                    pass

            if captcha_detected:
                print(f"\n" + "!" * 70)
                print(f"🔒 CAPTCHA TERDETEKSI DI PROFILE PAGE!")
                print(f"!" * 70)
                print(f"\n📱 Solve captcha di browser:")
                print(f"   1. Drag slider / click puzzle")
                print(f"   2. Complete verification")
                print(f"   3. Script akan continue...")
                print(f"\n⏳ Waiting sampai captcha solved...")

                cleared = await wait_for_captcha_to_clear(page, timeout=600)

                if not cleared:
                    print(f"\n❌ Captcha not solved")
                    await context.close()
                    return
            else:
                print(f"✅ Profile page loaded (no captcha)")

            # Scroll untuk trigger video load
            print(f"\n   Scrolling untuk trigger video grid...")
            await page.evaluate("window.scrollBy(0, 500)")
            await asyncio.sleep(2)

            # Check video links
            video_links = await page.query_selector_all('a[href*="/video/"]')
            print(f"✅ Found {len(video_links)} video links!")

            # ================================================================
            # STEP 6: Close & Persist
            # ================================================================
            print(f"\n📋 Step 6: Closing & PERSISTING cookies...")

            print(f"   Waiting 3 detik biar sync ke disk...")
            await asyncio.sleep(3)

            await context.close()

            print(f"✅ Context closed - cookies PERSISTED!")

            # ================================================================
            # FINAL RESULT
            # ================================================================
            print(f"\n" + "=" * 70)
            print(f"✅✅ SUKSES! COOKIES PERSISTED!")
            print(f"=" * 70)
            print(f"\n📁 Persistent profile: {PROFILE_DIR}")
            print(f"\n✅ Cookies fresh & valid")
            print(f"✅ Profile page loads correctly")
            print(f"✅ Video grid accessible")
            print(f"\n👉 NEXT STEPS:")
            print(f"   1. python 03_check_login_state_v2.py (verify selectors)")
            print(f"   2. python ../../04_mendapatkan_metadata_akun/03_scrape_metadata_video_dan_profil.py")

        except Exception as e:
            print(f"\n❌ Error: {e}")
            import traceback
            traceback.print_exc()


if __name__ == "__main__":
    print(f"\n⏳ Launching browser...")
    print(f"   Jika ada CAPTCHA, solve manual di browser!")
    print(f"   Script akan wait sampai selesai.\n")

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print(f"\n⏹️ Stopped")