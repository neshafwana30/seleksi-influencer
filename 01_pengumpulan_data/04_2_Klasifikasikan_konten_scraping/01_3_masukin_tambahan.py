"""
PREPARE LABELED DATA - INIT SEBELUM LABELING
=============================================
Copy data dari videos_to_label.csv ke labeled_videos.csv
dengan deduplikasi (cek username + video_id)

Workflow:
1. Load videos_to_label.csv (sample yang mau di-label)
2. Load labeled_videos.csv (kalo ada, hasil labeling sebelumnya)
3. Deduplicate by (username, video_id) - jangan ada duplikat
4. Append yang baru ke labeled_videos.csv
5. Backup labeled_videos lama
6. Print summary

Jalanin SEKALI di awal sebelum mulai labeling!
"""

import pandas as pd
import logging
from datetime import datetime
from config import OUTPUT_VIDEOS_TO_LABEL, OUTPUT_LABELED_VIDEOS

logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)

def prepare_labeled_data():
    logger.info("\n" + "="*70)
    logger.info("PREPARE LABELED DATA - INIT SEBELUM LABELING")
    logger.info("="*70)
    
    # ===== LOAD VIDEOS TO LABEL =====
    logger.info(f"\n📂 Loading videos_to_label.csv...")
    try:
        df_to_label = pd.read_csv(OUTPUT_VIDEOS_TO_LABEL, dtype={'video_id': str})
        logger.info(f"✅ Loaded: {len(df_to_label)} videos")
    except Exception as e:
        logger.error(f"❌ Error: {e}")
        return False
    
    # ===== LOAD EXISTING LABELED =====
    logger.info(f"\n📂 Loading labeled_videos.csv...")
    try:
        df_labeled = pd.read_csv(OUTPUT_LABELED_VIDEOS, dtype={'video_id': str})
        logger.info(f"✅ Loaded: {len(df_labeled)} videos (existing)")
        has_existing = True
    except FileNotFoundError:
        logger.info(f"⚠️  File not found (first time, OK)")
        df_labeled = pd.DataFrame()
        has_existing = False
    except Exception as e:
        logger.error(f"❌ Error: {e}")
        return False
    
    # ===== IDENTIFY DUPLICATE KEY =====
    duplicate_key = ['username', 'video_id']
    
    # ===== BACKUP EXISTING =====
    if has_existing and len(df_labeled) > 0:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = OUTPUT_LABELED_VIDEOS.parent / f"labeled_videos_BACKUP_{timestamp}.csv"
        df_labeled.to_csv(backup_path, index=False)
        logger.info(f"✅ Backup created: {backup_path}")
    
    # ===== DEDUPLICATE =====
    logger.info(f"\n🔍 Deduplicating by (username, video_id)...")
    
    if has_existing and len(df_labeled) > 0:
        # Existing keys yang akan di-exclude
        existing_keys = set(
            df_labeled[duplicate_key].apply(tuple, axis=1)
        )
        logger.info(f"   Existing keys: {len(existing_keys)}")
        
        # Filter to_label - exclude yang sudah ada
        df_to_label['_key'] = df_to_label[duplicate_key].apply(tuple, axis=1)
        df_new = df_to_label[~df_to_label['_key'].isin(existing_keys)].copy()
        df_new = df_new.drop('_key', axis=1)
        
        logger.info(f"   New videos (setelah exclude): {len(df_new)}")
        logger.info(f"   Duplicates removed: {len(df_to_label) - len(df_new)}")
    else:
        # First time - semua dianggap baru
        df_new = df_to_label.copy()
        logger.info(f"   All {len(df_new)} videos dianggap baru (first time)")
    
    # ===== COMBINE =====
    logger.info(f"\n📝 Combining...")
    
    if has_existing and len(df_labeled) > 0:
        # Ensure columns match
        cols_to_keep = [col for col in df_labeled.columns]
        df_new_clean = df_new[cols_to_keep] if all(col in df_new.columns for col in cols_to_keep) else df_new
        
        df_combined = pd.concat([df_labeled, df_new_clean], ignore_index=True)
        logger.info(f"   Combined: {len(df_labeled)} (existing) + {len(df_new)} (new) = {len(df_combined)} total")
    else:
        df_combined = df_new.copy()
        logger.info(f"   Total: {len(df_combined)} videos")
    
    # ===== ENSURE COLUMNS =====
    # Add empty label columns kalo belum ada
    if 'manual_label' not in df_combined.columns:
        df_combined['manual_label'] = ''
    if 'label_timestamp' not in df_combined.columns:
        df_combined['label_timestamp'] = ''
    
    df_combined['manual_label'] = df_combined['manual_label'].fillna('').astype(str)
    df_combined['label_timestamp'] = df_combined['label_timestamp'].fillna('').astype(str)
    
    # ===== FINAL DEDUP (security) =====
    logger.info(f"\n🔒 Final security dedup (by username + video_id)...")
    n_before = len(df_combined)
    df_combined = df_combined.drop_duplicates(subset=duplicate_key, keep='first').reset_index(drop=True)
    n_after = len(df_combined)
    n_removed = n_before - n_after
    
    if n_removed > 0:
        logger.info(f"   Removed {n_removed} duplicates")
    else:
        logger.info(f"   No duplicates found ✅")
    
    # ===== SAVE =====
    logger.info(f"\n💾 Saving to {OUTPUT_LABELED_VIDEOS}...")
    df_combined.to_csv(OUTPUT_LABELED_VIDEOS, index=False)
    logger.info(f"✅ Saved: {len(df_combined)} videos")
    
    # ===== SUMMARY =====
    logger.info(f"\n{'='*70}")
    logger.info(f"✅ COMPLETE!")
    logger.info(f"{'='*70}")
    
    # Count labeled vs unlabeled
    labeled_count = (df_combined['manual_label'] != '').sum()
    unlabeled_count = len(df_combined) - labeled_count
    
    logger.info(f"\n📊 SUMMARY:")
    logger.info(f"   Total videos: {len(df_combined)}")
    logger.info(f"   Already labeled: {labeled_count}")
    logger.info(f"   Waiting to label: {unlabeled_count}")
    logger.info(f"   Influencers: {df_combined['username'].nunique()}")
    
    # Breakdown
    logger.info(f"\n📋 Top 10 influencers:")
    top_inf = df_combined['username'].value_counts().head(10)
    for inf, count in top_inf.items():
        labeled_inf = (df_combined[(df_combined['username'] == inf) & (df_combined['manual_label'] != '')]).shape[0]
        logger.info(f"   @{inf:25s}: {count:4d} videos ({labeled_inf} labeled)")
    
    logger.info(f"\n📁 File: {OUTPUT_LABELED_VIDEOS}")
    logger.info(f"{'='*70}\n")
    
    return True

if __name__ == "__main__":
    prepare_labeled_data()