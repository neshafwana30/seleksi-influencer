"""
CLEANUP: metadata_profil.csv

Masalah yang dibenerin:
1. Kolom 'followers' dan 'following' isinya SAMA -- keduanya kebawa string
   gabungan kayak "153Following6.4MFollowers177.8MLikes". Ini bug di
   scraper (selector-nya nangkep 1 elemen besar yang isinya gabungan 3
   angka, bukan elemen followers/following yang terpisah).
   -> Script ini PARSE ulang string gabungan itu, pisahin jadi 3 kolom:
      following, followers, likes -- masing-masing angka PENUH (bukan
      "6.4M" lagi, tapi 6400000).
2. Kolom 'bio' ada yang mengandung newline (bio aslinya emang multi-baris
   di TikTok) -- di-flatten jadi 1 baris, whitespace berlebih dirapiin.

Cara pakai:
    python 00d_cleanup_metadata_profil.py

File asli di-backup dulu (.bak_TIMESTAMP) sebelum ditimpa.
"""

import csv
import os
import re
import shutil
from datetime import datetime

# ⬇️ GANTI SESUAI PATH KAMU
ROOT_DIR = r"C:\Users\nesha\Tugas_akhir\seleksi-influencer"
INPUT_FILE = os.path.join(
    ROOT_DIR, "01_pengumpulan_data", "04_mendapatkan_metadata_akun", "metadata_profil.csv"
)

# Kolom output yang baru: following/followers/likes dipisah, bio dirapiin
OUTPUT_FIELDS = ["username", "following", "followers", "likes", "bio",
                  "display_name", "is_verified", "avatar_url", "scraped_at"]

# Pattern buat parse string gabungan kayak:
# "153Following6.4MFollowers177.8MLikes"
# Grup 1: angka+suffix sebelum "Following"
# Grup 2: angka+suffix antara "Following" dan "Followers"
# Grup 3: angka+suffix antara "Followers" dan "Likes"
COMBINED_PATTERN = re.compile(
    r'([\d]*\.?[\d]+\s*[KMB]?)\s*Following'
    r'([\d]*\.?[\d]+\s*[KMB]?)\s*Followers'
    r'([\d]*\.?[\d]+\s*[KMB]?)\s*Likes',
    re.IGNORECASE,
)


def is_pure_number(text):
    """
    True kalau text isinya angka murni (cuma digit, boleh ada spasi di
    ujung yang di-strip dulu) -- kayak "421800" atau "38700000".
    Dipakai buat DETEKSI: apakah baris ini UDAH pernah dibersihin
    sebelumnya (angka penuh), atau MASIH raw gabungan (ada huruf
    "Following"/"Followers"/"Likes" di dalamnya).

    Kenapa perlu ini: kalau script ini di-run 2x di file yang SAMA (yang
    udah bersih dari run pertama), COMBINED_PATTERN gak bakal match sama
    sekali di angka murni kayak "421800" (soalnya patternnya nyari kata
    "Following"/"Followers"/"Likes" yang emang cuma ada di data MENTAH).
    Tanpa pengecekan ini, parse_combined_stats() bakal return (None, None,
    None) buat SEMUA baris yang udah bersih -> data bagus ke-hapus jadi
    kosong. Makanya baris yang UDAH angka murni harus di-skip dari proses
    parsing, langsung dipakai apa adanya.
    """
    if not text:
        return False
    return text.strip().isdigit()


def convert_shorthand_to_number(text):
    """
    Convert angka shorthand TikTok ke angka penuh (integer):
      "153"    -> 153
      "6.4M"   -> 6400000
      "177.8M" -> 177800000
      "19.5K"  -> 19500
      ""       -> None (kosong/gak valid)

    K = ribu (x1,000), M = juta (x1,000,000), B = milyar (x1,000,000,000)
    """
    if not text:
        return None
    text = text.strip()
    if not text:
        return None

    match = re.match(r'^([\d]*\.?[\d]+)\s*([KMB])?$', text, re.IGNORECASE)
    if not match:
        return None

    number_part = float(match.group(1))
    suffix = (match.group(2) or "").upper()

    multiplier = {"": 1, "K": 1_000, "M": 1_000_000, "B": 1_000_000_000}
    value = number_part * multiplier.get(suffix, 1)

    return int(round(value))


def parse_combined_stats(raw_text):
    """
    Parse string gabungan "153Following6.4MFollowers177.8MLikes" jadi
    3 angka penuh: (following, followers, likes).

    Return (None, None, None) kalau raw_text kosong / gak match pattern
    (misal baris yang followers/following/bio-nya emang kosong dari awal,
    kayak baris 'dokterprasadja' di data kamu -- itu artinya profil gagal
    ke-scrape total, bukan salah parse).
    """
    if not raw_text:
        return None, None, None

    match = COMBINED_PATTERN.search(raw_text)
    if not match:
        return None, None, None

    following_raw, followers_raw, likes_raw = match.groups()

    following = convert_shorthand_to_number(following_raw)
    followers = convert_shorthand_to_number(followers_raw)
    likes = convert_shorthand_to_number(likes_raw)

    return following, followers, likes


