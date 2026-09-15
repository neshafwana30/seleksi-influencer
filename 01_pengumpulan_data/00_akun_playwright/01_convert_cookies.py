"""
Convert hasil export dari extension Cookie-Editor (tiktok_cookies_raw.json)
ke format yang bisa dipakai Playwright (tiktok_cookies.json).

Cara pakai:
1. Install extension "Cookie-Editor" di Chrome
2. Login TikTok di Chrome biasa (bukan incognito)
3. Di halaman tiktok.com, buka Cookie-Editor -> Export -> pilih JSON
4. Paste hasilnya ke file 'tiktok_cookies_raw.json' di folder yang sama
   dengan script ini
5. Run script ini
"""

import json
import os

BASE_DIR = r"01_pengumpulan_data\00_akun_playwright"
RAW_FILE = os.path.join(BASE_DIR, "tiktok_cookies_raw.json")
OUTPUT_FILE = os.path.join(BASE_DIR, "tiktok_cookies.json")

SAME_SITE_MAP = {
    "no_restriction": "None",
    "unspecified": "Lax",
    "lax": "Lax",
    "strict": "Strict",
    "none": "None",
}


def main():
    if not os.path.exists(RAW_FILE):
        print(f"❌ File {RAW_FILE} gak ketemu.")
        print("   Export cookies dari Cookie-Editor dulu, paste ke file itu.")
        return

    with open(RAW_FILE, "r", encoding="utf-8") as f:
        raw_cookies = json.load(f)

    converted = []
    for c in raw_cookies:
        # skip cookie non-tiktok kalau ada kebawa
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

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(converted, f, indent=2)

    print(f"✅ Berhasil convert {len(converted)} cookies TikTok")
    print(f"   Disimpan ke: {OUTPUT_FILE}")
    print("   Sekarang jalankan 'apply_cookies_to_playwright.py'")


if __name__ == "__main__":
    main()