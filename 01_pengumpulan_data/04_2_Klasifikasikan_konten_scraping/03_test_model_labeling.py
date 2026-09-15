"""
03_TEST_MODEL_LABELING.py
=========================
Training & Testing dengan 70/30 split dari labeled_videos.csv (ADVANCED PATTERNS)

STEP 1: Load & split 70/30 per influencer (stratified)
STEP 2: Learn ADVANCED influencer characteristics dari train set (70%)
STEP 3: Save learned patterns ke JSON (reusable untuk classifier)
STEP 4: Classify test set (30%) pakai learned patterns + Ollama
STEP 5: Evaluate results & save reports

ADVANCED PATTERNS YANG DI-EXTRACT:
  - Content style (emoji, hashtag, caption length)
  - Medical indicators (keywords, medical terms frequency)
  - Error patterns & misclassification insights
  - Profile summary untuk classifier guidance

Output:
  - hasil_03/train_set.csv
  - hasil_03/test_set.csv
  - hasil_03/learned_patterns.json  ← ADVANCED: lebih detailed
  - hasil_03/accuracy_report.csv
  - hasil_03/accuracy_summary.txt

Usage:
    python 03_test_model_labeling.py
"""

import pandas as pd
import ollama
import re
import json
import time
import logging
import sys
from datetime import datetime
from pathlib import Path
from collections import defaultdict, Counter
from config import OUTPUT_LABELED_VIDEOS, validate_paths

# ============================================================================
# LOGGING
# ============================================================================

logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)

# ============================================================================
# CONFIG
# ============================================================================

OUTPUT_DIR = Path(OUTPUT_LABELED_VIDEOS).parent
HASIL_DIR = OUTPUT_DIR / "hasil_03"
HASIL_DIR.mkdir(exist_ok=True)

TRAIN_SET = HASIL_DIR / "train_set.csv"
TEST_SET = HASIL_DIR / "test_set.csv"
LEARNED_PATTERNS = HASIL_DIR / "learned_patterns.json"
ACCURACY_REPORT = HASIL_DIR / "accuracy_report.csv"
ACCURACY_SUMMARY = HASIL_DIR / "accuracy_summary.txt"

TRAIN_SPLIT_RATIO = 0.7  # ⬆️ Updated: 70% train, 30% test

# ============================================================================
# 🌡️ CPU THROTTLING CONFIG -- biar laptop gak overheat
# ============================================================================

OLLAMA_NUM_THREAD = 6
DELAY_BETWEEN_REQUESTS = 2
COOLDOWN_EVERY_N_VIDEOS = 750
COOLDOWN_DURATION = 30

# ============================================================================
# HELPER FUNCTIONS - EXTRACT ADVANCED PATTERNS
# ============================================================================

def extract_keywords(texts, top_n=10):
    """Extract top keywords dari list of texts"""
    texts = [str(t).lower() for t in texts if pd.notna(t) and len(str(t)) > 0]
    if not texts:
        return []
    
    # Split ke words, filter short words
    all_words = []
    for text in texts:
        words = re.findall(r'\b[a-zა-ჯ]{3,}\b', text)  # >3 chars, skip stopwords simple
        all_words.extend(words)
    
    # Get top keywords
    word_counts = Counter(all_words)
    return [(word, count) for word, count in word_counts.most_common(top_n)]

def count_medical_terms(texts):
    """Count posts yang mention medical terms"""
    medical_keywords = [
        'dokter', 'kesehatan', 'penyakit', 'gejala', 'obat', 'medis', 
        'kesehatan mental', 'diet', 'nutrisi', 'olahraga', 'fitness',
        'psikolog', 'bidan', 'perawat', 'operasi', 'terapi',
        'kesehatan reproduksi', 'kehamilan', 'menstruasi', 'kesehatan pria',
        'kesehatan wanita', 'stress', 'trauma', 'parenting', 'kulit'
    ]
    
    texts = [str(t).lower() for t in texts if pd.notna(t)]
    if not texts:
        return 0
    
    count = 0
    for text in texts:
        if any(term in text for term in medical_keywords):
            count += 1
    
    return count / len(texts) * 100

