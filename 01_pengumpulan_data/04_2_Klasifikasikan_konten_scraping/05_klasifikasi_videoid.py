"""
05_CLASSIFY_VIDEO.py
====================
Classify unlabeled videos pakai learned patterns dari 03_test_model_labeling.py

WORKFLOW:
1. Load learned_patterns.json (dari hasil_03)
2. Load labeled_videos.csv atau metadata_video.csv
3. Pisahin: yang sudah manual_label vs yang belum
4. Classify yang belum dengan logic check_this
5. Output: labeled_metadata_video.csv (di folder 04_2)

check_this logic:
  - TRUE = butuh manual review (adalah is_confidence=False)
    Kondisi: 2+ dari 3 FALSE: (accuracy<80%, confidence<70%, no_caption)
             ATAU caption ambigu (cuma hashtag/stitch/mention)
  - FALSE = confident, tidak perlu di-cek manual (adalah is_confidence=True)
    Kondisi: max 1 kondisi FALSE DAN caption clear

AUTO-RESUME: Script otomatis skip video yang sudah ada di output CSV.
Aman untuk di-rerun berkali-kali tanpa duplikasi.

BEFORE RUNNING:
✅ Run 03_test_model_labeling.py dulu (generate learned_patterns.json)
✅ Update FILE paths di bagian CONFIG bawah
✅ Pastikan Ollama running: ollama serve

Usage:
    python 05_classify_video.py

Ganti source data:
    Buka line: df = load_source_data(use_metadata=False)
    Ganti False ke True pakai metadata_video.csv
"""

import pandas as pd
import ollama
import json
import re
import time
import logging
import sys
from datetime import datetime
from pathlib import Path

# ============================================================================
# LOGGING
# ============================================================================

logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)

# ============================================================================
# ⚠️  CONFIG - UPDATE PATHS SESUAI SISTEM KAMU
# ============================================================================

# Folder utama (ubah sesuai lokasi di komputermu)
BASE_DIR = Path(r"01_pengumpulan_data/04_2_Klasifikasikan_konten_scraping")

# Source files
FILE_LABELED = BASE_DIR / "labeled_videos.csv"           # File yang dipake sekarang
FILE_METADATA = BASE_DIR / "../04_1_mendapatkan_metadata_akun/metadata_video.csv"  # Alternative

# Learned patterns dari step 03 (WAJIB ADA!)
FILE_LEARNED_PATTERNS = BASE_DIR / "hasil_03/learned_patterns.json"

# Output file
OUTPUT_FILE = BASE_DIR / "labeled_metadata_video.csv"

# ============================================================================
# CPU THROTTLING CONFIG (sama seperti 03)
# ============================================================================

OLLAMA_NUM_THREAD = 8
DELAY_BETWEEN_REQUESTS = 3.0
COOLDOWN_EVERY_N_VIDEOS = 500
COOLDOWN_DURATION = 30

# ============================================================================
# VERIFICATION
# ============================================================================

def verify_prerequisites():
    """Verify semua requirement sebelum jalan"""
    logger.info("\n" + "="*70)
    logger.info("VERIFICATION - Checking Prerequisites")
    logger.info("="*70 + "\n")
    
    errors = []
    
    # Check 1: Learned patterns
    if not FILE_LEARNED_PATTERNS.exists():
        errors.append(f"❌ learned_patterns.json not found!")
        errors.append(f"   Expected: {FILE_LEARNED_PATTERNS}")
        errors.append(f"   Action: Run 03_test_model_labeling.py first!")
    else:
        logger.info(f"✅ learned_patterns.json exists")
    
    # Check 2: Source data
    if not FILE_LABELED.exists() and not FILE_METADATA.exists():
        errors.append(f"❌ No source data found!")
        errors.append(f"   Expected one of:")
        errors.append(f"     - {FILE_LABELED}")
        errors.append(f"     - {FILE_METADATA}")
    else:
        if FILE_LABELED.exists():
            logger.info(f"✅ labeled_videos.csv exists")
        if FILE_METADATA.exists():
            logger.info(f"✅ metadata_video.csv exists")
    
    # Check 3: Output directory
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    logger.info(f"✅ Output directory ready: {OUTPUT_FILE.parent}")
    
    # Check 4: Ollama
    try:
        ollama.list()
        logger.info(f"✅ Ollama is running")
    except:
        errors.append(f"❌ Ollama not running!")
        errors.append(f"   Action: Run 'ollama serve' di terminal lain")
    
    if errors:
        logger.error("\n❌ VERIFICATION FAILED:\n")
        for error in errors:
            logger.error(error)
        return False
    
    logger.info(f"\n✅ ALL CHECKS PASSED - Ready to classify!")
    return True

