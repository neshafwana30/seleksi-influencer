#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
LABELING KOMENTAR - TA NESHA (VERSI 2 KELAS: NEGATIF / NON-NEGATIF)
================================================================================

Aplikasi Flask untuk labeling manual komentar TikTok: negatif / non-negatif.

DEFINISI:
  - NEGATIF     : komentar yang menyasar influencer atau kontennya (kritik, tidak suka,
                  tidak nyaman, merasa berlebihan, meragukan kredibilitas, menyebut hoaks).
  - NON-NEGATIF : semua yang lain (pertanyaan lanjutan, cerita pengalaman pribadi,
                  reaksi ke penyakitnya seperti "serem"/"kasihan", keluhan ke RS/pemerintah/obat,
                  dukungan, dll).

FITUR:
  - Dynamic labelers: 1 tetap (Shafwa) + 1 input nama (dengan saved list)
  - Setiap orang file CSV terpisah
  - Auto-save per klik
  - Keyboard shortcuts: ← → ↑
  - Migrasi otomatis file label 3 kelas lama -> 2 kelas (dengan backup)
  - ngrok support (jalanin dengan: python labeling_final.py --tunnel)

CARA JALANIN:
  1. python labeling_final.py              (localhost: http://localhost:5000)
  2. python labeling_final.py --tunnel     (ngrok)

KEYBOARD SHORTCUTS:
  ← (left arrow)  = NEGATIF
  → (right arrow) = NON-NEGATIF
  ↑ (up arrow)    = PREVIOUS

================================================================================
"""

import re
import sys
import json
import shutil
from pathlib import Path
from datetime import datetime

import pandas as pd
from flask import Flask, render_template_string, request, jsonify

# ============================================================================
# CONFIGURATION
# ============================================================================

def find_base_path():
    """Auto-detect folder 02_IndoBert."""
    current = Path(__file__).resolve().parent
    for _ in range(5):
        if (current / "02_IndoBert").is_dir():
            return current / "02_IndoBert"
        if current.name == "02_IndoBert":
            return current
        if current.parent == current:
            break
        current = current.parent
    return None

BASE_PATH = find_base_path()
if not BASE_PATH:
    BASE_PATH = Path(__file__).resolve().parent.parent / "02_IndoBert"

DATA_FILE = BASE_PATH / "01_praprocessing" / "hasil" / "sampel_labeling.csv"
OUTPUT_DIR = BASE_PATH / "labeling"
SAVED_LABELERS_FILE = OUTPUT_DIR / ".labelers.json"

LABELER_DEFAULT = "Shafwa"
LABEL_CHOICES = ["negatif", "non-negatif"]

# Nama kolom di sampel_labeling.csv yang diisi saat labeling
COL_LABEL = "label_manual"
COL_NOTE = "catatan"
COL_LABEL_3K = "label_3kelas"   # label asli 3 kelas (negatif/netral/positif) dari sebelum penggabungan

# --- Jawaban model (IndoBERTweet, keluaran tetap 3 kelas) ---
# Prediksi dihitung dulu lewat prediksi_model.py, disimpan terpisah dari file label.
PREDIKSI_FILE = BASE_PATH / "01_praprocessing" / "hasil" / "prediksi_model.csv"

# Cara memetakan keluaran model 3 kelas ke 2 kelas (KEPUTUSAN MASIH PENDING, tinggal ganti di sini):
#   "A" = argmax 3 kelas dulu, lalu netral & positif digabung jadi non-negatif
#   "B" = P(non-negatif) = p_netral + p_positif, dibandingkan dengan p_negatif
MODEL_MODE = "A"

# Hanya labeler di daftar ini yang melihat jawaban model (SETELAH memilih label).
# Labeler lain tetap buta. Kosongkan daftar ([]) kalau mau semua buta.
MODEL_VISIBLE_FOR = ["Shafwa"]
COL_MODEL_SEEN = "model_dilihat"   # 1 = jawaban model sudah dilihat (label terkunci)
COL_MODEL_LABEL = "model_label"    # jawaban model (2 kelas), ditulis HANYA saat sudah ditampilkan
COL_ALASAN = "alasan_model"        # alasan model dianggap meleset (diisi kalau pilihanmu beda dengan model)
# Pilihan alasan awal. Alasan baru ditambah langsung dari aplikasi (kolom "tambah alasan"),
# disimpan di labeling/.alasan.json, dan otomatis muncul lagi di sesi berikutnya.
ALASAN_DEFAULT = ["kata negatif topik", "sarkasme/jokes", "di luar topik/bukan ke konten"]
ALASAN_FILE = OUTPUT_DIR / ".alasan.json"
ALASAN_MAKS = 30          # batas jumlah pilihan alasan
ALASAN_MAKS_PANJANG = 40  # batas panjang tiap alasan (karakter)

# ============================================================================
# FLASK APP
# ============================================================================

app = Flask(__name__)
app.config['JSON_AS_ASCII'] = False

state = {
    'dfs': {},
    'sessions': {},
    'saved_labelers': [],
    'preds': None,     # row_id -> {'label', 'conf', 'probs'}; None = belum dicek, {} = tidak tersedia
    'alasan': None,    # daftar pilihan alasan model meleset (dimuat dari .alasan.json)
}

def get_alasan():
    """Daftar pilihan alasan: dari .alasan.json kalau ada, kalau tidak pakai ALASAN_DEFAULT."""
    if state['alasan'] is None:
        opsi = list(ALASAN_DEFAULT)
        if ALASAN_FILE.exists():
            try:
                with open(ALASAN_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f).get('opsi', [])
                if isinstance(data, list) and data and all(isinstance(x, str) for x in data):
                    opsi = data
            except Exception:
                pass
        state['alasan'] = opsi
    return state['alasan']

def save_alasan():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(ALASAN_FILE, 'w', encoding='utf-8') as f:
        json.dump({'opsi': state['alasan']}, f, ensure_ascii=False, indent=2)

# ============================================================================
# LABELER MANAGEMENT
# ============================================================================

def load_saved_labelers():
    """Load daftar nama labeler yang pernah diinput."""
    if SAVED_LABELERS_FILE.exists():
        try:
            with open(SAVED_LABELERS_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                state['saved_labelers'] = data.get('labelers', [])
        except:
            state['saved_labelers'] = []
    return state['saved_labelers']

def save_labelers():
    """Save daftar labeler ke file."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(SAVED_LABELERS_FILE, 'w', encoding='utf-8') as f:
        json.dump({'labelers': state['saved_labelers']}, f, ensure_ascii=False, indent=2)

def add_labeler(name):
    """Tambah nama labeler baru ke saved list."""
    if name and name not in state['saved_labelers'] and name != LABELER_DEFAULT:
        state['saved_labelers'].append(name)
        save_labelers()

def get_available_labelers():
    """Return daftar labeler yang tersedia."""
    labelers = [LABELER_DEFAULT]
    if state['saved_labelers']:
        labelers.extend(state['saved_labelers'])
    return labelers

# ============================================================================
# DATA LOADING & SAVING
# ============================================================================

def output_path(name):
    """Path ke file CSV output labeler."""
    return OUTPUT_DIR / f"labels_{name.lower()}.csv"

def petakan_prediksi(p_neg, p_netral, p_pos):
    """Petakan probabilitas model 3 kelas ke 2 kelas sesuai MODEL_MODE.
    Return (label, conf, probs) dengan probs = {'negatif': ..., 'non-negatif': ...}."""
    p_non = p_netral + p_pos
    if MODEL_MODE == "B":
        label = 'negatif' if p_neg > p_non else 'non-negatif'
    else:  # "A": argmax 3 kelas dulu
        terbesar = max((p_neg, 'negatif'), (p_netral, 'netral'), (p_pos, 'positif'))[1]
        label = 'negatif' if terbesar == 'negatif' else 'non-negatif'
    probs = {'negatif': p_neg, 'non-negatif': p_non}
    return label, probs[label], probs

def ensure_predictions(master_df):
    """Muat prediksi model sekali. Kalau file tidak ada / tidak cocok dengan sampel, jawaban model dimatikan."""
    if state['preds'] is not None:
        return state['preds']
    state['preds'] = {}
    if not PREDIKSI_FILE.exists():
        print(f"  ⚠️  {PREDIKSI_FILE.name} tidak ditemukan -> jawaban model TIDAK ditampilkan "
              f"(jalankan prediksi_model.py dulu)")
        return state['preds']
    try:
        pred = pd.read_csv(PREDIKSI_FILE, dtype=str, keep_default_na=False, encoding='utf-8')
        if pred['comment_text_clean'].tolist() != master_df['comment_text_clean'].fillna('').tolist():
            print("  ⚠️  prediksi_model.csv tidak cocok dengan sampel_labeling.csv "
                  "(urutan/isi berbeda) -> jawaban model TIDAK ditampilkan. Jalankan ulang prediksi_model.py")
            return state['preds']
        for _, r in pred.iterrows():
            label, conf, probs = petakan_prediksi(
                float(r['p_negatif']), float(r['p_netral']), float(r['p_positif']))
            state['preds'][int(r['row_id'])] = {'label': label, 'conf': conf, 'probs': probs}
        print(f"  🤖 Prediksi model dimuat: {len(state['preds']):,} komentar (pemetaan mode {MODEL_MODE})")
    except Exception as e:
        state['preds'] = {}
        print(f"  ⚠️  Gagal membaca prediksi model ({e}) -> jawaban model TIDAK ditampilkan")
    return state['preds']

def model_visible(name):
    """True kalau labeler ini boleh melihat jawaban model dan prediksinya tersedia."""
    return name in MODEL_VISIBLE_FOR and bool(state['preds'])

def migrasi_ke_biner(df, path):
    """Ubah file label 3 kelas lama jadi 2 kelas. Hanya jalan sekali (kolom label_3kelas belum ada).
    - negatif tetap negatif; netral & positif jadi non-negatif
    - label asli disimpan di kolom label_3kelas
    - file asli dibackup dulu ke labels_<nama>_backup_3kelas.csv"""
    backup = path.with_name(path.stem + "_backup_3kelas.csv")
    if not backup.exists():
        shutil.copy2(path, backup)
        print(f"  🛟 Backup label 3 kelas: {backup}")

    lama = df[COL_LABEL].fillna('')
    df[COL_LABEL_3K] = lama
    jadi_non = lama.isin(['netral', 'positif'])
    df.loc[jadi_non, COL_LABEL] = 'non-negatif'

    if COL_MODEL_LABEL in df.columns:
        df[COL_MODEL_LABEL] = df[COL_MODEL_LABEL].fillna('').replace(
            {'netral': 'non-negatif', 'positif': 'non-negatif'})

    n_neg = int((lama == 'negatif').sum())
    print(f"  🔄 Migrasi 3 -> 2 kelas: {n_neg} negatif tetap, {int(jadi_non.sum())} jadi non-negatif")
    return df

def load_person_df(name):
    """Load dataframe untuk 1 labeler."""
    if name in state['dfs']:
        return state['dfs'][name]

    if not DATA_FILE.exists():
        raise FileNotFoundError(
            f"\n{'='*70}\n"
            f"❌ FATAL: File data tidak ditemukan!\n"
            f"   Lokasi expected: {DATA_FILE}\n"
            f"{'='*70}\n"
            f"\n📋 Cek:\n"
            f"  1. Apakah sampel_labeling.csv ada di:\n"
            f"     {BASE_PATH / '01_praprocessing' / 'hasil'}\n"
            f"  2. Kalau belum, jalankan dulu: praprocessing_komentar.ipynb\n"
            f"  3. Kalau lokasi berbeda, edit BASE_PATH di file ini\n"
            f"\n{'='*70}\n"
        )

    print(f"  📖 Loading master data: {DATA_FILE}")
    master_df = pd.read_csv(DATA_FILE, dtype=str, encoding='utf-8')

    # Kolom sesuai output praprocessing_komentar.ipynb (Bagian 7)
    required_cols = ['username', 'video_id', 'comment_text_clean']
    missing = [c for c in required_cols if c not in master_df.columns]
    if missing:
        raise ValueError(
            f"❌ Kolom yang hilang: {missing}. Kolom yang ada: {list(master_df.columns)}"
        )

    ensure_predictions(master_df)

    path = output_path(name)

    if path.exists():
        print(f"  💾 Found existing: {path}")
        df = pd.read_csv(path, dtype=str, encoding='utf-8')
        if COL_LABEL_3K not in df.columns:
            df = migrasi_ke_biner(df, path)
        if len(master_df) > len(df):
            print(f"  ℹ️  New batch: {len(master_df) - len(df)} samples")
            new_data = master_df.iloc[len(df):].copy()
            new_data[COL_LABEL] = ''
            new_data[COL_NOTE] = ''
            df = pd.concat([df, new_data], ignore_index=True)
    else:
        print(f"  ✨ Creating new: {path}")
        df = master_df.copy()
        df[COL_LABEL] = ''
        df[COL_NOTE] = ''

    for col, default in [(COL_LABEL, ''), (COL_NOTE, ''), (COL_LABEL_3K, ''), ('uji_ulang', '0'),
                         ('comment_text_raw', ''), ('commenter_username', ''),
                         (COL_MODEL_SEEN, '0'), (COL_MODEL_LABEL, ''), (COL_ALASAN, '')]:
        if col not in df.columns:
            df[col] = default
        df[col] = df[col].fillna(default)
    df['comment_text_clean'] = df['comment_text_clean'].fillna('')

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False, encoding='utf-8')

    state['dfs'][name] = df
    return df

