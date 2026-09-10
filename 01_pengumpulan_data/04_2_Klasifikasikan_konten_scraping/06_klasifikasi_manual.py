"""
06_MANUAL_CLASSIFY.py
======================
Web app buat manual review video yang butuh dicek (check_this=True,
method='ollama_enhanced' ATAU 'no_caption_learned') dari
labeled_metadata_video.csv (hasil 05).

⬇️ BEDA DARI VERSI SEBELUMNYA: TIDAK bikin file baru. Langsung UPDATE
   baris yang bersangkutan di labeled_metadata_video.csv itu sendiri:
     - predicted_label  -> diganti sesuai keputusan manual kamu
     - method            -> diganti jadi 'manual_check' (jejak: ini udah dicek manual)
     - check_this        -> TETAP True (jejak: video ini pernah butuh manual review)
     - reviewed_at       -> timestamp kapan direview

   Karena method berubah jadi 'manual_check', video itu otomatis KELUAR
   dari antrian review kalau kamu rerun script -- resume otomatis, tanpa
   perlu file terpisah atau dict tracking tambahan.

Cara kerja:
- Video di-embed langsung (TikTok embed player)
- Prediksi model ditampilin sebagai HINT (biar cepet decide)
- Arrow LEFT (←)  = NOT_HEALTH
- Arrow RIGHT (→) = HEALTH
- Arrow UP (↑) / tombol Previous = balik ke video sebelumnya (relabel)
- Auto-save PER KLIK, langsung update + rewrite labeled_metadata_video.csv

Usage:
    python 06_manual_classify.py
    Buka browser: http://localhost:5000
"""

from flask import Flask, render_template_string, request, jsonify
import pandas as pd
import re
import logging
from datetime import datetime
from pathlib import Path

logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)

# ============================================================================
# PATH CONFIG - CUSTOMIZE DI SINI
# ============================================================================

# File ini dipakai SEBAGAI INPUT SEKALIGUS OUTPUT (di-update langsung, in-place)
MASTER_FILE = Path(r"C:\Users\nesha\Tugas_akhir\seleksi-influencer\01_pengumpulan_data\04_2_Klasifikasikan_konten_scraping\labeled_metadata_video.csv")

# ============================================================================
# GLOBAL STATE
# ============================================================================

data = {
    'master_df': None,    # SELURUH data dari labeled_metadata_video.csv (di-load sekali)
    'queue': [],           # list of df index (label) yang butuh direview, snapshot di awal
    'current_pos': 0,      # posisi di dalam 'queue' (bukan index df langsung)
}

# ============================================================================
# HELPERS
# ============================================================================

def extract_video_id(video_id):
    vid = str(video_id).strip()
    return vid if vid.isdigit() else re.sub(r'\D', '', vid)

def rewrite_master_csv():
    """Tulis ULANG seluruh labeled_metadata_video.csv dari master_df in-memory.
    Dipanggil tiap ada 1 review, jadi file di-disk selalu konsisten -- kalau
    program di-close paksa di tengah jalan, data yang udah direview tetap aman."""
    data['master_df'].to_csv(MASTER_FILE, index=False, encoding='utf-8')

# ============================================================================
# LOAD DATA
# ============================================================================

def load_data():
    """Load labeled_metadata_video.csv, susun antrian video yang butuh review."""
    if not MASTER_FILE.exists():
        logger.error(f"❌ {MASTER_FILE} not found!")
        logger.error("   Run 05_classify_video.py dulu.")
        return False

    try:
        df = pd.read_csv(MASTER_FILE, dtype={'username': str, 'video_id': str})
    except Exception as e:
        logger.error(f"❌ Error load {MASTER_FILE}: {e}")
        return False

    df['description'] = df['description'].fillna('')

    # Tambah kolom reviewed_at kalau belum ada (buat jejak kapan direview manual)
    if 'reviewed_at' not in df.columns:
        df['reviewed_at'] = ''

    data['master_df'] = df

    # ⬇️ ANTRIAN: check_this=True DAN method masih 'ollama_enhanced' ATAU 'no_caption_learned'.
    # Video yang SUDAH direview manual otomatis method-nya 'manual_check',
    # jadi otomatis KELUAR dari antrian ini kalau di-rerun -> resume otomatis.
    check_this_bool = df['check_this'].astype(str).str.lower() == 'true'
    is_pending_method = df['method'].isin(['ollama_enhanced', 'no_caption_learned'])
    is_pending = is_pending_method & check_this_bool
    queue = df[is_pending].index.tolist()

    data['queue'] = queue
    data['current_pos'] = 0

    total_check_this = check_this_bool.sum()
    already_reviewed = ((df['method'] == 'manual_check') & check_this_bool).sum()

    logger.info(f"✅ Loaded {len(df)} total videos dari {MASTER_FILE.name}")
    logger.info(f"   Total check_this=True (butuh/pernah butuh review): {total_check_this}")
    logger.info(f"   Sudah direview manual (method='manual_check'): {already_reviewed}")
    logger.info(f"   Sisa di antrian (ollama_enhanced + no_caption_learned): {len(queue)}")

    return True

