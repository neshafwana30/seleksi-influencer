"""
MOBILE LABELING APP - SWIPE GESTURE + NGROK TUNNEL (FIXED)
===========================================================
Mobile-friendly labeling dengan:
- Swipe LEFT (←) = NOT HEALTH
- Swipe RIGHT (→) = HEALTH
- Scroll/Swipe UP (↑) = Previous
- Tombol FLOATING di atas (selalu klik-able)
- Save otomatis ke labeled_videos.csv
- Support ngrok tunnel untuk beda WiFi

File logic:
1. Load labeled_videos.csv (kalo ada, ini hasil labeling + resume)
2. Fallback ke videos_to_label.csv (kalo gak ada labeled yet)
3. Save hasil ke labeled_videos.csv

Usage:
    python label_app_mobile.py              # Same WiFi
    python label_app_mobile.py --tunnel     # Beda WiFi (ngrok)
"""

from flask import Flask, render_template_string, request, jsonify
import pandas as pd
import traceback
import re
from datetime import datetime
from config import OUTPUT_VIDEOS_TO_LABEL, OUTPUT_LABELED_VIDEOS
import socket
import sys

app = Flask(__name__)

# ============================================================================
# GLOBAL STATE
# ============================================================================

data = {
    'df': None,
    'current_index': 0,
    'source_file': None,
}

# ============================================================================
# HELPERS
# ============================================================================

