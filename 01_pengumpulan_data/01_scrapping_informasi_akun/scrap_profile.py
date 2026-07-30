import asyncio
import logging
import os
import polars as pl

# -------------------------------------------------------------
# CONFIGURATION: Pembungkam Spam Log Browser Automation
# -------------------------------------------------------------
logging.basicConfig(level=logging.ERROR)
logging.getLogger("zendriver").setLevel(logging.CRITICAL)
logging.getLogger("uc_driver").setLevel(logging.CRITICAL)

from pytok.tiktok import PyTok
from pytok.utils import get_user_df

def crosscheck_progress(txt_path, csv_path):
    print("\n🔍 === MELAKUKAN CROSSCHECK DATA ===")
    if not os.path.exists(txt_path):
        print(f"❌ File TXT tidak ditemukan: {txt_path}")
        return [], False
        
    with open(txt_path, "r", encoding="utf-8") as f:
        all_usernames = [line.strip() for line in f if line.strip()]
    
    print(f"📄 Total Target di TXT  : {len(all_usernames)} akun")

    scraped_usernames = set()
    has_progress = False

    if os.path.exists(csv_path) and os.path.getsize(csv_path) > 0:
        try:
            existing_df = pl.read_csv(
                csv_path, 
                infer_schema=False, 
                truncate_ragged_lines=True,
                ignore_errors=True
            )
            
            if "unique_id" in existing_df.columns:
                scraped_usernames = set(existing_df["unique_id"].drop_nulls().to_list())
                has_progress = True
                print(f"📊 Akun aman di CSV    : {len(scraped_usernames)} akun")
        except Exception as e:
            print(f"⚠️ Gagal membaca CSV: {e}")
    else:
        print("📊 Akun aman di CSV    : 0 akun (File baru/kosong)")

    remaining_targets = [u for u in all_usernames if u not in scraped_usernames]
    print(f"🎯 SISA YANG DI-SCRAPE : {len(remaining_targets)} akun")
    print("=====================================\n")
    
    return remaining_targets, has_progress

async def run_bulk_profile_pipeline():
    input_file = r"01_pengumpulan_data\01_scrapping_informasi_akun\list_dokter.txt"
    output_file = r"01_pengumpulan_data\01_scrapping_informasi_akun\informasi_dokter.csv"
    
    target_usernames, has_progress = crosscheck_progress(input_file, output_file)
    is_first_row = not has_progress 

    if not target_usernames:
        print("✅ Semua username sudah selesai di-scrap. Tidak ada yang perlu dikerjakan!")
        return

    print(f"🚀 === MEMULAI SCRAPING UNTUK {len(target_usernames)} AKUN ===\n")
    
    async with PyTok(
        request_delay=60,            
        manual_captcha_solves=True,  
        log_captcha_solves=True,     
        headless=False              
    ) as api:
        
        for index, target_username in enumerate(target_usernames, 1):
            print(f"[{index}/{len(target_usernames)}] Menarik data: @{target_username}...")
            
            try:
                user = api.user(username=target_username)
                user_data = await user.info()
                
                # Buat DataFrame awal lewat utility pytok
                user_df = get_user_df([user_data])
                
                # --- FUNGSI PENCARI OTOMATIS (REKURSIF) ---
                # Mencari key tertentu di seluruh lapisan JSON tanpa peduli jalurnya
                def find_in_json(data, target_keys):
                    if isinstance(data, dict):
                        for key in target_keys:
                            if key in data:
                                return data[key]
                        for v in data.values():
                            res = find_in_json(v, target_keys)
                            if res is not None:
                                return res
                    elif isinstance(data, list):
                        for item in data:
                            res = find_in_json(item, target_keys)
                            if res is not None:
                                return res
                    return None

                # Ambil total likes (heartCount/heart) dan privateAccount
                total_likes = find_in_json(user_data, ['heartCount', 'heart']) or 0
                is_private = find_in_json(user_data, ['privateAccount']) or False

                # Timpa kolom num_likes secara paksa
                user_df = user_df.with_columns(pl.lit(int(total_likes)).alias("num_likes"))

                # Cek status private account
                status_text = "Private Account" if is_private else "Public"
                user_df = user_df.with_columns(pl.lit(status_text).alias("scraping_status"))
                
            except Exception as e:
                print(f"   ❌ Gagal/Private ketat untuk @{target_username}: {e}")
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
                    "scraping_status": ["Gagal ditarik / Kemungkinan Private Ketat"]
                })
            
            # Bersihkan Enter di Bio
            if "signature" in user_df.columns:
                user_df = user_df.with_columns(
                    pl.col("signature").cast(pl.String).str.replace_all(r"[\n\r]+", " ").str.strip_chars()
                )

            # Langsung Write CSV
            try:
                with open(output_file, "ab") as f_out:
                    user_df.write_csv(f_out, include_header=is_first_row)
                
                print(f"   💾 @{target_username} sukses masuk CSV!")
                is_first_row = False  
                
            except Exception as save_error:
                print(f"   ⚠️ Gagal write ke CSV untuk @{target_username}: {save_error}")

            if index < len(target_usernames):
                await asyncio.sleep(5)

    print(f"\n🎉 === SELESAI === 🎉")

if __name__ == "__main__":
    asyncio.run(run_bulk_profile_pipeline())