# ============================================================================
# STEP 1: LOAD LEARNED PATTERNS
# ============================================================================

def load_learned_patterns():
    """Load learned patterns dari JSON"""
    try:
        with open(FILE_LEARNED_PATTERNS, 'r', encoding='utf-8') as f:
            patterns = json.load(f)
        
        logger.info(f"\n✅ Loaded learned patterns")
        logger.info(f"   Overall accuracy: {patterns['metadata']['overall_accuracy']:.1f}%")
        logger.info(f"   Influencers: {len(patterns['influencers'])}")
        
        return patterns
    except Exception as e:
        logger.error(f"❌ Error loading learned patterns: {e}")
        return None

# ============================================================================
# STEP 2: LOAD SOURCE DATA
# ============================================================================

def load_source_data(use_metadata=False):
    """Load source data: labeled_videos.csv atau metadata_video.csv"""
    if use_metadata:
        file = FILE_METADATA
        name = "metadata_video.csv"
    else:
        file = FILE_LABELED
        name = "labeled_videos.csv"
    
    if not file.exists():
        logger.error(f"❌ {file} not found!")
        return None
    
    try:
        df = pd.read_csv(file)
        df['video_id'] = df['video_id'].astype(str)
        logger.info(f"✅ Loaded {len(df)} videos dari {name}")
        return df
    except Exception as e:
        logger.error(f"❌ Error loading {name}: {e}")
        return None

# ============================================================================
# STEP 3: BUILD ENHANCED PROMPT
# ============================================================================

def build_enhanced_prompt(description, username, patterns):
    """Build prompt pakai learned patterns"""
    inf_data = patterns['influencers'].get(username, {})
    health_pct = inf_data.get('health_percentage', 50)
    
    base_prompt = """Kamu adalah sistem klasifikasi akademik untuk penelitian tugas akhir tentang kategorisasi konten edukasi kesehatan di TikTok Indonesia. Tugasmu murni melabeli data.

DEFINISI "KONTEN KESEHATAN":
✓ Kondisi medis, penyakit, gejala, pengobatan (fisik & mental)
✓ Kesehatan reproduksi & seksual (menstruasi, kehamilan, KB, kesehatan pria/wanita)
✓ Kesehatan mental (stress, trauma, parenting)
✓ Nutrisi, diet, pola makan untuk tujuan kesehatan
✓ Olahraga/fitness dengan tujuan kesehatan
✓ Edukasi dari tenaga medis
✓ Perawatan tubuh/kulit dengan tujuan medis

BUKAN kesehatan:
✗ Fashion/makeup murni estetika
✗ Vlog harian tanpa unsur medis
✗ Hiburan murni, gaming, teknologi, finansial, travel non-kesehatan"""
    
    if health_pct > 70:
        base_prompt += f"\n\n💡 KONTEKS: @{username} biasanya upload konten kesehatan ({health_pct:.0f}%)."
    elif health_pct < 30:
        base_prompt += f"\n\n💡 KONTEKS: @{username} jarang upload konten kesehatan ({health_pct:.0f}%). Hati-hati jangan false positive."
    else:
        base_prompt += f"\n\n💡 KONTEKS: @{username} upload balanced health & non-health ({health_pct:.0f}%)."
    
    base_prompt += f"""

Caption video:
"{description}"

Jawab HANYA dalam format ini:
LABEL: HEALTH atau NOT_HEALTH
CONFIDENCE: angka 0.0-1.0"""
    
    return base_prompt

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
# STEP 4: IN-MEMORY CACHE FOR DEDUPLICATION
# ============================================================================

