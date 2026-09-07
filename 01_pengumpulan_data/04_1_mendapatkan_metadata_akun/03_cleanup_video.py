"""
CLEANUP: metadata_video.csv (gabungan)

Yang dibenerin:
1. Kolom 'description' dirapiin jadi 1 baris (newline -> spasi, whitespace
   berlebih di-collapse).
2. Baris yang like_count, comment_count, share_count, DAN save_count
   semuanya 0 (atau kosong) DIHAPUS -- ini indikasi extract-nya gagal
   diam-diam, bukan video yang beneran 0 engagement.

PENTING soal resume: scraper nge-skip video yang video_id-nya UDAH ADA di
metadata_video.csv. Begitu baris all-zero DIHAPUS dari sini, video itu
otomatis dianggap "belum discrape" lagi -> bakal ke-retry sendiri di run
scraper berikutnya. Gak perlu edit apapun di master CSV / script scraper.

Cara pakai:
    python 00e_cleanup_metadata_video.py

File asli di-backup dulu (.bak_TIMESTAMP) sebelum ditimpa. Baris yang
dihapus disimpan terpisah ke _removed_zero_rows.csv, buat jejak/referensi.
"""

import csv
import os
import re
import shutil
from datetime import datetime

# ⬇️ GANTI SESUAI PATH KAMU
ROOT_DIR = r"C:\Users\nesha\Tugas_akhir\seleksi-influencer"
INPUT_FILE = os.path.join(
    ROOT_DIR, "01_pengumpulan_data", "04_mendapatkan_metadata_akun", "metadata_video.csv"
)
REMOVED_LOG_FILE = os.path.join(
    ROOT_DIR, "01_pengumpulan_data", "04_mendapatkan_metadata_akun", "_removed_zero_rows.csv"
)

STAT_COLUMNS = ["like_count", "comment_count", "share_count", "save_count"]


def clean_text_field(raw_text):
    """Rapiin teks jadi 1 baris: newline -> spasi, whitespace berlebih di-collapse."""
    if not raw_text:
        return ""
    flattened = raw_text.replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
    flattened = re.sub(r'\s+', ' ', flattened)
    return flattened.strip()


def is_all_zero(row):
    """True kalau ke-4 kolom stats semuanya '0' (atau kosong)."""
    for col in STAT_COLUMNS:
        val = str(row.get(col, "")).strip()
        if val not in ("0", ""):
            return False
    return True


def cleanup():
    if not os.path.exists(INPUT_FILE):
        print(f"❌ File gak ketemu: {INPUT_FILE}")
        return

    with open(INPUT_FILE, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []
        rows = list(reader)

    print(f"📖 Baca {INPUT_FILE}")
    print(f"   Kolom: {fieldnames}")
    print(f"   Total baris: {len(rows)}")

    if not rows:
        print("⚠️  File kosong, gak ada yang di-cleanup.")
        return

    missing_cols = [c for c in STAT_COLUMNS if c not in fieldnames]
    if missing_cols:
        print(f"❌ Kolom stats gak lengkap, gak ketemu: {missing_cols}")
        return
    if "description" not in fieldnames:
        print("⚠️  Kolom 'description' gak ketemu, bagian rapiin bio di-skip.")

    # Backup dulu
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = INPUT_FILE + f".bak_{timestamp}"
    shutil.copy2(INPUT_FILE, backup_path)
    print(f"\n💾 Backup dibuat: {backup_path}")

    kept_rows = []
    removed_rows = []
    n_had_newline = 0

    for row in rows:
        # --- Cek all-zero DULU, kalau all-zero langsung masuk removed,
        # gak perlu repot rapiin description-nya (toh mau dibuang).
        if is_all_zero(row):
            removed_rows.append(row)
            continue

        # --- Rapiin description jadi 1 baris
        raw_desc = row.get("description", "")
        if raw_desc and ("\n" in raw_desc or "\r" in raw_desc):
            n_had_newline += 1

        new_row = dict(row)
        new_row["description"] = clean_text_field(raw_desc)
        kept_rows.append(new_row)

    print(f"\n   Baris all-zero (dihapus)              : {len(removed_rows)}")
    print(f"   Baris valid (disimpan)                 : {len(kept_rows)}")
    print(f"   - dari situ, ada newline di description : {n_had_newline}")

    if removed_rows:
        with open(REMOVED_LOG_FILE, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(removed_rows)
        print(f"\n📝 Baris yang dihapus disimpan ke: {REMOVED_LOG_FILE}")
        print(f"   (video_id di file ini bakal otomatis ke-retry di run scraper berikutnya)")

    # Tulis ulang file utama (tanpa baris all-zero, description udah rapi)
    with open(INPUT_FILE, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(kept_rows)

    print(f"\n✅ CLEANUP SELESAI")
    print(f"   {len(removed_rows)} baris all-zero dihapus")
    print(f"   {len(kept_rows)} baris valid tetap ada, description udah dirapiin")
    print(f"\n   Kalau ada yang salah, file asli ada di backup:")
    print(f"   {backup_path}")


if __name__ == "__main__":
    cleanup()