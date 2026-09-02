import asyncio
import os
from playwright.async_api import async_playwright

# ⬇️ PATH FIXED yang SAMA PERSIS di SEMUA script (login, apply cookies,
# scraping) — WAJIB sama biar semua share profile login yang sama,
# gak peduli file .py-nya ditaruh di folder mana.
BASE_DIR = r"C:\Users\nesha\Tugas_akhir\seleksi-influencer"
PROFILE_DIR = os.path.join(BASE_DIR, "tiktok_browser_profile")

REALISTIC_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)


async def main():
    print(f"📁 Profile akan disimpan/dipakai di: {PROFILE_DIR}\n")

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

        print("🌐 Buka halaman login TikTok...")
        # Langsung ke halaman login, skip homepage/feed video
        # (feed "For You" autoplay video terus, bikin ribet & kadang
        # keliatan kayak "refresh sendiri")
        await page.goto(
            "https://www.tiktok.com/login/phone-or-email/email",
            wait_until="load",
            timeout=60000,
        )

        print("\n" + "=" * 60)
        print("👉 SILAKAN LOGIN MANUAL DI BROWSER YANG TERBUKA")
        print("   - Klik tombol 'Log in'")
        print("   - Login pakai akun TikTok kamu")
        print("   - Selesaikan captcha / verifikasi apapun yang muncul")
        print("   - Pastikan kamu benar-benar masuk (lihat foto profil")
        print("     kamu muncul di pojok kanan atas)")
        print("   - SANTAI AJA, gak ada script yang jalan sekarang,")
        print("     jadi gak akan ke-flag 'unusual activity'")
        print("=" * 60)
        print("\n⏸️  Setelah SELESAI login, balik ke terminal ini dan")
        print("    tekan ENTER untuk menyimpan session & keluar.\n")

        # Blocking input, tapi gak ganggu browser karena gak ada
        # aksi apapun yang dilakukan script selama nunggu ini
        await asyncio.get_event_loop().run_in_executor(
            None, input, ">>> Tekan ENTER setelah login selesai: "
        )

        print("\n💾 Menyimpan session...")
        # kasih waktu sebentar biar cookies/localStorage sempat
        # ke-flush ke disk sebelum context ditutup
        await asyncio.sleep(3)

        await context.close()
        print(f"✅ Session tersimpan di: {PROFILE_DIR}")
        print("   Sekarang kamu bisa jalanin script scraping,")
        print("   dia akan otomatis pakai session login ini.")


if __name__ == "__main__":
    asyncio.run(main())