_existing_ids_cache = None

def load_existing_ids_into_memory():
    """Load semua (username, video_id) dari output CSV ke memory"""
    global _existing_ids_cache
    _existing_ids_cache = set()
    
    if not OUTPUT_FILE.exists():
        return _existing_ids_cache
    
    try:
        df = pd.read_csv(
            OUTPUT_FILE,
            usecols=['username', 'video_id'],
            dtype={'username': str, 'video_id': str},
            on_bad_lines='skip',
            engine='python',
        )
        _existing_ids_cache = set(zip(df['username'], df['video_id']))
        logger.info(f"📋 Loaded {len(_existing_ids_cache)} existing videos dari output CSV")
    except Exception as e:
        logger.warning(f"⚠️  Could not load existing IDs: {e}")
        logger.warning("   Starting fresh (possible duplicates if file exists)")
        _existing_ids_cache = set()
    
    return _existing_ids_cache

def get_existing_video_ids():
    """Return in-memory cache"""
    global _existing_ids_cache
    if _existing_ids_cache is None:
        load_existing_ids_into_memory()
    return _existing_ids_cache

def init_output_csv():
    """Initialize output CSV with header"""
    if not OUTPUT_FILE.exists():
        OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
        header_df = pd.DataFrame(columns=[
            'username', 'video_id', 'description', 'predicted_label',
            'predicted_confidence', 'check_this', 'method'
        ])
        header_df.to_csv(OUTPUT_FILE, index=False, encoding='utf-8')
        logger.info(f"📝 Initialized output CSV: {OUTPUT_FILE.name}")

def append_to_csv(row_dict):
    """Append 1 row ke CSV, skip jika sudah ada"""
    global _existing_ids_cache
    if _existing_ids_cache is None:
        load_existing_ids_into_memory()
    
    key = (row_dict['username'], row_dict['video_id'])
    
    if key in _existing_ids_cache:
        return False  # Already exists
    
    # Append ke file
    row_df = pd.DataFrame([row_dict])
    row_df.to_csv(OUTPUT_FILE, mode='a', header=False, index=False, encoding='utf-8')
    
    # Update cache
    _existing_ids_cache.add(key)
    
    return True

# ============================================================================
# STEP 5: HELPERS
# ============================================================================

def is_ambiguous_caption(description):
    """Check caption ambiguous (cuma hashtag/mention tanpa konten)"""
    if not description or len(description) < 10:
        return True
    
    stripped = description.strip()
    
    if stripped.startswith('#') or stripped.startswith('@'):
        return True
    
    if '#stitch' in stripped.lower() or '#duet' in stripped.lower():
        if len(stripped) < 30:
            return True
    
    return False

def write_labeled_videos(labeled_df):
    """Write manually-labeled videos to CSV (skip yang sudah ada)"""
    if len(labeled_df) == 0:
        return
    
    existing_ids = get_existing_video_ids()
    labeled_filter = labeled_df[~labeled_df.apply(
        lambda x: (x['username'], x['video_id']) in existing_ids, 
        axis=1
    )]
    
    if len(labeled_filter) == 0:
        logger.info("✅ Semua labeled videos sudah di-output")
        return
    
    logger.info(f"💾 Writing {len(labeled_filter)} already-labeled videos ke CSV...")
    
    for _, row in labeled_filter.iterrows():
        row_dict = {
            'username': row['username'],
            'video_id': row['video_id'],
            'description': str(row['description'])[:100] if pd.notna(row['description']) else "",
            'predicted_label': row['manual_label'],
            'predicted_confidence': 1.0,
            'check_this': False,
            'method': 'manual_labeled',
        }
        append_to_csv(row_dict)
    
    logger.info(f"✅ Wrote {len(labeled_filter)} labeled videos to CSV")

