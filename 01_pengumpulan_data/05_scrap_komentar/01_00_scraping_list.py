"""
generate_scrape_list.py
========================
Generate scraping target list dari health_videos.csv hasil klasifikasi.
Apply cap 20 video/influencer, tulis ke CSV dengan kolom tracking.

Input:
    01_pengumpulan_data/04_2_Klasifikasikan_konten_scraping/health_videos.csv
    (output dari langkah klasifikasi sebelumnya)

Output:
    01_pengumpulan_data/05_scrap_komentar/to_scrape.csv
    (list video yang harus di-scrape, dengan status tracking)

Kolom output:
    - username: influencer handle
    - video_id: video ID
    - scraped: True/False (status)
    - scraped_at: timestamp kapan di-scrape (kosong di awal)
"""

import csv
from pathlib import Path
from collections import defaultdict
from datetime import datetime

# ============================================================
# PATH
# ============================================================
ROOT_DIR = Path(r"01_pengumpulan_data/04_2_Klasifikasikan_konten_scraping")
INPUT_CSV = ROOT_DIR / "health_videos.csv"

OUTPUT_BASE_DIR = Path(r"01_pengumpulan_data/05_scrap_komentar")
OUTPUT_BASE_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_CSV = OUTPUT_BASE_DIR / "to_scrape.csv"

MAX_VIDEOS_PER_INFLUENCER = 20

# ============================================================
# FIELDS
# ============================================================
OUTPUT_FIELDS = ["username", "video_id", "scraped", "scraped_at"]


def main():
    print("=" * 70)
    print("GENERATE SCRAPING TARGET LIST")
    print("=" * 70)

    if not INPUT_CSV.exists():
        print(f"❌ Input CSV gak ketemu: {INPUT_CSV}")
        return

    # Load semua health videos
    all_videos = []
    with open(INPUT_CSV, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            all_videos.append(row)

    print(f"✅ Loaded {len(all_videos)} health videos dari klasifikasi")

    # Cap 20 video per influencer
    count_per_influencer = defaultdict(int)
    to_scrape = []

    for video in all_videos:
        username = video.get("username")
        video_id = video.get("video_id")

        if count_per_influencer[username] >= MAX_VIDEOS_PER_INFLUENCER:
            continue

        count_per_influencer[username] += 1
        to_scrape.append({
            "username": username,
            "video_id": video_id,
            "scraped": "False",  # Default: belum di-scrape
            "scraped_at": "",
        })

    print(f"\n📊 Summary:")
    print(f"   Total videos di input: {len(all_videos)}")
    print(f"   Setelah cap {MAX_VIDEOS_PER_INFLUENCER}/influencer: {len(to_scrape)}")
    print(f"   Jumlah influencer: {len(count_per_influencer)}")

    # Tulis ke CSV
    with open(OUTPUT_CSV, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_FIELDS)
        writer.writeheader()
        writer.writerows(to_scrape)

    print(f"\n✅ Scrape list generated: {OUTPUT_CSV}")
    print(f"\n📋 Kolom:")
    print(f"   - username: influencer handle")
    print(f"   - video_id: video ID")
    print(f"   - scraped: True/False status")
    print(f"   - scraped_at: timestamp (akan di-update pas scraping)")
    print(f"\n🚀 Sekarang jalankan scraper:")
    print(f"   python 07_SCRAPE_COMMENTS_2AKUN_FINAL.py")


if __name__ == "__main__":
    main()