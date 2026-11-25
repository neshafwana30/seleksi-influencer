#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
REVIEW KANDIDAT NEGATIF - TA NESHA
================================================================================

Aplikasi Flask buat review manual kandidat komentar negatif hasil pipeline
Layer 1 (semantic search bge-m3) + Layer 2 (LLM judge qwen2.5:7b).

Kamu lihat komentar + jawaban LLM judge (judge_label, judge_alasan, skor
kemiripan) sekaligus, lalu putuskan: NEGATIF (terima) / BUKAN NEGATIF (tolak) /
RAGU (lewati, revisit nanti).

FITUR:
  - 1 labeler aja (kamu), nggak ada halaman pilih nama
  - Auto-save tiap klik, SELALU bikin backup .bak dulu sebelum nulis ulang CSV
  - Resume otomatis (baca review_manual.csv kalau sudah ada, lanjut dari situ)
  - Keyboard shortcuts: ← NEGATIF | → BUKAN NEGATIF | ↓ RAGU/skip | ↑ Previous
  - Video TikTok embed (lazy-load, toggle)

CARA JALANIN:
  python label_kandidat_negatif.py
  lalu buka http://localhost:5050

INPUT  : 01_pengumpulan_data/06_data_negatif_tambahan/hasil/kandidat_final_untuk_review.csv
OUTPUT : 01_pengumpulan_data/06_data_negatif_tambahan/hasil/review_manual.csv
================================================================================
"""

import shutil
import sys
from pathlib import Path

import pandas as pd
from flask import Flask, render_template_string, request, jsonify

# ============================================================================
# CONFIGURATION
# ============================================================================

def find_hasil_dir():
    """Auto-detect folder 01_pengumpulan_data/06_data_negatif_tambahan/hasil."""
    current = Path(__file__).resolve().parent
    for _ in range(6):
        candidate = current / "01_pengumpulan_data" / "06_data_negatif_tambahan" / "hasil"
        if candidate.is_dir():
            return candidate
        if current.name == "06_data_negatif_tambahan":
            return current / "hasil"
        if current.name == "hasil" and current.parent.name == "06_data_negatif_tambahan":
            return current
        if current.parent == current:
            break
        current = current.parent
    return None

HASIL_DIR = find_hasil_dir()
if not HASIL_DIR:
    # fallback: asumsikan script ini ditaruh di folder 06_data_negatif_tambahan
    HASIL_DIR = Path(__file__).resolve().parent / "hasil"

MASTER_FILE = HASIL_DIR / "kandidat_final_untuk_review.csv"
OUTPUT_FILE = HASIL_DIR / "review_manual.csv"

# REPO_ROOT: .../hasil -> 06_data_negatif_tambahan -> 01_pengumpulan_data -> root repo
REPO_ROOT = HASIL_DIR.parent.parent.parent
BASELINE_LABELED_PATH = REPO_ROOT / "02_IndoBert" / "data" / "siap_latih" / "binary" / "dataset_final.csv"

COL_LABEL = "label_manual"      # 'negatif' / 'bukan_negatif' / '' (belum)
COL_NOTE = "catatan"

LABEL_NEGATIF = "negatif"
LABEL_BUKAN_NEGATIF = "bukan_negatif"
LABEL_CHOICES = [LABEL_NEGATIF, LABEL_BUKAN_NEGATIF]  # RAGU/skip tidak nulis label, cuma lewat

TARGET_NEGATIF = 500   # perkiraan target total komentar negatif biar model bisa belajar (ganti sesuka kamu)

PORT = 5050

# ============================================================================
# FLASK APP
# ============================================================================

app = Flask(__name__)
app.config['JSON_AS_ASCII'] = False

state = {
    'df': None,
    'queue': None,   # list of df index, urutan review (belum termasuk yang sudah di-skip permanen)
    'pos': 0,
    'baseline_negatif': None,  # jumlah negatif yang SUDAH ada di dataset_final.csv (hasil labeling awal)
}

# ============================================================================
# TARGET KOMENTAR NEGATIF
# ============================================================================

def get_baseline_negatif():
    """Hitung berapa komentar negatif yang SUDAH ada dari labeling awal (dataset_final.csv).
    Dihitung sekali, di-cache. Kalau file tidak ketemu/kolom nggak cocok, baseline = 0
    (dianggap 'tidak diketahui', target cuma ngitung dari hasil review ini)."""
    if state['baseline_negatif'] is not None:
        return state['baseline_negatif']

    baseline = 0
    try:
        if BASELINE_LABELED_PATH.exists():
            base_df = pd.read_csv(BASELINE_LABELED_PATH, dtype=str, keep_default_na=False, encoding='utf-8')
            col = None
            for candidate_col in ('label_name', 'label_final', 'label'):
                if candidate_col in base_df.columns:
                    col = candidate_col
                    break
            if col:
                baseline = int((base_df[col].astype(str).str.strip().str.lower() == 'negatif').sum())
                print(f"  📊 Baseline negatif dari {BASELINE_LABELED_PATH.name} (kolom '{col}'): {baseline}")
            else:
                print(f"  ⚠️  {BASELINE_LABELED_PATH.name} ditemukan tapi tidak ada kolom label yang dikenali "
                      f"-> baseline dianggap 0 (target cuma hitung dari review ini)")
        else:
            print(f"  ⚠️  {BASELINE_LABELED_PATH.name} tidak ditemukan -> baseline dianggap 0 "
                  f"(target cuma hitung dari review ini)")
    except Exception as e:
        print(f"  ⚠️  Gagal baca {BASELINE_LABELED_PATH.name} ({e}) -> baseline dianggap 0")

    state['baseline_negatif'] = baseline
    return baseline

def get_target_progress(df):
    """Hitung progres target: baseline (dataset awal) + negatif baru yang diterima lewat review ini."""
    baseline = get_baseline_negatif()
    new_negatif = int((df[COL_LABEL] == LABEL_NEGATIF).sum())
    total = baseline + new_negatif
    pct = min(100.0, (total / TARGET_NEGATIF * 100.0)) if TARGET_NEGATIF else 0.0
    return {
        'baseline': baseline,
        'new': new_negatif,
        'total': total,
        'target': TARGET_NEGATIF,
        'pct': pct,
    }

# ============================================================================
# DATA LOADING & SAVING (append/update only, backup sebelum nulis ulang)
# ============================================================================

def backup_before_write(path: Path):
    """Backup isi file lama ke SATU file .bak (ditimpa tiap kali, bukan numpuk per-timestamp)
    sebelum file aslinya ditulis ulang. Jadi selalu ada 1 salinan versi sebelumnya buat jaga-jaga."""
    if path.exists():
        bak_path = path.with_name(f'{path.name}.bak')
        try:
            shutil.copy2(path, bak_path)
        except Exception as e:
            print(f'  ⚠️  Gagal bikin backup {bak_path.name}: {e}')

def load_df():
    """Load dataframe (resume dari review_manual.csv kalau ada, kalau belum mulai dari master)."""
    if state['df'] is not None:
        return state['df']

    if not MASTER_FILE.exists():
        raise FileNotFoundError(
            f"\n{'='*70}\n"
            f"❌ FATAL: File kandidat tidak ditemukan!\n"
            f"   Lokasi expected: {MASTER_FILE}\n"
            f"{'='*70}\n"
        )

    print(f"  📖 Loading master: {MASTER_FILE}")
    master_df = pd.read_csv(MASTER_FILE, dtype=str, keep_default_na=False, encoding='utf-8')

    required_cols = ['comment_id', 'comment_text']
    missing = [c for c in required_cols if c not in master_df.columns]
    if missing:
        raise ValueError(f"❌ Kolom yang hilang di {MASTER_FILE.name}: {missing}")

    if OUTPUT_FILE.exists():
        print(f"  💾 Resume dari: {OUTPUT_FILE}")
        df = pd.read_csv(OUTPUT_FILE, dtype=str, keep_default_na=False, encoding='utf-8')
        # kalau master nambah kandidat baru (misal layer1/layer2 di-run ulang dgn kandidat lebih banyak),
        # gabungkan komentar yang belum ada di review_manual.csv, tanpa menyentuh yang sudah ada
        existing_ids = set(df['comment_id'])
        new_rows = master_df[~master_df['comment_id'].isin(existing_ids)].copy()
        if len(new_rows):
            print(f"  ℹ️  Kandidat baru ditemukan: {len(new_rows)}")
            new_rows[COL_LABEL] = ''
            new_rows[COL_NOTE] = ''
            df = pd.concat([df, new_rows], ignore_index=True)
    else:
        print(f"  ✨ Mulai baru: {OUTPUT_FILE}")
        df = master_df.copy()
        df[COL_LABEL] = ''
        df[COL_NOTE] = ''

    for col, default in [(COL_LABEL, ''), (COL_NOTE, '')]:
        if col not in df.columns:
            df[col] = default
        df[col] = df[col].fillna(default)

    for col in ('username', 'video_id', 'comment_text', 'skor_kemiripan_negatif',
                'judge_label', 'judge_alasan'):
        if col not in df.columns:
            df[col] = ''
        df[col] = df[col].fillna('')

    save_df(df, first_save=True)
    state['df'] = df
    return df

def save_df(df=None, first_save=False):
    df = df if df is not None else state['df']
    if not first_save:
        backup_before_write(OUTPUT_FILE)
    HASIL_DIR.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUTPUT_FILE, index=False, encoding='utf-8')

def get_queue():
    if state['queue'] is None:
        df = load_df()
        state['queue'] = df.index.tolist()
        state['pos'] = 0
        # mulai dari komentar pertama yang belum dilabel (resume)
        for i, idx in enumerate(state['queue']):
            if df.at[idx, COL_LABEL] == '':
                state['pos'] = i
                break
        else:
            state['pos'] = len(state['queue'])
    return state['queue'], state['pos']

# ============================================================================
# HTTP ROUTES
# ============================================================================

@app.route('/')
def index():
    try:
        df = load_df()
        queue, pos = get_queue()
        total = len(queue)
        reviewed = sum(1 for idx in queue if df.at[idx, COL_LABEL] != '')
        tp = get_target_progress(df)

        if pos >= total:
            return render_template_string(DONE_HTML, total=total, tp=tp)

        df_idx = queue[pos]
        row = df.loc[df_idx]

        return render_template_string(
            MAIN_HTML,
            progress=(reviewed / total * 100) if total else 0,
            progress_bar=f"{reviewed}/{total}",
            username=row.get('username', ''),
            video_id=str(row.get('video_id', '')).strip(),
            comment_text=str(row.get('comment_text', '')),
            judge_label=str(row.get('judge_label', '')),
            judge_alasan=str(row.get('judge_alasan', '')),
            skor=row.get('skor_kemiripan_negatif', ''),
            catatan=str(row.get(COL_NOTE, '')),
            label_dipilih=str(row.get(COL_LABEL, '')),
            has_prev=(pos > 0),
            tp=tp,
        )
    except Exception as e:
        import traceback
        return f"""
        <html><head><title>Error</title></head><body style="font-family:monospace;padding:20px;">
        <h2>❌ Error</h2><p>{str(e)}</p><pre>{traceback.format_exc()}</pre>
        </body></html>
        """, 500

@app.route('/label', methods=['POST'])
def label_comment():
    try:
        payload = request.get_json(silent=True) or {}
        label = payload.get('label')
        catatan = str(payload.get('catatan', '')).strip()

        if label not in LABEL_CHOICES:
            return jsonify({'error': f'Label tidak valid: {label}'}), 400

        df = load_df()
        queue, pos = get_queue()
        if pos >= len(queue):
            return jsonify({'error': 'Semua kandidat sudah di-review'}), 400

        df_idx = queue[pos]
        df.at[df_idx, COL_LABEL] = label
        df.at[df_idx, COL_NOTE] = catatan
        save_df(df)

        state['pos'] += 1
        if state['pos'] >= len(queue):
            return jsonify({'done': True, 'message': f'✅ Selesai! {len(queue)} kandidat di-review.'})
        return jsonify({'success': True})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

@app.route('/skip', methods=['POST'])
def skip_comment():
    """Lewati tanpa nulis label (RAGU) - maju ke komentar berikutnya, bisa di-review lagi nanti
    dengan cara jalanin ulang (posisi RAGU tetap kosong -> kequeue lagi kalau kembali ke awal)."""
    try:
        queue, pos = get_queue()
        if pos >= len(queue):
            return jsonify({'error': 'Sudah di akhir'}), 400
        state['pos'] += 1
        if state['pos'] >= len(queue):
            return jsonify({'done': True, 'message': 'Sudah sampai akhir daftar (masih ada yang di-skip).'})
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/previous', methods=['POST'])
def previous_comment():
    try:
        queue, pos = get_queue()
        if pos <= 0:
            return jsonify({'error': 'Sudah di awal'}), 400
        state['pos'] -= 1
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/note', methods=['POST'])
def save_note_only():
    """Simpan catatan tanpa ubah label (dipakai saat ngetik di textarea sebelum pilih label)."""
    try:
        payload = request.get_json(silent=True) or {}
        catatan = str(payload.get('catatan', '')).strip()
        df = load_df()
        queue, pos = get_queue()
        if pos >= len(queue):
            return jsonify({'error': 'Sudah di akhir'}), 400
        df_idx = queue[pos]
        df.at[df_idx, COL_NOTE] = catatan
        save_df(df)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ============================================================================
# HTML TEMPLATE
# ============================================================================

MAIN_HTML = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Review Kandidat Negatif</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            display: flex;
            justify-content: center;
            align-items: flex-start;
            padding: 30px 16px;
        }
        .container {
            background: white;
            border-radius: 16px;
            box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);
            max-width: 560px;
            width: 100%;
            padding: 28px 26px;
            margin-top: 20px;
        }
        .header { display: flex; align-items: center; gap: 12px; margin-bottom: 18px; }
        .btn-prev {
            background: #f0f0f5; color: #555; border: none; border-radius: 10px;
            padding: 10px 14px; font-size: 13px; font-weight: 600; cursor: pointer;
        }
        .btn-prev:hover:not(:disabled) { background: #667eea; color: white; }
        .btn-prev:disabled { opacity: 0.3; cursor: not-allowed; }
        .progress-section { flex: 1; }
        .progress-bar { width: 100%; height: 8px; background: #e0e0e0; border-radius: 10px; overflow: hidden; margin-bottom: 6px; }
        .progress-fill { height: 100%; background: linear-gradient(90deg, #667eea 0%, #764ba2 100%); width: {{ progress }}%; transition: width 0.3s; }
        .progress-text { font-size: 12px; color: #666; font-weight: 600; }

        .target-box {
            border-radius: 12px; padding: 12px 16px; margin-bottom: 18px; border: 2px solid #ff8787;
            background: #fff0f0;
        }
        .target-top { display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 6px; }
        .target-label { font-size: 12px; color: #555; font-weight: 700; }
        .target-count { font-size: 16px; color: #e03131; font-weight: 800; }
        .target-bar { width: 100%; height: 8px; background: #ffd8d8; border-radius: 10px; overflow: hidden; margin-bottom: 4px; }
        .target-fill { height: 100%; background: #e03131; width: {{ tp.pct }}%; transition: width 0.3s; }
        .target-detail { font-size: 11px; color: #a94442; }

        .comment-container { margin-bottom: 18px; }
        .username { font-size: 15px; color: #333; margin-bottom: 10px; font-weight: 700; }
        .comment-text {
            font-size: 15px; color: #333; background: #f9f9f9; padding: 16px; border-radius: 12px;
            line-height: 1.6; white-space: pre-wrap; word-break: break-word; border-left: 4px solid #667eea;
        }

        .judge-box {
            border-radius: 12px; padding: 14px 16px; margin-bottom: 18px; border: 2px solid #ffd43b;
            background: #fff9e6;
        }
        .judge-row { display: flex; justify-content: space-between; font-size: 13px; color: #333; margin-bottom: 4px; }
        .judge-row b { text-transform: uppercase; }
        .judge-alasan { font-size: 12px; color: #777; margin-top: 6px; font-style: italic; }

        .video-box { margin-bottom: 18px; }
        .video-bar { display: flex; justify-content: space-between; align-items: center; gap: 10px; }
        .video-bar a { color: #667eea; text-decoration: none; font-weight: 600; font-size: 12px; }
        .btn-video { background: #f0f0f5; color: #555; border: none; border-radius: 10px; padding: 9px 14px; font-size: 13px; font-weight: 600; cursor: pointer; }
        .btn-video:hover { background: #667eea; color: white; }
        .video-wrap { margin-top: 12px; display: flex; justify-content: center; }
        .video-wrap iframe { width: 100%; max-width: 340px; height: 620px; border: 0; border-radius: 12px; background: #000; }

        .catatan {
            width: 100%; padding: 10px 12px; border: 2px solid #e0e0e0; border-radius: 10px;
            font-size: 13px; font-family: inherit; margin-bottom: 14px; resize: vertical;
        }
        .catatan:focus { outline: none; border-color: #667eea; }

        .controls { display: flex; flex-direction: column; gap: 10px; margin-bottom: 14px; }
        .btn-label { padding: 15px; border: none; border-radius: 12px; font-size: 14px; font-weight: 700; color: white; cursor: pointer; transition: all 0.2s; }
        .btn-label:hover:not(:disabled) { transform: translateY(-2px); box-shadow: 0 8px 24px rgba(0,0,0,0.2); }
        .btn-negatif { background: #ff6b6b; }
        .btn-negatif:hover { background: #ff5252; }
        .btn-bukan { background: #51cf66; }
        .btn-bukan:hover { background: #40c057; }
        .btn-ragu { background: #868e96; }
        .btn-ragu:hover { background: #74797f; }

        .hints { font-size: 12px; color: #666; background: #f0f0f5; padding: 12px; border-radius: 10px; text-align: center; }
        .loading { opacity: 0.5; pointer-events: none; }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <button class="btn-prev" id="prevBtn" onclick="goPrevious()" {{ 'disabled' if not has_prev else '' }}>↑ Previous</button>
            <div class="progress-section">
                <div class="progress-bar"><div class="progress-fill"></div></div>
                <div class="progress-text">{{ progress_bar }} di-review</div>
            </div>
        </div>

        <div class="target-box">
            <div class="target-top">
                <span class="target-label">🎯 Target komentar negatif</span>
                <span class="target-count">{{ tp.total }} / {{ tp.target }}</span>
            </div>
            <div class="target-bar"><div class="target-fill"></div></div>
            <div class="target-detail">{{ tp.baseline }} dari dataset awal + {{ tp.new }} baru diterima dari review ini</div>
        </div>

        <div class="comment-container">
            <div class="username">@{{ username }}</div>
            <div class="comment-text">{{ comment_text }}</div>
        </div>

        <div class="judge-box">
            <div class="judge-row"><span>Jawaban LLM judge</span><b>{{ judge_label }}</b></div>
            <div class="judge-row"><span>Skor kemiripan negatif</span><b>{{ skor }}</b></div>
            {% if judge_alasan %}<div class="judge-alasan">"{{ judge_alasan }}"</div>{% endif %}
        </div>

        {% if video_id and video_id.isdigit() %}
        <div class="video-box">
            <div class="video-bar">
                <button type="button" class="btn-video" id="videoToggle" onclick="toggleVideo()">▶ Tampilkan video</button>
                <a href="https://www.tiktok.com/@{{ username }}/video/{{ video_id }}" target="_blank" rel="noopener">buka di TikTok →</a>
            </div>
            <div id="videoWrap" class="video-wrap" style="display:none"></div>
        </div>
        {% endif %}

        <textarea id="catatan" class="catatan" rows="2" placeholder="Catatan (opsional)">{{ catatan }}</textarea>

        <div class="controls">
            <button class="btn-label btn-negatif" onclick="labelComment('negatif')">← NEGATIF (terima)</button>
            <button class="btn-label btn-bukan" onclick="labelComment('bukan_negatif')">BUKAN NEGATIF (tolak) →</button>
            <button class="btn-label btn-ragu" onclick="skipComment()">↓ RAGU (lewati)</button>
        </div>

        <div class="hints">⌨️ ← Negatif | → Bukan negatif | ↓ Ragu/lewati | ↑ Previous</div>
    </div>

    <script>
        const VIDEO_ID = {{ video_id | tojson }};
        let busy = false;

        function setLoading(state) {
            busy = state;
            document.querySelectorAll('.btn-label, .btn-prev').forEach(b => {
                if (state) b.classList.add('loading'); else b.classList.remove('loading');
            });
        }

        function labelComment(label) {
            if (busy) return;
            setLoading(true);
            fetch('/label', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ label: label, catatan: document.getElementById('catatan').value })
            })
            .then(r => r.json())
            .then(data => {
                if (data.error) { alert('❌ ' + data.error); setLoading(false); return; }
                if (data.done) {
                    document.body.innerHTML = '<div class="container" style="text-align:center;margin-top:100px;max-width:480px;"><div style="font-size:72px;margin-bottom:20px;">✅</div><h2>' + data.message + '</h2><p style="color:#999;margin:14px 0;">review_manual.csv udah disimpen.</p></div>';
                } else { location.reload(); }
            })
            .catch(e => { alert('❌ ' + e.message); setLoading(false); });
        }

        function skipComment() {
            if (busy) return;
            setLoading(true);
            fetch('/skip', { method: 'POST' })
            .then(r => r.json())
            .then(data => {
                if (data.error) { alert('❌ ' + data.error); setLoading(false); return; }
                if (data.done) {
                    document.body.innerHTML = '<div class="container" style="text-align:center;margin-top:100px;max-width:480px;"><div style="font-size:48px;margin-bottom:20px;">🏁</div><h2>' + data.message + '</h2></div>';
                } else { location.reload(); }
            })
            .catch(e => { alert('❌ ' + e.message); setLoading(false); });
        }

        function goPrevious() {
            if (busy) return;
            setLoading(true);
            fetch('/previous', { method: 'POST' })
            .then(r => r.json())
            .then(data => {
                if (data.error) { alert('⚠️ ' + data.error); setLoading(false); }
                else if (data.success) { location.reload(); }
            })
            .catch(e => { alert('❌ ' + e.message); setLoading(false); });
        }

        function showVideo(show) {
            const wrap = document.getElementById('videoWrap');
            const btn = document.getElementById('videoToggle');
            if (!wrap || !btn) return;
            if (show && !wrap.firstChild) {
                const f = document.createElement('iframe');
                f.src = 'https://www.tiktok.com/embed/v2/' + encodeURIComponent(VIDEO_ID);
                f.allow = 'encrypted-media; fullscreen';
                f.allowFullscreen = true;
                f.loading = 'lazy';
                wrap.appendChild(f);
            }
            wrap.style.display = show ? 'flex' : 'none';
            btn.textContent = show ? '⏸ Sembunyikan video' : '▶ Tampilkan video';
        }
        function toggleVideo() {
            const wrap = document.getElementById('videoWrap');
            showVideo(wrap.style.display === 'none');
            document.getElementById('videoToggle').blur();
        }

        document.addEventListener('keydown', (e) => {
            if (e.target && (e.target.tagName === 'TEXTAREA' || e.target.tagName === 'INPUT')) return;
            if (e.key === 'ArrowLeft') { e.preventDefault(); labelComment('negatif'); }
            else if (e.key === 'ArrowRight') { e.preventDefault(); labelComment('bukan_negatif'); }
            else if (e.key === 'ArrowDown') { e.preventDefault(); skipComment(); }
            else if (e.key === 'ArrowUp') { e.preventDefault(); goPrevious(); }
        });
    </script>
</body>
</html>
"""

