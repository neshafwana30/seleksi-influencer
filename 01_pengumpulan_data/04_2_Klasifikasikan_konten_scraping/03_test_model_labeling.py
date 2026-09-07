"""
VALIDATE & TRAIN - 60/40 SPLIT
===============================
1. Load labeled_videos.csv (500 videos yang udah Nesha label manual)
2. Split: 60% train (300) - analyze patterns, 40% test (200) - test model
3. Learn influencer characteristics dari train set
4. Classify test set dengan enhanced prompt (pakai learned patterns)
5. Compare predictions vs actual labels
6. Output: accuracy_report.csv + accuracy_summary.txt

Usage:
    python validate_and_train.py
"""

import pandas as pd
import ollama
import re
import logging
import sys
from datetime import datetime
from pathlib import Path
from collections import defaultdict
from config import OUTPUT_LABELED_VIDEOS, validate_paths

# ============================================================================
# LOGGING
# ============================================================================

logging.basicConfig(
    level=logging.INFO,
    format='%(message)s',
)
logger = logging.getLogger(__name__)

# ============================================================================
# CONFIG
# ============================================================================

OUTPUT_DIR = Path(OUTPUT_LABELED_VIDEOS).parent
HASIL_DIR = OUTPUT_DIR / "hasil_03"

# Create hasil_03 folder if not exists
HASIL_DIR.mkdir(exist_ok=True)

TRAIN_SET = HASIL_DIR / "train_set.csv"
TEST_SET = HASIL_DIR / "test_set.csv"
ACCURACY_REPORT = HASIL_DIR / "accuracy_report.csv"
ACCURACY_SUMMARY = HASIL_DIR / "accuracy_summary.txt"

# ============================================================================
# STEP 1: LOAD & SPLIT
# ============================================================================

def load_and_split():
    """Load labeled_videos.csv dan split 60/40 per influencer (stratified)
    FILTER: hanya ambil videos yang sudah dilabel (manual_label not null/not empty)"""
    logger.info("\n" + "="*70)
    logger.info("STEP 1: LOAD & SPLIT DATA")
    logger.info("="*70)
    
    if not OUTPUT_LABELED_VIDEOS.exists():
        logger.error(f"❌ {OUTPUT_LABELED_VIDEOS} not found!")
        return None, None
    
    df = pd.read_csv(OUTPUT_LABELED_VIDEOS)
    df['video_id'] = df['video_id'].astype(str)
    
    # FILTER: hanya ambil yang sudah dilabel
    df_labeled = df[(df['manual_label'].notna()) & (df['manual_label'] != '')].copy()
    
    logger.info(f"\n✅ Loaded {len(df_labeled)} labeled videos (dari {len(df)} total)")
    logger.info(f"   Influencers: {df_labeled['username'].nunique()}")
    logger.info(f"   Health: {(df_labeled['manual_label'] == 'health').sum()} ({(df_labeled['manual_label'] == 'health').mean()*100:.1f}%)")
    logger.info(f"   Non-health: {(df_labeled['manual_label'] == 'not_health').sum()} ({(df_labeled['manual_label'] == 'not_health').mean()*100:.1f}%)")
    
    # Split 60/40 per influencer (stratified)
    train_dfs = []
    test_dfs = []
    
    for username in df_labeled['username'].unique():
        inf_data = df_labeled[df_labeled['username'] == username]
        n = len(inf_data)
        split_idx = int(n * 0.6)
        
        # Shuffle
        inf_data_shuffled = inf_data.sample(frac=1, random_state=42)
        train_dfs.append(inf_data_shuffled.iloc[:split_idx])
        test_dfs.append(inf_data_shuffled.iloc[split_idx:])
    
    train_df = pd.concat(train_dfs, ignore_index=True)
    test_df = pd.concat(test_dfs, ignore_index=True)
    
    train_df.to_csv(TRAIN_SET, index=False)
    test_df.to_csv(TEST_SET, index=False)
    
    logger.info(f"\n📊 Split (60/40):")
    logger.info(f"   Train: {len(train_df)} videos ({len(train_df)/len(df_labeled)*100:.1f}%) → hasil_03/train_set.csv")
    logger.info(f"   Test: {len(test_df)} videos ({len(test_df)/len(df_labeled)*100:.1f}%) → hasil_03/test_set.csv")
    
    return train_df, test_df