def save_person_df(name):
    """Simpan progress labeling."""
    state['dfs'][name].to_csv(output_path(name), index=False, encoding='utf-8')

def get_session(name):
    """Ambil atau bikin session state."""
    if name not in state['sessions']:
        df = load_person_df(name)
        pending = df[df[COL_LABEL] == ''].index.tolist()
        state['sessions'][name] = {'queue': pending, 'pos': 0}
    return state['sessions'][name]

# ============================================================================
# HTTP ROUTES
# ============================================================================

@app.route('/')
def index():
    """Halaman utama."""
    name = request.args.get('labeler', '')
    mode = request.args.get('mode', 'select')

    available = get_available_labelers()

    # Mode: input nama baru (HARUS dicek duluan, karena di mode ini 'name' memang kosong)
    if mode == 'input':
        return render_template_string(INPUT_HTML, labeler_default=LABELER_DEFAULT)

    # Mode: select labeler
    if mode == 'select' or not name:
        return render_template_string(
            SELECT_HTML,
            labeler_default=LABELER_DEFAULT,
            labelers_saved=state['saved_labelers']
        )

    # Mode: labeling
    if name not in available and name != LABELER_DEFAULT:
        return render_template_string(
            SELECT_HTML,
            labeler_default=LABELER_DEFAULT,
            labelers_saved=state['saved_labelers'],
            error="Labeler tidak dikenal. Pilih ulang."
        )

    try:
        df = load_person_df(name)
        sess = get_session(name)
        queue, pos = sess['queue'], sess['pos']

        total = len(queue)
        reviewed = sum(1 for idx in queue if df.at[idx, COL_LABEL] != '')

        if not queue or pos >= total:
            return render_template_string(DONE_HTML, name=name, total=total)

        df_idx = queue[pos]
        row = df.loc[df_idx]

        # Label terkunci + jawaban model ditampilkan, hanya kalau jawaban itu memang sudah dilihat
        pred = state['preds'].get(int(df_idx)) if model_visible(name) else None
        locked = bool(pred) and row[COL_MODEL_SEEN] == '1' and row[COL_LABEL] != ''
        p = pred['probs'] if pred else {}

        return render_template_string(
            MAIN_HTML,
            locked=locked,
            alasan_opsi=get_alasan(),
            alasan_dipilih=str(row[COL_ALASAN]) if locked else '',
            label_dipilih=str(row[COL_LABEL]),
            pred_label=pred['label'] if locked else '',
            pred_conf=round(pred['conf'] * 100) if locked else 0,
            pred_probs=(f"negatif {p['negatif']*100:.0f}% · non-negatif {p['non-negatif']*100:.0f}%") if locked else '',
            sama=bool(locked and pred['label'] == row[COL_LABEL]),
            name=name,
            progress=(reviewed / total * 100) if total else 0,
            progress_bar=f"{reviewed}/{total}",
            username=row['username'],                       # influencer (pemilik video)
            commenter=str(row['commenter_username']),       # akun yang berkomentar
            video_id=str(row['video_id']).strip(),
            comment_text=str(row['comment_text_clean']),
            comment_raw=str(row['comment_text_raw']),
            catatan=str(row[COL_NOTE]),
            uji_ulang=str(row.get('uji_ulang', '0')),
            has_prev=(pos > 0),
        )

    except Exception as e:
        import traceback
        return f"""
        <html><head><title>Error</title></head><body style="font-family:monospace;padding:20px;">
        <h2>❌ Error</h2>
        <p>{str(e)}</p>
        <pre>{traceback.format_exc()}</pre>
        <p><a href="/">← Back</a></p>
        </body></html>
        """, 500

