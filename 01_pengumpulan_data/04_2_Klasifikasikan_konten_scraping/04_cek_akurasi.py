"""
ANALYZE ACCURACY - Visualize & Report Results
==============================================
Load accuracy_report.csv (sudah ada dari validate_and_train.py)
- Hitung confusion matrix (absolute numbers)
- Generate grafik: accuracy, precision, recall, F1-score
- Breakdown health predictions: TP, FP, FN, TN
- Per-influencer accuracy ranking

BUKAN re-run klasifikasi - cuma analyze hasil yang sudah ada!

Usage:
    python analyze_accuracy.py
"""

import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
from pathlib import Path
import logging

# ============================================================================
# CONFIG
# ============================================================================

OUTPUT_DIR = Path(__file__).parent if Path(__file__).parent != Path('.') else Path('.')
HASIL_03_DIR = OUTPUT_DIR / "hasil_03"
HASIL_04_DIR = OUTPUT_DIR / "hasil_04"

# Create hasil_04 folder if not exists
HASIL_04_DIR.mkdir(exist_ok=True)

# Input dari hasil_03 (output dari validate_and_train.py)
ACCURACY_REPORT = HASIL_03_DIR / "accuracy_report.csv"
ANALYSIS_REPORT = HASIL_04_DIR / "accuracy_analysis_report.txt"
CONFUSION_MATRIX_CSV = HASIL_04_DIR / "confusion_matrix.csv"
METRICS_PLOT = HASIL_04_DIR / "accuracy_metrics.png"
PREDICTIONS_PLOT = HASIL_04_DIR / "predictions_breakdown.png"

# ============================================================================
# LOGGING
# ============================================================================

logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)

# ============================================================================
# LOAD DATA
# ============================================================================

def load_results():
    """Load accuracy_report.csv"""
    if not ACCURACY_REPORT.exists():
        logger.error(f"❌ {ACCURACY_REPORT} not found!")
        return None
    
    df = pd.read_csv(ACCURACY_REPORT)
    logger.info(f"✅ Loaded hasil_03/{ACCURACY_REPORT.name}")
    logger.info(f"   Rows: {len(df)}")
    logger.info(f"   Columns: {list(df.columns)}\n")
    
    return df

# ============================================================================
# CALCULATE METRICS
# ============================================================================

def calculate_metrics(df):
    """Calculate all metrics"""
    
    # Confusion matrix
    tp = ((df['predicted_label'] == 'health') & (df['actual_label'] == 'health')).sum()
    fp = ((df['predicted_label'] == 'health') & (df['actual_label'] == 'not_health')).sum()
    tn = ((df['predicted_label'] == 'not_health') & (df['actual_label'] == 'not_health')).sum()
    fn = ((df['predicted_label'] == 'not_health') & (df['actual_label'] == 'health')).sum()
    
    total = len(df)
    accuracy = df['correct'].mean() * 100
    precision = tp / (tp + fp) * 100 if (tp + fp) > 0 else 0
    recall = tp / (tp + fn) * 100 if (tp + fn) > 0 else 0
    f1 = 2 * (precision * recall) / (precision + recall) if (precision + recall) > 0 else 0
    
    # Specificity
    specificity = tn / (tn + fp) * 100 if (tn + fp) > 0 else 0
    
    # Per-influencer breakdown
    per_inf = {}
    for username in df['username'].unique():
        inf_df = df[df['username'] == username]
        inf_metrics = {
            'total': len(inf_df),
            'correct': inf_df['correct'].sum(),
            'accuracy': inf_df['correct'].mean() * 100,
            'tp': ((inf_df['predicted_label'] == 'health') & (inf_df['actual_label'] == 'health')).sum(),
            'fp': ((inf_df['predicted_label'] == 'health') & (inf_df['actual_label'] == 'not_health')).sum(),
            'tn': ((inf_df['predicted_label'] == 'not_health') & (inf_df['actual_label'] == 'not_health')).sum(),
            'fn': ((inf_df['predicted_label'] == 'not_health') & (inf_df['actual_label'] == 'health')).sum(),
        }
        per_inf[username] = inf_metrics
    
    # Health vs Non-health breakdown
    actual_health = (df['actual_label'] == 'health').sum()
    actual_non_health = (df['actual_label'] == 'not_health').sum()
    pred_health_correct = tp
    pred_health_wrong = fp
    pred_non_health_correct = tn
    pred_non_health_wrong = fn
    
    metrics = {
        'total': total,
        'accuracy': accuracy,
        'precision': precision,
        'recall': recall,
        'f1': f1,
        'specificity': specificity,
        'tp': tp,
        'fp': fp,
        'tn': tn,
        'fn': fn,
        'actual_health': actual_health,
        'actual_non_health': actual_non_health,
        'pred_health_correct': pred_health_correct,
        'pred_health_wrong': pred_health_wrong,
        'pred_non_health_correct': pred_non_health_correct,
        'pred_non_health_wrong': pred_non_health_wrong,
        'per_influencer': per_inf
    }
    
    return metrics

