import asyncio
import csv
import os
import random
from datetime import datetime, timezone
from playwright.async_api import async_playwright

# ⬇️ PATH FIXED yang SAMA PERSIS dengan apply_cookies_to_playwright.py
# dan login_tiktok.py — WAJIB sama biar semua script share profile
# login yang sama.
BASE_DIR = r"C:\Users\nesha\Tugas_akhir\seleksi-influencer\Playwright_test"


def video_id_to_date(video_id: str) -> str:
    """
    TikTok video ID menyimpan timestamp creation di 32 bit pertama (snowflake-like ID).
    Ini cara paling reliable buat dapetin tanggal post tanpa scrape dari DOM.
    """
    try:
        vid_int = int(video_id)
        timestamp = vid_int >> 32
        dt = datetime.fromtimestamp(timestamp, tz=timezone.utc)
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return ""


async def wait_for_login_wall_clear(page, timeout=180):
    """
    Deteksi modal/wall 'Log in to TikTok' dan BLOKIR eksekusi script
    sampai modal itu beneran hilang dari layar.

    Ini penting: elemen video di grid tetap ada di DOM walau ketutup
    modal login (cuma keblok visual), jadi wait_for_selector biasa bisa
    lolos duluan padahal modal login masih nongol. Kalau script tetap
    scroll/klik saat modal login masih ada, itu pola yang TikTok anggap
    'unusual activity' (otomatisasi jalan bareng proses login manual).
    """
    login_wall_selectors = [
        'text="Log in to TikTok"',
        '[data-e2e="login-modal"]',
        'div[role="dialog"]:has-text("Log in")',
        'div[role="dialog"]:has-text("Continue with")',
    ]

    print("🔎 Cek apakah ada login wall yang nutup halaman...")
    start = asyncio.get_event_loop().time()
    was_found = False

    while asyncio.get_event_loop().time() - start < timeout:
        found = False
        for sel in login_wall_selectors:
            try:
                elem = await page.query_selector(sel)
                if elem and await elem.is_visible():
                    found = True
                    break
            except Exception:
                continue

        if not found:
            if was_found:
                print("\n✅ Login wall sudah hilang, aman untuk lanjut.")
            else:
                print("✅ Gak ada login wall, aman untuk lanjut.")
            # jeda kecil biar TikTok gak lihat "langsung gerak" sesaat
            # setelah modal ketutup — mirip jeda manusia baca layar dulu
            await asyncio.sleep(random.uniform(2, 4))
            return True

        if not was_found:
            print("\n" + "=" * 60)
            print("🔒 LOGIN WALL TERDETEKSI — SCRIPT DIPAUSE TOTAL")
            print("   Selesaikan login manual dulu di browser.")
            print("   Script BARU lanjut setelah wall ini beneran hilang.")
            print("=" * 60)
        was_found = True

        print("   ⏳ Masih nunggu kamu selesai login...", end="\r")
        await asyncio.sleep(3)

    print("\n⚠️ Timeout nunggu login wall clear, tetap lanjut (hati-hati, mungkin masih ketutup modal)")
    return False


async def collect_video_ids(page, username, max_videos=50):
    """
    TAHAP 1: Buka profile, scroll, kumpulin semua video ID + URL
    """
    profile_url = f"https://www.tiktok.com/@{username}"
    print(f"👤 Buka profile: {profile_url}")
    await page.goto(profile_url, wait_until="load", timeout=60000)
    await asyncio.sleep(3)

    print("\n" + "=" * 60)
    print("🔒 KALAU MUNCUL CAPTCHA, SOLVE MANUAL SEKARANG")
    print("   Script nunggu sampai video grid muncul (timeout 2 menit)")
    print("=" * 60 + "\n")

    try:
        await page.wait_for_selector('a[href*="/video/"]', timeout=120000)
    except Exception:
        print("⚠️ Video grid gak muncul, cek manual browsernya")

    # WAJIB: pastikan login wall beneran hilang sebelum mulai gerak
    await wait_for_login_wall_clear(page, timeout=180)

    print("✅ Mulai scroll & kumpulin video ID...\n")
    await asyncio.sleep(2)

    video_urls = set()
    same_count_streak = 0
    scroll_count = 0

    while len(video_urls) < max_videos and same_count_streak < 5:
        links = await page.query_selector_all('a[href*="/video/"]')
        before = len(video_urls)

        for link in links:
            href = await link.get_attribute("href")
            if href and "/video/" in href:
                video_urls.add(href)

        after = len(video_urls)
        if after == before:
            same_count_streak += 1
        else:
            same_count_streak = 0

        print(f"  🔄 Scroll #{scroll_count} — {len(video_urls)} video ID terkumpul...")

        await page.mouse.wheel(0, 1500)
        scroll_count += 1
        await asyncio.sleep(random.uniform(2, 4))

    video_list = list(video_urls)[:max_videos]
    print(f"\n✅ Total {len(video_list)} video URL terkumpul\n")
    return video_list