DONE_HTML = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Selesai!</title>
    <style>
        * { margin: 0; padding: 0; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh; display: flex; justify-content: center; align-items: center; padding: 20px;
        }
        .container {
            background: white; border-radius: 16px; box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);
            max-width: 480px; width: 100%; padding: 60px 40px; text-align: center;
        }
        h1 { font-size: 80px; margin-bottom: 20px; }
        h2 { font-size: 20px; color: #333; margin-bottom: 12px; font-weight: 700; }
        p { color: #666; margin-bottom: 12px; font-size: 14px; }
    </style>
</head>
<body>
    <div class="container">
        <h1>✅</h1>
        <h2>Semua kandidat sudah di-review!</h2>
        <p>{{ total }} kandidat diproses, tersimpan di review_manual.csv</p>
        <p style="color:#e03131;font-weight:700;">🎯 Total negatif sekarang: {{ tp.total }} / {{ tp.target }}
        ({{ tp.baseline }} dataset awal + {{ tp.new }} dari review ini)</p>
    </div>
</body>
</html>
"""

# ============================================================================
# MAIN
# ============================================================================

def validate_setup():
    print("\n📋 Validation:")
    print(f"  ✓ Hasil dir: {HASIL_DIR}")
    if not MASTER_FILE.exists():
        print(f"\n  ❌ FATAL: {MASTER_FILE.name} tidak ditemukan di {HASIL_DIR}")
        return False
    print(f"  ✓ Master file ditemukan: {MASTER_FILE.name}")
    return True

if __name__ == '__main__':
    print("\n" + "=" * 80)
    print("🏷️  REVIEW KANDIDAT NEGATIF - TA NESHA".center(80))
    print("=" * 80)

    if not validate_setup():
        sys.exit(1)

    print(f"\n🌐 Buka: http://localhost:{PORT}")
    print("⌨️  ← Negatif | → Bukan negatif | ↓ Ragu/lewati | ↑ Previous")
    print("⏹️  Stop: CTRL+C\n")

    app.run(debug=False, host='0.0.0.0', port=PORT, use_reloader=False)