@app.route('/add-labeler', methods=['POST'])
def add_labeler_api():
    """POST /add-labeler buat nambahin labeler baru."""
    try:
        data = request.get_json(silent=True)
        name = (data.get('name', '') if data else '').strip()

        if not name:
            return jsonify({'error': 'Nama tidak boleh kosong'}), 400

        if name.lower() == LABELER_DEFAULT.lower():
            return jsonify({'error': f'Nama tidak boleh sama dengan {LABELER_DEFAULT}'}), 400

        # Nama dipakai jadi nama file, jadi dibatasi huruf/angka/spasi/_/-
        if not re.fullmatch(r"[A-Za-z0-9 _-]{1,30}", name):
            return jsonify({'error': 'Nama hanya boleh huruf, angka, spasi, _ atau - (maks 30 karakter)'}), 400
        if name.lower() in [n.lower() for n in state['saved_labelers']]:
            # sudah ada: langsung lanjut pakai nama yang tersimpan
            name = next(n for n in state['saved_labelers'] if n.lower() == name.lower())

        add_labeler(name)
        return jsonify({
            'success': True,
            'message': f'{name} ditambahkan',
            'redirect': f'/?labeler={name}&mode=label'
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/label', methods=['POST'])
def label_comment():
    """POST /label?labeler=Shafwa buat simpan label."""
    try:
        name = request.args.get('labeler', '')
        if name not in get_available_labelers():
            return jsonify({'error': 'Labeler tidak dikenal'}), 400

        payload = request.get_json(silent=True)
        label = payload.get('label') if payload else None
        catatan = (payload.get('catatan', '') if payload else '').strip()

        if label not in LABEL_CHOICES:
            return jsonify({'error': f'Label tidak valid: {label}'}), 400

        df = load_person_df(name)
        sess = get_session(name)
        queue, pos = sess['queue'], sess['pos']

        if pos >= len(queue):
            return jsonify({'error': 'Semua komentar sudah dilabel'}), 400

        df_idx = queue[pos]

        # Jawaban model sudah pernah dilihat untuk komentar ini -> label tidak boleh diubah lagi
        if df.at[df_idx, COL_MODEL_SEEN] == '1' and df.at[df_idx, COL_LABEL] != '':
            return jsonify({'error': 'Label terkunci karena jawaban model sudah dilihat. Tekan panah kanan untuk lanjut.'}), 400

        df.at[df_idx, COL_LABEL] = label
        df.at[df_idx, COL_NOTE] = catatan

        # Labeler yang boleh melihat model: simpan label, tampilkan jawaban model, JANGAN maju dulu.
        # Maju ke komentar berikutnya baru saat panah kanan/spasi ditekan (/next).
        pred = state['preds'].get(int(df_idx)) if model_visible(name) else None
        if pred:
            df.at[df_idx, COL_MODEL_SEEN] = '1'
            df.at[df_idx, COL_MODEL_LABEL] = pred['label']
            save_person_df(name)
            return jsonify({'success': True, 'reveal': True})

        save_person_df(name)
        sess['pos'] += 1

        if sess['pos'] >= len(queue):
            return jsonify({
                'done': True,
                'message': f'✅ {name} selesai! {len(queue)} komentar dilabel.'
            })

        return jsonify({'success': True})

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

@app.route('/add-alasan', methods=['POST'])
def add_alasan_api():
    """POST /add-alasan?labeler=Shafwa {"nama": "..."}: tambah pilihan alasan baru (permanen)."""
    try:
        name = request.args.get('labeler', '')
        if name not in get_available_labelers():
            return jsonify({'error': 'Labeler tidak dikenal'}), 400

        payload = request.get_json(silent=True) or {}
        teks = re.sub(r'\s+', ' ', str(payload.get('nama', ''))).strip()

        if not teks:
            return jsonify({'error': 'Alasan tidak boleh kosong'}), 400
        if len(teks) > ALASAN_MAKS_PANJANG:
            return jsonify({'error': f'Alasan terlalu panjang (maks {ALASAN_MAKS_PANJANG} karakter)'}), 400

        opsi = get_alasan()
        sudah = next((o for o in opsi if o.lower() == teks.lower()), None)
        if sudah:                       # sudah ada: pakai yang lama, jangan dobel
            return jsonify({'success': True, 'opsi': opsi, 'dipilih': sudah, 'baru': False})
        if len(opsi) >= ALASAN_MAKS:
            return jsonify({'error': f'Maksimal {ALASAN_MAKS} pilihan alasan'}), 400

        opsi.append(teks)
        save_alasan()
        return jsonify({'success': True, 'opsi': opsi, 'dipilih': teks, 'baru': True})

    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/next', methods=['POST'])
def next_comment():
    """POST /next?labeler=Shafwa: lanjut ke komentar berikutnya (dipakai setelah jawaban model tampil)."""
    try:
        name = request.args.get('labeler', '')
        if name not in get_available_labelers():
            return jsonify({'error': 'Labeler tidak dikenal'}), 400

        payload = request.get_json(silent=True) or {}
        df = load_person_df(name)
        sess = get_session(name)
        queue, pos = sess['queue'], sess['pos']

        if pos >= len(queue):
            return jsonify({'error': 'Sudah di akhir'}), 400
        df_idx = queue[pos]
        if df.at[df_idx, COL_LABEL] == '':
            return jsonify({'error': 'Komentar ini belum dilabel'}), 400

        # catatan boleh ditambah/diubah setelah melihat jawaban model
        if 'catatan' in payload:
            df.at[df_idx, COL_NOTE] = str(payload['catatan']).strip()

        # alasan model meleset: hanya dari daftar pilihan, dan hanya kalau memang beda dengan model
        if 'alasan' in payload:
            alasan = str(payload['alasan']).strip()
            beda = df.at[df_idx, COL_MODEL_LABEL] not in ('', df.at[df_idx, COL_LABEL])
            if alasan in get_alasan() and beda:
                df.at[df_idx, COL_ALASAN] = alasan
            elif alasan == '':
                df.at[df_idx, COL_ALASAN] = ''
        save_person_df(name)

        sess['pos'] += 1
        return jsonify({'success': True, 'done': sess['pos'] >= len(queue)})

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500

@app.route('/previous', methods=['POST'])
def previous_comment():
    """POST /previous?labeler=Shafwa buat balik ke komentar sebelumnya."""
    try:
        name = request.args.get('labeler', '')
        if name not in get_available_labelers():
            return jsonify({'error': 'Labeler tidak dikenal'}), 400
        sess = get_session(name)
        if sess['pos'] <= 0:
            return jsonify({'error': 'Sudah di awal'}), 400
        sess['pos'] -= 1
        return jsonify({'success': True})

    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ============================================================================
# HTML TEMPLATES
# ============================================================================

SELECT_HTML = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Labeling Komentar</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            display: flex;
            justify-content: center;
            align-items: center;
            padding: 30px 16px;
        }
        .container {
            background: white;
            border-radius: 16px;
            box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);
            max-width: 400px;
            width: 100%;
            padding: 40px 30px;
            text-align: center;
        }
        h1 { font-size: 24px; color: #333; margin-bottom: 8px; font-weight: 700; }
        .subtitle { font-size: 14px; color: #999; margin-bottom: 30px; }

        .error-box {
            background: #ffe0e0;
            color: #c41e1e;
            padding: 12px;
            border-radius: 8px;
            margin-bottom: 20px;
            font-size: 13px;
        }

        a.pick {
            display: block;
            padding: 16px;
            margin-bottom: 12px;
            border-radius: 12px;
            background: #f0f0f5;
            color: #333;
            text-decoration: none;
            font-weight: 700;
            font-size: 15px;
            transition: all 0.2s;
        }
        a.pick:hover {
            background: #667eea;
            color: white;
            transform: translateY(-2px);
            box-shadow: 0 8px 24px rgba(102, 126, 234, 0.4);
        }
        a.pick-new {
            background: #e8f5e9;
            color: #2e7d32;
        }
        a.pick-new:hover {
            background: #2e7d32;
            color: white;
        }
        .hint { margin-top: 20px; font-size: 12px; color: #999; }
    </style>
</head>
<body>
    <div class="container">
        <h1>🏷️ Labeling Komentar</h1>
        <p class="subtitle">TA Nesha - Sentimen Influencer Kesehatan</p>

        {% if error %}
        <div class="error-box">{{ error }}</div>
        {% endif %}

        <p style="margin-bottom: 20px; color: #666; font-size: 13px;">Siapa yang labeling?</p>

        <a class="pick" href="/?labeler={{ labeler_default }}&mode=label">{{ labeler_default }}</a>

        {% for saved in labelers_saved %}
        <a class="pick" href="/?labeler={{ saved }}&mode=label">{{ saved }}</a>
        {% endfor %}

        <a class="pick pick-new" href="/?mode=input">+ Nama baru</a>

        <div class="hint">
            💡 Arrow keys: ← → ↑ (lebih cepat!)
        </div>
    </div>
</body>
</html>
"""

INPUT_HTML = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Input Nama Labeler</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            display: flex;
            justify-content: center;
            align-items: center;
            padding: 30px 16px;
        }
        .container {
            background: white;
            border-radius: 16px;
            box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);
            max-width: 380px;
            width: 100%;
            padding: 40px 30px;
            text-align: center;
        }
        h1 { font-size: 20px; color: #333; margin-bottom: 8px; font-weight: 700; }
        .subtitle { font-size: 13px; color: #999; margin-bottom: 24px; }

        input {
            width: 100%;
            padding: 14px 12px;
            border: 2px solid #e0e0e0;
            border-radius: 10px;
            font-size: 15px;
            font-family: inherit;
            margin-bottom: 14px;
            transition: border 0.2s;
        }
        input:focus {
            outline: none;
            border-color: #667eea;
        }

        .button-group {
            display: flex;
            gap: 10px;
        }
        button {
            flex: 1;
            padding: 14px;
            border: none;
            border-radius: 10px;
            font-size: 15px;
            font-weight: 700;
            cursor: pointer;
            transition: all 0.2s;
        }
        .btn-submit {
            background: #667eea;
            color: white;
        }
        .btn-submit:hover:not(:disabled) {
            background: #5568d3;
            transform: translateY(-2px);
        }
        .btn-cancel {
            background: #f0f0f5;
            color: #555;
        }
        .btn-cancel:hover {
            background: #e0e0e0;
        }
        button:disabled {
            opacity: 0.5;
            cursor: not-allowed;
        }
        .error { color: #c41e1e; font-size: 13px; margin-top: 10px; }
        .loading { opacity: 0.5; pointer-events: none; }
    </style>
</head>
<body>
    <div class="container">
        <h1>Nama Labeler Baru</h1>
        <p class="subtitle">Masukkan nama kamu untuk mulai labeling</p>

        <input type="text" id="nameInput" placeholder="Contoh: Budi, Ani, dll" autofocus>
        <div id="error" class="error"></div>

        <div class="button-group">
            <button class="btn-submit" onclick="submitName()">Mulai Labeling</button>
            <button class="btn-cancel" onclick="history.back()">Batal</button>
        </div>
    </div>

    <script>
        const input = document.getElementById('nameInput');

        function submitName() {
            const name = input.value.trim();

            if (!name) {
                document.getElementById('error').textContent = '⚠️ Nama tidak boleh kosong';
                return;
            }

            const btn = event.target;
            btn.disabled = true;
            btn.classList.add('loading');

            fetch('/add-labeler', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name: name })
            })
            .then(r => r.json())
            .then(data => {
                if (data.error) {
                    document.getElementById('error').textContent = '❌ ' + data.error;
                    btn.disabled = false;
                    btn.classList.remove('loading');
                } else if (data.redirect) {
                    window.location.href = data.redirect;
                }
            })
            .catch(e => {
                document.getElementById('error').textContent = '❌ Error: ' + e.message;
                btn.disabled = false;
                btn.classList.remove('loading');
            });
        }

        input.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') submitName();
        });
    </script>
</body>
</html>
"""

MAIN_HTML = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Labeling - {{ name }}</title>
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
            max-width: 550px;
            width: 100%;
            padding: 28px 26px;
            margin-top: 20px;
        }
        .header {
            display: flex;
            align-items: center;
            gap: 12px;
            margin-bottom: 14px;
        }
        .btn-prev {
            background: #f0f0f5;
            color: #555;
            border: none;
            border-radius: 10px;
            padding: 10px 14px;
            font-size: 13px;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.2s;
        }
        .btn-prev:hover:not(:disabled) { background: #667eea; color: white; }
        .btn-prev:disabled { opacity: 0.3; cursor: not-allowed; }

        .progress-section { flex: 1; }
        .progress-bar {
            width: 100%;
            height: 8px;
            background: #e0e0e0;
            border-radius: 10px;
            overflow: hidden;
            margin-bottom: 6px;
        }
        .progress-fill {
            height: 100%;
            background: linear-gradient(90deg, #667eea 0%, #764ba2 100%);
            width: {{ progress }}%;
            transition: width 0.3s;
        }
        .progress-text { font-size: 12px; color: #666; font-weight: 600; }

        .top-info {
            display: flex;
            justify-content: space-between;
            margin-bottom: 16px;
            font-size: 12px;
            color: #999;
        }
        .labeler-name { font-weight: 700; color: #667eea; }
        .badge-uji {
            display: inline-block;
            background: #fff3cd;
            color: #856404;
            padding: 3px 8px;
            border-radius: 4px;
            font-size: 11px;
            font-weight: 700;
            margin-left: 6px;
        }
        a.switch { color: #667eea; text-decoration: none; font-weight: 600; }

        .comment-container { margin-bottom: 24px; }
        .username { font-size: 15px; color: #333; margin-bottom: 10px; font-weight: 700; }
        .comment-text {
            font-size: 15px;
            color: #333;
            background: #f9f9f9;
            padding: 16px;
            border-radius: 12px;
            line-height: 1.6;
            white-space: pre-wrap;
            word-break: break-word;
            border-left: 4px solid #667eea;
        }
        .meta { font-size: 12px; color: #999; margin-top: 10px; }
        .meta a { color: #667eea; text-decoration: none; font-weight: 600; }

        .controls { display: flex; flex-direction: row; gap: 10px; margin-bottom: 14px; }
        .btn-label {
            flex: 1;
            padding: 18px 10px;
            border: none;
            border-radius: 12px;
            font-size: 14px;
            font-weight: 700;
            color: white;
            cursor: pointer;
            transition: all 0.2s;
        }
        .btn-label:hover:not(:disabled) { transform: translateY(-2px); box-shadow: 0 8px 24px rgba(0,0,0,0.2); }
        .btn-label:disabled { opacity: 0.5; cursor: not-allowed; }
        .btn-negatif { background: #ff6b6b; }
        .btn-negatif:hover:not(:disabled) { background: #ff5252; }
        .btn-nonneg { background: #4c6ef5; }
        .btn-nonneg:hover:not(:disabled) { background: #3b5bdb; }

        .reveal { border-radius: 12px; padding: 14px 16px; margin-bottom: 12px; border: 2px solid; }
        .reveal.sama { background: #ebfbee; border-color: #51cf66; }
        .reveal.beda { background: #fff4e6; border-color: #ff922b; }
        .reveal-row { display: flex; justify-content: space-between; font-size: 14px; color: #333; margin-bottom: 4px; }
        .reveal-status { font-size: 15px; font-weight: 700; margin-top: 6px; }
        .reveal.sama .reveal-status { color: #2b8a3e; }
        .reveal.beda .reveal-status { color: #d9480f; }
        .reveal-probs { font-size: 12px; color: #777; margin-top: 4px; }
        .alasan { margin-bottom: 12px; }
        .alasan-judul { font-size: 12px; color: #777; margin-bottom: 6px; }
        .chips { display: flex; flex-wrap: wrap; gap: 8px; }
        .chip { border: 2px solid #dee2e6; background: white; color: #444; border-radius: 999px;
                padding: 8px 14px; font-size: 13px; font-weight: 600; cursor: pointer; }
        .chip:hover { border-color: #667eea; }
        .chip.on { background: #667eea; border-color: #667eea; color: white; }
        .alasan-tambah { display: flex; gap: 8px; margin-top: 10px; }
        .alasan-tambah input { flex: 1; min-width: 0; padding: 9px 12px; border: 2px solid #e0e0e0; border-radius: 10px;
                               font-size: 13px; font-family: inherit; }
        .alasan-tambah input:focus { outline: none; border-color: #667eea; }
        .chip-add { border: none; background: #e7ecff; color: #3b4cca; border-radius: 10px; padding: 0 14px;
                    font-size: 13px; font-weight: 700; cursor: pointer; white-space: nowrap; }
        .chip-add:hover { background: #667eea; color: white; }
        .chip-no { display: inline-block; min-width: 18px; text-align: center; font-weight: 700; opacity: 0.7; }
        .btn-next { width: 100%; padding: 15px; border: none; border-radius: 12px; font-size: 14px;
                    font-weight: 700; color: white; background: #667eea; cursor: pointer; margin-bottom: 14px; }
        .btn-next:hover { background: #5568d3; }
        .username .sub { font-weight: 400; font-size: 12px; color: #999; margin-left: 6px; }
        .video-box { margin-bottom: 18px; }
        .video-bar { display: flex; justify-content: space-between; align-items: center; gap: 10px; }
        .video-bar a { color: #667eea; text-decoration: none; font-weight: 600; font-size: 12px; }
        .btn-video { background: #f0f0f5; color: #555; border: none; border-radius: 10px;
                     padding: 9px 14px; font-size: 13px; font-weight: 600; cursor: pointer; }
        .btn-video:hover { background: #667eea; color: white; }
        .video-wrap { margin-top: 12px; display: flex; justify-content: center; }
        .video-wrap iframe { width: 100%; max-width: 340px; height: 620px; border: 0; border-radius: 12px; background: #000; }
        .video-note { font-size: 11px; color: #aaa; margin-top: 8px; }
        .catatan { width: 100%; padding: 10px 12px; border: 2px solid #e0e0e0; border-radius: 10px;
                   font-size: 13px; font-family: inherit; margin-bottom: 14px; resize: vertical; }
        .catatan:focus { outline: none; border-color: #667eea; }
        .hints { font-size: 12px; color: #666; background: #f0f0f5; padding: 12px; border-radius: 10px; text-align: center; }
        .rule { font-size: 11px; color: #888; background: #fafafa; border: 1px dashed #ddd; border-radius: 10px;
                padding: 8px 12px; margin-bottom: 14px; line-height: 1.5; }
        .loading { opacity: 0.5; pointer-events: none; }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <button class="btn-prev" id="prevBtn" onclick="goPrevious()" {{ 'disabled' if not has_prev else '' }}>
                ↑ Previous
            </button>
            <div class="progress-section">
                <div class="progress-bar"><div class="progress-fill"></div></div>
                <div class="progress-text">{{ progress_bar }} dilabel</div>
            </div>
        </div>

        <div class="top-info">
            <span class="labeler-name">{{ name }} {% if uji_ulang == '1' %}<span class="badge-uji">Re-check 10%</span>{% endif %}</span>
            <a class="switch" href="/">ganti</a>
        </div>

        <div class="comment-container">
            <div class="username">
                {% if commenter %}@{{ commenter }}{% else %}Komentar{% endif %}
                <span class="sub">di video @{{ username }}</span>
            </div>
            <div class="comment-text">{{ comment_text }}</div>
            {% if comment_raw and comment_raw != comment_text %}
            <div class="meta">Teks asli: {{ comment_raw }}</div>
            {% endif %}
        </div>

        {% if video_id and video_id.isdigit() %}
        <div class="video-box">
            <div class="video-bar">
                <button type="button" class="btn-video" id="videoToggle" onclick="toggleVideo()">▶ Tampilkan video</button>
                <a href="https://www.tiktok.com/@{{ username }}/video/{{ video_id }}" target="_blank" rel="noopener">buka di TikTok →</a>
            </div>
            <div id="videoWrap" class="video-wrap" style="display:none"></div>
            <div class="video-note">Kalau video tidak muncul (dihapus/privat), pakai link "buka di TikTok". Setelah klik di dalam video, klik area kosong dulu supaya tombol panah aktif lagi.</div>
        </div>
        {% endif %}

        <div class="rule">
            <b>Negatif</b> = menyasar influencer/kontennya (kritik, tidak suka, tidak nyaman, berlebihan, meragukan kredibilitas, hoaks).<br>
            <b>Non-negatif</b> = selain itu (pertanyaan, cerita pribadi, reaksi ke penyakitnya, keluhan ke RS/pemerintah/obat, dukungan).
        </div>

        <textarea id="catatan" class="catatan" rows="2"
                  placeholder="Catatan (opsional): isi kalau ragu, ambigu, sarkasme, atau di luar topik kesehatan">{{ catatan }}</textarea>

        {% if locked %}
        <div class="reveal {{ 'sama' if sama else 'beda' }}">
            <div class="reveal-row"><span>Pilihanmu</span><b>{{ label_dipilih }}</b></div>
            <div class="reveal-row"><span>Jawaban model</span><b>{{ pred_label }} ({{ pred_conf }}%)</b></div>
            <div class="reveal-status">{{ '✓ Sama dengan model' if sama else '✗ Beda dengan model' }}</div>
            <div class="reveal-probs">{{ pred_probs }}</div>
        </div>
        {% if not sama %}
        <div class="alasan">
            <div class="alasan-judul">Kenapa menurutmu model meleset? (opsional)</div>
            <div class="chips" id="chips"></div>
            <div class="alasan-tambah">
                <input type="text" id="alasanBaru" maxlength="40" autocomplete="off"
                       placeholder="Alasan lain? Ketik lalu Enter untuk menambah pilihan baru">
                <button type="button" class="chip-add" onclick="tambahAlasan()">+ Tambah</button>
            </div>
        </div>
        {% endif %}
        <button class="btn-next" onclick="goNext()">Lanjut → (panah kanan)</button>

        <div class="hints">
            🔒 Label terkunci &nbsp;|&nbsp; → lanjut{% if not sama %} &nbsp;|&nbsp; angka = pilih alasan (boleh dilewati){% endif %} &nbsp;|&nbsp; ↑ Previous
        </div>
        {% else %}
        <div class="controls">
            <button class="btn-label btn-negatif" onclick="labelComment('negatif')">← NEGATIF</button>
            <button class="btn-label btn-nonneg" onclick="labelComment('non-negatif')">NON-NEGATIF →</button>
        </div>

        <div class="hints">
            ⌨️ ← Negatif | → Non-negatif | ↑ Previous
        </div>
        {% endif %}
    </div>

    <script>
        const LABELER = {{ name | tojson }};
        const VIDEO_ID = {{ video_id | tojson }};
        const LOCKED = {{ locked | tojson }};
        let ALASAN_OPSI = {{ alasan_opsi | tojson }};
        const ADA_ALASAN = {{ (locked and not sama) | tojson }};   // panel alasan hanya muncul kalau beda dengan model
        let ALASAN_SEL = {{ alasan_dipilih | tojson }};
        let busy = false;   // cegah tekan tombol dua kali saat permintaan sedang berjalan

        // Gambar ulang tombol alasan (textContent, jadi teks apa pun aman dari injeksi HTML)
        function renderChips() {
            const box = document.getElementById('chips');
            if (!box) return;
            box.innerHTML = '';
            ALASAN_OPSI.forEach((teks, i) => {
                const b = document.createElement('button');
                b.type = 'button';
                b.className = 'chip' + (teks === ALASAN_SEL ? ' on' : '');
                const no = document.createElement('span');
                no.className = 'chip-no';
                no.textContent = i < 9 ? String(i + 1) : '•';   // angka keyboard hanya sampai 9
                b.appendChild(no);
                b.appendChild(document.createTextNode(' ' + teks));
                b.onclick = () => pilihAlasan(i);
                box.appendChild(b);
            });
        }

        // Klik/tekan angka = pilih alasan; tekan lagi = batalkan pilihan
        function pilihAlasan(i) {
            if (!ADA_ALASAN || ALASAN_OPSI[i] === undefined) return;
            ALASAN_SEL = (ALASAN_SEL === ALASAN_OPSI[i]) ? '' : ALASAN_OPSI[i];
            renderChips();
        }

        // Tambah pilihan alasan baru: disimpan permanen, dan langsung terpilih untuk komentar ini
        function tambahAlasan() {
            const inp = document.getElementById('alasanBaru');
            if (!inp || busy) return;
            const nama = inp.value.trim();
            if (!nama) return;
            busy = true;
            fetch('/add-alasan?labeler=' + encodeURIComponent(LABELER), {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ nama: nama })
            })
            .then(r => r.json())
            .then(data => {
                busy = false;
                if (data.error) { alert('❌ ' + data.error); return; }
                ALASAN_OPSI = data.opsi;
                ALASAN_SEL = data.dipilih;
                inp.value = '';
                inp.blur();          // supaya panah kanan langsung bisa dipakai lagi
                renderChips();
            })
            .catch(e => { busy = false; alert('❌ ' + e.message); });
        }
        (function () {
            const inp = document.getElementById('alasanBaru');
            if (inp) inp.addEventListener('keydown', (e) => {
                if (e.key === 'Enter') { e.preventDefault(); tambahAlasan(); }
            });
            renderChips();
        })();

        function goNext() {
            if (busy) return;
            setLoading(true);
            const body = { catatan: document.getElementById('catatan').value };
            if (ADA_ALASAN) body.alasan = ALASAN_SEL;
            fetch('/next?labeler=' + encodeURIComponent(LABELER), {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body)
            })
            .then(r => r.json())
            .then(data => {
                if (data.error) { alert('❌ ' + data.error); setLoading(false); return; }
                location.reload();
            })
            .catch(e => { alert('❌ ' + e.message); setLoading(false); });
        }

        // Video dimuat hanya saat dibuka (hemat kuota). Pilihan buka/tutup diingat di browser.
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
            try { localStorage.setItem('showVideo', show ? '1' : '0'); } catch (e) {}
        }
        function toggleVideo() {
            const wrap = document.getElementById('videoWrap');
            showVideo(wrap.style.display === 'none');
            document.getElementById('videoToggle').blur();
        }
        document.addEventListener('DOMContentLoaded', () => {
            try { if (localStorage.getItem('showVideo') === '1') showVideo(true); } catch (e) {}
        });

        function setLoading(state) {
            busy = state;
            document.querySelectorAll('.btn-label, .btn-prev').forEach(b => {
                if (state) b.classList.add('loading');
                else b.classList.remove('loading');
            });
        }

        function labelComment(label) {
            if (busy) return;
            setLoading(true);
            fetch('/label?labeler=' + encodeURIComponent(LABELER), {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ label: label, catatan: document.getElementById('catatan').value })
            })
            .then(r => r.json())
            .then(data => {
                if (data.error) {
                    // Label sudah terkunci (misalnya tombol ditekan dua kali): cukup muat ulang, tanpa popup
                    if (String(data.error).includes('terkunci')) { location.reload(); return; }
                    alert('❌ ' + data.error);
                    setLoading(false);
                    return;
                }
                if (data.reveal) {
                    location.reload();   // halaman dimuat ulang dalam keadaan terkunci + jawaban model tampil
                    return;
                }
                if (data.done) {
                    document.body.innerHTML = '<div class="container" style="text-align:center;margin-top:100px;max-width:480px;"><div style="font-size:72px;margin-bottom:20px;">✅</div><h2>' + data.message + '</h2><p style="color:#999;margin:14px 0;">File CSV udah disimpen.</p><a href="/" style="color:#667eea;font-weight:600;">← Labeler lain</a></div>';
                } else if (data.success) {
                    location.reload();
                }
            })
            .catch(e => {
                alert('❌ ' + e.message);
                setLoading(false);
            });
        }

        function goPrevious() {
            if (busy) return;
            setLoading(true);
            fetch('/previous?labeler=' + encodeURIComponent(LABELER), { method: 'POST' })
            .then(r => r.json())
            .then(data => {
                if (data.error) {
                    alert('⚠️ ' + data.error);
                    setLoading(false);
                } else if (data.success) {
                    location.reload();
                }
            })
            .catch(e => {
                alert('❌ ' + e.message);
                setLoading(false);
            });
        }

        document.addEventListener('keydown', (e) => {
            // Saat mengetik catatan, tombol panah jangan dianggap label
            if (e.target && (e.target.tagName === 'TEXTAREA' || e.target.tagName === 'INPUT')) return;
            if (LOCKED) {
                // Setelah jawaban model tampil: label tidak bisa diubah. → atau spasi = lanjut, angka = alasan
                if (e.key === 'ArrowRight' || e.key === ' ' || e.code === 'Space') { e.preventDefault(); goNext(); }
                else if (e.key === 'ArrowUp') { e.preventDefault(); goPrevious(); }
                else if (/^[1-9]$/.test(e.key)) { pilihAlasan(parseInt(e.key, 10) - 1); }
                return;
            }
            if (e.key === 'ArrowLeft') { e.preventDefault(); labelComment('negatif'); }
            else if (e.key === 'ArrowRight') { e.preventDefault(); labelComment('non-negatif'); }
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
            min-height: 100vh;
            display: flex;
            justify-content: center;
            align-items: center;
            padding: 20px;
        }
        .container {
            background: white;
            border-radius: 16px;
            box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);
            max-width: 480px;
            width: 100%;
            padding: 60px 40px;
            text-align: center;
        }
        h1 { font-size: 80px; margin-bottom: 20px; }
        h2 { font-size: 20px; color: #333; margin-bottom: 12px; font-weight: 700; }
        p { color: #666; margin-bottom: 12px; font-size: 14px; }
        a { color: #667eea; text-decoration: none; font-weight: 600; }
        a:hover { text-decoration: underline; }
    </style>
</head>
<body>
    <div class="container">
        <h1>✅</h1>
        <h2>{{ name }}, selesai!</h2>
        <p>{{ total }} komentar dilabel dan disimpen.</p>
        <p><a href="/">← Labeler lain</a></p>
    </div>
</body>
</html>
"""

# ============================================================================
# MAIN - STARTUP & NGROK
# ============================================================================

def print_banner():
    """Print welcome banner."""
    print("\n" + "=" * 80)
    print("🏷️  LABELING KOMENTAR - TA NESHA (2 KELAS)".center(80))
    print("=" * 80)

def validate_setup():
    """Validate setup."""
    print("\n📋 Validation:")
    print(f"  ✓ Base path: {BASE_PATH}")

    if not DATA_FILE.exists():
        print(f"\n  ❌ FATAL: sampel_labeling.csv not found at {DATA_FILE}")
        return False

    print(f"  ✓ Data file exists")

    try:
        df = pd.read_csv(DATA_FILE, nrows=1, dtype=str, encoding='utf-8')
        print(f"  ✓ Data readable: {len(df.columns)} columns")
    except Exception as e:
        print(f"  ❌ Error reading data: {e}")
        return False

    if PREDIKSI_FILE.exists():
        print(f"  ✓ Prediksi model ditemukan (pemetaan mode {MODEL_MODE}) -> jawaban model tampil untuk: "
              f"{', '.join(MODEL_VISIBLE_FOR) or '(tidak ada)'}")
    else:
        print(f"  ⚠️  {PREDIKSI_FILE.name} belum ada: jawaban model TIDAK akan tampil. "
              f"Jalankan prediksi_model.py dulu kalau mau memakainya.")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    return True

def print_instructions(url_base):
    """Print instruksi."""
    print("\n" + "=" * 80)
    print("🚀 SERVER STARTING")
    print("=" * 80)
    print(f"\n🌐 Buka: {url_base}")
    print(f"\n⌨️  Keyboard:")
    print(f"   ← (left)  = NEGATIF")
    print(f"   → (right) = NON-NEGATIF")
    print(f"   ↑ (up)    = PREVIOUS")
    print(f"\n👥 Labelers:")
    print(f"   1. {LABELER_DEFAULT} (always available)")
    print(f"   2. Input nama baru (saved for next time)")
    print(f"   3. Pilih dari saved labelers")
    print(f"\n⏹️  Stop: CTRL+C")
    print("=" * 80 + "\n")

if __name__ == '__main__':
    # Load saved labelers
    load_saved_labelers()

    print_banner()

    if not validate_setup():
        sys.exit(1)

    # Check for ngrok flag
    use_ngrok = '--tunnel' in sys.argv

    if use_ngrok:
        try:
            from pyngrok import ngrok
            print("\n🔗 Setting up ngrok tunnel...")

            # Start ngrok tunnel
            public_url = ngrok.connect(5000)
            print(f"✅ Ngrok tunnel started: {public_url}")

            print_instructions(str(public_url))

            app.run(debug=False, host='0.0.0.0', port=5000, use_reloader=False)

        except ImportError:
            print("\n❌ ngrok not installed. Run: pip install pyngrok")
            print("    Or jalanin tanpa --tunnel: python labeling_final.py")
            sys.exit(1)
    else:
        print_instructions("http://localhost:5000")
        app.run(debug=False, host='0.0.0.0', port=5000, use_reloader=False)