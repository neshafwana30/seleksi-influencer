import asyncio
import csv
import random
from datetime import datetime
from playwright.async_api import async_playwright

async def scrape_tiktok_comments(video_url, output_file="comments.csv", max_comments=100):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        page = await browser.new_page()
        
        try:
            print(f"📺 Buka video: {video_url}")
            await page.goto(video_url, wait_until="load", timeout=60000)
            await asyncio.sleep(3)
            
            # Klik tab Comments
            print("🖱️  Klik tab Comments...")
            try:
                await page.click("text=Comments", timeout=5000)
            except:
                await page.click('[data-e2e="comment-icon"]', timeout=5000)
            
            print("\n" + "="*60)
            print("🔒 KALAU MUNCUL CAPTCHA, SOLVE MANUAL SEKARANG")
            print("   Script akan otomatis lanjut begitu comments muncul")
            print("   (timeout 2 menit, jadi santai aja solve-nya)")
            print("="*60 + "\n")
            
            # Wait sampai comment container beneran muncul (otomatis lanjut kalau udah solved)
            await page.wait_for_selector('[data-e2e="comment-level-1"]', timeout=120000)
            print("✅ Comments terdeteksi! Mulai scraping...\n")
            await asyncio.sleep(2)
            
            comments_data = []
            comment_count = 0
            scroll_count = 0
            same_count_streak = 0
            
            while comment_count < max_comments and same_count_streak < 5:
                comment_containers = await page.query_selector_all('[data-e2e="comment-level-1"]')
                
                before = len(comments_data)
                
                for container in comment_containers:
                    if comment_count >= max_comments:
                        break
                    
                    try:
                        username_elem = await container.query_selector('[data-e2e="comment-username-1"]')
                        author = (await username_elem.text_content()).strip() if username_elem else "unknown"
                        
                        # Ambil semua teks dari container, lalu pisahkan dari username
                        full_text = (await container.text_content()).strip()
                        
                        # Coba selector spesifik untuk text comment
                        text_elem = await container.query_selector('[data-e2e="comment-text-1"], p, span[class*="Text"]')
                        text = (await text_elem.text_content()).strip() if text_elem else full_text
                        
                        # Like count
                        like_elem = await container.query_selector('[data-e2e="comment-like-count-1"]')
                        likes = (await like_elem.text_content()).strip() if like_elem else "0"
                        
                        if not text or len(text) < 1:
                            continue
                        
                        # Skip duplikat
                        key = (author, text)
                        if any((c['author_name'], c['text']) == key for c in comments_data):
                            continue
                        
                        row = {
                            'author_name': author,
                            'text': text[:500],
                            'likes': likes,
                            'scraped_at': datetime.now().isoformat()
                        }
                        comments_data.append(row)
                        comment_count += 1
                        
                        print(f"  ✅ [{comment_count}] @{author}: {text[:60]}")
                    
                    except Exception:
                        continue
                
                after = len(comments_data)
                if after == before:
                    same_count_streak += 1
                else:
                    same_count_streak = 0
                
                # Scroll comment panel (bukan whole page) untuk load lebih banyak
                scroll_count += 1
                print(f"  🔄 Scroll comments panel #{scroll_count}...")
                try:
                    await page.evaluate("""
                        () => {
                            const panel = document.querySelector('[data-e2e="comment-level-1"]')?.closest('div[class*="Scroll"], div[style*="overflow"]');
                            if (panel) panel.scrollTop = panel.scrollHeight;
                            else window.scrollBy(0, 800);
                        }
                    """)
                except:
                    await page.mouse.wheel(0, 800)
                
                # Delay human-like sebelum scroll berikutnya
                delay = random.uniform(3, 6)
                await asyncio.sleep(delay)
            
            if comments_data:
                with open(output_file, 'w', newline='', encoding='utf-8') as f:
                    writer = csv.DictWriter(f, fieldnames=comments_data[0].keys())
                    writer.writeheader()
                    writer.writerows(comments_data)
                
                print(f"\n✅ SELESAI!")
                print(f"   📊 Total: {comment_count} comments")
                print(f"   📁 File: {output_file}")
            else:
                print("❌ Tidak ada comments yang berhasil di-scrape")
        
        except Exception as e:
            print(f"❌ Error: {e}")
            import traceback
            traceback.print_exc()
        
        finally:
            print("\n⏸️  Browser tetap terbuka 10 detik...")
            await asyncio.sleep(10)
            await browser.close()

async def main():
    video_url = "https://www.tiktok.com/@tirtacipeng/video/7678524985136254229"
    await scrape_tiktok_comments(
        video_url=video_url,
        output_file="comments_tirtacipeng.csv",
        max_comments=100
    )

if __name__ == "__main__":
    asyncio.run(main())