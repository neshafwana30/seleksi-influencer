import asyncio
import os
import pygame

from playwright.async_api import async_playwright


ROOT_DIR = r"C:\Users\nesha\Tugas_akhir\seleksi-influencer"

DATA_DIR = os.path.join(
    ROOT_DIR,
    "01_pengumpulan_data"
)

PROFILE_DIR = os.path.join(
    DATA_DIR,
    "00_akun_playwright",
    "tiktok_browser_profile"
)

WARNING_MP3 = os.path.join(
    DATA_DIR,
    "03_videoid_perakun",
    "warning.mp3"
)


def play_warning():
    print("🔊 Mencoba memutar warning.mp3...")

    if not os.path.exists(WARNING_MP3):
        print(f"❌ File tidak ditemukan:")
        print(WARNING_MP3)
        return

    print(f"✅ File ditemukan: {WARNING_MP3}")

    try:
        pygame.mixer.init()
        pygame.mixer.music.load(WARNING_MP3)
        pygame.mixer.music.play()

        print("🎵 warning.mp3 sedang diputar!")

    except Exception as e:
        print(f"❌ Gagal memutar audio: {e}")


def stop_warning():
    if pygame.mixer.get_init():
        pygame.mixer.music.stop()
        pygame.mixer.quit()

    print("🔇 Audio dihentikan")


async def test_audio_in_tiktok():

    async with async_playwright() as p:

        print("🚀 Membuka Chromium...")

        context = await p.chromium.launch_persistent_context(
            PROFILE_DIR,
            headless=False,
            viewport={
                "width": 1400,
                "height": 900
            },
            args=[
                "--disable-blink-features=AutomationControlled"
            ],
        )

        page = (
            context.pages[0]
            if context.pages
            else await context.new_page()
        )

        print("🌐 Membuka TikTok...")

        await page.goto(
            "https://www.tiktok.com",
            wait_until="domcontentloaded"
        )

        print("✅ TikTok berhasil dibuka!")

        # TEST AUDIO
        play_warning()

        print("⏳ Browser dan audio dibiarkan 10 detik...")

        await asyncio.sleep(10)

        stop_warning()

        print("🔒 Menutup browser...")

        await context.close()


if __name__ == "__main__":
    asyncio.run(test_audio_in_tiktok())