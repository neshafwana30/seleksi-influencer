#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Hitung sebaran hasil prediksi model (negatif / netral / positif) dari prediksi_model.csv.
Juga menampilkan perbandingan pemetaan 2 kelas opsi A dan B (hanya untuk dilihat, tidak mengubah file).

Cara pakai (dari folder yang berisi 02_IndoBert):
    python hitung_prediksi_model.py
    python hitung_prediksi_model.py "02_IndoBert\\01_praprocessing\\hasil\\prediksi_model.csv"
"""

import sys
from pathlib import Path

import pandas as pd

DEFAULT_PATH = Path("02_IndoBert") / "01_praprocessing" / "hasil" / "prediksi_model.csv"
KELAS = ["negatif", "netral", "positif"]


def tampil(judul, seri, total):
    print(judul)
    for k, n in seri.items():
        print(f"  {k:<12}: {n:>7,}  ({n / total * 100:5.1f}%)")
    print(f"  {'total':<12}: {total:>7,}\n")


def main():
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PATH
    if not path.exists():
        print(f"File tidak ditemukan: {path.resolve()}")
        sys.exit(1)

    df = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8")
    perlu = ["model_label", "p_negatif", "p_netral", "p_positif"]
    hilang = [c for c in perlu if c not in df.columns]
    if hilang:
        print(f"Kolom hilang: {hilang}. Kolom yang ada: {list(df.columns)}")
        sys.exit(1)

    total = len(df)
    tampil("Prediksi model 3 kelas (model_label):",
           df["model_label"].value_counts().reindex(KELAS, fill_value=0), total)

    p_neg = df["p_negatif"].astype(float)
    p_non = df["p_netral"].astype(float) + df["p_positif"].astype(float)

    # A: argmax 3 kelas lalu digabung
    a = df["model_label"].where(df["model_label"] == "negatif", "non-negatif")
    # B: p_negatif dibanding p_netral + p_positif
    b = pd.Series("non-negatif", index=df.index).where(p_neg <= p_non, "negatif")

    kelas2 = ["negatif", "non-negatif"]
    tampil("Pemetaan 2 kelas opsi A (argmax lalu gabung):", a.value_counts().reindex(kelas2, fill_value=0), total)
    tampil("Pemetaan 2 kelas opsi B (p_negatif vs p_netral + p_positif):", b.value_counts().reindex(kelas2, fill_value=0), total)
    print(f"Komentar yang labelnya beda antara opsi A dan B: {(a != b).sum():,} ({(a != b).mean() * 100:.1f}%)")


if __name__ == "__main__":
    main()