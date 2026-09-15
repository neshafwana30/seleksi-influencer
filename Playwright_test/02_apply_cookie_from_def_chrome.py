import asyncio
import json
import os
from playwright.async_api import async_playwright

# ⬇️ PATH FIXED (bukan relatif ke lokasi file ini) — biar SEMUA script
# (login, apply cookies, scraping) selalu pakai folder profile yang
# SAMA PERSIS, gak peduli file .py-nya ditaruh di folder mana.
# GANTI base path ini kalau folder project kamu bukan di sini.
BASE_DIR = r"C:\Users\nesha\Tugas_akhir\seleksi-influencer\Playwright_test"
PROFILE_DIR = os.path.join(BASE_DIR, "tiktok_browser_profile")
COOKIES_FILE = os.path.join(BASE_DIR, "tiktok_cookies.json")

REALISTIC_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)


async def main():
    if not os.path.exists(COOKIES_FILE):
        print(f"❌ File {COOKIES_FILE} gak ketemu.")
        print("   Jalankan 'import_chrome_cookies.py' dulu.")
        return

    with open(COOKIES_FILE, "r", encoding="utf-8") as f:
        cookies = json.load(f)

    print(f"🍪 Memuat {len(cookies)} cookies dari {COOKIES_FILE}")
    print(f"📁 Profile Playwright: {PROFILE_DIR}")

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

        # Suntikkan cookies dari Chrome asli ke context Playwright ini
        await context.add_cookies(cookies)
        print("✅ Cookies berhasil di-inject ke Playwright")

        page = context.pages[0] if context.pages else await context.new_page()

        print("🌐 Buka tiktok.com buat verifikasi login...")
        await page.goto("https://www.tiktok.com", wait_until="load", timeout=60000)
        await asyncio.sleep(4)

        # Cek apakah beneran udah login (gak ada tombol "Log in" lagi)
        login_button = await page.query_selector('text="Log in"')
        if login_button and await login_button.is_visible():
            print("⚠️  Kelihatannya masih belum login (tombol 'Log in' masih ada).")
            print("    Cek manual di browser yang kebuka.")
        else:
            print("🎉 SUKSES! Sepertinya udah login lewat cookies yang di-import.")
            print("   Session ini udah tersimpan di profile Playwright.")
            print("   Sekarang kamu bisa langsung jalankan script scraping.")

        print("\n⏸️  Browser tetap terbuka 20 detik buat kamu cek manual...")
        await asyncio.sleep(20)

        await context.close()


if __name__ == "__main__":
    asyncio.run(main())