def analyze_caption_style(texts):
    """Analyze style indicators dari captions"""
    texts = [str(t) for t in texts if pd.notna(t) and len(str(t)) > 0]
    if not texts:
        return {
            'emoji_frequency': 0,
            'hashtag_frequency': 0,
            'avg_caption_length': 0,
            'has_question_pct': 0,
            'has_cta_pct': 0
        }
    
    emoji_count = sum(1 for t in texts if re.search(r'[😀-🙏🏻]', t))
    hashtag_count = sum(1 for t in texts if re.search(r'#\w+', t))
    question_count = sum(1 for t in texts if '?' in t)
    cta_count = sum(1 for t in texts if any(cta in t.lower() for cta in ['yuk', 'coba', 'lihat', 'tonton', 'follow', 'subscribe']))
    
    avg_length = sum(len(t) for t in texts) / len(texts)
    
    return {
        'emoji_frequency': emoji_count / len(texts) * 100,
        'hashtag_frequency': hashtag_count / len(texts) * 100,
        'avg_caption_length': round(avg_length, 1),
        'has_question_pct': question_count / len(texts) * 100,
        'has_cta_pct': cta_count / len(texts) * 100
    }

def generate_profile_summary(username, inf_data, learnings_base):
    """Generate human-readable profile summary"""
    health_pct = learnings_base['health_pct']
    style = learnings_base.get('content_style', {})
    medical = learnings_base.get('medical_indicators', {})
    
    # Determine posting style
    if style.get('emoji_frequency', 0) > 40:
        style_desc = "casual, emoji-heavy"
    elif style.get('emoji_frequency', 0) > 20:
        style_desc = "conversational"
    else:
        style_desc = "formal"
    
    # Health focus
    if health_pct > 75:
        focus = "Strong medical/health focus"
    elif health_pct > 50:
        focus = "Balanced health and lifestyle"
    else:
        focus = "Mostly non-health content"
    
    # Activity pattern
    avg_len = style.get('avg_caption_length', 0)
    if avg_len > 200:
        activity = "Detailed, long-form captions"
    elif avg_len > 100:
        activity = "Medium-length captions"
    else:
        activity = "Short, snappy captions"
    
    summary = f"{focus}. Posts {style_desc}. {activity}."
    if medical.get('medical_terms_frequency', 0) > 30:
        summary += " Uses medical terminology frequently."
    
    return summary

def analyze_errors_from_predictions(predictions_df):
    """Analyze error patterns dari test predictions"""
    errors = {
        'false_positives': [],
        'false_negatives': [],
        'fn_count': 0,
        'fp_count': 0,
    }
    
    fp_mask = (predictions_df['predicted_label'] == 'health') & (predictions_df['actual_label'] == 'not_health')
    fn_mask = (predictions_df['predicted_label'] == 'not_health') & (predictions_df['actual_label'] == 'health')
    
    errors['fp_count'] = fp_mask.sum()
    errors['fn_count'] = fn_mask.sum()
    
    # Get sample FPs & FNs
    fp_samples = predictions_df[fp_mask][['description', 'predicted_confidence']].head(3)
    fn_samples = predictions_df[fn_mask][['description', 'predicted_confidence']].head(3)
    
    if len(fp_samples) > 0:
        errors['false_positives'] = [
            {"caption": row['description'][:80], "confidence": row['predicted_confidence']} 
            for _, row in fp_samples.iterrows()
        ]
    
    if len(fn_samples) > 0:
        errors['false_negatives'] = [
            {"caption": row['description'][:80], "confidence": row['predicted_confidence']} 
            for _, row in fn_samples.iterrows()
        ]
    
    return errors

# ============================================================================
# VALIDATION: Ensure semua influencer punya training + test data
# ============================================================================

