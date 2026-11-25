"""
01 - Scraping Akun Dokter/Kandidat Influencer Kesehatan
=========================================================
Tahap 3.1.2.1 (Identifikasi Kandidat Akun) pada proposal TA.

Alur:
1. extract_usernames_from_raw()
   Membaca hasil copy-paste pencarian TikTok (teks mentah berisi "Avatar for
   <username>" atau "@username") dan mengekstrak daftar username unik.
2. scrape_account_profiles()
   Melakukan scraping metadata profil (followers, bio, jumlah video, dst)
   untuk setiap username menggunakan PyTok, dengan dukungan resume (skip
   akun yang sudah berhasil di-scrape sebelumnya).

Konsolidasi dari:
- 01_pengumpulan_data/01_scrapping_informasi_akun/01_filterdokter.py
- 01_pengumpulan_data/01_scrapping_informasi_akun/02_scrap_profile.py

Prasyarat: cookies TikTok sudah disiapkan lewat 00_setup_tiktok_login.py.

CATATAN EKSEKUSI: Step 2 (scrape_account_profiles) adalah scraping berat
(request_delay 60 detik + jeda antar akun) -- TIDAK perlu dijalankan ulang
kalau final_scripts/output/01_metadata_akun_mentah.csv sudah ada, karena
hasilnya sudah dipakai di tahap 02. Fungsi ini otomatis skip kalau output
sudah ada. Hapus file output-nya manual kalau memang mau scraping ulang
(misal nambah kandidat akun baru).
"""

import asyncio
import logging
import re
from pathlib import Path

import polars as pl

logging.basicConfig(level=logging.ERROR)
logging.getLogger("zendriver").setLevel(logging.CRITICAL)
logging.getLogger("uc_driver").setLevel(logging.CRITICAL)

from pytok.tiktok import PyTok
from pytok.utils import get_user_df

# -------------------------------------------------------------------------
# KONFIGURASI PATH
# -------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent
LEGACY_DATA_DIR = BASE_DIR / "01_pengumpulan_data" / "01_scrapping_informasi_akun"
OUTPUT_DIR = Path(__file__).resolve().parent / "output" / "01_scraping_akun_dokter"

# Input mentah (hasil copy-paste pencarian TikTok) -- tetap di lokasi lama
# karena ini artefak manual sekali-paste, bukan hasil scraping.
RAW_SEARCH_RESULTS = LEGACY_DATA_DIR / "list_dokter_raw.txt"

# Semua input/output pipeline final ada di final_scripts/output/01_scraping_akun_dokter/
USERNAMES_CLEAN = OUTPUT_DIR / "username_kandidat_mentah.txt"      # output step 1 / input step 2
PROFILE_METADATA_OUTPUT = OUTPUT_DIR / "metadata_akun_mentah.csv"  # output step 2 (SUDAH ADA, skip scraping ulang)

REQUEST_DELAY_SECONDS = 5