# ============================================================================
# STEP 2: LEARN INFLUENCER PATTERNS
# ============================================================================

def learn_patterns(train_df):
    """Analyze train set, learn influencer characteristics"""
    logger.info("\n" + "="*70)
    logger.info("STEP 2: LEARN INFLUENCER CHARACTERISTICS")
    logger.info("="*70)
    
    learnings = defaultdict(dict)
    
    for username in train_df['username'].unique():
        inf_data = train_df[train_df['username'] == username]
        
        # Health percentage
        health_count = (inf_data['manual_label'] == 'health').sum()
        health_pct = health_count / len(inf_data) * 100
        
        # Caption patterns
        no_caption = inf_data['description'].fillna('').str.len() < 5
        no_caption_pct = no_caption.sum() / len(inf_data) * 100
        
        # Sample descriptions
        health_samples = inf_data[inf_data['manual_label'] == 'health']['description'].head(3).tolist()
        non_health_samples = inf_data[inf_data['manual_label'] == 'not_health']['description'].head(3).tolist()
        
        learnings[username] = {
            'total_videos': len(inf_data),
            'health_count': health_count,
            'health_pct': health_pct,
            'no_caption_pct': no_caption_pct,
            'health_samples': [str(s)[:100] for s in health_samples if pd.notna(s)],
            'non_health_samples': [str(s)[:100] for s in non_health_samples if pd.notna(s)]
        }
    
    logger.info(f"\n✅ Analyzed {len(learnings)} influencers:\n")
    
    for username, info in sorted(learnings.items(), key=lambda x: x[1]['health_pct'], reverse=True):
        logger.info(f"@{username}:")
        logger.info(f"  Health: {info['health_pct']:.1f}%")
        logger.info(f"  Non-health: {100-info['health_pct']:.1f}%")
        logger.info(f"  No caption: {info['no_caption_pct']:.1f}%")
        if info['health_samples']:
            logger.info(f"  Example health: {info['health_samples'][0][:50]}...")
        if info['non_health_samples']:
            logger.info(f"  Example non-health: {info['non_health_samples'][0][:50]}...")
        logger.info("")
    
    return learnings

# ============================================================================
# STEP 3: ENHANCED CLASSIFICATION
# ============================================================================

def build_enhanced_prompt(description, username, learnings):
    """Build prompt dengan learned patterns per influencer"""
    inf_info = learnings.get(username, {})
    health_pct = inf_info.get('health_pct', 50)
    
    base_prompt = """Kamu adalah sistem klasifikasi akademik untuk penelitian tugas akhir tentang kategorisasi konten edukasi kesehatan di TikTok Indonesia. Tugasmu murni melabeli data, bukan mendiskusikan isinya.

DEFINISI "KONTEN KESEHATAN":
✓ Kondisi medis, penyakit, gejala, pengobatan (fisik & mental)
✓ Kesehatan reproduksi & seksual (menstruasi, kehamilan, KB, kesehatan pria/wanita, edukasi seksual)
✓ Kesehatan mental (stress, trauma, parenting, kesehatan mental anak-remaja-dewasa)
✓ Nutrisi, diet, pola makan untuk tujuan kesehatan
✓ Olahraga/fitness dengan tujuan kesehatan
✓ Edukasi dari tenaga medis (dokter segala spesialis, psikolog, bidan, perawat)
✓ Perawatan tubuh/kulit dengan tujuan medis

BUKAN kesehatan:
✗ Fashion/makeup murni estetika
✗ Vlog harian tanpa unsur medis
✗ Hiburan murni, gaming, teknologi, finansial, travel non-kesehatan"""
    
    # Add influencer-specific context
    if health_pct > 70:
        base_prompt += f"\n\n💡 KONTEKS: Influencer @{username} biasanya upload konten kesehatan ({health_pct:.0f}% dari video mereka adalah health content)."
    elif health_pct < 30:
        base_prompt += f"\n\n💡 KONTEKS: Influencer @{username} jarang upload konten kesehatan ({health_pct:.0f}% saja). Hati-hati jangan false positive."
    else:
        base_prompt += f"\n\n💡 KONTEKS: Influencer @{username} upload konten kesehatan dan non-kesehatan balanced ({health_pct:.0f}%)."
    
    base_prompt += f"""

Caption video:
"{description}"

Jawab HANYA dalam format ini (tanpa penjelasan tambahan):
LABEL: HEALTH atau NOT_HEALTH
CONFIDENCE: angka 0.0-1.0"""
    
    return base_prompt

