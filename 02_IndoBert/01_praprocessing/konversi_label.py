#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Konversi label 3 kelas -> 2 kelas pada labels_shafwa.csv

  negatif  -> negatif
  netral   -> non-negatif
  positif  -> non-negatif

- Label asli disimpan di kolom label_3kelas
- File asli dibackup dulu ke labels_shafwa_backup_3kelas.csv (tidak menimpa backup yang sudah ada)
- Kolom model_label (jawaban model yang sudah terlihat) ikut dikonversi
- Aman dijalankan ulang: kalau kolom label_3kelas sudah ada, skrip berhenti tanpa mengubah apa pun
- Format kolom dan nama kolom sama dengan migrasi otomatis di labeling_final.py

Cara pakai (jalankan dari folder yang berisi 02_IndoBert):
    python konversi_label_2kelas.py
    python konversi_label_2kelas.py "02_IndoBert\\labeling\\labels_shafwa.csv"
"""

import shutil
import sys
from pathlib import Path

import pandas as pd

DEFAULT_PATH = Path("02_IndoBert") / "labeling" / "labels_shafwa.csv"

COL_LABEL = "label_manual"
COL_LABEL_3K = "label_3kelas"
COL_MODEL_LABEL = "model_label"


def main():
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PATH
    if not path.exists():
        print(f"File tidak ditemukan: {path.resolve()}")
        print("Jalankan dari folder yang berisi 02_IndoBert, atau beri path lengkap sebagai argumen.")
        sys.exit(1)

    df = pd.read_csv(path, dtype=str, encoding="utf-8")

    if COL_LABEL not in df.columns:
        print(f"Kolom '{COL_LABEL}' tidak ada. Kolom yang ada: {list(df.columns)}")
        sys.exit(1)

    if COL_LABEL_3K in df.columns:
        print(f"Kolom '{COL_LABEL_3K}' sudah ada: file ini sudah dikonversi. Tidak ada yang diubah.")
        sys.exit(0)

    lama = df[COL_LABEL].fillna("")
    valid = {"", "negatif", "netral", "positif"}
    aneh = sorted(set(lama) - valid)
    if aneh:
        print(f"Ada nilai label tak dikenal: {aneh}. Berhenti, tidak ada yang diubah.")
        sys.exit(1)

    backup = path.with_name(path.stem + "_backup_3kelas.csv")
    if not backup.exists():
        shutil.copy2(path, backup)
        print(f"Backup dibuat: {backup}")
    else:
        print(f"Backup sudah ada, tidak ditimpa: {backup}")

    df[COL_LABEL_3K] = lama
    jadi_non = lama.isin(["netral", "positif"])
    df.loc[jadi_non, COL_LABEL] = "non-negatif"

    if COL_MODEL_LABEL in df.columns:
        df[COL_MODEL_LABEL] = df[COL_MODEL_LABEL].fillna("").replace(
            {"netral": "non-negatif", "positif": "non-negatif"})

    df.to_csv(path, index=False, encoding="utf-8")

    print("\nRingkasan (sebelum -> sesudah):")
    print(f"  negatif     : {(lama == 'negatif').sum()} -> {(df[COL_LABEL] == 'negatif').sum()}")
    print(f"  netral      : {(lama == 'netral').sum()} -> 0")
    print(f"  positif     : {(lama == 'positif').sum()} -> 0")
    print(f"  non-negatif : 0 -> {(df[COL_LABEL] == 'non-negatif').sum()}")
    print(f"  belum dilabel: {(lama == '').sum()} (tidak berubah)")
    print(f"\nSelesai: {path}")


if __name__ == "__main__":
    main()
    