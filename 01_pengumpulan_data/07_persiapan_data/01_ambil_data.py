#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Patch label di dataset_final.csv (02_IndoBert/data/siap_latih/dataset_final.csv)
buat 4 komentar yang dikoreksi jadi bukan negatif.

Dicocokkan pakai TEKS PERSIS di kolom comment_text_clean (bukan posisi/index),
biar presisi walau urutan baris berubah.

Kolom yang diubah (cuma utk 4 baris yang cocok):
  - label_final : "negatif" -> "netral"   (skema 3-kelas, hasil resolusi akhir)
  - label_name  : "negatif" -> "non-negatif"  (skema binary)
  - label       : 1 -> 0                  (versi numerik dari label_name)

Kolom yang TIDAK disentuh: label_shafwa, label_khalila (label individu annotator),
label_source, split, fold, dan baris lain yang tidak cocok.

Update-only: baris lain tidak disentuh. Backup 1 file (.bak, ditimpa tiap patch,
bukan numpuk) dibuat sebelum nulis ulang."""
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

DATASET_PATH = REPO_ROOT / '01_pengumpulan_data/' / '07_persiapan_data' / 'hasil' / 'dataset_final.csv'

TEXT_COL = 'comment_text_clean'

# Teks persis (strip whitespace di awal/akhir) yang mau dikoreksi -> (label_final baru)
KOREKSI_TEKS = {
    "ngeri bgtt": "netral",
    "Liat yt nya sampek abis kak, jangan cuman asal komen di tiktok yg setengah2, ini pasien nya di yt disebut kok nama dokternya dan rs nya, dan dia juga bawak bukti ctscen nya!!": "netral",
    "Nauzubillah tapi aku gak percaya dia pelakunya sedih ya Alloh kenapa harus begitu": "netral",
}
# Komentar ke-4 ("...trust issue ke lambung...") dicocokkan pakai substring unik,
# karena di teks aslinya ada variasi tanda baca kutip yang bisa beda-beda pas disalin.
KOREKSI_SUBSTRING_UNIK = "trust issue ke lambung"
LABEL_FINAL_BARU_SUBSTRING = "netral"

LABEL_NAME_BARU = "non-negatif"
LABEL_NUMERIK_BARU = 0

if not DATASET_PATH.exists():
    raise FileNotFoundError(f'{DATASET_PATH} tidak ditemukan')

df = pd.read_csv(DATASET_PATH, dtype=str, keep_default_na=False, encoding='utf-8')

required_cols = {TEXT_COL, 'label_final', 'label_name', 'label'}
missing = required_cols - set(df.columns)
if missing:
    raise ValueError(f'Kolom berikut tidak ada di file: {missing}. Kolom yang ada: {list(df.columns)}')

changed_log = []
not_found = []


def apply_patch(mask, new_label_final, deskripsi):
    if mask.sum() == 0:
        not_found.append(deskripsi)
        return
    old_final = df.loc[mask, 'label_final'].tolist()
    old_name = df.loc[mask, 'label_name'].tolist()
    old_num = df.loc[mask, 'label'].tolist()
    df.loc[mask, 'label_final'] = new_label_final
    df.loc[mask, 'label_name'] = LABEL_NAME_BARU
    df.loc[mask, 'label'] = str(LABEL_NUMERIK_BARU)
    changed_log.append((deskripsi, old_final, old_name, old_num))


for teks, new_label_final in KOREKSI_TEKS.items():
    mask = df[TEXT_COL].astype(str).str.strip() == teks.strip()
    apply_patch(mask, new_label_final, teks[:50])

mask_sub = df[TEXT_COL].astype(str).str.contains(KOREKSI_SUBSTRING_UNIK, na=False)
apply_patch(mask_sub, LABEL_FINAL_BARU_SUBSTRING, f'(substring) {KOREKSI_SUBSTRING_UNIK}')

print('--- Ringkasan patch dataset_final.csv ---')
for deskripsi, old_final, old_name, old_num in changed_log:
    print(f'  "{deskripsi}...":')
    print(f'      label_final {old_final} -> netral')
    print(f'      label_name  {old_name} -> {LABEL_NAME_BARU!r}')
    print(f'      label       {old_num} -> {LABEL_NUMERIK_BARU}')
if not_found:
    print(f'  PERHATIAN: berikut TIDAK DITEMUKAN (dilewati, cek manual): {not_found}')

if changed_log:
    bak_path = DATASET_PATH.with_name(f'{DATASET_PATH.name}.bak')
    shutil.copy2(DATASET_PATH, bak_path)
    df.to_csv(DATASET_PATH, index=False, encoding='utf-8')
    print(f'\n{len(changed_log)} baris diupdate. Backup disimpan ke: {bak_path.name}')
else:
    print('\nTidak ada baris yang diubah.')