# ============================================================================
# STEP 6: CLASSIFY UNLABELED VIDEOS
# ============================================================================

def classify_unlabeled(df, patterns):
    """Classify unlabeled videos - LANGSUNG WRITE KE CSV"""
    
    if 'manual_label' not in df.columns:
        df['manual_label'] = ''
    
    # Split data
    labeled = df[df['manual_label'].notna() & (df['manual_label'] != '')].copy()
    unlabeled = df[df['manual_label'].isna() | (df['manual_label'] == '')].copy()
    
    # Filter out yang sudah di-proses
    existing_ids = get_existing_video_ids()
    unlabeled_filter = unlabeled[~unlabeled.apply(
        lambda x: (x['username'], x['video_id']) in existing_ids, 
        axis=1
    )]
    
    logger.info(f"\n{'='*70}")
    logger.info(f"CLASSIFICATION")
    logger.info(f"{'='*70}")
    logger.info(f"\nAlready saved: {len(existing_ids)}")
    logger.info(f"Already labeled (manual): {len(labeled)}")
    logger.info(f"Need to classify: {len(unlabeled_filter)}")
    
    if len(unlabeled_filter) == 0:
        logger.info("✅ Semua video sudah ter-proses!")
        return True
    
    # Estimate time
    n_need_ollama = (unlabeled_filter['description'].fillna('').str.len() >= 5).sum()
    est_minutes = (n_need_ollama * (DELAY_BETWEEN_REQUESTS + 3)) / 60
    
    logger.info(f"\n🌡️  CPU THROTTLING: thread={OLLAMA_NUM_THREAD}, delay={DELAY_BETWEEN_REQUESTS}s")
    logger.info(f"⏱️  Estimated time: ~{est_minutes:.0f} minutes")
    logger.info(f"💾 Streaming to CSV (resume-safe)\n")
    
    # Initialize output
    init_output_csv()
    write_labeled_videos(labeled)
    
    # Classify + write
    ollama_call_count = 0
    n_classified = 0
    
    for idx, (_, row) in enumerate(unlabeled_filter.iterrows(), 1):
        video_id = row['video_id']
        username = row['username']
        description = str(row['description']).strip()[:800] if pd.notna(row['description']) else ""
        
        # Get influencer data
        has_caption = len(description) >= 5
        inf_data = patterns['influencers'].get(username, {})
        influencer_accuracy = inf_data.get('accuracy', 50)
        
        # Classify
        if len(description) < 5:
            # No caption: use influencer pattern
            health_pct = inf_data.get('health_percentage', 50)
            is_health = health_pct > 50
            confidence = abs(health_pct - 50) / 100
            method = 'no_caption_learned'
        else:
            try:
                prompt = build_enhanced_prompt(description, username, patterns)
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
                
                time.sleep(DELAY_BETWEEN_REQUESTS)
                
                if ollama_call_count % COOLDOWN_EVERY_N_VIDEOS == 0:
                    logger.info(f"   🧊 Cooldown {COOLDOWN_DURATION}s (batch {ollama_call_count})")
                    time.sleep(COOLDOWN_DURATION)
            
            except Exception as e:
                logger.debug(f"Error: {e}")
                is_health = False
                confidence = 0.5
                method = 'error'
        
        # Determine check_this
        # Count FALSE conditions: accuracy<80%, confidence<70%, no_caption
        conditions_false = []
        if influencer_accuracy < 80:
            conditions_false.append("acc<80%")
        if confidence < 0.70:
            conditions_false.append("conf<70%")
        if not has_caption:
            conditions_false.append("no_cap")
        
        n_false = len(conditions_false)
        is_caption_ambiguous = is_ambiguous_caption(description)
        
        # check_this = TRUE jika: (2+ FALSE) ATAU (ambiguous caption)
        check_this = (n_false > 1) or is_caption_ambiguous
        
        # Write to CSV
        row_dict = {
            'username': username,
            'video_id': video_id,
            'description': description[:100],
            'predicted_label': 'health' if is_health else 'not_health',
            'predicted_confidence': round(confidence, 3),
            'check_this': check_this,
            'method': method,
        }
        
        try:
            appended = append_to_csv(row_dict)
            if appended:
                n_classified += 1
        except Exception as e:
            logger.error(f"❌ Error writing to CSV: {e}")
            return False
        
        if idx % 50 == 0:
            logger.info(f"  Progress: {idx}/{len(unlabeled_filter)} | Saved: {n_classified}")
    
    logger.info(f"\n✅ Classified: {n_classified} videos")
    return True

