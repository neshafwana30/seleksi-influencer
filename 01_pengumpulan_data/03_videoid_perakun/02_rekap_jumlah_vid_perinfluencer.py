"""
Rekap jumlah video per influencer dari video_ids_master.csv.

Cuma hitung video yang needs_comment_scrape == True (masuk rentang
1 Jan 2026 - 31 Agu 2026 WIB), karena video di luar rentang itu gak
akan diproses ke tahap comment scraping.

Output: rekap_video_per_influencer.csv, kolom:
    username, jumlah_video
Diurutkan dari yang paling banyak upload video.
"""

import csv
import os
from collections import Counter

DATA_DIR = r"C:\Users\nesha\Tugas_akhir\seleksi-influencer\01_pengumpulan_data\03_videoid_perakun"
INPUT_CSV = os.path.join(DATA_DIR, "video_ids_master.csv")
OUTPUT_CSV = os.path.join(DATA_DIR, "rekap_video_per_influencer.csv")


def parse_needs_scrape(value: str) -> bool:
    """
    Kolom needs_comment_scrape ditulis csv.DictWriter dari nilai Python
    bool (True/False), jadi hasilnya string "True"/"False". Handle juga
    kemungkinan lowercase/whitespace biar aman.
    """
    return str(value).strip().lower() == "true"


def main():
    if not os.path.exists(INPUT_CSV):
        print(f"❌ File gak ketemu: {INPUT_CSV}")
        return

    counter = Counter()
    total_rows = 0
    total_true = 0

    with open(INPUT_CSV, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            total_rows += 1
            if parse_needs_scrape(row.get("needs_comment_scrape", "")):
                counter[row["username"]] += 1
                total_true += 1

    if not counter:
        print("⚠️ Gak ada video dengan needs_comment_scrape=True.")
        return

    # urutkan dari yang paling banyak upload
    sorted_counts = counter.most_common()

    with open(OUTPUT_CSV, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["username", "jumlah_video"])
        writer.writerows(sorted_counts)

    print(f"📊 Total baris di master CSV     : {total_rows}")
    print(f"📊 Total video needs_scrape=True : {total_true}")
    print(f"📊 Jumlah influencer punya video : {len(counter)}")
    print(f"\n{'Username':30s} | Jumlah Video")
    print("-" * 46)
    for username, count in sorted_counts:
        print(f"{username:30s} | {count}")

    print(f"\n✅ Rekap tersimpan di: {OUTPUT_CSV}")


if __name__ == "__main__":
    main()