def get_local_ip():
    """Get local network IP"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except:
        return "localhost"

def setup_ngrok():
    """Setup ngrok tunnel"""
    try:
        from pyngrok import ngrok
        
        try:
            tunnel = ngrok.connect(5000)
            public_url = tunnel.public_url
            print(f"\n{'='*70}")
            print(f"🌐 NGROK TUNNEL ACTIVE!")
            print(f"{'='*70}")
            print(f"\n📱 Public URL: {public_url}")
            print(f"\n   Copy & paste ke HP (WiFi beda juga bisa):")
            print(f"   {public_url}")
            print(f"\n{'='*70}\n")
            return True
        except Exception as e:
            print(f"\n⚠️  Error setting up ngrok: {e}")
            print(f"   Fallback ke local network")
            return False
    except ImportError:
        print(f"\n⚠️  pyngrok not installed!")
        print(f"   Install: pip install pyngrok")
        print(f"   Then run: python label_app_mobile.py --tunnel")
        return False

def extract_video_id(video_id, video_url):
    """Pastikan dapat video_id numeric murni untuk embed TikTok"""
    vid = str(video_id).strip()
    if vid.isdigit():
        return vid
    match = re.search(r'/video/(\d+)', str(video_url))
    return match.group(1) if match else vid

def count_labeled():
    """Hitung total video yang sudah punya label"""
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
    """
    Load data dengan prioritas:
    1. labeled_videos.csv (hasil labeling, untuk resume)
    2. videos_to_label.csv (sample baru, fresh start)
    """
    try:
        # PRIORITY 1: Load labeled_videos.csv (result + resume)
        if OUTPUT_LABELED_VIDEOS.exists():
            source_file = OUTPUT_LABELED_VIDEOS
            print(f"📂 Ditemukan progress labeling, resume dari: {source_file}")
        else:
            # PRIORITY 2: Load videos_to_label.csv (fresh)
            source_file = OUTPUT_VIDEOS_TO_LABEL
            print(f"📂 Fresh start dari: {source_file}")

        df = pd.read_csv(source_file, dtype={'video_id': str})
        
        # Normalize columns
        df['manual_label'] = df['manual_label'].fillna('').astype(str)
        if 'label_timestamp' not in df.columns:
            df['label_timestamp'] = ''
        df['label_timestamp'] = df['label_timestamp'].fillna('').astype(str)
        df['description'] = df['description'].fillna('').astype(str)

        data['df'] = df
        data['source_file'] = str(source_file)
        data['current_index'] = find_first_unlabeled_index(df)
        return True
    except FileNotFoundError:
        print(f"❌ File not found!")
        print(f"   {OUTPUT_LABELED_VIDEOS}")
        print(f"   {OUTPUT_VIDEOS_TO_LABEL}")
        return False
    except Exception as e:
        print(f"❌ Error loading file: {e}")
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
            return "Error: File not loaded."

        df = data['df']
        total = len(df)
        labeled = count_labeled()
        progress = (labeled / total * 100) if total > 0 else 0

        if data['current_index'] >= total:
            return render_template_string(DONE_HTML, total=total)

        current_video = df.iloc[data['current_index']]
        video_id = extract_video_id(current_video['video_id'], current_video['video_url'])
        existing_label = current_video['manual_label']

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
        return f"Error: {e}", 500

@app.route('/label', methods=['POST'])
def label_video():
    """Simpan label dan lanjut ke next"""
    try:
        if data['df'] is None:
            return jsonify({'error': 'No data loaded'}), 400

        payload = request.get_json(silent=True)
        if not payload:
            return jsonify({'error': 'No JSON body'}), 400

        label = payload.get('label')
        if label not in ['health', 'not_health']:
            return jsonify({'error': f'Invalid label: {label}'}), 400

        df = data['df']
        if data['current_index'] >= len(df):
            return jsonify({'error': 'All labeled'}), 400

        # Save label
        df.loc[data['current_index'], 'manual_label'] = label
        df.loc[data['current_index'], 'label_timestamp'] = datetime.now().isoformat()
        
        # ALWAYS save to labeled_videos.csv (regardless of source)
        df.to_csv(OUTPUT_LABELED_VIDEOS, index=False)

        # Next
        data['current_index'] += 1

        if data['current_index'] >= len(df):
            return jsonify({
                'done': True,
                'message': f'✅ Semua {len(df)} videos sudah dilabel!',
                'file': str(OUTPUT_LABELED_VIDEOS)
            })

        return jsonify({'success': True})

    except Exception as e:
        print("\n❌ ERROR in /label:")
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

@app.route('/previous', methods=['POST'])
def previous_video():
    """Balik ke video sebelumnya"""
    try:
        if data['df'] is None:
            return jsonify({'error': 'No data'}), 400

        if data['current_index'] <= 0:
            return jsonify({'error': 'First video'}), 400

        data['current_index'] -= 1
        return jsonify({'success': True})

    except Exception as e:
        print("\n❌ ERROR in /previous:")
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

# ============================================================================
# HTML TEMPLATES - MOBILE OPTIMIZED (FIXED)
# ============================================================================

MAIN_HTML = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover, user-scalable=no">
    <title>Label Video</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        
        html, body { 
            width: 100%;
            height: 100%;
            overflow: hidden;
        }
        
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
            background: #f5f5f5;
            touch-action: manipulation;
            user-select: none;
            -webkit-user-select: none;
        }

        .container {
            width: 100%;
            height: 100vh;
            display: flex;
            flex-direction: column;
            background: white;
            overflow: hidden;
        }

        /* TOP BAR - STICKY */
        .top-bar {
            flex-shrink: 0;
            padding: 12px 16px;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            border-bottom: 1px solid #5a5a8a;
            z-index: 10;
        }

        .progress-text {
            font-size: 14px;
            font-weight: 600;
            margin-bottom: 6px;
            opacity: 0.9;
        }

        .progress-bar {
            width: 100%;
            height: 6px;
            background: rgba(255,255,255,0.3);
            border-radius: 3px;
            overflow: hidden;
        }

        .progress-fill {
            height: 100%;
            background: rgba(255,255,255,0.9);
            width: {{ progress }}%;
            transition: width 0.3s ease;
        }

        /* MAIN CONTENT */
        .main-content {
            flex: 1;
            overflow-y: auto;
            overflow-x: hidden;
            padding: 16px;
            display: flex;
            flex-direction: column;
            gap: 12px;
            scroll-behavior: smooth;
            -webkit-overflow-scrolling: touch;
        }

        .video-section {
            flex-shrink: 0;
        }

        .username {
            font-size: 18px;
            font-weight: 700;
            color: #333;
            margin-bottom: 8px;
        }

        .video-embed-wrapper {
            width: 100%;
            aspect-ratio: 9 / 16;
            background: #000;
            border-radius: 12px;
            overflow: hidden;
            position: relative;
            margin-bottom: 12px;
            box-shadow: 0 4px 12px rgba(0,0,0,0.15);
            pointer-events: none;
        }

        .video-embed-wrapper iframe {
            border: none;
            width: 100%;
            height: 100%;
            position: absolute;
            top: 0;
            left: 0;
            pointer-events: auto;
        }

        .fallback-link {
            display: block;
            text-align: center;
            font-size: 12px;
            color: #667eea;
            text-decoration: none;
            padding: 8px 0;
        }

        .already-labeled {
            font-size: 12px;
            font-weight: 600;
            padding: 8px 12px;
            border-radius: 6px;
            display: inline-block;
            margin-bottom: 8px;
        }
        .already-labeled.health { background: #e6f9ea; color: #2f9e44; }
        .already-labeled.not_health { background: #ffeaea; color: #e03131; }

        .description {
            font-size: 15px;
            color: #555;
            background: #f9f9f9;
            padding: 14px;
            border-radius: 8px;
            line-height: 1.6;
            border-left: 3px solid #667eea;
            white-space: pre-wrap;
            word-break: break-word;
        }

        .hint-swipe {
            font-size: 13px;
            color: #999;
            text-align: center;
            padding: 12px 0;
            border-top: 1px solid #eee;
            margin-top: 12px;
            margin-bottom: 60px;
        }

        /* FLOATING BUTTON BAR - ALWAYS ON TOP */
        .floating-controls {
            position: fixed;
            bottom: 0;
            left: 0;
            right: 0;
            padding: 16px;
            background: white;
            border-top: 1px solid #eee;
            display: flex;
            gap: 10px;
            z-index: 100;
            box-shadow: 0 -4px 12px rgba(0,0,0,0.1);
        }

        .btn {
            flex: 1;
            padding: 14px 12px;
            border: none;
            border-radius: 8px;
            font-size: 15px;
            font-weight: 700;
            cursor: pointer;
            transition: all 0.2s ease;
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 8px;
            touch-action: manipulation;
            user-select: none;
            z-index: 101;
        }

        .btn-no { 
            background: #ff6b6b; 
            color: white; 
        }
        .btn-no:active { 
            background: #ff5252; 
            transform: scale(0.98);
        }

        .btn-yes { 
            background: #51cf66; 
            color: white; 
        }
        .btn-yes:active { 
            background: #40c057; 
            transform: scale(0.98);
        }

        .btn-prev {
            background: #f0f0f5;
            color: #555;
            flex: 0 0 60px;
        }
        .btn-prev:active {
            background: #e2e2ea;
        }
        .btn-prev:disabled {
            opacity: 0.3;
            cursor: not-allowed;
        }

        .loading {
            opacity: 0.5;
            pointer-events: none;
        }

        .arrow { font-size: 20px; }

        /* GESTURE INDICATOR */
        .gesture-hint {
            position: fixed;
            bottom: 140px;
            left: 50%;
            transform: translateX(-50%);
            background: rgba(0,0,0,0.7);
            color: white;
            padding: 8px 16px;
            border-radius: 20px;
            font-size: 12px;
            opacity: 0;
            pointer-events: none;
            transition: opacity 0.2s ease;
            z-index: 99;
        }

        .gesture-hint.show { opacity: 1; }

        @media (max-width: 600px) {
            .main-content { padding: 12px; gap: 10px; }
            .floating-controls { padding: 12px; gap: 8px; }
            .btn { font-size: 14px; padding: 12px 10px; }
        }
    </style>
</head>
<body>
    <div class="container">
        <!-- TOP BAR -->
        <div class="top-bar">
            <div class="progress-text">Video {{ index }} / {{ total }}</div>
            <div class="progress-bar"><div class="progress-fill"></div></div>
            <div class="progress-text" style="margin-top: 4px;">{{ progress_bar }} labeled</div>
        </div>

        <!-- MAIN CONTENT (scrollable) -->
        <div class="main-content" id="mainContent">
            <div class="video-section">
                <div class="username">@{{ username }}</div>
                
                <div class="video-embed-wrapper" id="videoWrapper">
                    <iframe
                        src="https://www.tiktok.com/embed/v2/{{ video_id }}"
                        allow="encrypted-media;"
                        allowfullscreen
                        loading="lazy">
                    </iframe>
                </div>
                <a href="{{ url }}" target="_blank" class="fallback-link">
                    Video gak muncul? Buka link →
                </a>
            </div>

            {% if existing_label == 'health' %}
            <div class="already-labeled health">✓ Sudah: HEALTH</div>
            {% elif existing_label == 'not_health' %}
            <div class="already-labeled not_health">✓ Sudah: NOT HEALTH</div>
            {% endif %}

            <div class="description">{{ description }}</div>

            <div class="hint-swipe">
                Swipe left/right atau tekan tombol di bawah
            </div>
        </div>

        <!-- FLOATING BUTTON BAR (ALWAYS CLICKABLE) -->
        <div class="floating-controls" id="floatingControls">
            <button class="btn btn-prev" id="prevBtn" onclick="goPrevious()" {{ 'disabled' if not has_prev else '' }}>
                <span class="arrow">↑</span>
            </button>
            <button class="btn btn-no" id="notHealthBtn" onclick="labelVideo('not_health')">
                <span class="arrow">←</span> NOT
            </button>
            <button class="btn btn-yes" id="healthBtn" onclick="labelVideo('health')">
                HEALTH <span class="arrow">→</span>
            </button>
        </div>
    </div>

    <div class="gesture-hint" id="gestureHint"></div>

    <script>
        // ============================================================
        // TOUCH/SWIPE DETECTION (window level, bukan mainContent)
        // ============================================================
        
        let touchStartX = 0;
        let touchStartY = 0;
        let touchEndX = 0;
        let touchEndY = 0;
        let isVideoTap = false;
        
        const gestureHint = document.getElementById('gestureHint');
        const videoWrapper = document.getElementById('videoWrapper');
        
        // Detect swipe di window level
        document.addEventListener('touchstart', (e) => {
            touchStartX = e.changedTouches[0].screenX;
            touchStartY = e.changedTouches[0].screenY;
            
            // Check kalo tap di video area
            if (e.target.closest('#videoWrapper')) {
                isVideoTap = true;
            }
        }, false);
        
        document.addEventListener('touchend', (e) => {
            touchEndX = e.changedTouches[0].screenX;
            touchEndY = e.changedTouches[0].screenY;
            
            // Hanya process swipe kalo di video area
            if (isVideoTap && Math.abs(touchStartX - touchEndX) > 30) {
                handleSwipe();
            }
            isVideoTap = false;
        }, false);
        
        function handleSwipe() {
            const diffX = touchStartX - touchEndX;
            const diffY = touchStartY - touchEndY;
            const minSwipeDistance = 50;
            
            if (Math.abs(diffX) > Math.abs(diffY)) {
                // HORIZONTAL SWIPE
                if (Math.abs(diffX) > minSwipeDistance) {
                    if (diffX > 0) {
                        // Swipe LEFT → NOT HEALTH
                        showGestureHint('← NOT HEALTH');
                        labelVideo('not_health');
                    } else {
                        // Swipe RIGHT → HEALTH
                        showGestureHint('HEALTH →');
                        labelVideo('health');
                    }
                }
            } else {
                // VERTICAL SWIPE
                if (Math.abs(diffY) > minSwipeDistance) {
                    if (diffY > 0) {
                        // Swipe UP → PREVIOUS
                        showGestureHint('↑ Previous');
                        goPrevious();
                    }
                }
            }
        }
        
        function showGestureHint(text) {
            gestureHint.textContent = text;
            gestureHint.classList.add('show');
            setTimeout(() => gestureHint.classList.remove('show'), 800);
        }
        
        // ============================================================
        // API CALLS
        // ============================================================
        
        function setLoading(state) {
            document.querySelectorAll('.btn').forEach(b => {
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
                throw new Error('Server error');
            }))
            .then(data => {
                if (data.error) {
                    console.error('Error:', data.error);
                    setLoading(false);
                    return;
                }
                if (data.done) {
                    document.body.innerHTML = `
                        <div style="width: 100%; height: 100vh; display: flex; align-items: center; justify-content: center; flex-direction: column; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white;">
                            <div style="font-size: 64px; margin-bottom: 20px;">✅</div>
                            <h1 style="font-size: 28px; margin-bottom: 10px;">Selesai!</h1>
                            <p style="font-size: 16px; opacity: 0.9;">${data.message}</p>
                        </div>
                    `;
                } else if (data.success) {
                    location.reload();
                }
            })
            .catch(e => {
                console.error('Error:', e.message);
                setLoading(false);
            });
        }
        
        function goPrevious() {
            setLoading(true);
            fetch('/previous', { method: 'POST' })
            .then(r => r.json().catch(() => {
                throw new Error('Server error');
            }))
            .then(data => {
                if (data.error) {
                    setLoading(false);
                    return;
                }
                if (data.success) location.reload();
            })
            .catch(e => {
                console.error('Error:', e.message);
                setLoading(false);
            });
        }
    </script>
</body>
</html>
"""