def validate_split(train_df, test_df, original_df):
    """Validate bahwa influencer dengan ≥2 videos punya training AND test data"""
    logger.info("\n" + "="*70)
    logger.info("VALIDATION: Checking split integrity")
    logger.info("="*70)
    
    original_influencers = set(original_df['username'].unique())
    train_influencers = set(train_df['username'].unique())
    test_influencers = set(test_df['username'].unique())
    
    # Identify influencers dengan <2 videos (SKIPPED)
    skipped_influencers = []
    for username in original_influencers:
        if len(original_df[original_df['username'] == username]) < 2:
            skipped_influencers.append(username)
    
    # Valid influencers = yang punya ≥2 videos
    valid_influencers = original_influencers - set(skipped_influencers)
    
    # Check 1: Semua VALID influencer ada di training
    missing_in_train = valid_influencers - train_influencers
    if missing_in_train:
        logger.error(f"❌ {len(missing_in_train)} influencer TIDAK punya training data: {missing_in_train}")
        return False
    
    # Check 2: Semua VALID influencer ada di test
    missing_in_test = valid_influencers - test_influencers
    if missing_in_test:
        logger.error(f"❌ {len(missing_in_test)} influencer TIDAK punya test data: {missing_in_test}")
        return False
    
    # Check 3: Per-influencer split details dengan minimum threshold
    logger.info(f"\n📋 Per-Influencer Split Details:\n")
    logger.info(f"{'Influencer':<20} {'Total':<8} {'Train':<8} {'Test':<8} {'Status':<15}")
    logger.info("-" * 70)
    
    all_valid = True
    warn_count = 0
    
    for username in sorted(original_influencers):
        total = len(original_df[original_df['username'] == username])
        
        # SKIPPED: <2 videos
        if total < 2:
            logger.info(f"{username:<20} {total:<8} {'SKIP':<8} {'SKIP':<8} {'❌ SKIPPED':<15}")
            continue
        
        train_count = len(train_df[train_df['username'] == username])
        test_count = len(test_df[test_df['username'] == username])
        
        # Validation rules:
        # - Minimum 1 di training, 1 di test (hard requirement)
        # - Warn jika < 3 di salah satu (untuk robustness)
        status = "✓ OK"
        
        if train_count == 0 or test_count == 0:
            status = "❌ FAIL"
            all_valid = False
        elif train_count < 3 or test_count < 3:
            status = "⚠️  WARN"
            warn_count += 1
        
        logger.info(f"{username:<20} {total:<8} {train_count:<8} {test_count:<8} {status:<15}")
    
    logger.info("-" * 70)
    
    if not all_valid:
        logger.error("\n❌ VALIDATION FAILED! Some influencers missing train or test data.")
        return False
    
    logger.info(f"\n✅ VALIDATION PASSED!")
    logger.info(f"   ✓ {len(valid_influencers)} influencers have BOTH training AND test data")
    if len(skipped_influencers) > 0:
        logger.info(f"   ⚠️  {len(skipped_influencers)} influencer(s) SKIPPED (have <2 videos)")
        logger.info(f"      Skipped: {', '.join(sorted(skipped_influencers))}")
    if warn_count > 0:
        logger.info(f"   ⚠️  {warn_count} influencer(s) have <3 videos in train or test (small sample)")
    logger.info(f"   Total split: {len(train_df)} train + {len(test_df)} test = {len(train_df) + len(test_df)} used")
    logger.info(f"              ({len(original_df)} original - {len(skipped_influencers)} skipped)")
    
    return True

# ============================================================================
# STEP 1: LOAD & SPLIT
# ============================================================================

