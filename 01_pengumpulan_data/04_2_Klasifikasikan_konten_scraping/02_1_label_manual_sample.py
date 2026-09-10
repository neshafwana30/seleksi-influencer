"""
LABELING APP - ARROW KEYS
==========================
Simple Flask app untuk manual labeling dengan arrow keys
- Arrow LEFT (←)  = NOT HEALTH
- Arrow RIGHT (→) = HEALTH
- Arrow UP (↑) / tombol Previous = balik ke video sebelumnya (buat relabel)
- Video di-embed LANGSUNG di halaman (no need buka tab baru)

Usage:
    python label_app.py
    Buka: http://localhost:5000
"""

from flask import Flask, render_template_string, request, jsonify
import pandas as pd
import traceback
import re
from datetime import datetime
from config import OUTPUT_VIDEOS_TO_LABEL, OUTPUT_LABELED_VIDEOS

app = Flask(__name__)

# ============================================================================
# GLOBAL STATE
# ============================================================================

data = {
    'df': None,
    'current_index': 0,
}

# ============================================================================
# HELPERS
# ============================================================================

def extract_video_id(video_id, video_url):
    """Pastikan dapat video_id numeric murni untuk embed TikTok"""
    vid = str(video_id).strip()
    if vid.isdigit():
        return vid
    match = re.search(r'/video/(\d+)', str(video_url))
    return match.group(1) if match else vid

def count_labeled():
    """Hitung total video yang sudah punya label (dihitung dinamis)"""
    if data['df'] is None:
        return 0
    return int((data['df']['manual_label'] != '').sum())

def find_first_unlabeled_index(df):
    unlabeled = df[df['manual_label'] == '']
    return unlabeled.index[0] if len(unlabeled) > 0 else len(df)

# ============================================================================
# INITIALIZATION
# ============================================================================

def load_data():
    """Load CSV dan initialize state.
    Prioritas: kalau labeled_videos.csv sudah ada (ada progress sebelumnya),
    load itu dan lanjutkan dari situ. Kalau belum ada, mulai dari
    videos_to_label.csv (fresh/belum ada yang dilabel)."""
    try:
        if OUTPUT_LABELED_VIDEOS.exists():
            source_file = OUTPUT_LABELED_VIDEOS
            print(f"📂 Ditemukan progress sebelumnya, resume dari: {source_file}")
        else:
            source_file = OUTPUT_VIDEOS_TO_LABEL
            print(f"📂 Belum ada progress, mulai baru dari: {source_file}")

        df = pd.read_csv(source_file, dtype={'video_id': str})
        df['manual_label'] = df['manual_label'].fillna('').astype(str)
        if 'label_timestamp' not in df.columns:
            df['label_timestamp'] = ''
        df['label_timestamp'] = df['label_timestamp'].fillna('').astype(str)
        df['description'] = df['description'].fillna('').astype(str)

        data['df'] = df
        data['current_index'] = find_first_unlabeled_index(df)
        return True
    except Exception as e:
        print(f"❌ Error loading file: {e}")
        print(f"   Expected file: {OUTPUT_VIDEOS_TO_LABEL}")
        traceback.print_exc()
        return False

# ============================================================================
# ROUTES
# ============================================================================

@app.route('/')
def index():
    """Main page"""
    try:
        if data['df'] is None:
            return "Error: File not loaded. Make sure videos_to_label.csv exists."

        df = data['df']
        total = len(df)
        labeled = count_labeled()
        progress = (labeled / total * 100) if total > 0 else 0

        if data['current_index'] >= total:
            return render_template_string(DONE_HTML, total=total)

        current_video = df.iloc[data['current_index']]
        video_id = extract_video_id(current_video['video_id'], current_video['video_url'])
        existing_label = current_video['manual_label']  # '' / 'health' / 'not_health'

        return render_template_string(
            MAIN_HTML,
            index=data['current_index'] + 1,
            total=total,
            progress=progress,
            progress_bar=f"{labeled}/{total}",
            url=current_video['video_url'],
            video_id=video_id,
            username=current_video['username'],
            description=str(current_video['description']),
            existing_label=existing_label,
            has_prev=(data['current_index'] > 0)
        )
    except Exception as e:
        traceback.print_exc()
        return f"Error rendering page: {e}", 500

@app.route('/label', methods=['POST'])
def label_video():
    """Simpan label untuk video saat ini, lalu maju ke video berikutnya"""
    try:
        if data['df'] is None:
            return jsonify({'error': 'No data loaded'}), 400

        payload = request.get_json(silent=True)
        if not payload:
            return jsonify({'error': 'No JSON body received'}), 400

        label = payload.get('label')
        if label not in ['health', 'not_health']:
            return jsonify({'error': f'Invalid label: {label}'}), 400

        df = data['df']
        if data['current_index'] >= len(df):
            return jsonify({'error': 'All videos labeled'}), 400

        # Save label di posisi saat ini (relabel kalau sebelumnya sudah dilabel)
        df.loc[data['current_index'], 'manual_label'] = label
        df.loc[data['current_index'], 'label_timestamp'] = datetime.now().isoformat()
        df.to_csv(OUTPUT_LABELED_VIDEOS, index=False)

        # Maju satu video
        data['current_index'] += 1

        if data['current_index'] >= len(df):
            return jsonify({
                'done': True,
                'message': f'✅ All {len(df)} videos labeled!',
                'file': str(OUTPUT_LABELED_VIDEOS)
            })

        return jsonify({'success': True})

    except Exception as e:
        print("\n❌ ERROR in /label route:")
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

