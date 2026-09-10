"""
05_CLASSIFY_VIDEO.py
====================
Classify 1000 sample videos pakai learned patterns dari 03_test_model_labeling.py

WORKFLOW:
1. Load learned_patterns.json (dari hasil_03)
2. Load labeled_videos.csv atau metadata_video.csv
3. Pisahin: yang sudah manual_label vs yang belum
4. Classify yang belum dengan logic is_confidence
5. Output: labeled_metadata_video.csv (di folder 04_2)

is_confidence logic:
  - TRUE kalau max 1 dari 3 kondisi FALSE:
    • Influencer accuracy >= 80%
    • Model confidence >= 70%
    • Ada caption (len >= 5)
  - FALSE kalau 2+ kondisi FALSE

check_this column:
  - TRUE = butuh manual review (adalah is_confidence=False)
  - FALSE = confident, tidak perlu di-cek manual (adalah is_confidence=True)

AUTO-RESUME: Script otomatis skip video yang sudah ada di output CSV.
Aman untuk di-rerun berkali-kali tanpa duplikasi.

Usage:
    python 05_classify_video.py

Penggunaan file METADATA (metadata_video.csv) dari scraping:
    Buka file ini, cari baris:
        df = load_source_data(use_metadata=False)
    Ganti False ke True, jalankan script.
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
from config import validate_paths

# ============================================================================
# LOGGING
# ============================================================================

logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)

# ============================================================================
# PATH CONFIG - CUSTOMIZE DI SINI
# ============================================================================

# File yang dipake sekarang (labeled_videos.csv)
FILE_LABELED = Path(r"C:\Users\nesha\Tugas_akhir\seleksi-influencer\01_pengumpulan_data\04_2_Klasifikasikan_konten_scraping\labeled_videos.csv")

# File yang nanti bisa dipake (metadata_video.csv dari scraping)
FILE_METADATA = Path(r"C:\Users\nesha\Tugas_akhir\seleksi-influencer\01_pengumpulan_data\04_1_mendapatkan_metadata_akun\metadata_video.csv")

# Learned patterns dari 03_test_model_labeling.py
FILE_LEARNED_PATTERNS = Path(r"C:\Users\nesha\Tugas_akhir\seleksi-influencer\01_pengumpulan_data\04_2_Klasifikasikan_konten_scraping\hasil_03\learned_patterns.json")

# Output file
OUTPUT_FILE = Path(r"C:\Users\nesha\Tugas_akhir\seleksi-influencer\01_pengumpulan_data\04_2_Klasifikasikan_konten_scraping\labeled_metadata_video.csv")

# ============================================================================
# CONFIG - CPU THROTTLING (sama seperti 03)
# ============================================================================

OLLAMA_NUM_THREAD = 8
DELAY_BETWEEN_REQUESTS = 3.0
COOLDOWN_EVERY_N_VIDEOS = 500
COOLDOWN_DURATION = 30

# ============================================================================
# STEP 1: LOAD LEARNED PATTERNS
# ============================================================================

def load_learned_patterns():
    """Load learned patterns dari JSON"""
    if not FILE_LEARNED_PATTERNS.exists():
        logger.error(f"❌ {FILE_LEARNED_PATTERNS} not found!")
        logger.error("   Run 03_test_model_labeling.py dulu buat generate learned_patterns.json")
        return None
    
    with open(FILE_LEARNED_PATTERNS, 'r', encoding='utf-8') as f:
        patterns = json.load(f)
    
    logger.info(f"✅ Loaded learned patterns dari hasil_03/learned_patterns.json")
    logger.info(f"   Overall accuracy: {patterns['metadata']['overall_accuracy']:.1f}%")
    logger.info(f"   Influencers: {len(patterns['influencers'])}")
    
    return patterns

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
    
    df = pd.read_csv(file)
    df['video_id'] = df['video_id'].astype(str)
    logger.info(f"✅ Loaded {len(df)} videos dari {name}")
    
    return df

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
# STEP 4: CLASSIFY UNLABELED VIDEOS
# ============================================================================

# ============================================================================
# STEP 4: CLASSIFY UNLABELED VIDEOS (DIRECT CSV WRITE - NO BUFFER)
# ============================================================================

# ⬇️ IN-MEMORY CACHE: load sekali di awal, update terus selama proses jalan.
# Ini menghindari re-read CSV berkali-kali (lambat + rawan gagal parse kalau
# ada caption dengan koma/quote yang bikin pd.read_csv salah baca baris).
_existing_ids_cache = None

def load_existing_ids_into_memory():
    """Load semua (username, video_id) yang SUDAH ada di output CSV ke memory.
    Dipanggil SEKALI di awal. Robust terhadap baris CSV yang agak berantakan."""
    global _existing_ids_cache
    _existing_ids_cache = set()
    
    if not OUTPUT_FILE.exists():
        return _existing_ids_cache
    
    try:
        # dtype=str WAJIB -- video_id itu angka gede (>18 digit), kalau dibaca
        # sebagai int/float, presisi bisa berubah dan perbandingan jadi gagal.
        # on_bad_lines='skip' -- kalau ada baris yang somehow rusak/misformat,
        # skip baris itu aja daripada seluruh read gagal & return kosong.
        df = pd.read_csv(
            OUTPUT_FILE,
            usecols=['username', 'video_id'],
            dtype={'username': str, 'video_id': str},
            on_bad_lines='skip',
            engine='python',
        )
        _existing_ids_cache = set(zip(df['username'], df['video_id']))
        logger.info(f"📋 Loaded {len(_existing_ids_cache)} existing (username, video_id) dari CSV ke memory")
    except Exception as e:
        logger.warning(f"⚠️ Gagal load existing IDs dari CSV: {e}")
        logger.warning("   Mulai dengan cache kosong (kemungkinan ada duplikat kalau file sudah ada isinya)")
        _existing_ids_cache = set()
    
    return _existing_ids_cache

def get_existing_video_ids():
    """Return cache in-memory (load dulu kalau belum pernah)"""
    global _existing_ids_cache
    if _existing_ids_cache is None:
        load_existing_ids_into_memory()
    return _existing_ids_cache

def init_output_csv():
    """Init output CSV with header (jika belum ada)"""
    if not OUTPUT_FILE.exists():
        OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
        header_df = pd.DataFrame(columns=[
            'username', 'video_id', 'description', 'predicted_label',
            'predicted_confidence', 'check_this', 'method'
        ])
        header_df.to_csv(OUTPUT_FILE, index=False, encoding='utf-8')
        logger.info(f"📝 Init output CSV: {OUTPUT_FILE.name}")

def write_labeled_videos(labeled_df):
    """Write already-labeled videos ke CSV (skip yang sudah ada)"""
    if len(labeled_df) == 0:
        return
    
    existing_ids = get_existing_video_ids()
    labeled_filter = labeled_df[~labeled_df.apply(lambda x: (x['username'], x['video_id']) in existing_ids, axis=1)]
    
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
            'check_this': False,  # Sudah manual label, tidak perlu di-cek lagi
            'method': 'manual_labeled',
        }
        append_to_csv(row_dict)
    
    logger.info(f"✅ Wrote {len(labeled_filter)} labeled videos to CSV")

def is_ambiguous_caption(description):
    """Check kalau caption ambigu / tidak informatif (cuma hashtag/mention/stitch tanpa konten)"""
    if not description or len(description) < 10:
        return True
    
    # Cek kalau cuma stitch/duet/mention/hashtag tanpa konten nyata
    stripped = description.strip()
    
    # Cuma hashtag/mention
    if stripped.startswith('#') or stripped.startswith('@'):
        return True
    
    # Cuma stitch/duet mention
    if '#stitch' in stripped.lower() or '#duet' in stripped.lower():
        if len(stripped) < 30:  # Cuma mention, gak ada konten lainnya
            return True
    
    return False

def append_to_csv(row_dict):
    """Append 1 row langsung ke CSV, SKIP kalau sudah ada (cek pakai in-memory cache,
    BUKAN re-read file tiap kali -- jauh lebih cepat & gak rawan gagal parse)."""
    global _existing_ids_cache
    if _existing_ids_cache is None:
        load_existing_ids_into_memory()
    
    key = (row_dict['username'], row_dict['video_id'])
    
    if key in _existing_ids_cache:
        # Skip, sudah ada
        return False
    
    # Append ke file
    row_df = pd.DataFrame([row_dict])
    row_df.to_csv(OUTPUT_FILE, mode='a', header=False, index=False, encoding='utf-8')
    
    # Update cache in-memory
    _existing_ids_cache.add(key)
    
    return True

def classify_unlabeled(df, patterns):
    """Classify video yang belum punya manual_label
    🔥 LANGSUNG WRITE KE CSV - TIDAK SIMPEN DI MEMORY
    """
    if 'manual_label' not in df.columns:
        df['manual_label'] = ''
    
    # Pisahin
    labeled = df[df['manual_label'].notna() & (df['manual_label'] != '')].copy()
    unlabeled = df[df['manual_label'].isna() | (df['manual_label'] == '')].copy()
    
    # Get existing IDs dari CSV (buat skip yang sudah ada)
    existing_ids = get_existing_video_ids()
    unlabeled_filter = unlabeled[~unlabeled.apply(lambda x: (x['username'], x['video_id']) in existing_ids, axis=1)]
    
    logger.info(f"\n{'='*70}")
    logger.info(f"STEP: CLASSIFY UNLABELED VIDEOS (STREAM WRITE TO CSV)")
    logger.info(f"{'='*70}")
    logger.info(f"\nAlready saved to CSV: {len(existing_ids)}")
    logger.info(f"Already labeled (manual): {len(labeled)}")
    logger.info(f"Need to classify: {len(unlabeled_filter)}")
    
    if len(unlabeled_filter) == 0:
        logger.info("✅ Semua video sudah ter-proses!")
        return True
    
    # Estimate waktu
    n_need_ollama = (unlabeled_filter['description'].fillna('').str.len() >= 5).sum()
    est_minutes = (n_need_ollama * (DELAY_BETWEEN_REQUESTS + 3)) / 60
    
    logger.info(f"\n🌡️  CPU THROTTLING AKTIF (num_thread={OLLAMA_NUM_THREAD}, delay={DELAY_BETWEEN_REQUESTS}s)")
    logger.info(f"⏱️  Estimasi waktu: ~{est_minutes:.0f} menit")
    logger.info(f"💾 Langsung append ke CSV tiap video (resume-safe)\n")
    
    # Init CSV jika belum ada
    init_output_csv()
    
    # Write labeled videos dulu
    write_labeled_videos(labeled)
    
    # Classify + langsung write ke CSV
    ollama_call_count = 0
    n_classified = 0
    
    for idx, (_, row) in enumerate(unlabeled_filter.iterrows(), 1):
        video_id = row['video_id']
        username = row['username']
        description = str(row['description']).strip()[:800] if pd.notna(row['description']) else ""
        
        # Hitung is_confidence
        has_caption = len(description) >= 5
        inf_data = patterns['influencers'].get(username, {})
        influencer_accuracy = inf_data.get('accuracy', 50)
        
        if len(description) < 5:
            # No caption: gunakan influencer pattern
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
                    logger.info(f"   🧊 Cooldown {COOLDOWN_DURATION}s (video ke-{ollama_call_count})")
                    time.sleep(COOLDOWN_DURATION)
            
            except Exception as e:
                logger.debug(f"Error: {e}")
                is_health = False
                confidence = 0.5
                method = 'error'
        
        # ⬇️ LOGIC check_this: TRUE (butuh review) kalau:
        # - 2+ dari 3 kondisi FALSE, ATAU
        # - Caption ambigu (cuma hashtag/stitch/mention tanpa konten)
        
        conditions_false = []
        if influencer_accuracy < 80:
            conditions_false.append("accuracy<80%")
        if confidence < 0.70:
            conditions_false.append("confidence<70%")
        if not has_caption:
            conditions_false.append("no_caption")
        
        n_false_conditions = len(conditions_false)
        is_too_many_false = (n_false_conditions > 1)
        is_caption_ambiguous = is_ambiguous_caption(description)
        
        # Check_this = TRUE kalau: (2+ kondisi FALSE) ATAU (caption ambigu)
        check_this = is_too_many_false or is_caption_ambiguous
        
        # 💾 LANGSUNG APPEND KE CSV
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
            logger.error(f"❌ Error write ke CSV: {e}")
            return False
        
        if idx % 20 == 0:
            logger.info(f"  Progress: {idx}/{len(unlabeled_filter)} | Saved: {n_classified}")
    
    logger.info(f"\n✅ Classified: {n_classified} videos (langsung ke CSV, no buffer)")
    return True

# ============================================================================
# STEP 5: SAVE OUTPUT
# ============================================================================

def main():
    logger.info("\n" + "="*70)
    logger.info("05_CLASSIFY_VIDEO - CLASSIFY 1000 SAMPLE")
    logger.info("="*70 + "\n")
    
    if not FILE_LEARNED_PATTERNS.exists():
        logger.error("❌ Run 03_test_model_labeling.py first!")
        return False
    
    try:
        ollama.list()
    except:
        logger.error("❌ Ollama not running! Run: ollama serve")
        return False
    
    # Load
    patterns = load_learned_patterns()
    if patterns is None:
        return False
    
    logger.info(f"\n📂 Using: labeled_videos.csv (currently)")
    logger.info(f"   Ready for: metadata_video.csv (future)\n")
    
    df = load_source_data(use_metadata=False)
    if df is None:
        return False
    
    # Classify (langsung write ke CSV, no buffer)
    success = classify_unlabeled(df, patterns)
    
    if success:
        logger.info("\n" + "="*70)
        logger.info("✅ DONE!")
        logger.info(f"📝 Output: {OUTPUT_FILE.name}")
        logger.info("="*70 + "\n")
        return True
    else:
        logger.error("\n❌ Classification failed")
        return False

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)