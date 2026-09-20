#!/usr/bin/env python3
"""
10_DIAGNOSTIC_SIMPLE.py
========================
Simple one-script diagnostic:
1. Load CSV
2. Random pick 1 influencer
3. Navigate to profile (with cookies login)
4. Scroll grid asal pick 1 video
5. Click video (SPA routing)
6. Pause user for manual DOM inspection
7. Auto inspect & analyze
8. Recommend selectors

Run: python 10_DIAGNOSTIC_SIMPLE.py
"""

import asyncio
import csv
import json
import re
import random
from pathlib import Path
from datetime import datetime
from playwright.async_api import async_playwright


# ============================================================
# CONFIG
# ============================================================
CSV_PATH = Path(r"01_pengumpulan_data/05_scrap_komentar/health_videos.csv")
COOKIES_FILE = Path(r"01_pengumpulan_data/00_akun_playwright/tiktok_cookies.json")
PROFILE_DIR = Path(r"01_pengumpulan_data/04_2_Klasifikasikan_konten_scraping/tiktok_browser_profile")

DIAGNOSTICS_DIR = Path("diagnostics")
DIAGNOSTICS_DIR.mkdir(exist_ok=True, parents=True)

REALISTIC_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)


# ============================================================
# LOAD CSV & PICK RANDOM INFLUENCER
# ============================================================
def load_csv_and_pick_influencer():
    """Load CSV, group by username, random pick 1 influencer"""
    if not CSV_PATH.exists():
        print(f"❌ CSV not found: {CSV_PATH}")
        return None, None
    
    # Load & group by username
    videos_by_user = {}
    try:
        with open(CSV_PATH, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row.get("predicted_label") != "health":
                    continue
                username = row.get("username")
                video_id = row.get("video_id")
                if username and video_id:
                    if username not in videos_by_user:
                        videos_by_user[username] = []
                    videos_by_user[username].append(row)
    except Exception as e:
        print(f"❌ Error loading CSV: {e}")
        return None, None
    
    if not videos_by_user:
        print(f"❌ No health videos found!")
        return None, None
    
    # Random pick influencer
    username = random.choice(list(videos_by_user.keys()))
    videos = videos_by_user[username]
    
    print(f"✅ Loaded {len(videos_by_user)} influencers")
    print(f"✅ Random picked: @{username} ({len(videos)} health videos)")
    
    return username, videos


# ============================================================
# LOAD COOKIES
# ============================================================
def load_cookies_from_json():
    """Load cookies dari tiktok_cookies.json"""
    if not COOKIES_FILE.exists():
        print(f"⚠️  Cookies file not found: {COOKIES_FILE}")
        print(f"   Run: python apply_cookies_script.py dulu!")
        return None
    
    try:
        with open(COOKIES_FILE, "r", encoding="utf-8") as f:
            cookies = json.load(f)
        print(f"✅ Loaded {len(cookies)} cookies")
        return cookies
    except Exception as e:
        print(f"❌ Error loading cookies: {e}")
        return None


# ============================================================
# HELPER: Extract video ID dari grid
# ============================================================
async def get_grid_video_ids(page):
    """Ambil video IDs dari grid yang visible sekarang"""
    try:
        anchors = await page.query_selector_all('a[href*="/video/"]')
        results = []
        seen = set()
        
        for a in anchors:
            try:
                href = await a.get_attribute("href")
                if "/video/" in href:
                    vid = href.split("/video/")[1].split("?")[0].strip("/")
                    if vid and vid.isdigit() and vid not in seen:
                        seen.add(vid)
                        results.append((vid, a))
            except:
                continue
        
        return results
    except:
        return []


async def wait_url_change(page, old_url, timeout_ms=8000):
    """Wait sampai URL berubah (pas klik video)"""
    try:
        await page.wait_for_function(
            "oldUrl => window.location.href !== oldUrl",
            arg=old_url,
            timeout=timeout_ms,
        )
        return True
    except:
        return False


# ============================================================
# EXTRACT CLASSES & ATTRIBUTES
# ============================================================
def get_class_patterns_and_attrs(html: str):
    """Parse HTML & extract class names, data-attributes"""
    result = {
        "class_patterns": {},
        "data_attributes": set(),
    }
    
    for match in re.finditer(r'class=["\']([^"\']+)["\']', html):
        for cls in match.group(1).split():
            result["class_patterns"][cls] = result["class_patterns"].get(cls, 0) + 1
    
    for match in re.finditer(r'(data-[a-z\-]+)=["\']([^"\']+)["\']', html):
        result["data_attributes"].add(f"{match.group(1)}={match.group(2)}")
    
    result["data_attributes"] = sorted(list(result["data_attributes"]))
    return result


# ============================================================
# MAIN DIAGNOSTIC
# ============================================================
async def diagnostic_flow(username, videos, cookies):
    """
    Profile → Scroll → Pick random video → Click → Inspect DOM
    """
    
    profile_url = f"https://www.tiktok.com/@{username}"
    
    print(f"\n{'=' * 80}")
    print(f"🔍 DIAGNOSTIC - Profile Grid Scroll → Video Click → DOM Inspect")
    print(f"{'=' * 80}")
    print(f"Profile: {profile_url}")
    print(f"{'=' * 80}")
    
    async with async_playwright() as p:
        # Launch context dengan cookies
        print(f"\n📱 Launching browser dengan cookies...")
        context = await p.chromium.launch_persistent_context(
            str(PROFILE_DIR),
            headless=False,
            viewport={"width": 1400, "height": 900},
            user_agent=REALISTIC_UA,
            args=["--disable-blink-features=AutomationControlled"],
        )
        
        # Apply cookies
        await context.add_cookies(cookies)
        print(f"✅ Cookies applied ({len(cookies)} items)")
        
        page = context.pages[0] if context.pages else await context.new_page()
        
        try:
            # 1. Navigate to profile
            print(f"\n1️⃣  Navigate to profile...")
            try:
                await page.goto(profile_url, wait_until="load", timeout=30000)
                await asyncio.sleep(2)
                print(f"   ✅ Profile loaded")
            except Exception as e:
                print(f"   ❌ Failed: {e}")
                await context.close()
                return None
            
            # 2. Scroll grid sampai ketemu video
            print(f"\n2️⃣  Scroll grid asal pick 1 video...")
            scroll_count = 0
            max_scrolls = 10
            picked_video = None
            
            while scroll_count < max_scrolls:
                grid_videos = await get_grid_video_ids(page)
                print(f"   Scroll {scroll_count + 1}: Found {len(grid_videos)} videos")
                
                if len(grid_videos) > 0:
                    # Random pick dari video yang visible
                    vid, elem = random.choice(grid_videos)
                    picked_video = (vid, elem)
                    print(f"   ✅ Picked random: {vid}")
                    break
                
                # Scroll
                try:
                    await page.evaluate("window.scrollBy(0, window.innerHeight * 0.7)")
                    await asyncio.sleep(random.uniform(1, 2))
                except:
                    break
                
                scroll_count += 1
            
            if not picked_video:
                print(f"   ❌ No videos found in grid!")
                await context.close()
                return None
            
            # 3. Click video
            print(f"\n3️⃣  Click video (SPA routing)...")
            vid, elem = picked_video
            try:
                await elem.scroll_into_view_if_needed()
                await asyncio.sleep(0.5)
                await elem.click(timeout=15000)
                print(f"   ✅ Clicked")
            except Exception as e:
                print(f"   ❌ Failed to click: {e}")
                await context.close()
                return None
            
            # Wait for URL change
            old_url = profile_url
            opened = await wait_url_change(page, old_url, timeout_ms=8000)
            if not opened:
                print(f"   ❌ Video modal not opened")
                await context.close()
                return None
            
            await asyncio.sleep(2)
            print(f"   ✅ Video opened (current URL: {page.url})")
            
            # 4. Try click comments button
            print(f"\n4️⃣  Try opening comments section...")
            comment_selectors = [
                '[data-e2e="comment-icon"]',
                '[aria-label="Comments"]',
                'button[aria-label*="comment" i]',
            ]
            
            for sel in comment_selectors:
                try:
                    elem = await page.query_selector(sel)
                    if elem and await elem.is_visible():
                        print(f"   ✅ Found: {sel}")
                        await elem.click(timeout=5000)
                        await asyncio.sleep(2)
                        print(f"   ✅ Clicked comments")
                        break
                except:
                    pass
            
            # 5. PAUSE for user inspection
            print(f"\n5️⃣  PAUSE - Manual inspection")
            print(f"   " + "-" * 76)
            print(f"   DevTools buka? (F12)")
            print(f"   SCROLL comments section di video")
            print(f"   INSPECT element - catat:")
            print(f"      • Class names (comment-item, CommentItem, dll)")
            print(f"      • data-e2e attributes")
            print(f"      • Tag structure (div, li, article)")
            print(f"   PRESS ENTER when done...")
            
            input(f"\n   👉 PRESS ENTER...")
            
            # 6. Inspect DOM
            print(f"\n6️⃣  Inspecting DOM...")
            
            try:
                full_html = await page.content()
            except Exception as e:
                print(f"   ❌ Failed to get HTML: {e}")
                await context.close()
                return None
            
            # Save HTML
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            html_file = DIAGNOSTICS_DIR / f"dom_grid_{username}_{vid[:10]}_{timestamp}.html"
            with open(html_file, "w", encoding="utf-8") as f:
                f.write(full_html)
            print(f"   ✅ HTML saved: {html_file.name}")
            
            # Test selectors
            print(f"\n   Testing selectors...")
            
            test_selectors = [
                ('[data-e2e="comment-item"]', 'comment-item'),
                ('[class*="CommentItemContainer"]', 'CommentItemContainer'),
                ('[class*="CommentItem"]', 'CommentItem'),
                ('[class*="CommentList"]', 'CommentList'),
                ('[class*="comment-item"]', 'comment-item lowercase'),
                ('[class*="comment"]', 'comment broad'),
                ('[class*="Comment"]', 'Comment broad'),
                ('[role="article"]', 'role=article'),
                ('article', 'article tag'),
                ('li', 'li tag'),
            ]
            
            selector_results = {}
            for selector, desc in test_selectors:
                try:
                    elems = await page.query_selector_all(selector)
                    count = len(elems)
                    selector_results[selector] = {"found": count > 0, "count": count, "desc": desc}
                    status = "✅" if count > 0 else "❌"
                    print(f"      {status} {selector:<45} → {count}")
                except Exception as e:
                    selector_results[selector] = {"found": False, "error": str(e)[:30]}
            
            # Parse classes & attributes
            print(f"\n   Extracting class patterns...")
            elem_info = get_class_patterns_and_attrs(full_html)
            
            # Save results
            structure_info = {
                "timestamp": datetime.now().isoformat(),
                "method": "Profile Grid Scroll → Click → SPA Routing",
                "profile": profile_url,
                "video_id": vid,
                "selector_results": selector_results,
                "class_patterns": elem_info["class_patterns"],
                "data_attributes": elem_info["data_attributes"],
            }
            
            json_file = DIAGNOSTICS_DIR / f"dom_grid_{username}_{vid[:10]}_{timestamp}.json"
            with open(json_file, "w", encoding="utf-8") as f:
                json.dump(structure_info, f, indent=2, ensure_ascii=False)
            print(f"   ✅ JSON saved: {json_file.name}")
            
            # 7. Analyze & recommend
            print(f"\n{'=' * 80}")
            print(f"7️⃣  ANALYSIS & RECOMMENDATIONS")
            print(f"{'=' * 80}")
            
            working = {
                sel: info for sel, info in selector_results.items()
                if info.get("found")
            }
            
            print(f"\n✅ WORKING SELECTORS ({len(working)}):")
            if working:
                sorted_working = sorted(working.items(), key=lambda x: x[1]["count"], reverse=True)
                for sel, info in sorted_working:
                    print(f"   • {sel:<45} ({info['count']} found)")
            else:
                print(f"   ❌ NONE! Comments might not be loaded yet.")
                print(f"      → Try manual scroll first, then click comments button explicitly")
            
            # Top class patterns
            print(f"\n📋 TOP CLASS PATTERNS:")
            sorted_classes = sorted(elem_info["class_patterns"].items(), key=lambda x: x[1], reverse=True)
            
            relevant = [c for c, cnt in sorted_classes if any(
                kw in c.lower() for kw in ['comment', 'reply', 'item', 'list']
            )]
            
            if relevant:
                print(f"   (Comment-related):")
                for cls in relevant[:5]:
                    print(f"      • {cls}")
            
            # Recommendations
            print(f"\n🎯 RECOMMENDED SELECTORS")
            print(f"{'=' * 80}")
            
            recommendations = []
            
            if working:
                sorted_working = sorted(working.items(), key=lambda x: x[1]["count"], reverse=True)
                print(f"\n   🏆 TIER 1 (Proven):")
                for sel, info in sorted_working[:4]:
                    recommendations.append(sel)
                    print(f"      '{sel}',  # {info['count']} found")
            
            print(f"\n   🥈 TIER 2 (Generated):")
            if "comment-item" in elem_info["class_patterns"]:
                recommendations.append('[class*="comment-item"]')
                print(f"      '[class*=\"comment-item\"]',")
            
            if "CommentItem" in elem_info["class_patterns"]:
                recommendations.append('[class*="CommentItem"]')
                print(f"      '[class*=\"CommentItem\"]',")
            
            print(f"\n   🥉 TIER 3 (Fallback):")
            recommendations.append('[class*="Comment"]')
            print(f"      '[class*=\"Comment\"]',")
            recommendations.append('[role=\"article\"]')
            print(f"      '[role=\"article\"]',")
            
            # Code snippet
            print(f"\n📝 CODE SNIPPET (copy to 07_SCRAPE_COMMENTS.py line ~330):")
            print(f"{'=' * 80}")
            print(f"""
    item_selectors = [
""", end="")
            
            for sel in recommendations[:7]:
                print(f"        '{sel}',")
            
            print(f"""    ]
""")
            
            # Summary
            print(f"\n{'=' * 80}")
            print(f"✅ DIAGNOSTIC COMPLETE")
            print(f"{'=' * 80}")
            print(f"\n📁 Output files:")
            print(f"   JSON:  {json_file.name}")
            print(f"   HTML:  {html_file.name}")
            print(f"   Dir:   {DIAGNOSTICS_DIR}")
            
            print(f"\n📋 Next:")
            print(f"   1. Review selectors above")
            print(f"   2. Copy CODE SNIPPET")
            print(f"   3. Update extract_comments_from_dom() in 07_SCRAPE_COMMENTS.py (line ~330)")
            print(f"   4. Test scraping on 2-3 videos")
            
            await context.close()
            
            return {
                "json_file": json_file,
                "html_file": html_file,
                "recommendations": recommendations,
            }
        
        except Exception as e:
            print(f"\n❌ Error: {e}")
            import traceback
            traceback.print_exc()
            await context.close()
            return None


# ============================================================
# MAIN
# ============================================================
async def main():
    print(f"\n{'=' * 80}")
    print(f"  🔍 TikTok Comments DOM Diagnostic (Grid → Click → Inspect)")
    print(f"{'=' * 80}")
    
    # Load CSV & pick influencer
    print(f"\n📂 Loading CSV...")
    username, videos = load_csv_and_pick_influencer()
    
    if not username or not videos:
        print(f"❌ Failed to load/pick!")
        return
    
    # Load cookies
    print(f"\n🔐 Loading cookies...")
    cookies = load_cookies_from_json()
    
    if not cookies:
        print(f"❌ Failed to load cookies!")
        return
    
    # Run diagnostic
    result = await diagnostic_flow(username, videos, cookies)
    
    if result:
        print(f"\n✅ Diagnostic complete!")
    else:
        print(f"\n❌ Diagnostic failed!")


if __name__ == "__main__":
    asyncio.run(main())