# ============================================================================
# ROUTES
# ============================================================================

@app.route('/')
def index():
    try:
        queue = data['queue']
        df = data['master_df']

        if not queue:
            return "Tidak ada video yang perlu di-review (method='ollama_enhanced' & check_this=True kosong)."

        total = len(queue)
        # progress dihitung dari berapa banyak item di queue yang sudah 'manual_check'
        reviewed = sum(1 for idx in queue if df.at[idx, 'method'] == 'manual_check')
        progress = (reviewed / total * 100) if total > 0 else 0

        if data['current_pos'] >= total:
            return render_template_string(DONE_HTML, total=total)

        df_idx = queue[data['current_pos']]
        row = df.loc[df_idx]

        video_id = extract_video_id(row['video_id'])
        video_url = f"https://www.tiktok.com/@{row['username']}/video/{row['video_id']}"

        return render_template_string(
            MAIN_HTML,
            progress=progress,
            progress_bar=f"{reviewed}/{total}",
            username=row['username'],
            description=str(row['description']),
            predicted_label=row['predicted_label'],
            predicted_confidence=row['predicted_confidence'],
            video_id=video_id,
            url=video_url,
            has_prev=(data['current_pos'] > 0),
        )
    except Exception as e:
        import traceback
        traceback.print_exc()
        return f"Error: {e}", 500

@app.route('/label', methods=['POST'])
def label_video():
    try:
        queue = data['queue']
        df = data['master_df']

        payload = request.get_json(silent=True)
        if not payload:
            return jsonify({'error': 'No JSON body'}), 400

        label = payload.get('label')
        if label not in ['health', 'not_health']:
            return jsonify({'error': f'Invalid label: {label}'}), 400

        if data['current_pos'] >= len(queue):
            return jsonify({'error': 'All videos reviewed'}), 400

        df_idx = queue[data['current_pos']]

        # ⬇️ UPDATE IN-PLACE di master_df (bukan bikin baris/file baru):
        #    - predicted_label -> keputusan manual kamu
        #    - method          -> 'manual_check' (jejak: ini hasil review manual)
        #    - check_this      -> TETAP True (jejak: video ini pernah butuh manual review)
        #    - reviewed_at     -> timestamp
        df.at[df_idx, 'predicted_label'] = label
        df.at[df_idx, 'predicted_confidence'] = 1.0
        df.at[df_idx, 'method'] = 'manual_check'
        df.at[df_idx, 'check_this'] = True
        df.at[df_idx, 'reviewed_at'] = datetime.now().isoformat()

        rewrite_master_csv()

        data['current_pos'] += 1

        if data['current_pos'] >= len(queue):
            return jsonify({
                'done': True,
                'message': f'✅ Semua {len(queue)} video sudah direview!',
                'file': str(MASTER_FILE)
            })

        return jsonify({'success': True})

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

@app.route('/previous', methods=['POST'])
def previous_video():
    try:
        if data['current_pos'] <= 0:
            return jsonify({'error': 'Sudah di video paling awal'}), 400
        data['current_pos'] -= 1
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ============================================================================
# HTML
# ============================================================================