def clean_bio(raw_bio):
    """
    Rapiin bio jadi 1 baris:
    - Ganti semua newline (\\n, \\r\\n) jadi spasi
    - Collapse multiple whitespace jadi 1 spasi
    - Strip leading/trailing whitespace
    """
    if not raw_bio:
        return ""
    # Ganti newline jadi spasi dulu
    flattened = raw_bio.replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
    # Collapse whitespace berlebih (termasuk tab, multiple spasi) jadi 1 spasi
    flattened = re.sub(r'\s+', ' ', flattened)
    return flattened.strip()


def cleanup():
    if not os.path.exists(INPUT_FILE):
        print(f"❌ File gak ketemu: {INPUT_FILE}")
        return

    with open(INPUT_FILE, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        original_fields = reader.fieldnames or []
        rows = list(reader)

    print(f"📖 Baca {INPUT_FILE}")
    print(f"   Kolom asli: {original_fields}")
    print(f"   Total baris: {len(rows)}")

    if not rows:
        print("⚠️  File kosong, gak ada yang di-cleanup.")
        return

    # Backup dulu
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = INPUT_FILE + f".bak_{timestamp}"
    shutil.copy2(INPUT_FILE, backup_path)
    print(f"\n💾 Backup dibuat: {backup_path}")

    cleaned_rows = []
    n_parsed_ok = 0
    n_parse_failed = 0
    n_empty = 0
    n_already_clean = 0

    # ⬇️ Deteksi: apakah file ini SUDAH pernah dibersihin (punya kolom
    # 'likes' terpisah dari run cleanup sebelumnya)? Kalau iya, kita perlu
    # LEBIH HATI-HATI -- per baris, cek dulu apakah followers-nya masih
    # raw gabungan atau udah angka murni, baru putuskan mau di-parse atau
    # dipakai apa adanya.
    already_migrated_file = "likes" in original_fields

    for row in rows:
        username = row.get("username", "")
        raw_followers = row.get("followers", "")
        raw_bio = row.get("bio", "")

        if not raw_followers:
            # Baris kayak 'dokterprasadja' -- profil gagal ke-scrape dari awal
            following, followers, likes = None, None, None
            n_empty += 1

        elif is_pure_number(raw_followers):
            # ⬇️ BARU: data ini UDAH BERSIH (angka murni) -- entah karena
            # file ini emang udah pernah di-cleanup sebelumnya, atau
            # (di masa depan) scraper udah dibenerin buat langsung nulis
            # angka bersih dari awal. JANGAN diparse ulang pake
            # COMBINED_PATTERN (bakal gagal & jadi None) -- langsung
            # pakai value yang udah ada di kolom following/followers/likes
            # apa adanya.
            following = row.get("following", "").strip() or None
            followers = raw_followers.strip()
            likes = row.get("likes", "").strip() or None if already_migrated_file else None
            n_already_clean += 1

        else:
            # Data masih RAW gabungan (ada kata "Following"/"Followers"/
            # "Likes" di dalamnya) -- parse seperti biasa.
            following, followers, likes = parse_combined_stats(raw_followers)
            if following is None and followers is None and likes is None:
                print(f"   ⚠️  Gagal parse stats buat @{username}: '{raw_followers[:80]}'")
                n_parse_failed += 1
            else:
                n_parsed_ok += 1

        new_row = {
            "username": username,
            "following": following if following is not None else "",
            "followers": followers if followers is not None else "",
            "likes": likes if likes is not None else "",
            "bio": clean_bio(raw_bio),
            "display_name": row.get("display_name", ""),
            "is_verified": row.get("is_verified", ""),
            "avatar_url": row.get("avatar_url", ""),
            "scraped_at": row.get("scraped_at", ""),
        }
        cleaned_rows.append(new_row)

    # Tulis ulang file
    with open(INPUT_FILE, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_FIELDS)
        writer.writeheader()
        writer.writerows(cleaned_rows)

    print(f"\n✅ CLEANUP SELESAI")
    print(f"   Udah bersih sebelumnya (di-skip parsing) : {n_already_clean}")
    print(f"   Berhasil di-parse (dari raw gabungan)     : {n_parsed_ok}")
    print(f"   Gagal di-parse                            : {n_parse_failed}")
    print(f"   Kosong dari awal                          : {n_empty}")
    print(f"   Total ditulis ulang                       : {len(cleaned_rows)}")
    print(f"\n   Kolom baru: {OUTPUT_FIELDS}")
    print(f"   Kalau ada yang salah, file asli ada di backup:")
    print(f"   {backup_path}")

    if n_parse_failed > 0:
        print(f"\n⚠️  Ada {n_parse_failed} baris yang gagal di-parse (isi followers-nya")
        print(f"   ada tapi formatnya gak match pattern yang diharapkan).")
        print(f"   Cek manual baris-baris itu, mungkin formatnya beda dikit.")


if __name__ == "__main__":
    cleanup()