# ============================================================================
# SAVE CONFUSION MATRIX
# ============================================================================

def save_confusion_matrix(metrics):
    """Save confusion matrix (absolute numbers)"""
    cm_data = {
        'Metric': ['True Positive (Health)', 'False Positive', 'False Negative', 'True Negative (Non-Health)'],
        'Count': [metrics['tp'], metrics['fp'], metrics['fn'], metrics['tn']],
        'Percentage': [
            metrics['tp'] / metrics['total'] * 100,
            metrics['fp'] / metrics['total'] * 100,
            metrics['fn'] / metrics['total'] * 100,
            metrics['tn'] / metrics['total'] * 100
        ]
    }
    
    cm_df = pd.DataFrame(cm_data)
    cm_df.to_csv(CONFUSION_MATRIX_CSV, index=False)
    
    logger.info(f"✅ Confusion Matrix saved: hasil_04/{CONFUSION_MATRIX_CSV.name}\n")
    return cm_df

# ============================================================================
# VISUALIZATIONS
# ============================================================================

def plot_metrics(metrics):
    """Generate accuracy/precision/recall/F1 bar chart"""
    fig, axes = plt.subplots(2, 2, figsize=(12, 10))
    fig.suptitle('Model Performance Metrics (Test Set)', fontsize=16, fontweight='bold')
    
    # Accuracy
    ax = axes[0, 0]
    ax.bar(['Accuracy'], [metrics['accuracy']], color='#667eea', width=0.6)
    ax.set_ylim(0, 100)
    ax.set_ylabel('Percentage (%)', fontweight='bold')
    ax.set_title('Overall Accuracy')
    ax.text(0, metrics['accuracy'] + 2, f"{metrics['accuracy']:.1f}%", ha='center', fontweight='bold')
    
    # Precision & Recall
    ax = axes[0, 1]
    metrics_list = ['Precision', 'Recall', 'F1-Score']
    values = [metrics['precision'], metrics['recall'], metrics['f1']]
    colors = ['#51cf66', '#ff922b', '#a78bfa']
    bars = ax.bar(metrics_list, values, color=colors, width=0.6)
    ax.set_ylim(0, 100)
    ax.set_ylabel('Percentage (%)', fontweight='bold')
    ax.set_title('Precision, Recall, F1-Score')
    for bar, val in zip(bars, values):
        height = bar.get_height()
        ax.text(bar.get_x() + bar.get_width()/2., height + 1, f'{val:.1f}%', 
                ha='center', va='bottom', fontweight='bold', fontsize=9)
    
    # Specificity & Recall Comparison
    ax = axes[1, 0]
    metric_names = ['Recall (True Positive Rate)', 'Specificity (True Negative Rate)']
    metric_vals = [metrics['recall'], metrics['specificity']]
    bars = ax.bar(metric_names, metric_vals, color=['#51cf66', '#ff6b6b'], width=0.6)
    ax.set_ylim(0, 100)
    ax.set_ylabel('Percentage (%)', fontweight='bold')
    ax.set_title('Sensitivity vs Specificity')
    for bar, val in zip(bars, metric_vals):
        height = bar.get_height()
        ax.text(bar.get_x() + bar.get_width()/2., height + 1, f'{val:.1f}%', 
                ha='center', va='bottom', fontweight='bold', fontsize=9)
    
    # Confusion Matrix Visual
    ax = axes[1, 1]
    cm_values = [[metrics['tp'], metrics['fp']], [metrics['fn'], metrics['tn']]]
    sns.heatmap(cm_values, annot=True, fmt='d', cmap='Blues', cbar=False, ax=ax,
                xticklabels=['Predicted Health', 'Predicted Non-Health'],
                yticklabels=['Actual Health', 'Actual Non-Health'])
    ax.set_title('Confusion Matrix (Absolute Numbers)')
    
    plt.tight_layout()
    plt.savefig(METRICS_PLOT, dpi=300, bbox_inches='tight')
    logger.info(f"✅ Metrics plot saved: hasil_04/{METRICS_PLOT.name}")

