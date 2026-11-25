"""
generate_scrape_list.py
========================
Generate scraping target list dengan STRATIFIED SAMPLING by month + engagement.

Join health_videos.csv dengan metadata_video.csv (by video_id) buat dapetin:
- Tanggal dari scraped_at di metadata_video (ISO format: 2026-09-02T10:16:55.168688)
- Engagement metrics (like_count: "79.9K", comment_count: "316", dll)

Strategi:
1. Load health_videos.csv & metadata_video.csv
2. Join by video_id
3. Parse tanggal (extract bulan) - handle ISO format dengan microseconds
4. Parse like/comment count (handle "79.9K" format)
5. Hitung engagement rate (comment_count / like_count)
6. Group by influencer + bulan
7. Per group: split 50% HIGH engagement, 50% MEDIUM
8. Random sample, cap 20 video per influencer

Input:
    01_pengumpulan_data/04_2_Klasifikasikan_konten_scraping/health_videos.csv
    01_pengumpulan_data/04_1_mendapatkan_metadata_akun/metadata_video.csv

Output:
    01_pengumpulan_data/05_scrap_komentar/to_scrape.csv
"""

import csv
import random
from pathlib import Path
from collections import defaultdict
from datetime import datetime

# ============================================================
# PATH
# ============================================================
HEALTH_CSV = Path(r"01_pengumpulan_data/04_2_Klasifikasikan_konten_scraping/health_videos.csv")
METADATA_CSV = Path(r"01_pengumpulan_data/04_1_mendapatkan_metadata_akun/metadata_video.csv")

OUTPUT_BASE_DIR = Path(r"01_pengumpulan_data/05_scrap_komentar")
OUTPUT_BASE_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_CSV = OUTPUT_BASE_DIR / "to_scrape.csv"

MAX_VIDEOS_PER_INFLUENCER = 20
RANDOM_SEED = 42

# ============================================================
# FIELDS
# ============================================================
OUTPUT_FIELDS = ["username", "video_id", "month", "engagement_tier", "scraped", "scraped_at"]


def parse_date(date_str):
    """
    Parse date string, return month (1-12). 
    Handle ISO format: 2026-09-02T10:16:55.168688
    """
    if not date_str:
        return None
    date_str = str(date_str).strip()
    
    # Format yang paling umum di TikTok metadata: ISO dengan/tanpa microseconds
    formats = [
        "%Y-%m-%dT%H:%M:%S.%f",  # 2026-09-02T10:16:55.168688
        "%Y-%m-%dT%H:%M:%S",      # 2026-09-02T10:16:55
        "%Y-%m-%d %H:%M:%S.%f",   # 2026-09-02 10:16:55.168688
        "%Y-%m-%d %H:%M:%S",      # 2026-09-02 10:16:55
        "%Y-%m-%d",               # 2026-09-02
        "%Y/%m/%d",
        "%d-%m-%Y",
        "%d/%m/%Y",
    ]
    
    for fmt in formats:
        try:
            dt = datetime.strptime(date_str, fmt)
            return dt.month
        except ValueError:
            continue
    
    print(f"   ⚠️ Gak bisa parse tanggal: '{date_str}'")
    return None


def parse_count_string(count_str):
    """
    Parse count string dengan K/M suffix: "79.9K" -> 79900, "1.2M" -> 1200000
    Atau plain number: "316" -> 316
    """
    if not count_str:
        return 0
    
    count_str = str(count_str).strip().upper()
    
    try:
        # Kalau plain number
        if not any(c.isalpha() for c in count_str):
            return int(float(count_str))
        
        # Kalau ada K/M/B suffix
        if "K" in count_str:
            return int(float(count_str.replace("K", "")) * 1_000)
        elif "M" in count_str:
            return int(float(count_str.replace("M", "")) * 1_000_000)
        elif "B" in count_str:
            return int(float(count_str.replace("B", "")) * 1_000_000_000)
        else:
            return int(float(count_str))
    except (ValueError, AttributeError):
        return 0


def calculate_engagement_rate(comment_count, like_count):
    """
    Calculate engagement rate: comment_count / like_count.
    Higher rate = more people comment per like (= genuine engagement).
    """
    try:
        like_count = int(like_count) if like_count else 0
        comment_count = int(comment_count) if comment_count else 0
        if like_count == 0:
            return comment_count  # Fallback
        return comment_count / like_count
    except (ValueError, TypeError):
        return 0


