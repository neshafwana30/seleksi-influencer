#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Split dataset_final.csv jadi train (80%) / test (20%), stratified berdasarkan label_final (3 kelas).
Split ini dipakai bareng buat 2 eksperimen (3 kelas & binary) karena label_name (binary) itu
penyederhanaan dari label_final, jadi proporsinya otomatis ikut terjaga.

Di dalam train, ditambah assignment fold (default 5-fold CV, StratifiedKFold berdasarkan
label_final) buat dipakai di 02_finetune_cv.ipynb.

OPSI 2 (sesuai keputusan): split & fold SELURUH baris (lama + baru) dihitung ulang dari nol,
bukan cuma nambahin ke baris baru. Backup 1 file (.bak, ditimpa tiap jalan) dibuat dulu
sebelum nulis ulang.

Random seed di-fix (RANDOM_SEED) biar reproducible -- catat angka ini di metodologi skripsi.
"""
import shutil
from pathlib import Path

import pandas as pd
from sklearn.model_selection import StratifiedKFold, train_test_split

RANDOM_SEED = 42
TEST_SIZE = 0.2
N_FOLDS = 5


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

if not DATASET_PATH.exists():
    raise FileNotFoundError(f'{DATASET_PATH} tidak ditemukan')

df = pd.read_csv(DATASET_PATH, dtype=str, keep_default_na=False, encoding='utf-8')

if 'label_final' not in df.columns:
    raise ValueError('Kolom label_final tidak ada di dataset_final.csv')
if 'label_name' not in df.columns:
    raise ValueError('Kolom label_name tidak ada di dataset_final.csv')

# --- Split train/test, stratified by label_final ---
train_idx, test_idx = train_test_split(
    df.index,
    test_size=TEST_SIZE,
    stratify=df['label_final'],
    random_state=RANDOM_SEED,
)

df['split'] = ''
df.loc[train_idx, 'split'] = 'train'
df.loc[test_idx, 'split'] = 'test'

# --- Fold assignment (CV), cuma di dalam train, stratified by label_final ---
df['fold'] = ''
df_train = df.loc[train_idx]

min_class_count_train = df_train['label_final'].value_counts().min()
effective_folds = min(N_FOLDS, int(min_class_count_train))
if effective_folds < N_FOLDS:
    print(f'PERHATIAN: kelas terkecil di train cuma ada {min_class_count_train} anggota, '
          f'jumlah fold diturunkan dari {N_FOLDS} jadi {effective_folds} biar tiap fold tetap valid.')
if effective_folds < 2:
    raise ValueError('Kelas terkecil di train kurang dari 2 anggota, CV ga bisa dijalankan. '
                      'Cek lagi datanya.')

skf = StratifiedKFold(n_splits=effective_folds, shuffle=True, random_state=RANDOM_SEED)
for fold_num, (_, val_idx_in_train) in enumerate(skf.split(df_train.index, df_train['label_final']), start=1):
    actual_idx = df_train.index[val_idx_in_train]
    df.loc[actual_idx, 'fold'] = str(fold_num)

# --- Simpan ---
bak_path = DATASET_PATH.with_name(f'{DATASET_PATH.name}.bak')
shutil.copy2(DATASET_PATH, bak_path)
df.to_csv(DATASET_PATH, index=False, encoding='utf-8')


# --- Report ---
def laporan_proporsi(sub_df, nama_kolom, judul):
    print(f'\n{judul}')
    counts = sub_df[nama_kolom].value_counts()
    total = len(sub_df)
    for label, jumlah in counts.items():
        pct = 100 * jumlah / total if total else 0
        print(f'  {label:14s} : {jumlah:5d}  ({pct:5.1f}%)')
    print(f'  {"TOTAL":14s} : {total:5d}')


print('=' * 60)
print('LAPORAN HASIL SPLIT')
print('=' * 60)
print(f'Total komentar           : {len(df)}')
print(f'Train (latihan + CV)     : {len(train_idx)}  ({100 * len(train_idx) / len(df):.1f}%)')
print(f'Test (ujian akhir)       : {len(test_idx)}  ({100 * len(test_idx) / len(df):.1f}%)')
print(f'Random seed              : {RANDOM_SEED}  (catat ini di metodologi, biar reproducible)')
print(f'Jumlah fold CV           : {effective_folds}')

laporan_proporsi(df[df['split'] == 'train'], 'label_final', '--- Proporsi label_final (3 KELAS) di TRAIN ---')
laporan_proporsi(df[df['split'] == 'test'], 'label_final', '--- Proporsi label_final (3 KELAS) di TEST ---')
laporan_proporsi(df[df['split'] == 'train'], 'label_name', '--- Proporsi label_name (BINARY) di TRAIN ---')
laporan_proporsi(df[df['split'] == 'test'], 'label_name', '--- Proporsi label_name (BINARY) di TEST ---')

print('\n--- Jumlah baris per fold (di dalam TRAIN) ---')
fold_counts = df[df['split'] == 'train']['fold'].value_counts().sort_index()
for fold_num, jumlah in fold_counts.items():
    print(f'  fold {fold_num} : {jumlah}')

print(f'\nDisimpan ke: {DATASET_PATH}')
print(f'Backup disimpan ke: {bak_path.name}')