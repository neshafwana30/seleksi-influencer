"""
CHECK_COOKIES_STATE.py
=======================
Utility untuk verify cookies file dan browser profile state

Ini helpful buat debugging sebelum run scraping script
"""

import json
from pathlib import Path

DATA_DIR = Path("01_pengumpulan_data")
COOKIES_FILE = DATA_DIR / "00_akun_playwright" / "tiktok_cookies.json"  # Correct path!
PROFILE_DIR = DATA_DIR / "04_2_Klasifikasikan_konten_scraping" / "tiktok_browser_profile"


def check_cookies_file():
    """Check cookies file exists dan valid"""
    print("\n" + "=" * 70)
    print("🔍 CHECK COOKIES FILE")
    print("=" * 70)
    
    if not COOKIES_FILE.exists():
        print(f"\n❌ COOKIES FILE NOT FOUND!")
        print(f"   Expected: {COOKIES_FILE}")
        print(f"   Should be at: 01_pengumpulan_data/00_akun_playwright/tiktok_cookies.json")
        print(f"   Action: Run apply_cookies.py script dulu!")
        return False
    
    print(f"✅ File exists: {COOKIES_FILE}")
    
    # Check file size
    file_size = COOKIES_FILE.stat().st_size
    if file_size < 100:
        print(f"⚠️  File too small ({file_size} bytes) - might be empty or corrupted")
        return False
    
    print(f"✅ File size: {file_size} bytes")
    
    # Try load JSON
    try:
        with open(COOKIES_FILE, "r", encoding="utf-8") as f:
            cookies = json.load(f)
        
        if not isinstance(cookies, list):
            print(f"⚠️  Cookies is not a list, it's: {type(cookies)}")
            return False
        
        print(f"✅ JSON valid, contains {len(cookies)} cookies")
        
        # Sample first cookie
        if cookies:
            first = cookies[0]
            print(f"\n   Sample cookie #1:")
            print(f"   - name: {first.get('name', '?')}")
            print(f"   - domain: {first.get('domain', '?')}")
            print(f"   - value length: {len(first.get('value', ''))} chars")
        
        return True
        
    except json.JSONDecodeError as e:
        print(f"❌ JSON INVALID: {e}")
        return False
    except Exception as e:
        print(f"❌ Error reading file: {e}")
        return False


def check_profile_dir():
    """Check browser profile folder"""
    print("\n" + "=" * 70)
    print("🔍 CHECK BROWSER PROFILE")
    print("=" * 70)
    
    if not PROFILE_DIR.exists():
        print(f"⚠️  Profile directory doesn't exist: {PROFILE_DIR}")
        print(f"   This is OK - akan dibuat saat apply_cookies.py runs")
        return None
    
    print(f"✅ Profile exists: {PROFILE_DIR}")
    
    # Check size
    try:
        total_size = sum(f.stat().st_size for f in PROFILE_DIR.rglob('*') if f.is_file())
        print(f"   Total size: {total_size / 1024 / 1024:.1f} MB")
    except Exception:
        pass
    
    # Check important files
    important_files = [
        "Default/Cookies",
        "Default/Local Storage/leveldb",
        "Default/Network Persistent State",
    ]
    
    print(f"\n   Checking key files:")
    for fname in important_files:
        fpath = PROFILE_DIR / fname
        if fpath.exists():
            print(f"   ✅ {fname}")
        else:
            print(f"   ⚠️  {fname} (missing)")
    
    return True


def check_health_videos():
    """Check health_videos.csv exists"""
    print("\n" + "=" * 70)
    print("🔍 CHECK INPUT FILE (health_videos.csv)")
    print("=" * 70)
    
    input_file = DATA_DIR / "04_2_Klasifikasikan_konten_scraping" / "health_videos.csv"
    
    if not input_file.exists():
        print(f"❌ INPUT FILE NOT FOUND!")
        print(f"   Expected: {input_file}")
        print(f"   Action: Run 06_EXPORT_VIDEOS.py dulu!")
        return False
    
    print(f"✅ File exists: {input_file.name}")
    
    # Count lines
    try:
        with open(input_file, "r", encoding="utf-8") as f:
            lines = len(f.readlines())
        print(f"✅ Contains {lines - 1} videos (+ 1 header)")
        return True
    except Exception as e:
        print(f"⚠️  Error reading file: {e}")
        return False


def main():
    print("\n\n")
    print(" " * 20 + "🔧 COOKIES & SETUP CHECKER 🔧")
    print(" " * 20 + "=" * 40)
    
    results = {
        "cookies": check_cookies_file(),
        "profile": check_profile_dir(),
        "input": check_health_videos(),
    }
    
    print("\n" + "=" * 70)
    print("📊 SUMMARY")
    print("=" * 70)
    
    print(f"\n✅ = Ready     ⚠️ = Warning (might be OK)     ❌ = Error (must fix)")
    print(f"\nCookies file:       {'✅' if results['cookies'] else '❌'}")
    print(f"Browser profile:    {'✅' if results['profile'] else '⚠️' if results['profile'] is None else '❌'}")
    print(f"Input file:         {'✅' if results['input'] else '❌'}")
    
    print(f"\n" + "=" * 70)
    
    if results['cookies'] and results['input']:
        print("✅ READY TO SCRAPE!")
        print("\n👉 Next steps:")
        print("   1. If profile_dir missing or old: run apply_cookies.py")
        print("   2. Then run: python 07_SCRAPE_COMMENTS.py")
        return 0
    else:
        print("⚠️ FIX THESE FIRST:")
        if not results['cookies']:
            print("   - Run: python apply_cookies.py")
        if not results['input']:
            print("   - Run: python ../04_2_Klasifikasikan_konten_scraping/06_EXPORT_VIDEOS.py")
        return 1


if __name__ == "__main__":
    exit(main())