#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Ambil komentar NEGATIF dan POSITIF yang ada di TRAIN (hasil split_data.py) buat dibikin
sintesis paraphrase -- karena berdua sama-sama minoritas dibanding netral.
Baris TEST sengaja DI-SKIP, biar paraphrase ga "bocor" ke data ujian.

Kenapa 1 file gabungan, bukan 2 file beda:
  - negatif  : minoritas di SKEMA 3 KELAS maupun BINARY (negatif ya negatif di kedua skema)
               -> paraphrase negatif dipake di 3 kelas & binary.
  - positif  : minoritas cuma di SKEMA 3 KELAS (positif vs netral). Di BINARY, positif gabung
               jadi "non-negatif" bareng netral yang udah dominan, jadi paraphrase positif
               GA PERLU dipake di eksperimen binary.
Makanya tiap baris ditandain kelas_asli (positif/negatif) + catatan_skema (kemana paraphrase
ini relevan dipakai), biar pas bikin dataset training per eksperimen gampang filter-nya.

Read-only terhadap dataset_final.csv (cuma bikin file baru, ga nulis ulang/nimpa apapun)."""
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

DATASET_PATH = REPO_ROOT / '01_pengumpulan_data' / '07_persiapan_data' / 'hasil' / 'dataset_final.csv'
OUT_PATH = REPO_ROOT / '01_pengumpulan_data' / '07_persiapan_data' / 'hasil' / 'minoritas_train_untuk_sintesis.csv'

CATATAN_SKEMA = {
    'negatif': '3 kelas & binary',
    'positif': '3 kelas saja',
}

if not DATASET_PATH.exists():
    raise FileNotFoundError(f'{DATASET_PATH} tidak ditemukan.')

df = pd.read_csv(DATASET_PATH, dtype=str, keep_default_na=False, encoding='utf-8')

required = {'split', 'label_final', 'comment_text_clean'}
missing = required - set(df.columns)
if missing:
    raise ValueError(f'Kolom berikut tidak ada: {missing}')

if not (df['split'] == 'train').any():
    raise ValueError('Kolom split belum keisi ("train"/"test"). Jalankan split_data.py dulu sebelum ini.')

df_minoritas = df[(df['split'] == 'train') & (df['label_final'].isin(['negatif', 'positif']))].copy()

if len(df_minoritas) == 0:
    raise ValueError('Ga ada baris negatif/positif di train. Cek lagi nilai label_final/split-nya.')

cols_keep = [c for c in ['comment_id', 'username', 'video_id', 'comment_text_clean', 'label_source']
             if c in df_minoritas.columns]
df_out = df_minoritas[cols_keep + ['label_final']].reset_index(drop=True)
df_out = df_out.rename(columns={'label_final': 'kelas_asli'})
df_out['catatan_skema'] = df_out['kelas_asli'].map(CATATAN_SKEMA)
df_out.insert(0, 'row_num', df_out.index)

df_out.to_csv(OUT_PATH, index=False, encoding='utf-8')

print('Jumlah per kelas di TRAIN:')
print(df_out['kelas_asli'].value_counts().to_string())
if 'label_source' in df_out.columns:
    print('\nAsal baris (label_source) per kelas:')
    print(df_out.groupby(['kelas_asli', 'label_source']).size().to_string())
print(f'\nDisimpan ke: {OUT_PATH}')
print('\n--> Kirim file ini (attachment) biar bisa dibikinin sintesis paraphrase-nya.')