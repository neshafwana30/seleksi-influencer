#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Gabungin komentar negatif baru (dari review_manual.csv, label_manual == 'negatif')
ke dataset_final.csv. Baris yang source="baseline" di komentar_negatif_gabungan.csv
DI-SKIP karena udah ada di dataset_final.csv (duplikat) -- cuma dipakai buat cross-check
jumlah, bukan buat sumber baris baru.

Baris baru diisi sesuai hasil diskusi:
  - label_shafwa, label_khalila, label_final, label_name  -> "negatif"
  - label (numerik)                                        -> 1
  - label_source                                            -> "review_manual"
  - comment_id                                              -> diisi (kolom baru, blank utk baris lama)
  - split, fold                                             -> dibiarkan kosong (diisi pas tahap split nanti)
  - comment_text_clean, text_model -> dibersihkan dari URL & mention (@user) sesuai
    aturan di keputusan_metodologi.md (emoji dibiarkan, ga ada stemming/normalisasi slang)

Update-only: baris lama di dataset_final.csv TIDAK diubah/dihapus, cuma ditambah baris baru.
Backup 1 file (.bak, ditimpa tiap jalan, bukan numpuk) dibuat sebelum nulis ulang."""
import re
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

DATASET_PATH = REPO_ROOT / '01_pengumpulan_data' / '07_persiapan_data' / 'hasil' / 'dataset_final.csv'
GABUNGAN_PATH = REPO_ROOT / '01_pengumpulan_data' / '07_persiapan_data' / 'hasil' / 'komentar_negatif_gabungan.csv'
REVIEW_MANUAL_PATH = REPO_ROOT / '01_pengumpulan_data' / '06_data_negatif_tambahan' / 'hasil' / 'review_manual.csv'

URL_PATTERN = re.compile(r'(https?://\S+|www\.\S+)', re.IGNORECASE)
MENTION_PATTERN = re.compile(r'@\w+')


def clean_text(text: str) -> str:
    """Sesuai keputusan_metodologi.md: hapus URL & mention (tanpa diganti token),
    emoji dibiarkan, ga ada stemming/stopword/normalisasi slang."""
    text = str(text)
    text = URL_PATTERN.sub('', text)
    text = MENTION_PATTERN.sub('', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


if not DATASET_PATH.exists():
    raise FileNotFoundError(f'{DATASET_PATH} tidak ditemukan')
if not REVIEW_MANUAL_PATH.exists():
    raise FileNotFoundError(f'{REVIEW_MANUAL_PATH} tidak ditemukan')

df_dataset = pd.read_csv(DATASET_PATH, dtype=str, keep_default_na=False, encoding='utf-8')
df_review = pd.read_csv(REVIEW_MANUAL_PATH, dtype=str, keep_default_na=False, encoding='utf-8')

required_review_cols = {'comment_id', 'username', 'video_id', 'comment_text', 'label_manual'}
missing = required_review_cols - set(df_review.columns)
if missing:
    raise ValueError(f'Kolom berikut tidak ada di review_manual.csv: {missing}')

# Tambah kolom comment_id ke dataset_final.csv kalau belum ada (blank utk baris lama)
if 'comment_id' not in df_dataset.columns:
    df_dataset['comment_id'] = ''

# Ambil cuma yang label_manual == 'negatif'
df_review_negatif = df_review[df_review['label_manual'] == 'negatif'].copy()

# Cross-check (opsional) ke komentar_negatif_gabungan.csv kalau filenya ada
if GABUNGAN_PATH.exists():
    df_gab = pd.read_csv(GABUNGAN_PATH, dtype=str, keep_default_na=False, encoding='utf-8')
    n_review_di_gabungan = (df_gab['source'] == 'review').sum() if 'source' in df_gab.columns else None
    if n_review_di_gabungan is not None and n_review_di_gabungan != len(df_review_negatif):
        print(f'  PERHATIAN: jumlah review negatif di review_manual.csv ({len(df_review_negatif)}) '
              f'beda sama jumlah source=review di komentar_negatif_gabungan.csv ({n_review_di_gabungan}). '
              f'Cek lagi, bisa jadi salah satu file belum ter-update.')
else:
    print(f'  (info: {GABUNGAN_PATH.name} tidak ditemukan, skip cross-check jumlah)')

# Skip comment_id yang udah pernah ditambahin sebelumnya (biar aman kalau script dijalanin 2x)
existing_ids = set(df_dataset.loc[df_dataset['comment_id'] != '', 'comment_id'].astype(str))
df_review_negatif = df_review_negatif[~df_review_negatif['comment_id'].astype(str).isin(existing_ids)]

if len(df_review_negatif) == 0:
    print('\nTidak ada baris baru yang perlu ditambahkan (semua comment_id sudah ada / tidak ada yang negatif).')
else:
    baris_baru = pd.DataFrame({
        'username': df_review_negatif['username'],
        'video_id': df_review_negatif['video_id'],
        'comment_text_raw': df_review_negatif['comment_text'],
        'comment_text_clean': df_review_negatif['comment_text'].apply(clean_text),
        'label_shafwa': 'negatif',
        'label_khalila': 'negatif',
        'label_final': 'negatif',
        'label_source': 'review_manual',
        'label_name': 'negatif',
        'label': '1',
        'split': '',
        'fold': '',
        'comment_id': df_review_negatif['comment_id'],
    })
    baris_baru['text_model'] = baris_baru['comment_text_clean']

    # Samakan urutan kolom dengan dataset_final.csv (kolom yang mungkin ga ada di baris_baru diisi blank)
    for col in df_dataset.columns:
        if col not in baris_baru.columns:
            baris_baru[col] = ''
    baris_baru = baris_baru[df_dataset.columns]

    df_gabung = pd.concat([df_dataset, baris_baru], ignore_index=True)

    bak_path = DATASET_PATH.with_name(f'{DATASET_PATH.name}.bak')
    shutil.copy2(DATASET_PATH, bak_path)
    df_gabung.to_csv(DATASET_PATH, index=False, encoding='utf-8')

    print(f'\nBaris lama: {len(df_dataset)}')
    print(f'Baris baru ditambahkan: {len(baris_baru)}')
    print(f'Total setelah gabung: {len(df_gabung)}')
    print(f'Disimpan ke: {DATASET_PATH}')
    print(f'Backup disimpan ke: {bak_path.name}')