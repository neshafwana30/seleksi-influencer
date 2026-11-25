"""
04_EVALUATE_MODEL.py
====================
Comprehensive model evaluation dengan per-influencer error insights visualization

STEP 1: Load learned patterns & predictions dari hasil_03
STEP 2: Generate per-influencer error analysis
STEP 3: Create comprehensive evaluation report
STEP 4: Visualize insights dan recommendations

Output:
  - hasil_04/evaluation_report.txt
  - hasil_04/per_influencer_insights.json
  - hasil_04/error_patterns_summary.txt
  - hasil_04/model_recommendations.txt

Usage:
    python 04_evaluate_model.py
"""

import pandas as pd
import json
import logging
from pathlib import Path
from collections import defaultdict
from datetime import datetime

# ============================================================================
# LOGGING & CONFIG
# ============================================================================

logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)

OUTPUT_DIR = Path("01_pengumpulan_data/04_2_Klasifikasikan_konten_scraping")
HASIL_03_DIR = OUTPUT_DIR / "hasil_03"
HASIL_04_DIR = OUTPUT_DIR / "hasil_04"
HASIL_04_DIR.mkdir(exist_ok=True)

LEARNED_PATTERNS = HASIL_03_DIR / "learned_patterns.json"
ACCURACY_REPORT = HASIL_03_DIR / "accuracy_report.csv"

EVAL_REPORT = HASIL_04_DIR / "evaluation_report.txt"
PER_INF_INSIGHTS = HASIL_04_DIR / "per_influencer_insights.json"
ERROR_SUMMARY = HASIL_04_DIR / "error_patterns_summary.txt"
MODEL_RECS = HASIL_04_DIR / "model_recommendations.txt"

# ============================================================================
# LOAD DATA
# ============================================================================

def load_data():
    """Load learned patterns dan predictions dari previous run"""
    logger.info("\n" + "="*70)
    logger.info("LOADING DATA")
    logger.info("="*70)
    
    if not LEARNED_PATTERNS.exists():
        logger.error(f"❌ {LEARNED_PATTERNS} not found!")
        return None, None
    
    if not ACCURACY_REPORT.exists():
        logger.error(f"❌ {ACCURACY_REPORT} not found!")
        return None, None
    
    with open(LEARNED_PATTERNS, 'r', encoding='utf-8') as f:
        patterns = json.load(f)
    
    predictions_df = pd.read_csv(ACCURACY_REPORT)
    
    logger.info(f"\n✅ Loaded learned patterns from hasil_03/")
    logger.info(f"✅ Loaded {len(predictions_df)} predictions")
    logger.info(f"✅ {len(patterns['influencers'])} influencers analyzed")
    
    return patterns, predictions_df

# ============================================================================
# ANALYZE PER-INFLUENCER INSIGHTS
# ============================================================================