def plot_predictions_breakdown(metrics):
    """Health vs Non-Health predictions breakdown"""
    fig, axes = plt.subplots(1, 2, figsize=(14, 5))
    fig.suptitle('Predictions Breakdown', fontsize=16, fontweight='bold')
    
    # Health predictions
    ax = axes[0]
    health_data = [metrics['pred_health_correct'], metrics['pred_health_wrong']]
    health_labels = [
        f'Correct (TP)\n{metrics["pred_health_correct"]}',
        f'Wrong (FP)\n{metrics["pred_health_wrong"]}'
    ]
    colors_health = ['#51cf66', '#ff6b6b']
    wedges, texts, autotexts = ax.pie(health_data, labels=health_labels, autopct='%1.1f%%',
                                        colors=colors_health, startangle=90)
    ax.set_title(f'Health Predictions\n(Total: {metrics["actual_health"]} videos sebenarnya adalah HEALTH)')
    for autotext in autotexts:
        autotext.set_color('white')
        autotext.set_fontweight('bold')
    
    # Non-Health predictions
    ax = axes[1]
    non_health_data = [metrics['pred_non_health_correct'], metrics['pred_non_health_wrong']]
    non_health_labels = [
        f'Correct (TN)\n{metrics["pred_non_health_correct"]}',
        f'Wrong (FN)\n{metrics["pred_non_health_wrong"]}'
    ]
    colors_non_health = ['#4dabf7', '#ff922b']
    wedges, texts, autotexts = ax.pie(non_health_data, labels=non_health_labels, autopct='%1.1f%%',
                                        colors=colors_non_health, startangle=90)
    ax.set_title(f'Non-Health Predictions\n(Total: {metrics["actual_non_health"]} videos sebenarnya adalah NON-HEALTH)')
    for autotext in autotexts:
        autotext.set_color('white')
        autotext.set_fontweight('bold')
    
    plt.tight_layout()
    plt.savefig(PREDICTIONS_PLOT, dpi=300, bbox_inches='tight')
    logger.info(f"✅ Predictions plot saved: hasil_04/{PREDICTIONS_PLOT.name}")

# ============================================================================
# SAVE ANALYSIS REPORT
# ============================================================================

def save_analysis_report(metrics, cm_df):
    """Save detailed analysis report"""
    with open(ANALYSIS_REPORT, 'w', encoding='utf-8') as f:
        f.write("="*70 + "\n")
        f.write("ACCURACY ANALYSIS REPORT\n")
        f.write("="*70 + "\n\n")
        
        f.write("OVERALL PERFORMANCE METRICS\n")
        f.write("-"*70 + "\n")
        f.write(f"Total Videos Tested: {metrics['total']}\n")
        f.write(f"Accuracy: {metrics['accuracy']:.1f}%\n")
        f.write(f"Precision (TP / (TP+FP)): {metrics['precision']:.1f}%\n")
        f.write(f"Recall (TP / (TP+FN)): {metrics['recall']:.1f}%\n")
        f.write(f"F1-Score: {metrics['f1']:.1f}%\n")
        f.write(f"Specificity (TN / (TN+FP)): {metrics['specificity']:.1f}%\n\n")
        
        f.write("CONFUSION MATRIX (Absolute Numbers)\n")
        f.write("-"*70 + "\n")
        f.write(f"True Positive (TP): {metrics['tp']}\n")
        f.write(f"  → Correctly predicted HEALTH\n\n")
        f.write(f"False Positive (FP): {metrics['fp']}\n")
        f.write(f"  → Predicted HEALTH but actually NOT_HEALTH\n\n")
        f.write(f"True Negative (TN): {metrics['tn']}\n")
        f.write(f"  → Correctly predicted NOT_HEALTH\n\n")
        f.write(f"False Negative (FN): {metrics['fn']}\n")
        f.write(f"  → Predicted NOT_HEALTH but actually HEALTH\n\n")
        
        f.write("PREDICTIONS BREAKDOWN\n")
        f.write("-"*70 + "\n")
        f.write(f"Total actual HEALTH videos: {metrics['actual_health']}\n")
        f.write(f"  → Predicted correctly: {metrics['pred_health_correct']}\n")
        f.write(f"  → Predicted wrong: {metrics['pred_health_wrong']}\n\n")
        f.write(f"Total actual NON-HEALTH videos: {metrics['actual_non_health']}\n")
        f.write(f"  → Predicted correctly: {metrics['pred_non_health_correct']}\n")
        f.write(f"  → Predicted wrong: {metrics['pred_non_health_wrong']}\n\n")
        
        f.write("TOP 10 BEST PERFORMING INFLUENCERS\n")
        f.write("-"*70 + "\n")
        sorted_inf = sorted(metrics['per_influencer'].items(), 
                           key=lambda x: x[1]['accuracy'], reverse=True)[:10]
        for rank, (username, inf_metrics) in enumerate(sorted_inf, 1):
            f.write(f"{rank}. @{username}: {inf_metrics['accuracy']:.1f}% ")
            f.write(f"({inf_metrics['correct']}/{inf_metrics['total']})\n")
        
        f.write("\n" + "BOTTOM 10 WORST PERFORMING INFLUENCERS\n")
        f.write("-"*70 + "\n")
        sorted_inf_worst = sorted(metrics['per_influencer'].items(), 
                                 key=lambda x: x[1]['accuracy'])[:10]
        for rank, (username, inf_metrics) in enumerate(sorted_inf_worst, 1):
            f.write(f"{rank}. @{username}: {inf_metrics['accuracy']:.1f}% ")
            f.write(f"({inf_metrics['correct']}/{inf_metrics['total']})\n")
        
        f.write("\n" + "ALL INFLUENCERS RANKING\n")
        f.write("-"*70 + "\n")
        sorted_all = sorted(metrics['per_influencer'].items(), 
                           key=lambda x: x[1]['accuracy'], reverse=True)
        for username, inf_metrics in sorted_all:
            f.write(f"@{username}: {inf_metrics['accuracy']:.1f}% ")
            f.write(f"(TP:{inf_metrics['tp']}, FP:{inf_metrics['fp']}, ")
            f.write(f"TN:{inf_metrics['tn']}, FN:{inf_metrics['fn']})\n")
    
    logger.info(f"✅ Analysis report saved: hasil_04/{ANALYSIS_REPORT.name}\n")