def load_and_split():
    """Load labeled_videos.csv dan split 70/30 per influencer (stratified)
    ⚡ GUARANTEE: Semua 45 influencer punya training + test data"""
    logger.info("\n" + "="*70)
    logger.info("STEP 1: LOAD & SPLIT DATA (70/30 - STRATIFIED PER INFLUENCER)")
    logger.info("="*70)
    
    if not OUTPUT_LABELED_VIDEOS.exists():
        logger.error(f"❌ {OUTPUT_LABELED_VIDEOS} not found!")
        return None, None
    
    df = pd.read_csv(OUTPUT_LABELED_VIDEOS)
    df['video_id'] = df['video_id'].astype(str)
    
    # Filter: hanya ambil yang sudah dilabel
    df_labeled = df[(df['manual_label'].notna()) & (df['manual_label'] != '')].copy()
    
    logger.info(f"\n✅ Loaded {len(df_labeled)} labeled videos (dari {len(df)} total)")
    logger.info(f"   Influencers: {df_labeled['username'].nunique()}")
    logger.info(f"   Health: {(df_labeled['manual_label'] == 'health').sum()} ({(df_labeled['manual_label'] == 'health').mean()*100:.1f}%)")
    logger.info(f"   Non-health: {(df_labeled['manual_label'] == 'not_health').sum()} ({(df_labeled['manual_label'] == 'not_health').mean()*100:.1f}%)")
    
    # Split 70/30 per influencer (STRATIFIED - setiap influencer punya training + test)
    logger.info(f"\n🔄 Performing stratified 70/30 split PER INFLUENCER...")
    logger.info(f"   ⚠️  Handling edge cases: minimum 1 train + 1 test per influencer\n")
    
    train_dfs = []
    test_dfs = []
    split_details = []
    
    for username in sorted(df_labeled['username'].unique()):
        inf_data = df_labeled[df_labeled['username'] == username]
        n = len(inf_data)
        
        # EDGE CASE HANDLING: Ensure minimum 1 train + 1 test
        # Jika n < 2, ini impossible (skip atau warn)
        # Jika n == 2, minimum: 1 train, 1 test
        # Jika n > 2, gunakan 70/30 split
        
        if n < 2:
            logger.warning(f"   ⚠️  @{username}: hanya {n} video (skipped - need ≥2)")
            continue  # Skip influencer dengan <2 videos
        
        # Calculate split index dengan rounding UP untuk train (prefer more training)
        # Gunakan ceil untuk ensure train selalu ≥1
        import math
        split_idx = max(1, int(n * TRAIN_SPLIT_RATIO))  # At least 1 untuk train
        
        # Sanity check: pastikan test juga minimal 1
        if split_idx >= n:
            split_idx = n - 1  # Ensure at least 1 untuk test
        
        # Shuffle dengan random_state=42 buat reproducibility
        inf_data_shuffled = inf_data.sample(frac=1, random_state=42)
        
        train_part = inf_data_shuffled.iloc[:split_idx]
        test_part = inf_data_shuffled.iloc[split_idx:]
        
        train_dfs.append(train_part)
        test_dfs.append(test_part)
        
        split_details.append({
            'username': username,
            'total': n,
            'train': len(train_part),
            'test': len(test_part)
        })
    
    train_df = pd.concat(train_dfs, ignore_index=True) if train_dfs else pd.DataFrame()
    test_df = pd.concat(test_dfs, ignore_index=True) if test_dfs else pd.DataFrame()
    
    # Log split details untuk transparency
    n_skipped = len(df_labeled['username'].unique()) - len(split_details)
    if n_skipped > 0:
        logger.warning(f"\n⚠️  SKIPPED {n_skipped} influencer(s) dengan <2 videos")
        skipped_influencers = set(df_labeled['username'].unique()) - {d['username'] for d in split_details}
        for inf in skipped_influencers:
            n_videos = len(df_labeled[df_labeled['username'] == inf])
            logger.warning(f"     @{inf}: {n_videos} video(s) → need ≥2 for train/test split")
    
    # VALIDATE bahwa semua 45 influencer punya training + test data
    if not validate_split(train_df, test_df, df_labeled):
        logger.error("❌ Split validation FAILED! Cannot proceed.")
        return None, None
    
    train_df.to_csv(TRAIN_SET, index=False)
    test_df.to_csv(TEST_SET, index=False)
    
    logger.info(f"\n✅ Split files saved:")
    logger.info(f"   → hasil_03/train_set.csv ({len(train_df)} videos)")
    logger.info(f"   → hasil_03/test_set.csv ({len(test_df)} videos)")
    
    return train_df, test_df

# ============================================================================
# STEP 2: LEARN ADVANCED INFLUENCER PATTERNS
# ============================================================================