MAIN_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>Manual Review</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            display: flex;
            justify-content: center;
            align-items: flex-start;
            padding: 30px 20px;
        }
        .container {
            background: white;
            border-radius: 14px;
            box-shadow: 0 20px 60px rgba(0,0,0,0.3);
            max-width: 900px;
            width: 100%;
            padding: 26px 30px;
        }
        .top-bar { display: flex; align-items: center; gap: 14px; margin-bottom: 18px; }
        .progress { flex: 1; }
        .progress-bar {
            width: 100%; height: 8px; background: #e0e0e0; border-radius: 10px;
            overflow: hidden; margin-bottom: 8px;
        }
        .progress-fill {
            height: 100%;
            background: linear-gradient(90deg, #667eea 0%, #764ba2 100%);
            width: {{ progress }}%;
        }
        .progress-text { font-size: 14px; color: #666; font-weight: 500; }
        .btn-prev {
            flex-shrink: 0; background: #f0f0f5; color: #555; border: none;
            border-radius: 8px; padding: 10px 16px; font-size: 14px; font-weight: 600;
            cursor: pointer; display: flex; align-items: center; gap: 6px;
        }
        .btn-prev:hover:not(:disabled) { background: #e2e2ea; }
        .btn-prev:disabled { opacity: 0.35; cursor: not-allowed; }

        .main-layout { display: flex; gap: 28px; align-items: flex-start; }
        .left-col { flex-shrink: 0; width: 340px; }
        .right-col { flex: 1; min-width: 260px; display: flex; flex-direction: column; }

        .username { font-size: 19px; color: #333; margin-bottom: 10px; font-weight: 700; }

        .video-embed-wrapper {
            width: 100%; height: 605px; overflow: hidden; border-radius: 12px;
            background: #000; position: relative;
        }
        .video-embed-wrapper iframe {
            border: none; width: 325px; height: 738px;
            transform: scale(1.05); transform-origin: top left;
            position: absolute; top: 0; left: 50%; margin-left: -170px;
        }
        .fallback-link {
            display: block; text-align: center; font-size: 13px; color: #667eea;
            margin-top: 10px; text-decoration: none;
        }

        .ai-hint {
            padding: 14px 16px; border-radius: 10px; margin-bottom: 16px;
            font-size: 15px; font-weight: 600; display: flex;
            align-items: center; justify-content: space-between;
        }
        .ai-hint.health { background: #e6f9ea; color: #2f9e44; }
        .ai-hint.not_health { background: #ffeaea; color: #e03131; }
        .ai-hint .conf { font-size: 13px; font-weight: 500; opacity: 0.8; }

        .description {
            font-size: 17px; color: #333; background: #f9f9f9; padding: 18px;
            border-radius: 10px; line-height: 1.7; margin-bottom: 20px;
            max-height: 260px; overflow-y: auto; white-space: pre-wrap;
            word-break: break-word; flex: 1;
        }

        .controls { display: flex; flex-direction: column; gap: 12px; }
        .btn {
            padding: 18px 16px; border: none; border-radius: 10px; font-size: 17px;
            font-weight: 700; cursor: pointer; display: flex; align-items: center;
            justify-content: center; gap: 10px;
        }
        .btn-no { background: #ff6b6b; color: white; }
        .btn-no:hover { background: #ff5252; }
        .btn-yes { background: #51cf66; color: white; }
        .btn-yes:hover { background: #40c057; }

        .hint { margin-top: 16px; font-size: 13px; color: #999; text-align: center; }
        .arrow { font-size: 19px; }
        .loading { opacity: 0.5; pointer-events: none; }

        @media (max-width: 700px) {
            .main-layout { flex-direction: column; }
            .left-col { width: 100%; }
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="top-bar">
            <button class="btn-prev" id="prevBtn" onclick="goPrevious()" {{ 'disabled' if not has_prev else '' }}>
                <span class="arrow">↑</span> Previous
            </button>
            <div class="progress">
                <div class="progress-bar"><div class="progress-fill"></div></div>
                <div class="progress-text">{{ progress_bar }} direview</div>
            </div>
        </div>

        <div class="main-layout">
            <div class="left-col">
                <div class="username">@{{ username }}</div>
                <div class="video-embed-wrapper">
                    <iframe
                        src="https://www.tiktok.com/embed/v2/{{ video_id }}"
                        allow="encrypted-media;" allowfullscreen tabindex="-1">
                    </iframe>
                </div>
                <a href="{{ url }}" target="_blank" class="fallback-link">
                    Video gak muncul? Buka di tab baru →
                </a>
            </div>

            <div class="right-col">
                <div class="ai-hint {{ predicted_label }}">
                    <span>🤖 Model bilang: {{ 'HEALTH' if predicted_label == 'health' else 'NOT HEALTH' }}</span>
                    <span class="conf">confidence {{ (predicted_confidence * 100) | round(0) }}%</span>
                </div>

                <div class="description">{{ description if description else '(caption kosong)' }}</div>

                <div class="controls">
                    <button class="btn btn-no" onclick="labelVideo('not_health')">
                        <span class="arrow">←</span> NOT HEALTH
                    </button>
                    <button class="btn btn-yes" onclick="labelVideo('health')">
                        HEALTH <span class="arrow">→</span>
                    </button>
                </div>

                <div class="hint">
                    💡 ← NOT HEALTH &nbsp;|&nbsp; → HEALTH &nbsp;|&nbsp; ↑ Previous
                </div>
            </div>
        </div>
    </div>

    <script>
        function setLoading(state) {
            document.querySelectorAll('.btn, .btn-prev').forEach(b => {
                if (state) b.classList.add('loading'); else b.classList.remove('loading');
            });
        }

        function labelVideo(label) {
            setLoading(true);
            fetch('/label', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ label: label })
            })
            .then(r => r.json().catch(() => { throw new Error('Server error, cek terminal Flask.'); }))
            .then(data => {
                if (data.error) { alert('Error: ' + data.error); setLoading(false); return; }
                if (data.done) {
                    document.body.innerHTML = '<div class="container" style="text-align:center;margin-top:50px;max-width:500px;"><h1 style="color:#51cf66;font-size:48px;">✅</h1><h2>' + data.message + '</h2><p style="color:#666;margin-top:20px;">File: ' + data.file + '</p></div>';
                } else if (data.success) { location.reload(); }
            })
            .catch(e => { alert('Error: ' + e.message); setLoading(false); });
        }

        function goPrevious() {
            setLoading(true);
            fetch('/previous', { method: 'POST' })
            .then(r => r.json().catch(() => { throw new Error('Server error.'); }))
            .then(data => {
                if (data.error) { setLoading(false); return; }
                if (data.success) location.reload();
            })
            .catch(e => { alert('Error: ' + e.message); setLoading(false); });
        }

        document.addEventListener('keydown', (e) => {
            if (e.key === 'ArrowLeft') labelVideo('not_health');
            else if (e.key === 'ArrowRight') labelVideo('health');
            else if (e.key === 'ArrowUp') { e.preventDefault(); goPrevious(); }
        });

        // Fix: iframe TikTok (cross-origin) suka nyuri keyboard focus.
        window.addEventListener('blur', () => { setTimeout(() => window.focus(), 50); });
    </script>
</body>
</html>
"""

DONE_HTML = """
<!DOCTYPE html>
<html><head><title>Done!</title>
<style>
    * { margin:0; padding:0; }
    body { font-family:-apple-system,sans-serif; background:linear-gradient(135deg,#667eea,#764ba2);
        min-height:100vh; display:flex; justify-content:center; align-items:center; padding:20px; }
    .container { background:white; border-radius:12px; box-shadow:0 20px 60px rgba(0,0,0,0.3);
        max-width:500px; width:100%; padding:50px; text-align:center; }
    h1 { font-size:72px; margin-bottom:20px; }
    h2 { font-size:28px; color:#333; margin-bottom:15px; }
    p { font-size:16px; color:#666; }
</style></head>
<body><div class="container">
    <h1>✅</h1>
    <h2>Semua {{ total }} video sudah direview!</h2>
    <p>Hasilnya langsung ke-update di labeled_metadata_video.csv</p>
</div></body></html>
"""

# ============================================================================
# RUN
# ============================================================================

if __name__ == '__main__':
    print("\n" + "="*70)
    print("06_MANUAL_CLASSIFY - REVIEW VIDEO YANG BUTUH DICEK")
    print("="*70)

    if not load_data():
        print("❌ Gagal load data")
        exit(1)

    print(f"\n🌐 Buka browser: http://localhost:5000")
    print(f"⌨️  ← NOT HEALTH | → HEALTH | ↑ Previous\n")

    app.run(debug=False, host='localhost', port=5000)