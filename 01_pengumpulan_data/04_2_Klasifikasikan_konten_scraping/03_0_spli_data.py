"""
verify_split_integrity.py
=========================
Verify bahwa split 70/30 benar-benar stratified per influencer.
Ensure SEMUA 45 influencer punya BOTH training dan test data.

Usage:
    python verify_split_integrity.py
"""

import pandas as pd
import json
from pathlib import Path
from collections import Counter
from config import OUTPUT_LABELED_VIDEOS

def verify_split():
    """Check split integrity"""
    
    print("\n" + "="*80)
    print("SPLIT INTEGRITY VERIFICATION")
    print("="*80)
    
    if not OUTPUT_LABELED_VIDEOS.exists():
        print(f"❌ {OUTPUT_LABELED_VIDEOS} not found!")
        return False
    
    # Load data
    df = pd.read_csv(OUTPUT_LABELED_VIDEOS)
    df_labeled = df[(df['manual_label'].notna()) & (df['manual_label'] != '')].copy()
    
    print(f"\n✅ Loaded {len(df_labeled)} labeled videos")
    print(f"   Total influencers: {df_labeled['username'].nunique()}")
    
    # Simulate split 70/30
    train_split_ratio = 0.7
    train_data = []
    test_data = []
    
    for username in sorted(df_labeled['username'].unique()):
        inf_data = df_labeled[df_labeled['username'] == username]
        n = len(inf_data)
        split_idx = int(n * train_split_ratio)
        
        inf_data_shuffled = inf_data.sample(frac=1, random_state=42)
        train_data.append(inf_data_shuffled.iloc[:split_idx])
        test_data.append(inf_data_shuffled.iloc[split_idx:])
    
    train_df = pd.concat(train_data, ignore_index=True)
    test_df = pd.concat(test_data, ignore_index=True)
    
    print(f"\n📊 SPLIT RESULTS:")
    print(f"   Train (70%): {len(train_df)} videos")
    print(f"   Test (30%):  {len(test_df)} videos")
    print(f"   Total:       {len(df_labeled)} videos")
    
    # Verification
    original_influencers = set(df_labeled['username'].unique())
    train_influencers = set(train_df['username'].unique())
    test_influencers = set(test_df['username'].unique())
    
    print(f"\n" + "="*80)
    print("VERIFICATION CHECKS:")
    print("="*80)
    
    # Check 1
    missing_in_train = original_influencers - train_influencers
    if missing_in_train:
        print(f"\n❌ CHECK 1 FAILED: {len(missing_in_train)} influencers missing from TRAIN")
        print(f"   Missing: {missing_in_train}")
        return False
    else:
        print(f"\n✅ CHECK 1 PASSED: All {len(original_influencers)} influencers in TRAIN set")
    
    # Check 2
    missing_in_test = original_influencers - test_influencers
    if missing_in_test:
        print(f"\n❌ CHECK 2 FAILED: {len(missing_in_test)} influencers missing from TEST")
        print(f"   Missing: {missing_in_test}")
        return False
    else:
        print(f"\n✅ CHECK 2 PASSED: All {len(original_influencers)} influencers in TEST set")
    
    # Check 3: Each influencer has both train and test
    both_sets = train_influencers & test_influencers
    if len(both_sets) != len(original_influencers):
        print(f"\n❌ CHECK 3 FAILED: Not all influencers in both sets")
        return False
    else:
        print(f"\n✅ CHECK 3 PASSED: All {len(original_influencers)} influencers in BOTH train AND test")
    
    # Detailed per-influencer breakdown
    print(f"\n" + "="*80)
    print("DETAILED PER-INFLUENCER BREAKDOWN (45 influencers):")
    print("="*80 + "\n")
    print(f"{'Influencer':<25} {'Total':<10} {'Train':<10} {'Test':<10} {'Train%':<10} {'Status':<15}")
    print("-" * 90)
    
    all_ok = True
    min_warn_threshold = 3
    
    for username in sorted(original_influencers):
        total = len(df_labeled[df_labeled['username'] == username])
        train_count = len(train_df[train_df['username'] == username])
        test_count = len(test_df[test_df['username'] == username])
        train_pct = (train_count / total * 100) if total > 0 else 0
        
        status = "✓ OK"
        if train_count == 0 or test_count == 0:
            status = "❌ FAIL"
            all_ok = False
        elif train_count < min_warn_threshold or test_count < min_warn_threshold:
            status = "⚠️  WARN (small)"
        
        print(f"{username:<25} {total:<10} {train_count:<10} {test_count:<10} {train_pct:>6.1f}%  {status:<15}")
    
    print("-" * 90)
    print(f"{'TOTAL':<25} {len(df_labeled):<10} {len(train_df):<10} {len(test_df):<10}")
    
    if not all_ok:
        print("\n❌ VERIFICATION FAILED")
        return False
    
    # Additional statistics
    print(f"\n" + "="*80)
    print("ADDITIONAL STATISTICS:")
    print("="*80)
    
    # Health/non-health distribution in train vs test
    train_health = (train_df['manual_label'] == 'health').sum()
    test_health = (test_df['manual_label'] == 'health').sum()
    
    print(f"\n📊 Label distribution:")
    print(f"   Train: {train_health} health ({train_health/len(train_df)*100:.1f}%)")
    print(f"   Test:  {test_health} health ({test_health/len(test_df)*100:.1f}%)")
    
    # Per-influencer label distribution in test
    print(f"\n📋 Test set label distribution per influencer:")
    print(f"{'Influencer':<25} {'Health':<10} {'Non-Health':<12} {'Health %':<12}")
    print("-" * 70)
    
    for username in sorted(test_influencers):
        inf_test = test_df[test_df['username'] == username]
        health = (inf_test['manual_label'] == 'health').sum()
        non_health = (inf_test['manual_label'] == 'not_health').sum()
        health_pct = (health / len(inf_test) * 100) if len(inf_test) > 0 else 0
        
        print(f"{username:<25} {health:<10} {non_health:<12} {health_pct:>6.1f}%")
    
    print("\n" + "="*80)
    print("✅ ALL VERIFICATION CHECKS PASSED!")
    print("="*80)
    print("\n🎯 Summary:")
    print(f"   - {len(original_influencers)} influencers ✓")
    print(f"   - All have training data (70%) ✓")
    print(f"   - All have test data (30%) ✓")
    print(f"   - Stratified split per influencer ✓")
    print(f"\n✅ Ready to run 03_TEST_MODEL_LABELING.py!")
    print("="*80 + "\n")
    
    return True

if __name__ == "__main__":
    success = verify_split()
    exit(0 if success else 1)