def learn_advanced_patterns(train_df):
    """Analyze train set, learn ADVANCED influencer characteristics (only for valid influencers)"""
    logger.info("\n" + "="*70)
    logger.info("STEP 2: LEARN ADVANCED INFLUENCER CHARACTERISTICS")
    logger.info("="*70)
    
    n_influencers = train_df['username'].nunique()
    logger.info(f"\nLearning patterns dari {n_influencers} influencers in training set...")
    
    learnings = defaultdict(dict)
    
    for username in train_df['username'].unique():
        inf_data = train_df[train_df['username'] == username]
        health_data = inf_data[inf_data['manual_label'] == 'health']
        non_health_data = inf_data[inf_data['manual_label'] == 'not_health']
        
        # BASIC STATS
        health_count = len(health_data)
        health_pct = health_count / len(inf_data) * 100
        
        no_caption = inf_data['description'].fillna('').str.len() < 5
        no_caption_pct = no_caption.sum() / len(inf_data) * 100
        
        # ADVANCED PATTERNS
        health_captions = health_data['description']
        non_health_captions = non_health_data['description']
        
        # 1. CONTENT STYLE
        health_style = analyze_caption_style(health_captions)
        non_health_style = analyze_caption_style(non_health_captions)
        
        # 2. KEYWORDS
        health_keywords = extract_keywords(health_captions, top_n=8)
        non_health_keywords = extract_keywords(non_health_captions, top_n=5)
        
        # 3. MEDICAL INDICATORS
        health_medical_pct = count_medical_terms(health_captions)
        non_health_medical_pct = count_medical_terms(non_health_captions)
        
        # SAMPLES
        health_samples = health_data['description'].head(3).tolist()
        non_health_samples = non_health_data['description'].head(3).tolist()
        
        learnings[username] = {
            'health_pct': health_pct,
            'no_caption_pct': no_caption_pct,
            'total_videos': len(inf_data),
            
            # Content style
            'content_style': {
                'health_emoji_freq': round(health_style.get('emoji_frequency', 0), 1),
                'health_hashtag_freq': round(health_style.get('hashtag_frequency', 0), 1),
                'health_avg_caption_length': health_style.get('avg_caption_length', 0),
                'health_question_pct': round(health_style.get('has_question_pct', 0), 1),
                'health_cta_pct': round(health_style.get('has_cta_pct', 0), 1),
                'non_health_avg_caption_length': non_health_style.get('avg_caption_length', 0),
            },
            
            # Medical indicators
            'medical_indicators': {
                'health_medical_terms_freq': round(health_medical_pct, 1),
                'non_health_medical_terms_freq': round(non_health_medical_pct, 1),
            },
            
            # Keywords
            'health_keywords': [{"keyword": kw[0], "count": kw[1]} for kw in health_keywords],
            'non_health_keywords': [{"keyword": kw[0], "count": kw[1]} for kw in non_health_keywords],
            
            # Samples (for reference)
            'health_samples': [str(s)[:100] for s in health_samples if pd.notna(s)],
            'non_health_samples': [str(s)[:100] for s in non_health_samples if pd.notna(s)]
        }
    
    logger.info(f"\n✅ Analyzed {len(learnings)} influencers with ADVANCED patterns:\n")
    
    for username in sorted(learnings.keys(), key=lambda x: learnings[x]['health_pct'], reverse=True):
        info = learnings[username]
        logger.info(f"@{username}: {info['health_pct']:.1f}% health | emoji:{info['content_style']['health_emoji_freq']:.0f}% | "
                   f"medical:{info['medical_indicators']['health_medical_terms_freq']:.0f}%")
    
    return learnings

# ============================================================================
# HELPER: Convert numpy types to Python native types
# ============================================================================

def convert_numpy_types(obj):
    """Convert numpy types to Python native types untuk JSON serialization"""
    import numpy as np
    
    if isinstance(obj, dict):
        return {key: convert_numpy_types(value) for key, value in obj.items()}
    elif isinstance(obj, list):
        return [convert_numpy_types(item) for item in obj]
    elif isinstance(obj, (np.integer, np.floating)):
        return obj.item()  # Convert numpy scalar to Python native type
    elif isinstance(obj, np.ndarray):
        return obj.tolist()
    elif hasattr(obj, 'item'):  # Fallback for other numpy types
        try:
            return obj.item()
        except:
            return obj
    return obj

# ============================================================================
# STEP 3: SAVE ADVANCED LEARNED PATTERNS
# ============================================================================