# ============================================================================
# MAIN
# ============================================================================

def main():
    logger.info("\n" + "="*70)
    logger.info("05_CLASSIFY_VIDEO - CLASSIFY UNLABELED VIDEOS")
    logger.info("="*70 + "\n")
    
    # Verify prerequisites
    if not verify_prerequisites():
        return False
    
    # Load patterns
    patterns = load_learned_patterns()
    if patterns is None:
        return False
    
    # Load source data - PRIORITIZE MANUAL LABELS!
    logger.info(f"\n📂 Loading source data...")
    logger.info(f"   Step 1: Load manual labels dari labeled_videos.csv...")
    
    df_labeled = load_source_data(use_metadata=False)  # labeled_videos.csv
    if df_labeled is None:
        logger.warning("⚠️  labeled_videos.csv not found, will use metadata_video.csv only")
        df_labeled = pd.DataFrame()
    else:
        logger.info(f"   ✅ Loaded {len(df_labeled)} labeled videos")
    
    logger.info(f"   Step 2: Load metadata dari metadata_video.csv (untuk unlabeled)...")
    df_metadata = load_source_data(use_metadata=True)  # metadata_video.csv
    if df_metadata is None:
        logger.error("❌ No metadata found!")
        return False
    else:
        logger.info(f"   ✅ Loaded {len(df_metadata)} total videos dari metadata")
    
    # COMBINE: Prioritize manual labels
    logger.info(f"\n   Step 3: Merging - prioritize manual labels over metadata...")
    
    if len(df_labeled) > 0:
        # Mark videos yang sudah ada di labeled_videos.csv
        labeled_ids = set(zip(df_labeled['username'], df_labeled['video_id']))
        
        # Update metadata dengan manual labels
        for idx, row in df_metadata.iterrows():
            key = (row['username'], row['video_id'])
            if key in labeled_ids:
                # Ada di labeled_videos.csv, copy manual label
                labeled_row = df_labeled[(df_labeled['username'] == row['username']) & 
                                        (df_labeled['video_id'] == row['video_id'])].iloc[0]
                df_metadata.at[idx, 'manual_label'] = labeled_row['manual_label']
        
        n_with_labels = (df_metadata['manual_label'].notna() & (df_metadata['manual_label'] != '')).sum()
        logger.info(f"   ✅ {n_with_labels} videos now have manual labels")
    
    # Classify (only unlabeled)
    success = classify_unlabeled(df_metadata, patterns)
    
    if success:
        logger.info("\n" + "="*70)
        logger.info("✅ CLASSIFICATION COMPLETE")
        logger.info(f"📝 Output: {OUTPUT_FILE}")
        logger.info("="*70 + "\n")
        
        # Show summary
        try:
            result_df = pd.read_csv(OUTPUT_FILE)
            n_check = (result_df['check_this'] == True).sum()
            n_confident = (result_df['check_this'] == False).sum()
            logger.info(f"\n📊 SUMMARY:")
            logger.info(f"   Total: {len(result_df)} videos")
            logger.info(f"   Confident (check_this=False): {n_confident}")
            logger.info(f"   Need review (check_this=True): {n_check}")
        except:
            pass
        
        return True
    else:
        logger.error("\n❌ Classification failed")
        return False

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)