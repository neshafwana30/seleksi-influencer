import pandas as pd
from pathlib import Path

def find_repo_root():
    """Auto-detect folder root repo (yang punya 01_pengumpulan_data DAN 02_IndoBert),
    jangan andelin Path.cwd() doang krn kadang beda sama folder notebook."""
    current = Path.cwd()
    for _ in range(6):
        if (current / '01_pengumpulan_data').is_dir() and (current / '02_IndoBert').is_dir():
            return current
        if current.parent == current:
            break
        current = current.parent
    return None

REPO_ROOT = find_repo_root()
if REPO_ROOT is None:
    raise FileNotFoundError(
        'Nggak ketemu folder root repo (yang punya 01_pengumpulan_data + 02_IndoBert) '
        'dari posisi sekarang. Jalankan notebook dari dalam folder repo, atau isi REPO_ROOT manual.'
    )
print(f'Repo root ketemu di: {REPO_ROOT}')

BASELINE_PATH = REPO_ROOT / '02_IndoBert' / 'data' / 'siap_latih' / 'binary' / 'dataset_final.csv'
HASIL_DIR     = REPO_ROOT / '01_pengumpulan_data' / '06_data_negatif_tambahan' / 'hasil'
REVIEW_PATH   = HASIL_DIR / 'review_manual.csv'
OUT_PATH      = HASIL_DIR / 'komentar_negatif_gabungan.csv'

def _pick_col(df, candidates):
    for c in candidates:
        if c in df.columns:
            return c
    return None

sumber = []

# --- baseline: dataset_final.csv (binary) ---
if BASELINE_PATH.exists():
    base_df = pd.read_csv(BASELINE_PATH, dtype=str, keep_default_na=False, encoding='utf-8')
    label_col = _pick_col(base_df, ['label_name', 'label_final', 'label'])
    text_col = _pick_col(base_df, ['comment_text_clean', 'text_model', 'comment_text_raw'])
    if label_col and text_col:
        base_neg = base_df[base_df[label_col].astype(str).str.strip().str.lower() == 'negatif'].copy()
        base_neg = base_neg[base_neg[text_col].astype(str).str.strip() != '']
        for i, row in base_neg.reset_index(drop=True).iterrows():
            sumber.append({'source': 'baseline', 'original_id': f'baseline_{i}', 'original_text': row[text_col]})
        print(f'Baseline (dataset_final.csv): {len(base_neg)} komentar negatif ditemukan (kolom label={label_col!r}, text={text_col!r})')
    else:
        print(f'PERHATIAN: {BASELINE_PATH.name} ditemukan tapi kolom label/text tidak dikenali -> baseline dilewati. Kolom yang ada: {list(base_df.columns)}')
else:
    print(f'PERHATIAN: {BASELINE_PATH} tidak ditemukan -> baseline dilewati')

# --- hasil review manual (kandidat tambahan) ---
if REVIEW_PATH.exists():
    review_df = pd.read_csv(REVIEW_PATH, dtype=str, keep_default_na=False, encoding='utf-8')
    if 'label_manual' in review_df.columns and 'comment_text' in review_df.columns:
        review_neg = review_df[review_df['label_manual'].astype(str).str.strip().str.lower() == 'negatif'].copy()
        review_neg = review_neg[review_neg['comment_text'].astype(str).str.strip() != '']
        for _, row in review_neg.iterrows():
            cid = row.get('comment_id', '') or f'row{_}'
            sumber.append({'source': 'review', 'original_id': f'review_{cid}', 'original_text': row['comment_text']})
        print(f'Review manual (review_manual.csv): {len(review_neg)} komentar negatif ditemukan (dari {len(review_df)} total baris)')
    else:
        print(f'PERHATIAN: {REVIEW_PATH.name} ditemukan tapi kolom label_manual/comment_text tidak ada -> dilewati')
else:
    print(f'PERHATIAN: {REVIEW_PATH} tidak ditemukan -> dilewati')

sumber_df = pd.DataFrame(sumber).drop_duplicates(subset=['original_id']).reset_index(drop=True)
HASIL_DIR.mkdir(parents=True, exist_ok=True)
sumber_df.to_csv(OUT_PATH, index=False)

print(f'\nTotal komentar negatif digabung: {len(sumber_df)}')
print(f'Disimpan ke: {OUT_PATH}')
print(sumber_df.head(10).to_string())