def save_learned_patterns(learnings, summary, per_inf, predictions_df):
    """Save learned patterns ke JSON dengan ADVANCED details"""
    patterns_data = {
        "metadata": {
            "created_at": datetime.now().isoformat(),
            "train_split": TRAIN_SPLIT_RATIO * 100,
            "test_split": (1 - TRAIN_SPLIT_RATIO) * 100,
            "overall_accuracy": summary['accuracy'],
            "overall_precision": summary['precision'],
            "overall_recall": summary['recall'],
            "overall_f1": summary['f1'],
        },
        "influencers": {}
    }
    
    for username, info in learnings.items():
        inf_metrics = per_inf.get(username, {})
        
        # Analyze errors untuk influencer ini
        inf_predictions = predictions_df[predictions_df['username'] == username]
        inf_errors = analyze_errors_from_predictions(inf_predictions)
        
        # Generate summary
        profile_summary = generate_profile_summary(username, None, info)
        
        patterns_data["influencers"][username] = {
            "profile_summary": profile_summary,
            
            "basic_stats": {
                "health_percentage": round(info['health_pct'], 1),
                "no_caption_percentage": round(info['no_caption_pct'], 1),
                "total_videos_analyzed": info['total_videos'],
            },
            
            "test_performance": {
                "accuracy": round(inf_metrics.get('accuracy', 0), 1),
                "correct_predictions": inf_metrics.get('correct', 0),
                "total_test_videos": inf_metrics.get('count', 0),
            },
            
            "content_style": {
                "health_posts": {
                    "emoji_frequency": info['content_style']['health_emoji_freq'],
                    "hashtag_frequency": info['content_style']['health_hashtag_freq'],
                    "avg_caption_length": info['content_style']['health_avg_caption_length'],
                    "has_question_pct": info['content_style']['health_question_pct'],
                    "has_cta_pct": info['content_style']['health_cta_pct'],
                },
                "non_health_posts": {
                    "avg_caption_length": info['content_style']['non_health_avg_caption_length'],
                }
            },
            
            "medical_indicators": {
                "health_posts_with_medical_terms": info['medical_indicators']['health_medical_terms_freq'],
                "non_health_posts_with_medical_terms": info['medical_indicators']['non_health_medical_terms_freq'],
            },
            
            "top_health_keywords": info['health_keywords'],
            "top_non_health_keywords": info['non_health_keywords'],
            
            "classification_errors": {
                "false_positives": inf_errors['fp_count'],
                "false_negatives": inf_errors['fn_count'],
                "sample_false_positives": inf_errors['false_positives'],
                "sample_false_negatives": inf_errors['false_negatives'],
            },
            
            "classifier_guidance": generate_classifier_guidance(username, info, inf_metrics, inf_errors),
            
            "samples": {
                "health_samples": info['health_samples'],
                "non_health_samples": info['non_health_samples']
            }
        }
    
    # Convert numpy types to Python native types before JSON dump
    patterns_data = convert_numpy_types(patterns_data)
    
    with open(LEARNED_PATTERNS, 'w', encoding='utf-8') as f:
        json.dump(patterns_data, f, ensure_ascii=False, indent=2)
    
    logger.info(f"\n✅ Advanced learned patterns saved: hasil_03/{LEARNED_PATTERNS.name}")

def generate_classifier_guidance(username, info, metrics, errors):
    """Generate guidance untuk classifier based on patterns"""
    guidance = []
    
    # Health indicator guidance
    health_pct = info['health_pct']
    if health_pct > 75:
        guidance.append("High confidence for health-related posts - this creator focuses on health content")
    elif health_pct < 30:
        guidance.append("Be cautious of false positives - this creator rarely posts health content")
    
    # Style guidance
    emoji_freq = info['content_style']['health_emoji_freq']
    if emoji_freq > 50:
        guidance.append("Posts are casual & emoji-heavy - don't overweight emoji as health indicator")
    
    # Medical term guidance
    med_freq = info['medical_indicators']['health_medical_terms_freq']
    if med_freq > 50:
        guidance.append("Strong medical terminology in health posts - medical terms are reliable indicator")
    
    # Error guidance
    if errors['fp_count'] > errors['fn_count']:
        guidance.append(f"Watch for false positives ({errors['fp_count']}x) - review non-health samples carefully")
    elif errors['fn_count'] > errors['fp_count']:
        guidance.append(f"Watch for false negatives ({errors['fn_count']}x) - some health posts might be subtle")
    
    return guidance if guidance else ["No specific guidance"]

# ============================================================================
# STEP 4: BUILD ENHANCED PROMPT
# ============================================================================

def build_enhanced_prompt(description, username, learnings):
    """Build prompt pakai learned patterns (lebih detailed sekarang)"""
    inf_info = learnings.get(username, {})
    health_pct = inf_info.get('health_pct', 50)
    medical_freq = inf_info.get('medical_indicators', {}).get('health_medical_terms_freq', 0)
    
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
    
    # Learned context
    if health_pct > 75:
        base_prompt += f"\n\n💡 CREATOR PATTERN: @{username} biasanya upload konten kesehatan ({health_pct:.0f}%). Tingkatkan confidence untuk posts yang berhubungan kesehatan."
    elif health_pct < 30:
        base_prompt += f"\n\n💡 CREATOR PATTERN: @{username} jarang upload kesehatan ({health_pct:.0f}%). HATI-HATI false positive - jangan overinterpret medical mentions."
    else:
        base_prompt += f"\n\n💡 CREATOR PATTERN: @{username} posting balanced ({health_pct:.0f}% health). Evaluate carefully."
    
    # Medical indicator hint
    if medical_freq > 60:
        base_prompt += f" ⚕️ Medical terms sangat sering dalam posts creator - gunakan sebagai strong signal."
    elif medical_freq < 20:
        base_prompt += f" ⚕️ Medical terms jarang - jangan rely heavy pada medical keywords untuk classifier."
    
    base_prompt += f"""

Caption video:
"{description}"

Jawab HANYA dalam format ini:
LABEL: HEALTH atau NOT_HEALTH
CONFIDENCE: angka 0.0-1.0"""
    
    return base_prompt