def main():
    print("=" * 70)
    print("GENERATE SCRAPING TARGET LIST (STRATIFIED BY MONTH + ENGAGEMENT)")
    print("=" * 70)

    # Check files exist
    if not HEALTH_CSV.exists():
        print(f"❌ {HEALTH_CSV} tidak ditemukan!")
        return
    if not METADATA_CSV.exists():
        print(f"❌ {METADATA_CSV} tidak ditemukan!")
        return

    # Load metadata_video (punya tanggal & engagement metrics)
    print(f"\n📂 Loading metadata...")
    metadata = {}
    with open(METADATA_CSV, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            video_id = row.get("video_id")
            if video_id:
                metadata[video_id] = row
    print(f"   ✅ Loaded {len(metadata)} videos dari metadata_video.csv")

    # Load health_videos
    print(f"\n📂 Loading health videos...")
    health_videos = []
    with open(HEALTH_CSV, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            health_videos.append(row)
    print(f"   ✅ Loaded {len(health_videos)} videos dari health_videos.csv")

    # ⭐ Join: health_videos + metadata
    print(f"\n🔗 Joining & parsing...")
    all_videos = []
    missing_metadata = 0
    parse_errors = 0

    for hv in health_videos:
        video_id = hv.get("video_id")
        meta = metadata.get(video_id)

        if not meta:
            missing_metadata += 1
            continue

        # Parse tanggal dari metadata scraped_at
        scraped_at = meta.get("scraped_at", "")
        month = parse_date(scraped_at)

        if not month:
            parse_errors += 1
            continue

        # Parse like_count & comment_count (handle K/M suffix)
        like_count = parse_count_string(meta.get("like_count", 0))
        comment_count = parse_count_string(meta.get("comment_count", 0))

        # Hitung engagement rate
        engagement_rate = calculate_engagement_rate(comment_count, like_count)

        all_videos.append({
            "username": hv.get("username"),
            "video_id": video_id,
            "month": month,
            "engagement_rate": engagement_rate,
            "like_count": like_count,
            "comment_count": comment_count,
            "scraped_at": scraped_at,
        })

    print(f"   ✅ Joined {len(all_videos)} videos")
    if missing_metadata > 0:
        print(f"   ⚠️ {missing_metadata} videos gak ada di metadata (skip)")
    if parse_errors > 0:
        print(f"   ⚠️ {parse_errors} videos punya tanggal yang gak valid (skip)")

    if not all_videos:
        print(f"❌ Gak ada video yang bisa di-join!")
        return

    # ⭐ Group by influencer + month
    groups = defaultdict(lambda: defaultdict(list))
    for video in all_videos:
        username = video.get("username")
        month = video.get("month")
        groups[username][month].append(video)

    # ⭐ Per group: split 50-50 (high vs medium engagement)
    random.seed(RANDOM_SEED)
    to_scrape = []
    influencer_count = defaultdict(int)

    for username, months_videos in groups.items():
        # Sort videos per month by engagement rate (descending)
        for month, videos in months_videos.items():
            videos.sort(key=lambda v: v["engagement_rate"], reverse=True)

            # Split 50-50
            split_idx = len(videos) // 2
            high_engagement = videos[:split_idx]  # Top 50%
            medium_engagement = videos[split_idx:]  # Bottom/medium 50%

            # Sample random dari masing-masing split
            remaining = MAX_VIDEOS_PER_INFLUENCER - influencer_count[username]
            if remaining <= 0:
                break

            n_high = min(len(high_engagement), remaining // 2 + 1)
            n_medium = min(len(medium_engagement), remaining // 2)

            sampled_high = random.sample(high_engagement, n_high) if n_high > 0 else []
            sampled_medium = random.sample(medium_engagement, n_medium) if n_medium > 0 else []

            # Add ke to_scrape
            for video in sampled_high + sampled_medium:
                if influencer_count[username] >= MAX_VIDEOS_PER_INFLUENCER:
                    break

                to_scrape.append({
                    "username": username,
                    "video_id": video.get("video_id"),
                    "month": month,
                    "engagement_tier": "HIGH" if video in sampled_high else "MEDIUM",
                    "scraped": "False",
                    "scraped_at": "",
                })
                influencer_count[username] += 1

            if influencer_count[username] >= MAX_VIDEOS_PER_INFLUENCER:
                break

    # Shuffle final list
    random.shuffle(to_scrape)

    print(f"\n📊 Summary:")
    print(f"   Total videos matched: {len(all_videos)}")
    print(f"   Total influencer: {len(influencer_count)}")
    print(f"   Total video ke-sample: {len(to_scrape)}")
    if len(influencer_count) > 0:
        print(f"   Rata-rata per influencer: {len(to_scrape) / len(influencer_count):.1f}")

    # Count HIGH vs MEDIUM
    if len(to_scrape) > 0:
        high_count = sum(1 for v in to_scrape if v["engagement_tier"] == "HIGH")
        medium_count = len(to_scrape) - high_count
        print(f"   HIGH engagement: {high_count} ({high_count/len(to_scrape)*100:.0f}%)")
        print(f"   MEDIUM engagement: {medium_count} ({medium_count/len(to_scrape)*100:.0f}%)")

        # Count per month
        month_names = {1: "Jan", 2: "Feb", 3: "Mar", 4: "Apr", 5: "May", 6: "Jun", 
                       7: "Jul", 8: "Aug", 9: "Sep", 10: "Oct", 11: "Nov", 12: "Dec"}
        month_count = defaultdict(int)
        for v in to_scrape:
            month_count[v["month"]] += 1
        print(f"   Distribution by month:")
        for month in sorted(month_count.keys()):
            print(f"      {month_names.get(month, f'Month {month}')}: {month_count[month]} videos")

    # Tulis ke CSV
    with open(OUTPUT_CSV, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_FIELDS)
        writer.writeheader()
        writer.writerows(to_scrape)

    print(f"\n✅ Scrape list generated: {OUTPUT_CSV}")
    print(f"   Random seed: {RANDOM_SEED} (reproducible)")
    print(f"\n📋 Kolom output:")
    print(f"   - username: influencer handle")
    print(f"   - video_id: video ID")
    print(f"   - month: bulan video (1-12, dari metadata scraped_at)")
    print(f"   - engagement_tier: HIGH (top 50% comment rate) atau MEDIUM")
    print(f"   - scraped: True/False status")
    print(f"   - scraped_at: timestamp (akan di-update pas scraping)")
    print(f"\n🚀 Sekarang jalankan scraper:")
    print(f"   python 07_SCRAPE_COMMENTS_2AKUN_FINAL.py")


if __name__ == "__main__":
    main()