def analyze_per_influencer(patterns, predictions_df):
    """Generate comprehensive per-influencer insights"""
    logger.info("\n" + "="*70)
    logger.info("ANALYZING PER-INFLUENCER INSIGHTS")
    logger.info("="*70)
    
    insights_data = {}
    
    for username, inf_patterns in patterns['influencers'].items():
        inf_predictions = predictions_df[predictions_df['username'] == username]
        
        if len(inf_predictions) == 0:
            continue
        
        # Calculate metrics
        accuracy = inf_predictions['correct'].mean() * 100
        total = len(inf_predictions)
        
        correct = inf_predictions['correct'].sum()
        pred_health = inf_predictions['predicted_label'] == 'health'
        actual_health = inf_predictions['actual_label'] == 'health'
        
        tp = (pred_health & actual_health).sum()
        fp = (pred_health & ~actual_health).sum()
        tn = (~pred_health & ~actual_health).sum()
        fn = (~pred_health & actual_health).sum()
        
        precision = tp / (tp + fp) * 100 if (tp + fp) > 0 else 0
        recall = tp / (tp + fn) * 100 if (tp + fn) > 0 else 0
        f1 = 2 * (precision * recall) / (precision + recall) if (precision + recall) > 0 else 0
        
        # Caption analysis
        captions = inf_predictions['description'].fillna('')
        avg_caption_len = captions.str.len().mean()
        empty_caption_pct = (captions.str.len() < 5).sum() / len(captions) * 100
        short_caption_pct = ((captions.str.len() >= 5) & (captions.str.len() < 50)).sum() / len(captions) * 100
        
        # Error patterns
        fn_posts = inf_predictions[(inf_predictions['actual_label'] == 'health') & 
                                   (inf_predictions['predicted_label'] == 'not_health')]
        fp_posts = inf_predictions[(inf_predictions['actual_label'] == 'not_health') & 
                                   (inf_predictions['predicted_label'] == 'health')]
        
        fn_count = len(fn_posts)
        fp_count = len(fp_posts)
        
        # Build insights
        error_insights = []
        
        if empty_caption_pct > 20:
            error_insights.append(f"Banyak caption kosong ({empty_caption_pct:.0f}%) - susah di-classify")
        
        if short_caption_pct > 30:
            error_insights.append(f"Caption sangat pendek ({avg_caption_len:.0f} chars rata-rata) - kurang informasi")
        
        if fn_count > fp_count * 2:
            error_insights.append(f"Model terlalu konservatif: FN ({fn_count}x) > FP ({fp_count}x)")
        elif fp_count > fn_count * 2:
            error_insights.append(f"Model terlalu agresif: FP ({fp_count}x) > FN ({fn_count}x)")
        
        if fn_count > 0:
            fn_avg_conf = fn_posts['predicted_confidence'].mean()
            if fn_avg_conf < 0.4:
                error_insights.append(f"Model tidak yakin saat salah (confidence {fn_avg_conf:.2f}) - perlu improvement")
        
        if len(error_insights) == 0:
            error_insights.append("Klasifikasi akurat - tidak ada issue yang signifikan")
        
        insights_data[username] = {
            'test_videos': total,
            'accuracy': round(accuracy, 1),
            'precision': round(precision, 1),
            'recall': round(recall, 1),
            'f1_score': round(f1, 1),
            'confusion_matrix': {
                'tp': int(tp),
                'fp': int(fp),
                'tn': int(tn),
                'fn': int(fn)
            },
            'caption_stats': {
                'avg_length': round(avg_caption_len, 1),
                'empty_pct': round(empty_caption_pct, 1),
                'short_pct': round(short_caption_pct, 1)
            },
            'error_counts': {
                'false_negatives': int(fn_count),
                'false_positives': int(fp_count)
            },
            'error_insights': error_insights,
            'health_percentage': round(inf_patterns.get('health_percentage', 0), 1),
            'samples': {
                'health': inf_patterns.get('health_samples', [])[:2],
                'non_health': inf_patterns.get('non_health_samples', [])[:2]
            }
        }
    
    logger.info(f"\n✅ Analyzed {len(insights_data)} influencers\n")
    
    # Log summary
    for username in sorted(insights_data.keys(), key=lambda x: insights_data[x]['accuracy'], reverse=True):
        info = insights_data[username]
        logger.info(f"@{username:<20} Accuracy: {info['accuracy']:>6.1f}% | FN:{info['error_counts']['false_negatives']:<2} FP:{info['error_counts']['false_positives']:<2}")
    
    return insights_data

# ============================================================================
# GENERATE ERROR PATTERN SUMMARY
# ============================================================================

