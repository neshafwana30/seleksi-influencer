"""
ADD MORE SAMPLES - 1000 VIDEOS FROM NEW INFLUENCERS
===================================================
Ambil 1000 sample BARU hanya dari influencer yang BELUM ADA di videos_to_label.csv

Strategy:
1. Load videos_to_label.csv → cari siapa aja influencer unik yang udah ada
2. Load metadata_video.csv → exclude influencer yang udah ada
3. Random sample 1000 dari yang belum ada
4. Backup file lama
5. Append ke videos_to_label.csv
Total jadi 2000!
"""

import pandas as pd
import logging
from datetime import datetime
from pathlib import Path
from config import INPUT_METADATA, OUTPUT_VIDEOS_TO_LABEL, validate_paths

logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)

def add_more_samples():
    logger.info("\n" + "="*70)
    logger.info("ADD MORE SAMPLES - 1000 VIDEOS FROM NEW INFLUENCERS")
    logger.info("="*70)
    
    # Validate paths
    if not validate_paths():
        return False
    
    # ===== LOAD EXISTING DATA =====
    logger.info(f"\n📂 Loading existing samples...")
    try:
        df_existing = pd.read_csv(OUTPUT_VIDEOS_TO_LABEL)
        logger.info(f"✅ Loaded {len(df_existing)} videos from videos_to_label.csv")
    except FileNotFoundError:
        logger.error(f"❌ File not found: {OUTPUT_VIDEOS_TO_LABEL}")
        logger.error(f"   Please run prepare_sample.py first!")
        return False
    except Exception as e:
        logger.error(f"❌ Error loading existing: {e}")
        return False
    
    # ===== FIND EXISTING INFLUENCERS =====
    existing_influencers = set(df_existing['username'].unique())
    n_existing_influencers = len(existing_influencers)
    
    logger.info(f"\n📊 Influencer yang SUDAH di-sample:")
    logger.info(f"   Total: {n_existing_influencers} influencers")
    logger.info(f"\n   Daftar:")
    for inf in sorted(existing_influencers):
        count = len(df_existing[df_existing['username'] == inf])
        logger.info(f"     @{inf:25s}: {count:4d} videos")
    
    # ===== LOAD METADATA =====
    logger.info(f"\n📂 Loading metadata...")
    try:
        df_metadata = pd.read_csv(INPUT_METADATA, dtype={'video_id': str})
        logger.info(f"✅ Loaded {len(df_metadata)} total videos")
    except Exception as e:
        logger.error(f"❌ Error loading metadata: {e}")
        return False
    
    all_influencers = set(df_metadata['username'].unique())
    n_all_influencers = len(all_influencers)
    
    logger.info(f"   Total influencers di metadata: {n_all_influencers}")
    
    # ===== FIND NEW INFLUENCERS =====
    new_influencers = all_influencers - existing_influencers
    n_new_influencers = len(new_influencers)
    
    logger.info(f"\n🎯 Influencer BARU (belum di-sample):")
    logger.info(f"   Total: {n_new_influencers} influencers")
    logger.info(f"\n   Daftar:")
    for inf in sorted(new_influencers):
        count = len(df_metadata[df_metadata['username'] == inf])
        logger.info(f"     @{inf:25s}: {count:4d} videos")
    
    if n_new_influencers == 0:
        logger.error(f"\n❌ Semua influencer sudah ter-sample!")
        logger.error(f"   Tidak ada influencer baru yang bisa diambil.")
        return False
    
    # ===== FILTER DATA =====
    logger.info(f"\n🔍 Filter metadata (exclude existing influencers)...")
    df_new = df_metadata[df_metadata['username'].isin(new_influencers)].copy()
    logger.info(f"✅ Remaining: {len(df_new)} videos dari {n_new_influencers} new influencers")
    
    # ===== CHECK IF ENOUGH DATA =====
    if len(df_new) < 1000:
        logger.error(f"\n❌ Tidak cukup data!")
        logger.error(f"   Ada hanya {len(df_new)} videos dari influencer baru")
        logger.error(f"   Butuh 1000 videos untuk sample")
        return False
    
    # ===== RANDOM SAMPLE =====
    logger.info(f"\n🎲 Random sampling 1000 videos...")
    sample = df_new.sample(n=1000, random_state=42).reset_index(drop=True)
    logger.info(f"✅ Sampled: {len(sample)} videos")
    
    # Check breakdown
    logger.info(f"\n   Breakdown per influencer (sample):")
    sample_counts = sample['username'].value_counts()
    for inf, count in sample_counts.items():
        logger.info(f"     @{inf:25s}: {count:4d} videos")
    
    # ===== BACKUP EXISTING =====
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = OUTPUT_VIDEOS_TO_LABEL.parent / f"videos_to_label_BACKUP_{timestamp}.csv"
    df_existing.to_csv(backup_path, index=False)
    logger.info(f"\n✅ Backup created: {backup_path}")
    
    # ===== PREPARE NEW SAMPLES =====
    logger.info(f"\n✨ Preparing new samples...")
    
    # Make sure columns match
    required_cols = ['username', 'video_id', 'video_url', 'is_photo', 'like_count', 
                     'comment_count', 'share_count', 'save_count', 'description', 'scraped_at']
    
    for col in required_cols:
        if col not in sample.columns:
            logger.error(f"❌ Missing column in metadata: {col}")
            return False
    
    # Select columns only
    sample = sample[required_cols].copy()
    
    # Add label columns
    sample['manual_label'] = ''
    sample['label_timestamp'] = ''
    
    # ===== APPEND =====
    logger.info(f"✅ Combining with existing data...")
    df_combined = pd.concat([df_existing, sample], ignore_index=True)
    
    # Remove any duplicates (by video_id, keep first)
    n_before_dedup = len(df_combined)
    df_combined = df_combined.drop_duplicates(subset=['video_id'], keep='first').reset_index(drop=True)
    n_after_dedup = len(df_combined)
    n_removed_dupes = n_before_dedup - n_after_dedup
    
    if n_removed_dupes > 0:
        logger.info(f"   Removed {n_removed_dupes} duplicates")
    
    # Save
    logger.info(f"✅ Saving to {OUTPUT_VIDEOS_TO_LABEL}...")
    df_combined.to_csv(OUTPUT_VIDEOS_TO_LABEL, index=False)
    
    # ===== FINAL SUMMARY =====
    final_influencer_count = len(df_combined['username'].unique())
    
    logger.info(f"\n{'='*70}")
    logger.info(f"✅ COMPLETE!")
    logger.info(f"{'='*70}")
    logger.info(f"\n📊 SUMMARY:")
    logger.info(f"   Videos sebelumnya: {len(df_existing)}")
    logger.info(f"   Videos baru ditambah: {len(sample)}")
    logger.info(f"   Total sekarang: {len(df_combined)}")
    logger.info(f"   Influencers sebelumnya: {n_existing_influencers}")
    logger.info(f"   Influencers baru: {n_new_influencers}")
    logger.info(f"   Total influencers sekarang: {final_influencer_count}")
    
    logger.info(f"\n📁 Files:")
    logger.info(f"   Original → {OUTPUT_VIDEOS_TO_LABEL}")
    logger.info(f"   Backup   → {backup_path}")
    
    logger.info(f"\n{'='*70}\n")
    
    return True

if __name__ == "__main__":
    add_more_samples()