def classify_with_learnings(test_df, learnings):
    """Classify test set dengan enhanced prompt"""
    logger.info("\n" + "="*70)
    logger.info("STEP 3: CLASSIFY TEST SET (40%)")
    logger.info("="*70)
    
    predictions = []
    total = len(test_df)
    
    logger.info(f"\nClassifying {total} videos...\n")
    
    for idx, row in test_df.iterrows():
        description = str(row['description']).strip()[:800] if pd.notna(row['description']) else ""
        
        if len(description) < 5:
            # No caption, gunakan influencer pattern
            username = row['username']
            inf_pct = learnings.get(username, {}).get('health_pct', 50)
            is_health = inf_pct > 50
            confidence = abs(inf_pct - 50) / 50
            method = 'no_caption_learned'
        else:
            try:
                prompt = build_enhanced_prompt(description, row['username'], learnings)
                response = ollama.generate(
                    model='qwen2.5:7b',
                    prompt=prompt,
                    stream=False,
                    options={'temperature': 0.1, 'num_predict': 100}
                )
                
                is_health, confidence = parse_response(response['response'])
                method = 'ollama_enhanced'
            except Exception as e:
                logger.debug(f"Error classifying {row['video_id']}: {e}")
                is_health = False
                confidence = 0.5
                method = 'error'
        
        predictions.append({
            'username': row['username'],
            'video_id': row['video_id'],
            'description': description[:100],
            'predicted_label': 'health' if is_health else 'not_health',
            'predicted_confidence': confidence,
            'actual_label': row['manual_label'],
            'correct': (is_health and row['manual_label'] == 'health') or (not is_health and row['manual_label'] == 'not_health'),
            'method': method
        })
        
        if (idx + 1) % 50 == 0:
            progress_pct = (idx + 1) / total * 100
            logger.info(f"  Progress: {progress_pct:.1f}% ({idx + 1}/{total})")
    
    return pd.DataFrame(predictions)

def parse_response(response_text):
    """Parse Ollama response"""
    try:
        label_match = re.search(r'LABEL:\s*(HEALTH|NOT_HEALTH)', response_text, re.IGNORECASE)
        if not label_match:
            return False, 0.5
        
        is_health = label_match.group(1).upper() == 'HEALTH'
        
        conf_match = re.search(r'CONFIDENCE:\s*([\d.]+)', response_text)
        confidence = float(conf_match.group(1)) if conf_match else 0.5
        confidence = max(0.0, min(1.0, confidence))
        
        return is_health, confidence
    except:
        return False, 0.5

# ============================================================================
# STEP 4: EVALUATE
# ============================================================================

def evaluate(predictions_df):
    """Calculate accuracy metrics"""
    logger.info("\n" + "="*70)
    logger.info("STEP 4: EVALUATE RESULTS")
    logger.info("="*70)
    
    # Overall metrics
    accuracy = predictions_df['correct'].mean() * 100
    
    pred_health = predictions_df['predicted_label'] == 'health'
    actual_health = predictions_df['actual_label'] == 'health'
    
    tp = (pred_health & actual_health).sum()
    fp = (pred_health & ~actual_health).sum()
    tn = (~pred_health & ~actual_health).sum()
    fn = (~pred_health & actual_health).sum()
    
    precision = tp / (tp + fp) * 100 if (tp + fp) > 0 else 0
    recall = tp / (tp + fn) * 100 if (tp + fn) > 0 else 0
    f1 = 2 * (precision * recall) / (precision + recall) if (precision + recall) > 0 else 0
    
    summary = {
        'total': len(predictions_df),
        'accuracy': accuracy,
        'precision': precision,
        'recall': recall,
        'f1': f1,
        'tp': tp,
        'fp': fp,
        'tn': tn,
        'fn': fn
    }
    
    # Per-influencer
    per_inf = {}
    for username in predictions_df['username'].unique():
        inf_pred = predictions_df[predictions_df['username'] == username]
        inf_acc = inf_pred['correct'].mean() * 100
        
        per_inf[username] = {
            'count': len(inf_pred),
            'accuracy': inf_acc,
            'correct': inf_pred['correct'].sum()
        }
    
    logger.info(f"\n✅ OVERALL RESULTS (Test Set - 40%):")
    logger.info(f"   Total videos: {summary['total']}")
    logger.info(f"   Accuracy: {summary['accuracy']:.1f}%")
    logger.info(f"   Precision: {summary['precision']:.1f}%")
    logger.info(f"   Recall: {summary['recall']:.1f}%")
    logger.info(f"   F1-Score: {summary['f1']:.1f}%")
    logger.info(f"\n   True Positive (TP): {summary['tp']/summary['total']*100:.1f}%")
    logger.info(f"   False Positive (FP): {summary['fp']/summary['total']*100:.1f}%")
    logger.info(f"   True Negative (TN): {summary['tn']/summary['total']*100:.1f}%")
    logger.info(f"   False Negative (FN): {summary['fn']/summary['total']*100:.1f}%")
    
    logger.info(f"\n📊 PER-INFLUENCER ACCURACY:")
    for username in sorted(per_inf.keys()):
        info = per_inf[username]
        logger.info(f"   @{username}: {info['accuracy']:.1f}%")
    
    return summary, per_inf, predictions_df

