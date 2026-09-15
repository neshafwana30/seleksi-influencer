"""
PREPARE SAMPLE FOR LABELING - 1000 VIDEOS
==========================================
Extract exactly 1000 random videos (proportional per influencer)
"""

import pandas as pd
import logging
from config import INPUT_METADATA, OUTPUT_VIDEOS_TO_LABEL, validate_paths

logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)

def prepare_sample():
    logger.info("\n" + "="*70)
    logger.info("PREPARE SAMPLE FOR LABELING - 1000 VIDEOS")
    logger.info("="*70)
    
    # Validate paths
    if not validate_paths():
        return False
    
    # Load data
    logger.info(f"\nLoading {INPUT_METADATA}...")
    try:
        df = pd.read_csv(INPUT_METADATA)
        logger.info(f"✅ Loaded {len(df)} total videos")
    except Exception as e:
        logger.error(f"❌ Error: {e}")
        return False
    
    # Check columns
    required_cols = ['username', 'video_id', 'video_url', 'description']
    if not all(col in df.columns for col in required_cols):
        logger.error(f"❌ Missing columns. Need: {required_cols}")
        return False
    
    # Proportional sampling
    logger.info(f"\nProportional sampling to get exactly 1000 videos...")
    unique_influencers = df['username'].unique()
    logger.info(f"Found {len(unique_influencers)} influencers\n")
    
    sample_dfs = []
    for influencer in unique_influencers:
        inf_videos = df[df['username'] == influencer]
        n_videos = len(inf_videos)
        
        # Proportional: (videos in this influencer / total) * 1000
        target_samples = max(1, int((n_videos / len(df)) * 1000))
        actual_samples = min(target_samples, n_videos)
        
        sample = inf_videos.sample(n=actual_samples, random_state=42)
        sample_dfs.append(sample)
        
        logger.info(f"  @{influencer:20s}: {n_videos:4d} videos → {actual_samples:3d} samples")
    
    # Combine
    sampled_df = pd.concat(sample_dfs, ignore_index=True)
    
    # Top-up if shortage
    if len(sampled_df) < 1000:
        shortage = 1000 - len(sampled_df)
        sampled_ids = set(sampled_df['video_id'].astype(str))
        remaining = df[~df['video_id'].astype(str).isin(sampled_ids)]
        
        if len(remaining) >= shortage:
            topup = remaining.sample(n=shortage, random_state=42)
            sampled_df = pd.concat([sampled_df, topup], ignore_index=True)
            logger.info(f"\n✅ Topped up with {shortage} videos to reach 1000")
    
    # Shuffle
    sampled_df = sampled_df.sample(frac=1, random_state=42).reset_index(drop=True)
    
    # Add columns
    sampled_df['manual_label'] = ''
    sampled_df['label_timestamp'] = ''
    
    # Save
    sampled_df.to_csv(OUTPUT_VIDEOS_TO_LABEL, index=False)
    
    logger.info(f"\n{'='*70}")
    logger.info(f"✅ SAMPLE PREPARED!")
    logger.info(f"Total: {len(sampled_df)} videos")
    logger.info(f"Saved to: {OUTPUT_VIDEOS_TO_LABEL}")
    logger.info("="*70 + "\n")
    
    return True

if __name__ == "__main__":
    prepare_sample()