# ============================================================================
# MAIN
# ============================================================================

def main():
    logger.info("\n" + "="*70)
    logger.info("ANALYZE ACCURACY")
    logger.info("="*70 + "\n")
    logger.info(f"📖 Input folder: hasil_03/ (dari validate_and_train.py)")
    logger.info(f"📁 Output folder: hasil_04/\n")
    
    # Load
    df = load_results()
    if df is None:
        return False
    
    # Calculate metrics
    logger.info("Calculating metrics...\n")
    metrics = calculate_metrics(df)
    
    # Save confusion matrix
    cm_df = save_confusion_matrix(metrics)
    print(cm_df)
    print()
    
    # Visualizations
    logger.info("Generating plots...\n")
    plot_metrics(metrics)
    plot_predictions_breakdown(metrics)
    
    # Analysis report
    logger.info("Generating analysis report...\n")
    save_analysis_report(metrics, cm_df)
    
    # Summary console
    logger.info("\n" + "="*70)
    logger.info("SUMMARY")
    logger.info("="*70)
    logger.info(f"\n📊 OVERALL METRICS:")
    logger.info(f"   Accuracy: {metrics['accuracy']:.1f}%")
    logger.info(f"   Precision: {metrics['precision']:.1f}%")
    logger.info(f"   Recall: {metrics['recall']:.1f}%")
    logger.info(f"   F1-Score: {metrics['f1']:.1f}%")
    
    logger.info(f"\n🎯 HEALTH PREDICTIONS:")
    logger.info(f"   Total HEALTH videos: {metrics['actual_health']}")
    logger.info(f"   → Correctly predicted: {metrics['pred_health_correct']}")
    logger.info(f"   → Incorrectly predicted: {metrics['pred_health_wrong']}")
    
    logger.info(f"\n❌ NON-HEALTH PREDICTIONS:")
    logger.info(f"   Total NON-HEALTH videos: {metrics['actual_non_health']}")
    logger.info(f"   → Correctly predicted: {metrics['pred_non_health_correct']}")
    logger.info(f"   → Incorrectly predicted: {metrics['pred_non_health_wrong']}")
    
    logger.info(f"\n📁 Output folder: hasil_04/")
    logger.info(f"   ✅ confusion_matrix.csv")
    logger.info(f"   ✅ accuracy_metrics.png")
    logger.info(f"   ✅ predictions_breakdown.png")
    logger.info(f"   ✅ accuracy_analysis_report.txt")
    logger.info("\n" + "="*70 + "\n")
    
    return True

if __name__ == "__main__":
    import sys
    success = main()
    sys.exit(0 if success else 1)