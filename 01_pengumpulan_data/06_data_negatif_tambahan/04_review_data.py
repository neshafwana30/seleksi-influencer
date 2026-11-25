#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Patch label_manual di review_manual.csv buat 4 komentar yang dikoreksi jadi bukan_negatif.
Update-only: cuma ganti nilai di baris yang cocok comment_id-nya, baris lain tidak disentuh.
Backup dibuat dulu (1 file .bak, ditimpa tiap patch, bukan numpuk) sebelum nulis ulang."""
import shutil
from pathlib import Path

import pandas as pd

def find_repo_root():
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
    raise FileNotFoundError('Nggak ketemu folder root repo. Jalankan dari dalam folder repo.')

REVIEW_PATH = REPO_ROOT / '01_pengumpulan_data' / '06_data_negatif_tambahan' / 'hasil' / 'review_manual.csv'

# comment_id -> label baru (hasil diskusi koreksi)
KOREKSI = {
    'review_7592739646564500242'.replace('review_', ''): 'bukan_negatif',  # "Knp pabriknya ga ttup aja si..."
    'review_7592645460424098578'.replace('review_', ''): 'bukan_negatif',  # "gila jahat banget"
    'review_7594711847862829845'.replace('review_', ''): 'bukan_negatif',  # "Cerita tak full...koma...intubate..."
    'review_7652223662414201620'.replace('review_', ''): 'bukan_negatif',  # comment soal Messi (OOT)
}

if not REVIEW_PATH.exists():
    raise FileNotFoundError(f'{REVIEW_PATH} tidak ditemukan')

df = pd.read_csv(REVIEW_PATH, dtype=str, keep_default_na=False, encoding='utf-8')
if 'comment_id' not in df.columns or 'label_manual' not in df.columns:
    raise ValueError(f'Kolom comment_id/label_manual tidak ada. Kolom yang ada: {list(df.columns)}')

df['comment_id'] = df['comment_id'].astype(str)

changed = []
not_found = []
for cid, new_label in KOREKSI.items():
    mask = df['comment_id'] == cid
    if mask.sum() == 0:
        not_found.append(cid)
        continue
    old_labels = df.loc[mask, 'label_manual'].tolist()
    df.loc[mask, 'label_manual'] = new_label
    changed.append((cid, old_labels, new_label))

print('--- Ringkasan patch review_manual.csv ---')
for cid, old_labels, new_label in changed:
    print(f'  comment_id={cid}: label_manual {old_labels} -> {new_label!r}')
if not_found:
    print(f'  PERHATIAN: comment_id berikut TIDAK DITEMUKAN di file (dilewati, cek lagi manual): {not_found}')

if changed:
    # backup 1 file (ditimpa tiap kali, bukan numpuk)
    bak_path = REVIEW_PATH.with_name(f'{REVIEW_PATH.name}.bak')
    shutil.copy2(REVIEW_PATH, bak_path)
    df.to_csv(REVIEW_PATH, index=False, encoding='utf-8')
    print(f'\n{len(changed)} baris diupdate. Backup disimpan ke: {bak_path.name}')
else:
    print('\nTidak ada baris yang diubah (semua comment_id tidak ditemukan atau KOREKSI kosong).')