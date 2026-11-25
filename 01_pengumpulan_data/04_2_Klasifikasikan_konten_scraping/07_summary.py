"""
06_EXPORT_VIDEOS.py
===================
Export videos by category to separate CSV files

Input: labeled_metadata_video.csv
Output:
  - health_videos.csv (hanya health content)
  - not_health_videos.csv (hanya non-health content)
"""

import pandas as pd
import logging
from pathlib import Path

logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)

BASE_DIR = Path(r"01_pengumpulan_data/04_2_Klasifikasikan_konten_scraping")

INPUT_FILE = BASE_DIR / "labeled_metadata_video.csv"
HEALTH_CSV = BASE_DIR / "health_videos.csv"
NOT_HEALTH_CSV = BASE_DIR / "not_health_videos.csv"

def main():
    logger.info("\n" + "="*70)
    logger.info("EXPORT VIDEOS BY CATEGORY")
    logger.info("="*70 + "\n")
    
    # Load
    if not INPUT_FILE.exists():
        logger.error(f"❌ {INPUT_FILE} not found!")
        return False
    
    logger.info(f"Loading {INPUT_FILE.name}...")
    df = pd.read_csv(INPUT_FILE)
    logger.info(f"✅ Loaded {len(df)} videos\n")
    
    # Split by category
    health_df = df[df['predicted_label'] == 'health'].copy()
    not_health_df = df[df['predicted_label'] == 'not_health'].copy()
    
    # Export health
    logger.info(f"Exporting health videos...")
    health_df.to_csv(HEALTH_CSV, index=False, encoding='utf-8')
    logger.info(f"✅ {len(health_df)} health videos → {HEALTH_CSV.name}")
    
    # Export not-health
    logger.info(f"\nExporting not-health videos...")
    not_health_df.to_csv(NOT_HEALTH_CSV, index=False, encoding='utf-8')
    logger.info(f"✅ {len(not_health_df)} not-health videos → {NOT_HEALTH_CSV.name}")
    
    # Summary
    logger.info("\n" + "="*70)
    logger.info("SUMMARY")
    logger.info("="*70)
    logger.info(f"Total videos: {len(df)}")
    logger.info(f"Health videos: {len(health_df)} ({len(health_df)/len(df)*100:.1f}%)")
    logger.info(f"Not-health videos: {len(not_health_df)} ({len(not_health_df)/len(df)*100:.1f}%)")
    logger.info("\n" + "="*70 + "\n")
    
    # Show sample
    logger.info("📋 SAMPLE - Health Videos (first 3):\n")
    for idx, row in health_df.head(3).iterrows():
        logger.info(f"  @{row['username']}: {row['description'][:60]}...")
        logger.info(f"     Confidence: {row['predicted_confidence']}, Check: {row['check_this']}\n")
    
    logger.info("📋 SAMPLE - Not-Health Videos (first 3):\n")
    for idx, row in not_health_df.head(3).iterrows():
        logger.info(f"  @{row['username']}: {row['description'][:60]}...")
        logger.info(f"     Confidence: {row['predicted_confidence']}, Check: {row['check_this']}\n")
    
    return True

if __name__ == "__main__":
    success = main()
    exit(0 if success else 1)