@app.route('/previous', methods=['POST'])
def previous_video():
    """Balik ke video sebelumnya (buat relabel kalau salah pencet)"""
    try:
        if data['df'] is None:
            return jsonify({'error': 'No data loaded'}), 400

        if data['current_index'] <= 0:
            return jsonify({'error': 'Sudah di video paling awal'}), 400

        data['current_index'] -= 1
        return jsonify({'success': True})

    except Exception as e:
        print("\n❌ ERROR in /previous route:")
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

# ============================================================================
# HTML TEMPLATES
# ============================================================================

MAIN_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>Video Labeling</title>
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

        .top-bar {
            display: flex;
            align-items: center;
            gap: 14px;
            margin-bottom: 18px;
        }

        .progress { flex: 1; }

        .progress-bar {
            width: 100%;
            height: 8px;
            background: #e0e0e0;
            border-radius: 10px;
            overflow: hidden;
            margin-bottom: 8px;
        }

        .progress-fill {
            height: 100%;
            background: linear-gradient(90deg, #667eea 0%, #764ba2 100%);
            width: {{ progress }}%;
            transition: width 0.3s ease;
        }

        .progress-text { font-size: 14px; color: #666; font-weight: 500; }

        .btn-prev {
            flex-shrink: 0;
            background: #f0f0f5;
            color: #555;
            border: none;
            border-radius: 8px;
            padding: 10px 16px;
            font-size: 14px;
            font-weight: 600;
            cursor: pointer;
            display: flex;
            align-items: center;
            gap: 6px;
            transition: all 0.2s ease;
        }
        .btn-prev:hover:not(:disabled) { background: #e2e2ea; transform: translateY(-1px); }
        .btn-prev:disabled { opacity: 0.35; cursor: not-allowed; }

        .main-layout {
            display: flex;
            gap: 28px;
            align-items: flex-start;
        }

        .left-col {
            flex-shrink: 0;
            width: 340px;
        }

        .right-col {
            flex: 1;
            min-width: 260px;
            display: flex;
            flex-direction: column;
        }

        .username {
            font-size: 19px;
            color: #333;
            margin-bottom: 10px;
            font-weight: 700;
        }

        .video-embed-wrapper {
            width: 100%;
            height: 605px;
            overflow: hidden;
            border-radius: 12px;
            background: #000;
            position: relative;
        }

        .video-embed-wrapper iframe {
            border: none;
            width: 325px;
            height: 738px;
            transform: scale(1.05);
            transform-origin: top left;
            position: absolute;
            top: 0;
            left: 50%;
            margin-left: -170px;
        }

        .fallback-link {
            display: block;
            text-align: center;
            font-size: 13px;
            color: #667eea;
            margin-top: 10px;
            text-decoration: none;
        }

        .already-labeled {
            font-size: 13px;
            font-weight: 600;
            padding: 8px 12px;
            border-radius: 6px;
            margin-bottom: 12px;
            display: inline-block;
        }
        .already-labeled.health { background: #e6f9ea; color: #2f9e44; }
        .already-labeled.not_health { background: #ffeaea; color: #e03131; }

        .description {
            font-size: 17px;
            color: #333;
            background: #f9f9f9;
            padding: 18px;
            border-radius: 10px;
            line-height: 1.7;
            margin-bottom: 20px;
            max-height: 320px;
            overflow-y: auto;
            white-space: pre-wrap;
            word-break: break-word;
            flex: 1;
        }

        .controls { display: flex; flex-direction: column; gap: 12px; }

        .btn {
            padding: 18px 16px;
            border: none;
            border-radius: 10px;
            font-size: 17px;
            font-weight: 700;
            cursor: pointer;
            transition: all 0.2s ease;
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 10px;
        }

        .btn-no { background: #ff6b6b; color: white; }
        .btn-no:hover { background: #ff5252; transform: translateY(-2px); }

        .btn-yes { background: #51cf66; color: white; }
        .btn-yes:hover { background: #40c057; transform: translateY(-2px); }

        .hint {
            margin-top: 16px;
            font-size: 13px;
            color: #999;
            text-align: center;
        }

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
                <div class="progress-text">{{ progress_bar }} labeled</div>
            </div>
        </div>

        <div class="main-layout">
            <div class="left-col">
                <div class="username">@{{ username }}</div>
                <div class="video-embed-wrapper">
                    <iframe
                        src="https://www.tiktok.com/embed/v2/{{ video_id }}"
                        allow="encrypted-media;"
                        allowfullscreen
                        tabindex="-1">
                    </iframe>
                </div>
                <a href="{{ url }}" target="_blank" class="fallback-link">
                    Video gak muncul? Buka di tab baru →
                </a>
            </div>

            <div class="right-col">
                {% if existing_label == 'health' %}
                <div class="already-labeled health">✓ Sudah dilabel: HEALTH</div>
                {% elif existing_label == 'not_health' %}
                <div class="already-labeled not_health">✓ Sudah dilabel: NOT HEALTH</div>
                {% endif %}

                <div class="description">{{ description }}</div>

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
                if (state) b.classList.add('loading');
                else b.classList.remove('loading');
            });
        }

        function labelVideo(label) {
            setLoading(true);
            fetch('/label', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ label: label })
            })
            .then(r => r.json().catch(() => {
                throw new Error('Server returned non-JSON response (kemungkinan error 500). Cek terminal Flask.');
            }))
            .then(data => {
                if (data.error) {
                    alert('Error: ' + data.error);
                    setLoading(false);
                    return;
                }
                if (data.done) {
                    document.body.innerHTML = '<div class="container" style="text-align: center; margin-top: 50px; max-width:500px;"><h1 style="color: #51cf66; font-size: 48px;">✅</h1><h2>' + data.message + '</h2><p style="color: #666; margin-top: 20px;">File saved: ' + data.file + '</p></div>';
                } else if (data.success) {
                    location.reload();
                }
            })
            .catch(e => {
                alert('Error: ' + e.message);
                setLoading(false);
            });
        }

        function goPrevious() {
            setLoading(true);
            fetch('/previous', { method: 'POST' })
            .then(r => r.json().catch(() => {
                throw new Error('Server returned non-JSON response. Cek terminal Flask.');
            }))
            .then(data => {
                if (data.error) {
                    setLoading(false);
                    return; // sudah di video paling awal, diam saja
                }
                if (data.success) location.reload();
            })
            .catch(e => {
                alert('Error: ' + e.message);
                setLoading(false);
            });
        }

        document.addEventListener('keydown', (e) => {
            if (e.key === 'ArrowLeft') {
                labelVideo('not_health');
            } else if (e.key === 'ArrowRight') {
                labelVideo('health');
            } else if (e.key === 'ArrowUp') {
                e.preventDefault();
                goPrevious();
            }
        });

        // FIX: klik/scroll di dalam iframe TikTok memindahkan keyboard focus
        // ke iframe (beda origin), jadi arrow key berhenti kedeteksi di halaman ini.
        // Trik: begitu window kehilangan fokus (karena iframe), langsung
        // balikin fokus ke halaman utama supaya arrow key tetap jalan.
        window.addEventListener('blur', () => {
            setTimeout(() => window.focus(), 50);
        });
    </script>
</body>
</html>
"""

DONE_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>Done!</title>
    <style>
        * { margin: 0; padding: 0; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            display: flex;
            justify-content: center;
            align-items: center;
            padding: 20px;
        }
        .container {
            background: white;
            border-radius: 12px;
            box-shadow: 0 20px 60px rgba(0,0,0,0.3);
            max-width: 500px;
            width: 100%;
            padding: 50px;
            text-align: center;
        }
        h1 { font-size: 72px; margin-bottom: 20px; }
        h2 { font-size: 28px; color: #333; margin-bottom: 15px; }
        p { font-size: 16px; color: #666; line-height: 1.6; }
        .next {
            margin-top: 30px;
            padding: 15px;
            background: #f9f9f9;
            border-radius: 8px;
            font-size: 14px;
            color: #666;
        }
        code {
            background: #f0f0f0;
            padding: 3px 8px;
            border-radius: 4px;
            font-family: monospace;
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>✅</h1>
        <h2>All {{ total }} videos labeled!</h2>
        <p>Great job! Your labels have been saved.</p>
        <div class="next">
            <strong>Next step:</strong><br>
            Run <code>python validate_accuracy.py</code> to check model accuracy
        </div>
    </div>
</body>
</html>
"""

# ============================================================================
# RUN
# ============================================================================

if __name__ == '__main__':
    print("\n" + "="*70)
    print("LABELING APP - ARROW KEYS (inline video embed + previous button)")
    print("="*70)

    if not load_data():
        print("❌ Failed to load data")
        exit(1)

    total = len(data['df'])
    labeled = count_labeled()

    print(f"\n✅ Total videos: {total}")
    print(f"Already labeled: {labeled}")
    print(f"Remaining: {total - labeled}")

    print(f"\n🌐 Starting Flask server...")
    print(f"Open browser: http://localhost:5000")
    print(f"\n⌨️  Controls:")
    print(f"  ← (left arrow)  = NOT HEALTH")
    print(f"  → (right arrow) = HEALTH")
    print(f"  ↑ (up arrow)    = Previous (relabel)")
    print(f"  Or click buttons\n")

    app.run(debug=True, host='localhost', port=5000)