# ============================================================================
# STEP 5: SAVE RESULTS
# ============================================================================

def save_results(summary, per_inf, predictions_df):
    """Save accuracy report & summary"""
    logger.info("\n" + "="*70)
    logger.info("STEP 5: SAVE RESULTS")
    logger.info("="*70)
    
    # Report CSV
    predictions_df.to_csv(ACCURACY_REPORT, index=False)
    logger.info(f"\n✅ Accuracy report saved: hasil_03/{ACCURACY_REPORT.name}")
    
    # Summary TXT
    with open(ACCURACY_SUMMARY, 'w', encoding='utf-8') as f:
        f.write("="*70 + "\n")
        f.write("VALIDATE & TRAIN - ACCURACY REPORT\n")
        f.write("="*70 + "\n\n")
        
        f.write(f"Generated: {datetime.now().isoformat()}\n\n")
        
        f.write("OVERALL METRICS (Test Set - 40% dari 500 labeled videos)\n")
        f.write("-"*70 + "\n")
        f.write(f"Total videos tested: {summary['total']}\n")
        f.write(f"Accuracy: {summary['accuracy']:.1f}%\n")
        f.write(f"Precision: {summary['precision']:.1f}%\n")
        f.write(f"Recall: {summary['recall']:.1f}%\n")
        f.write(f"F1-Score: {summary['f1']:.1f}%\n\n")
        
        f.write(f"Confusion Matrix (dalam persentase dari total test videos):\n")
        total = summary['total']
        f.write(f"  True Positive (correctly labeled HEALTH): {summary['tp']/total*100:.1f}%\n")
        f.write(f"  False Positive (predicted HEALTH, actually NOT): {summary['fp']/total*100:.1f}%\n")
        f.write(f"  True Negative (correctly labeled NOT_HEALTH): {summary['tn']/total*100:.1f}%\n")
        f.write(f"  False Negative (predicted NOT, actually HEALTH): {summary['fn']/total*100:.1f}%\n\n")
        
        f.write("PER-INFLUENCER BREAKDOWN\n")
        f.write("-"*70 + "\n")
        for username in sorted(per_inf.keys()):
            info = per_inf[username]
            f.write(f"@{username}: {info['accuracy']:.1f}%\n")
    
    logger.info(f"✅ Summary saved: hasil_03/{ACCURACY_SUMMARY.name}")
    
    logger.info("\n" + "="*70)
    logger.info("✅ VALIDATION COMPLETE!")
    logger.info("="*70)
    logger.info(f"\n📁 All output files saved in: hasil_03/\n")

# ============================================================================
# MAIN
# ============================================================================

def main():
    if not validate_paths():
        return False
    
    # Check Ollama
    try:
        ollama.list()
    except:
        logger.error("❌ Ollama not running! Run: ollama serve")
        return False
    
    # Load & split
    train_df, test_df = load_and_split()
    if train_df is None:
        return False
    
    # Learn patterns
    learnings = learn_patterns(train_df)
    
    # Classify test set
    predictions_df = classify_with_learnings(test_df, learnings)
    
    # Evaluate
    summary, per_inf, predictions_df = evaluate(predictions_df)
    
    # Save
    save_results(summary, per_inf, predictions_df)
    
    return True

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)