# ============================================================================
# STEP 5: CLASSIFY TEST SET
# ============================================================================

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

def classify_test_set(test_df, learnings):
    """Classify test set dengan CPU throttling"""
    logger.info("\n" + "="*70)
    logger.info("STEP 3: CLASSIFY TEST SET (30%)")
    logger.info("="*70)
    
    predictions = []
    total = len(test_df)
    
    n_need_ollama = (test_df['description'].fillna('').str.len() >= 5).sum()
    est_seconds = n_need_ollama * (DELAY_BETWEEN_REQUESTS + 3)
    est_minutes = est_seconds / 60
    
    logger.info(f"\n🌡️  CPU THROTTLING AKTIF:")
    logger.info(f"   num_thread: {OLLAMA_NUM_THREAD} (dibatasi, biar gak overheat)")
    logger.info(f"   Jeda antar video: {DELAY_BETWEEN_REQUESTS}s")
    logger.info(f"   Cooldown break: {COOLDOWN_DURATION}s tiap {COOLDOWN_EVERY_N_VIDEOS} video")
    logger.info(f"\nClassifying {total} videos ({n_need_ollama} butuh Ollama)...")
    logger.info(f"⏱️  Estimasi waktu: ~{est_minutes:.0f} menit (lebih lama dari sebelumnya, tapi lebih adem)\n")
    
    ollama_call_count = 0
    
    for idx, row in test_df.iterrows():
        description = str(row['description']).strip()[:800] if pd.notna(row['description']) else ""
        
        if len(description) < 5:
            # No caption: gunakan influencer pattern
            username = row['username']
            inf_pct = learnings.get(username, {}).get('health_pct', 50)
            is_health = inf_pct > 50
            confidence = abs(inf_pct - 50) / 100
            method = 'no_caption_learned'
        else:
            try:
                prompt = build_enhanced_prompt(description, row['username'], learnings)
                response = ollama.generate(
                    model='qwen2.5:7b',
                    prompt=prompt,
                    stream=False,
                    options={
                        'temperature': 0.1,
                        'num_predict': 100,
                        'num_thread': OLLAMA_NUM_THREAD,
                    }
                )
                is_health, confidence = parse_response(response['response'])
                method = 'ollama_enhanced'
                ollama_call_count += 1

                # Jeda WAJIB tiap habis manggil Ollama
                time.sleep(DELAY_BETWEEN_REQUESTS)

                # Cooldown break lebih panjang tiap N video
                if ollama_call_count % COOLDOWN_EVERY_N_VIDEOS == 0:
                    logger.info(f"   🧊 Cooldown break {COOLDOWN_DURATION}s (habis {ollama_call_count} video, biar CPU adem)...")
                    time.sleep(COOLDOWN_DURATION)

            except Exception as e:
                logger.debug(f"Error: {e}")
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
        
        if (idx + 1) % 20 == 0:
            logger.info(f"  Progress: {idx + 1}/{total} videos")
    
    return pd.DataFrame(predictions)

# ============================================================================
# STEP 6: EVALUATE
# ============================================================================