def generate_error_summary(patterns, insights_data):
    """Generate error pattern summary"""
    logger.info("\n" + "="*70)
    logger.info("ERROR PATTERN ANALYSIS")
    logger.info("="*70)
    
    # Categorize by error type
    high_fn = {}  # False negatives issue
    high_fp = {}  # False positives issue
    low_caption = {}  # Caption quality issue
    high_accuracy = {}  # Good accuracy
    
    for username, info in insights_data.items():
        fn_count = info['error_counts']['false_negatives']
        fp_count = info['error_counts']['false_positives']
        accuracy = info['accuracy']
        empty_pct = info['caption_stats']['empty_pct']
        
        if accuracy >= 80:
            high_accuracy[username] = accuracy
        elif fn_count > fp_count * 2:
            high_fn[username] = fn_count
        elif fp_count > fn_count * 2:
            high_fp[username] = fp_count
        
        if empty_pct > 20:
            low_caption[username] = empty_pct
    
    logger.info(f"\n📊 CATEGORIZED RESULTS:\n")
    
    if high_accuracy:
        logger.info(f"✅ HIGH ACCURACY ({len(high_accuracy)} creators):")
        for username in sorted(high_accuracy.keys(), key=lambda x: high_accuracy[x], reverse=True):
            logger.info(f"   @{username}: {high_accuracy[username]:.1f}%")
    
    if high_fn:
        logger.info(f"\n⚠️  FALSE NEGATIVE ISSUES ({len(high_fn)} creators):")
        logger.info(f"   Model terlalu konservatif (miss health posts)")
        for username in sorted(high_fn.keys(), key=lambda x: high_fn[x], reverse=True):
            logger.info(f"   @{username}: {high_fn[username]} false negatives")
    
    if high_fp:
        logger.info(f"\n⚠️  FALSE POSITIVE ISSUES ({len(high_fp)} creators):")
        logger.info(f"   Model terlalu agresif (predict health saat non-health)")
        for username in sorted(high_fp.keys(), key=lambda x: high_fp[x], reverse=True):
            logger.info(f"   @{username}: {high_fp[username]} false positives")
    
    if low_caption:
        logger.info(f"\n📝 CAPTION QUALITY ISSUES ({len(low_caption)} creators):")
        logger.info(f"   Banyak caption kosong/sangat pendek")
        for username in sorted(low_caption.keys(), key=lambda x: low_caption[x], reverse=True):
            logger.info(f"   @{username}: {low_caption[username]:.1f}% empty/short captions")
    
    return {
        'high_accuracy': high_accuracy,
        'high_fn': high_fn,
        'high_fp': high_fp,
        'low_caption': low_caption
    }

# ============================================================================
# GENERATE MODEL RECOMMENDATIONS
# ============================================================================

def generate_recommendations(error_summary, insights_data, patterns):
    """Generate actionable recommendations"""
    logger.info("\n" + "="*70)
    logger.info("MODEL RECOMMENDATIONS")
    logger.info("="*70)
    
    recommendations = []
    
    # Recommendation 1: High FN issue
    if error_summary['high_fn']:
        fn_creators = list(error_summary['high_fn'].keys())
        recommendations.append({
            'category': 'HIGH FALSE NEGATIVES',
            'priority': 'HIGH',
            'issue': f"{len(fn_creators)} creators punya banyak false negatives (model misses health posts)",
            'affected': fn_creators[:5],
            'action': 'Improve prompt untuk recognize subtle health indicators; Lower confidence threshold'
        })
    
    # Recommendation 2: High FP issue
    if error_summary['high_fp']:
        fp_creators = list(error_summary['high_fp'].keys())
        recommendations.append({
            'category': 'HIGH FALSE POSITIVES',
            'priority': 'HIGH',
            'issue': f"{len(fp_creators)} creators punya banyak false positives (model too aggressive)",
            'affected': fp_creators[:5],
            'action': 'Better filter untuk ambiguous terms (diet, kulit, sehat); Require more health indicators'
        })
    
    # Recommendation 3: Caption quality
    if error_summary['low_caption']:
        caption_creators = list(error_summary['low_caption'].keys())
        recommendations.append({
            'category': 'CAPTION QUALITY',
            'priority': 'MEDIUM',
            'issue': f"{len(caption_creators)} creators punya caption kosong/sangat pendek",
            'affected': caption_creators[:5],
            'action': 'Prioritize for manual labeling; Consider caption-less classification strategy'
        })
    
    # Recommendation 4: High accuracy
    if error_summary['high_accuracy']:
        recommendations.append({
            'category': 'GOOD PERFORMERS',
            'priority': 'LOW',
            'issue': f"{len(error_summary['high_accuracy'])} creators punya accuracy >=80%",
            'affected': list(error_summary['high_accuracy'].keys())[:5],
            'action': 'Use as baseline; Study their characteristics untuk understand success patterns'
        })
    
    logger.info(f"\n📋 RECOMMENDATIONS:\n")
    for i, rec in enumerate(recommendations, 1):
        logger.info(f"{i}. [{rec['priority']}] {rec['category']}")
        logger.info(f"   Issue: {rec['issue']}")
        logger.info(f"   Affected: {', '.join(rec['affected'][:3])}")
        logger.info(f"   Action: {rec['action']}\n")
    
    return recommendations

