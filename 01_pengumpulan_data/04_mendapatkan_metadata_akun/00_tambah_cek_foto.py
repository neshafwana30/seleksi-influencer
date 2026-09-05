"""
MIGRASI: Tambah kolom 'is_photo' ke metadata_video.csv yang LAMA (yang
ditulis sebelum kolom ini ada).

Kenapa perlu ini: script scraper versi baru nulis CSV dengan kolom
"is_photo" (True/False). Kalau file metadata_video.csv kamu udah ada
isinya dari run-run sebelumnya (belum ada kolom ini), baris-baris lama
itu gak punya nilai buat kolom ini -- bikin mismatch kalau dibaca ulang.

Asumsi yang dipakai script ini: SEMUA baris lama adalah video biasa
(bukan photo/slide), karena versi scraper sebelum ini emang belum bisa
nge-capture post foto sama sekali (chain-nya stop tiap ketemu /photo/).
Jadi aman nyimpulin: baris lama -> is_photo = False.

Cara kerja:
1. Baca metadata_video.csv yang lama
2. Kalau kolom 'is_photo' udah ada -> skip, gak diapa-apain (biar gak
   ketimpa kalau script ini gak sengaja dijalanin 2x)
3. Kalau kolom 'is_photo' BELUM ada -> tambahin ke SEMUA baris dengan
   value "False", di posisi setelah kolom 'video_url' (biar urutan
   kolom konsisten sama yang ditulis scraper versi baru)
4. Backup file asli dulu (.bak) sebelum nulis ulang, biar ada jaring
   pengaman kalau ada yang salah
5. Tulis ulang file dengan kolom baru
"""

import csv
import os
import shutil
from datetime import datetime

# ⬇️ GANTI SESUAI PATH KAMU
ROOT_DIR = r"C:\Users\nesha\Tugas_akhir\seleksi-influencer"
OUTPUT_VIDEO = os.path.join(
    ROOT_DIR, "01_pengumpulan_data", "04_mendapatkan_metadata_akun", "metadata_video.csv"
)

# Urutan kolom TARGET (harus sama persis kayak VIDEO_FIELDS di scraper
# versi baru, biar konsisten & gampang di-load ulang)
TARGET_FIELDS = ["username", "video_id", "video_url", "is_photo", "like_count",
                  "comment_count", "share_count", "save_count", "description",
                  "scraped_at"]


def migrate():
    if not os.path.exists(OUTPUT_VIDEO):
        print(f"❌ File gak ketemu: {OUTPUT_VIDEO}")
        print("   Gak ada yang perlu di-migrate (mungkin belum pernah scraping).")
        return

    # Baca semua baris + cek kolom apa aja yang ada sekarang
    with open(OUTPUT_VIDEO, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        current_fields = reader.fieldnames or []
        rows = list(reader)

    print(f"📖 Baca {OUTPUT_VIDEO}")
    print(f"   Kolom saat ini: {current_fields}")
    print(f"   Total baris: {len(rows)}")

    if "is_photo" in current_fields:
        print("\n✅ Kolom 'is_photo' SUDAH ADA di file ini.")
        print("   Gak ada yang perlu di-migrate, file udah versi baru.")
        return

    if not rows:
        print("\n⚠️  File ada tapi kosong (0 baris data). Gak ada yang perlu di-migrate.")
        return

    # Backup dulu sebelum nulis ulang
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = OUTPUT_VIDEO + f".bak_{timestamp}"
    shutil.copy2(OUTPUT_VIDEO, backup_path)
    print(f"\n💾 Backup dibuat: {backup_path}")

    # Tambahin is_photo=False ke semua baris, isi kolom yang mungkin
    # kurang (misal ada kolom baru lain di masa depan) dengan "" biar
    # DictWriter gak error.
    migrated_rows = []
    for row in rows:
        new_row = {field: row.get(field, "") for field in TARGET_FIELDS}
        new_row["is_photo"] = "False"  # asumsi: semua baris lama = video biasa
        migrated_rows.append(new_row)

    # Tulis ulang file dengan urutan kolom TARGET_FIELDS
    with open(OUTPUT_VIDEO, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=TARGET_FIELDS)
        writer.writeheader()
        writer.writerows(migrated_rows)

    print(f"\n✅ MIGRASI SELESAI")
    print(f"   {len(migrated_rows)} baris di-update dengan is_photo='False'")
    print(f"   Kolom sekarang: {TARGET_FIELDS}")
    print(f"\n   Kalau ada yang salah, file asli ada di backup:")
    print(f"   {backup_path}")


if __name__ == "__main__":
    migrate()