def evaluate(predictions_df):
    """Calculate metrics & validate semua influencer punya test data"""
    logger.info("\n" + "="*70)
    logger.info("STEP 4: EVALUATE RESULTS")
    logger.info("="*70)
    
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
        'tp': tp, 'fp': fp, 'tn': tn, 'fn': fn
    }
    
    per_inf = {}
    for username in sorted(predictions_df['username'].unique()):
        inf_pred = predictions_df[predictions_df['username'] == username]
        health_actual = (inf_pred['actual_label'] == 'health').sum()
        non_health_actual = (inf_pred['actual_label'] == 'not_health').sum()
        
        per_inf[username] = {
            'count': len(inf_pred),
            'accuracy': inf_pred['correct'].mean() * 100,
            'correct': inf_pred['correct'].sum(),
            'health_count': health_actual,
            'non_health_count': non_health_actual
        }
    
    logger.info(f"\n✅ OVERALL RESULTS (Test Set - 30%):")
    logger.info(f"   Accuracy: {accuracy:.1f}%")
    logger.info(f"   Precision: {precision:.1f}%")
    logger.info(f"   Recall: {recall:.1f}%")
    logger.info(f"   F1-Score: {f1:.1f}%")
    logger.info(f"   Total test videos: {len(predictions_df)}")
    
    # Per-influencer detailed report
    logger.info(f"\n📊 PER-INFLUENCER TEST DATA & ACCURACY:")
    logger.info(f"{'Influencer':<20} {'Test Vids':<12} {'Health/Non':<15} {'Accuracy':<12}")
    logger.info("-" * 70)
    
    for username in sorted(per_inf.keys()):
        info = per_inf[username]
        test_count = info['count']
        health_count = info['health_count']
        non_health_count = info['non_health_count']
        accuracy_pct = info['accuracy']
        
        logger.info(f"{username:<20} {test_count:<12} {health_count}/{non_health_count:<13} {accuracy_pct:>6.1f}%")
    
    logger.info("-" * 70)
    logger.info(f"{'TOTAL':<20} {len(predictions_df):<12} ✓ ALL {len(per_inf)} influencers have test data")
    
    return summary, per_inf, predictions_df

# ============================================================================
# STEP 7: SAVE RESULTS
# ============================================================================

def save_results(summary, per_inf, predictions_df):
    """Save reports"""
    logger.info("\n" + "="*70)
    logger.info("STEP 5: SAVE RESULTS")
    logger.info("="*70)
    
    predictions_df.to_csv(ACCURACY_REPORT, index=False)
    logger.info(f"\n✅ Accuracy report: hasil_03/{ACCURACY_REPORT.name}")
    
    with open(ACCURACY_SUMMARY, 'w', encoding='utf-8') as f:
        f.write("="*70 + "\n")
        f.write("TEST MODEL - ACCURACY REPORT (70/30 SPLIT)\n")
        f.write("="*70 + "\n\n")
        f.write(f"Generated: {datetime.now().isoformat()}\n\n")
        
        f.write("OVERALL METRICS (Test Set - 30%)\n")
        f.write("-"*70 + "\n")
        f.write(f"Total videos: {summary['total']}\n")
        f.write(f"Accuracy: {summary['accuracy']:.1f}%\n")
        f.write(f"Precision: {summary['precision']:.1f}%\n")
        f.write(f"Recall: {summary['recall']:.1f}%\n")
        f.write(f"F1-Score: {summary['f1']:.1f}%\n\n")
        
        f.write(f"Confusion Matrix:\n")
        f.write(f"  True Positives:  {summary['tp']}\n")
        f.write(f"  False Positives: {summary['fp']}\n")
        f.write(f"  True Negatives:  {summary['tn']}\n")
        f.write(f"  False Negatives: {summary['fn']}\n\n")
        
        f.write("PER-INFLUENCER ACCURACY\n")
        f.write("-"*70 + "\n")
        for username in sorted(per_inf.keys()):
            info = per_inf[username]
            f.write(f"@{username}: {info['accuracy']:.1f}% ({info['correct']}/{info['count']} correct)\n")
    
    logger.info(f"✅ Summary: hasil_03/{ACCURACY_SUMMARY.name}")
    logger.info("\n" + "="*70)
    logger.info("✅ DONE! Advanced patterns saved untuk classifier")
    logger.info("="*70 + "\n")

# ============================================================================
# MAIN
# ============================================================================

def main():
    if not validate_paths():
        return False
    
    try:
        ollama.list()
    except:
        logger.error("❌ Ollama not running! Run: ollama serve")
        return False
    
    train_df, test_df = load_and_split()
    if train_df is None:
        return False
    
    learnings = learn_advanced_patterns(train_df)
    predictions_df = classify_test_set(test_df, learnings)
    summary, per_inf, predictions_df = evaluate(predictions_df)
    save_learned_patterns(learnings, summary, per_inf, predictions_df)
    save_results(summary, per_inf, predictions_df)
    
    return True

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)