DONE_HTML = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
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
            padding: 40px 30px;
            text-align: center;
        }
        h1 { font-size: 60px; margin-bottom: 15px; }
        h2 { font-size: 24px; color: #333; margin-bottom: 10px; }
        p { font-size: 15px; color: #666; line-height: 1.6; }
        .next {
            margin-top: 25px;
            padding: 15px;
            background: #f9f9f9;
            border-radius: 8px;
            font-size: 13px;
            color: #666;
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>✅</h1>
        <h2>All {{ total }} videos labeled!</h2>
        <p>Semua video udah dilabel!</p>
        <div class="next">
            <strong>Next step:</strong><br>
            Run: <code>python validate_accuracy.py</code>
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
    print("MOBILE LABELING APP - SWIPE GESTURE")
    print("="*70)

    if not load_data():
        print("❌ Failed to load data")
        exit(1)

    total = len(data['df'])
    labeled = count_labeled()
    
    use_tunnel = '--tunnel' in sys.argv
    
    print(f"\n✅ Total videos: {total}")
    print(f"Already labeled: {labeled}")
    print(f"Remaining: {total - labeled}")
    print(f"📂 Source: {data['source_file']}")

    print(f"\n🌐 Starting Flask server...")
    
    if use_tunnel:
        print(f"\n🔗 Setting up ngrok tunnel...")
        if setup_ngrok():
            print(f"✅ Tunnel ready!")
        else:
            print(f"⚠️  Fallback ke local network")
    else:
        local_ip = get_local_ip()
        print(f"\n📱 Akses dari HP (same WiFi):")
        print(f"   http://{local_ip}:5000")
        print(f"\n   🔗 Beda WiFi? Run: python label_app_mobile.py --tunnel")
    
    print(f"\n⌨️  Controls:")
    print(f"  Swipe LEFT ← = NOT HEALTH")
    print(f"  Swipe RIGHT → = HEALTH")
    print(f"  Swipe UP ↑ = Previous")
    print(f"  Atau click tombol\n")

    app.run(debug=False, host='0.0.0.0', port=5000, use_reloader=False)