# -------------------------------------------------------------------------
# STEP 1 - Ekstraksi username dari hasil pencarian TikTok (teks mentah)
# -------------------------------------------------------------------------
def extract_usernames_from_raw(raw_path: Path = RAW_SEARCH_RESULTS,
                                output_path: Path = USERNAMES_CLEAN) -> list[str]:
    """Parse teks hasil copy-paste pencarian TikTok menjadi daftar username unik."""
    if output_path.exists():
        print(f"[Step 1] Sudah ada -> {output_path} (skip, tidak diproses ulang)")
        with open(output_path, "r", encoding="utf-8") as f:
            return [line.strip() for line in f if line.strip()]

    pattern_avatar = re.compile(r"Avatar for\s+([^\s\n]+)", re.IGNORECASE)
    pattern_at = re.compile(r"@([^\s\n]+)")

    if not raw_path.exists():
        raise FileNotFoundError(f"File hasil pencarian tidak ditemukan: {raw_path}")

    usernames = set()
    with open(raw_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()

            match_avatar = pattern_avatar.search(line)
            if match_avatar:
                usernames.add(match_avatar.group(1).lower())
                continue

            match_at = pattern_at.search(line)
            if match_at:
                usernames.add(match_at.group(1).lower())

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        for username in sorted(usernames):
            f.write(f"{username}\n")

    print(f"[Step 1] {len(usernames)} username unik diekstrak -> {output_path}")
    return sorted(usernames)


# -------------------------------------------------------------------------
# STEP 2 - Scraping metadata profil (resume-aware)
# -------------------------------------------------------------------------
def _get_remaining_targets(txt_path: Path, csv_path: Path) -> tuple[list[str], bool]:
    """Bandingkan daftar target username dengan yang sudah ada di CSV output."""
    with open(txt_path, "r", encoding="utf-8") as f:
        all_usernames = [line.strip() for line in f if line.strip()]

    scraped_usernames = set()
    has_progress = False

    if csv_path.exists() and csv_path.stat().st_size > 0:
        try:
            existing_df = pl.read_csv(
                csv_path, infer_schema=False,
                truncate_ragged_lines=True, ignore_errors=True,
            )
            if "unique_id" in existing_df.columns:
                scraped_usernames = set(existing_df["unique_id"].drop_nulls().to_list())
                has_progress = True
        except Exception as e:
            print(f"[Step 2] Gagal membaca progres CSV sebelumnya: {e}")

    remaining = [u for u in all_usernames if u not in scraped_usernames]
    print(f"[Step 2] Target: {len(all_usernames)} | Sudah di-scrape: {len(scraped_usernames)} | Sisa: {len(remaining)}")
    return remaining, has_progress


def _find_in_json(data, target_keys):
    """Cari salah satu dari target_keys di struktur JSON bertingkat (rekursif)."""
    if isinstance(data, dict):
        for key in target_keys:
            if key in data:
                return data[key]
        for v in data.values():
            res = _find_in_json(v, target_keys)
            if res is not None:
                return res
    elif isinstance(data, list):
        for item in data:
            res = _find_in_json(item, target_keys)
            if res is not None:
                return res
    return None


async def scrape_account_profiles(input_file: Path = USERNAMES_CLEAN,
                                   output_file: Path = PROFILE_METADATA_OUTPUT) -> None:
    """Scrape metadata profil TikTok untuk setiap username di input_file."""
    if output_file.exists():
        print(f"[Step 2] Sudah ada -> {output_file} (skip scraping berat, data lama dipakai)")
        return

    if not input_file.exists():
        raise FileNotFoundError(
            f"File username tidak ditemukan: {input_file}. Jalankan extract_usernames_from_raw() dulu."
        )

    target_usernames, has_progress = _get_remaining_targets(input_file, output_file)
    is_first_row = not has_progress

    if not target_usernames:
        print("[Step 2] Semua username sudah selesai di-scrape.")
        return

    print(f"[Step 2] Memulai scraping untuk {len(target_usernames)} akun...")

    async with PyTok(
        request_delay=60,
        manual_captcha_solves=True,
        log_captcha_solves=True,
        headless=False,
    ) as api:
        for index, target_username in enumerate(target_usernames, 1):
            print(f"[{index}/{len(target_usernames)}] Menarik data: @{target_username}...")

            try:
                user = api.user(username=target_username)
                user_data = await user.info()
                user_df = get_user_df([user_data])

                total_likes = _find_in_json(user_data, ["heartCount", "heart"]) or 0
                is_private = _find_in_json(user_data, ["privateAccount"]) or False

                user_df = user_df.with_columns(pl.lit(int(total_likes)).alias("num_likes"))
                status_text = "Private Account" if is_private else "Public"
                user_df = user_df.with_columns(pl.lit(status_text).alias("scraping_status"))

            except Exception as e:
                print(f"   Gagal/Private untuk @{target_username}: {e}")
                user_df = pl.DataFrame({
                    "id": [""],
                    "unique_id": [target_username],
                    "nickname": [""],
                    "signature": [""],
                    "verified": [False],
                    "num_following": [0],
                    "num_followers": [0],
                    "num_videos": [0],
                    "num_likes": [0],
                    "createtime": [""],
                    "scraping_status": ["Gagal ditarik / Kemungkinan Private Ketat"],
                })

            if "signature" in user_df.columns:
                user_df = user_df.with_columns(
                    pl.col("signature").cast(pl.String).str.replace_all(r"[\n\r]+", " ").str.strip_chars()
                )

            try:
                output_file.parent.mkdir(parents=True, exist_ok=True)
                with open(output_file, "ab") as f_out:
                    user_df.write_csv(f_out, include_header=is_first_row)
                print(f"   @{target_username} tersimpan.")
                is_first_row = False
            except Exception as save_error:
                print(f"   Gagal menyimpan @{target_username}: {save_error}")

            if index < len(target_usernames):
                await asyncio.sleep(REQUEST_DELAY_SECONDS)

    print("[Step 2] Selesai.")


# -------------------------------------------------------------------------
# MAIN
# -------------------------------------------------------------------------
async def main():
    extract_usernames_from_raw()
    await scrape_account_profiles()


if __name__ == "__main__":
    asyncio.run(main())