async def scrape_video_stats(page, video_url, referer=None):
    """
    TAHAP 2: Buka satu video, extract stats lengkap
    """
    # Retry sekali kalau kena block sementara (ERR_HTTP_RESPONSE_CODE_FAILURE / 403)
    for attempt in range(2):
        try:
            # Kasih referer = halaman profile, biar request ini keliatan
            # natural (kayak beneran diklik dari profile), bukan goto
            # "dari udara" tanpa asal-usul.
            goto_kwargs = {"wait_until": "load", "timeout": 60000}
            if referer:
                goto_kwargs["referer"] = referer
            await page.goto(video_url, **goto_kwargs)
            break
        except Exception as e:
            if attempt == 0:
                print(f"   ⚠️ Gagal buka ({e}), retry setelah jeda lebih lama...")
                await asyncio.sleep(random.uniform(35, 60))
            else:
                raise

    await asyncio.sleep(random.uniform(3, 5))

    # Cek lagi kalau-kalau login wall muncul di halaman video individual
    await wait_for_login_wall_clear(page, timeout=180)

    video_id = video_url.rstrip("/").split("/video/")[-1].split("?")[0]

    async def get_text(selector):
        try:
            elem = await page.query_selector(selector)
            if elem:
                return (await elem.text_content()).strip()
        except Exception:
            pass
        return ""

    desc = await get_text('[data-e2e="video-desc"]') or await get_text('h1')
    likes = await get_text('[data-e2e="like-count"]')
    comments = await get_text('[data-e2e="comment-count"]')
    shares = await get_text('[data-e2e="share-count"]')
    saves = await get_text('[data-e2e="undefined-count"]')  # bookmark/save biasanya gapunya e2e proper

    # Fallback kalau save-count gak ketemu lewat data-e2e, coba cari lewat urutan icon
    if not saves:
        try:
            counts = await page.query_selector_all('strong[data-e2e$="-count"]')
            values = []
            for c in counts:
                values.append((await c.text_content()).strip())
            # urutan biasanya: like, comment, save, share
            if len(values) >= 4:
                likes = likes or values[0]
                comments = comments or values[1]
                saves = saves or values[2]
                shares = shares or values[3]
        except Exception:
            pass

    author = video_url.split("/@")[-1].split("/")[0] if "/@" in video_url else ""

    return {
        "video_id": video_id,
        "author_name": author,
        "video_url": video_url,
        "desc": desc[:500],
        "like_count": likes,
        "comment_count": comments,
        "share_count": shares,
        "save_count": saves,
        "post_date_utc": video_id_to_date(video_id),
        "scraped_at": datetime.now().isoformat(),
    }


async def main():
    username = "tirtacipeng"       # ⬅️ GANTI username tanpa @
    max_videos = 30                # ⬅️ berapa video yang mau diambil
    output_file = f"videos_{username}.csv"

    # ⬇️ PENTING: pakai persistent profile, biar login/cookies KE-SAVE
    # antar run. Run pertama kamu login manual sekali di sini, run
    # berikutnya bakal auto-login pakai profile yang sama.
    profile_dir = os.path.join(BASE_DIR, "tiktok_browser_profile")
    print(f"📁 Pakai profile dari: {profile_dir}\n")

    profile_url = f"https://www.tiktok.com/@{username}"

    # User-agent realistis (default Playwright UA kadang keliatan aneh /
    # kebaca sebagai automation oleh WAF TikTok)
    REALISTIC_UA = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    )

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            profile_dir,
            headless=False,
            viewport={"width": 1400, "height": 900},
            user_agent=REALISTIC_UA,
            args=["--disable-blink-features=AutomationControlled"],
        )
        # Sembunyikan flag navigator.webdriver yang biasa dipakai buat
        # deteksi automation
        await context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        )

        page = context.pages[0] if context.pages else await context.new_page()

        try:
            video_urls = await collect_video_ids(page, username, max_videos=max_videos)

            if not video_urls:
                print("❌ Gak ada video yang ketemu, stop.")
                return

            # Jeda "baca-baca dulu" sebelum mulai buka video pertama,
            # biar transisi dari scroll profile -> buka video gak
            # keliatan instan/robotic
            print("😌 Jeda sejenak sebelum mulai buka video (biar natural)...")
            await asyncio.sleep(random.uniform(8, 15))

            results = []
            for i, url in enumerate(video_urls, start=1):
                print(f"🎬 [{i}/{len(video_urls)}] Scrape: {url}")
                try:
                    data = await scrape_video_stats(page, url, referer=profile_url)
                    results.append(data)
                    print(f"   ✅ likes={data['like_count']} comments={data['comment_count']} "
                          f"shares={data['share_count']} saves={data['save_count']} "
                          f"date={data['post_date_utc']}")
                except Exception as e:
                    print(f"   ⚠️ Gagal scrape video ini: {e}")

                # Delay panjang & human-like antar video
                delay = random.uniform(15, 30)
                print(f"   ⏳ Tunggu {delay:.1f}s sebelum video berikutnya...\n")
                await asyncio.sleep(delay)

            if results:
                with open(output_file, "w", newline="", encoding="utf-8") as f:
                    writer = csv.DictWriter(f, fieldnames=results[0].keys())
                    writer.writeheader()
                    writer.writerows(results)
                print(f"\n✅ SELESAI! {len(results)} video disimpan ke: {output_file}")
            else:
                print("❌ Tidak ada data yang berhasil di-scrape")

        except Exception as e:
            print(f"❌ Error: {e}")
            import traceback
            traceback.print_exc()

        finally:
            print("\n⏸️  Browser tetap terbuka 10 detik...")
            await asyncio.sleep(10)
            await context.close()


if __name__ == "__main__":
    asyncio.run(main())