# ============================================================================
# SAVE REPORTS
# ============================================================================

def save_reports(insights_data, error_summary, recommendations, patterns):
    """Save comprehensive evaluation reports"""
    logger.info("\n" + "="*70)
    logger.info("SAVING REPORTS")
    logger.info("="*70)
    
    # 1. Per-influencer insights JSON
    with open(PER_INF_INSIGHTS, 'w', encoding='utf-8') as f:
        json.dump(insights_data, f, ensure_ascii=False, indent=2)
    logger.info(f"\n✅ Saved: hasil_04/{PER_INF_INSIGHTS.name}")
    
    # 2. Error patterns summary
    with open(ERROR_SUMMARY, 'w', encoding='utf-8') as f:
        f.write("="*70 + "\n")
        f.write("ERROR PATTERNS SUMMARY\n")
        f.write("="*70 + "\n\n")
        f.write(f"Generated: {datetime.now().isoformat()}\n\n")
        
        f.write("HIGH ACCURACY CREATORS:\n")
        f.write("-"*70 + "\n")
        for username, acc in sorted(error_summary['high_accuracy'].items(), key=lambda x: x[1], reverse=True):
            f.write(f"@{username}: {acc:.1f}%\n")
        f.write("\n")
        
        f.write("FALSE NEGATIVE ISSUES (Model Misses Health):\n")
        f.write("-"*70 + "\n")
        for username, fn_count in sorted(error_summary['high_fn'].items(), key=lambda x: x[1], reverse=True):
            info = insights_data.get(username, {})
            error_insights = info.get('error_insights', [])
            f.write(f"@{username}: {fn_count} FN\n")
            for insight in error_insights:
                f.write(f"  - {insight}\n")
            f.write("\n")
        
        f.write("FALSE POSITIVE ISSUES (Model Too Aggressive):\n")
        f.write("-"*70 + "\n")
        for username, fp_count in sorted(error_summary['high_fp'].items(), key=lambda x: x[1], reverse=True):
            info = insights_data.get(username, {})
            error_insights = info.get('error_insights', [])
            f.write(f"@{username}: {fp_count} FP\n")
            for insight in error_insights:
                f.write(f"  - {insight}\n")
            f.write("\n")
        
        f.write("CAPTION QUALITY ISSUES:\n")
        f.write("-"*70 + "\n")
        for username, empty_pct in sorted(error_summary['low_caption'].items(), key=lambda x: x[1], reverse=True):
            info = insights_data.get(username, {})
            error_insights = info.get('error_insights', [])
            f.write(f"@{username}: {empty_pct:.1f}% empty/short\n")
            for insight in error_insights:
                f.write(f"  - {insight}\n")
            f.write("\n")
    
    logger.info(f"✅ Saved: hasil_04/{ERROR_SUMMARY.name}")
    
    # 3. Model recommendations
    with open(MODEL_RECS, 'w', encoding='utf-8') as f:
        f.write("="*70 + "\n")
        f.write("MODEL IMPROVEMENT RECOMMENDATIONS\n")
        f.write("="*70 + "\n\n")
        f.write(f"Generated: {datetime.now().isoformat()}\n\n")
        
        for i, rec in enumerate(recommendations, 1):
            f.write(f"{i}. [{rec['priority']}] {rec['category']}\n")
            f.write(f"   Problem: {rec['issue']}\n")
            f.write(f"   Affected: {', '.join(rec['affected'][:5])}\n")
            f.write(f"   Recommendation: {rec['action']}\n\n")
    
    logger.info(f"✅ Saved: hasil_04/{MODEL_RECS.name}")
    
    # 4. Main evaluation report
    with open(EVAL_REPORT, 'w', encoding='utf-8') as f:
        f.write("="*70 + "\n")
        f.write("COMPREHENSIVE MODEL EVALUATION REPORT\n")
        f.write("="*70 + "\n\n")
        f.write(f"Generated: {datetime.now().isoformat()}\n\n")
        
        f.write("OVERALL METRICS:\n")
        f.write("-"*70 + "\n")
        overall_acc = patterns['metadata']['overall_accuracy']
        overall_prec = patterns['metadata']['overall_precision']
        overall_rec = patterns['metadata']['overall_recall']
        overall_f1 = patterns['metadata']['overall_f1']
        
        f.write(f"Accuracy:  {overall_acc:.1f}%\n")
        f.write(f"Precision: {overall_prec:.1f}%\n")
        f.write(f"Recall:    {overall_rec:.1f}%\n")
        f.write(f"F1-Score:  {overall_f1:.1f}%\n\n")
        
        f.write("PER-INFLUENCER RESULTS:\n")
        f.write("-"*70 + "\n")
        f.write(f"{'Influencer':<20} {'Accuracy':<12} {'Precision':<12} {'Recall':<12} {'Test Videos':<12}\n")
        f.write("-"*70 + "\n")
        
        for username in sorted(insights_data.keys(), key=lambda x: insights_data[x]['accuracy'], reverse=True):
            info = insights_data[username]
            f.write(f"{username:<20} {info['accuracy']:>6.1f}%      {info['precision']:>6.1f}%      {info['recall']:>6.1f}%      {info['test_videos']:>6}\n")
        
        f.write("\n")
        f.write("DETAILED INSIGHTS:\n")
        f.write("-"*70 + "\n")
        
        for username in sorted(insights_data.keys(), key=lambda x: insights_data[x]['accuracy'], reverse=True):
            info = insights_data[username]
            f.write(f"\n@{username}:\n")
            f.write(f"  Accuracy: {info['accuracy']:.1f}%\n")
            f.write(f"  TP:{info['confusion_matrix']['tp']} FP:{info['confusion_matrix']['fp']} TN:{info['confusion_matrix']['tn']} FN:{info['confusion_matrix']['fn']}\n")
            f.write(f"  Caption avg: {info['caption_stats']['avg_length']:.0f} chars, Empty: {info['caption_stats']['empty_pct']:.1f}%\n")
            for insight in info['error_insights']:
                f.write(f"  • {insight}\n")
    
    logger.info(f"✅ Saved: hasil_04/{EVAL_REPORT.name}")
    
    logger.info("\n" + "="*70)
    logger.info("✅ ALL REPORTS SAVED")
    logger.info("="*70)

# ============================================================================
# MAIN
# ============================================================================

def main():
    patterns, predictions_df = load_data()
    if patterns is None:
        return False
    
    insights_data = analyze_per_influencer(patterns, predictions_df)
    error_summary = generate_error_summary(patterns, insights_data)
    recommendations = generate_recommendations(error_summary, insights_data, patterns)
    save_reports(insights_data, error_summary, recommendations, patterns)
    
    return True

if __name__ == "__main__":
    success = main()
    exit(0 if success else 1)