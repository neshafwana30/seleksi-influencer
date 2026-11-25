#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
prediksi_model.py
=================
Jalankan model sentimen IndoBERTweet pada sampel_labeling.csv, lalu simpan prediksinya
di file TERPISAH (hasil/prediksi_model.csv). File ini dibaca aplikasi labeling untuk
menampilkan "jawaban model" SETELAH kamu memilih label. Jawaban tidak pernah ditulis
ke file labelmu sebelum kamu memilih.

Model : Aardiiiiy/indobertweet-base-Indonesian-sentiment-analysis
Label : id2label dari config model = {0: Negative, 1: Neutral, 2: Positive}

Cara pakai (sekali saja, sebelum mulai labeling):
    pip install torch transformers pandas
    python prediksi_model.py

Opsi:
    --model NAMA_ATAU_FOLDER   ganti model (default: model di atas)
    --batch 32                 ukuran batch (turunkan kalau RAM/VRAM kurang)
    --max-len 128              panjang maksimum token (komentar TikTok pendek)

Catatan:
    - Teks yang dipakai: kolom comment_text_clean (sama dengan yang nanti dipakai pipeline).
    - Prediksi 2.000 komentar di CPU biasanya hitungan menit.
"""

import argparse
import sys
from pathlib import Path

import pandas as pd

MODEL_NAME = "Aardiiiiy/indobertweet-base-Indonesian-sentiment-analysis"

# Isi HANYA kalau id2label model tidak dikenali (script akan berhenti dan memberi tahu).
# Contoh: MANUAL_MAP = {0: "negatif", 1: "netral", 2: "positif"}
MANUAL_MAP = None

NAMA_LABEL = {  # nama di config model -> nama di aplikasi labeling
    "negative": "negatif", "neutral": "netral", "positive": "positif",
    "negatif": "negatif", "netral": "netral", "positif": "positif",
}


def find_base_path():
    """Cari folder 02_IndoBert (sama seperti labeling.py)."""
    current = Path(__file__).resolve().parent
    for _ in range(5):
        if (current / "02_IndoBert").is_dir():
            return current / "02_IndoBert"
        if current.name == "02_IndoBert":
            return current
        if current.parent == current:
            break
        current = current.parent
    return Path(__file__).resolve().parent.parent / "02_IndoBert"


BASE_PATH = find_base_path()
DATA_FILE = BASE_PATH / "01_praprocessing" / "hasil" / "sampel_labeling.csv"
OUT_FILE = BASE_PATH / "01_praprocessing" / "hasil" / "prediksi_model.csv"


def petakan_label(id2label):
    """Ubah id2label model jadi {id: negatif/netral/positif}. Berhenti kalau tidak jelas."""
    if MANUAL_MAP:
        return {int(k): v for k, v in MANUAL_MAP.items()}
    hasil = {}
    for k, v in id2label.items():
        nama = NAMA_LABEL.get(str(v).strip().lower())
        if nama is None:
            raise SystemExit(
                f"id2label model tidak dikenali: {id2label}\n"
                "Isi MANUAL_MAP di atas file ini, contoh {0: 'negatif', 1: 'netral', 2: 'positif'}"
            )
        hasil[int(k)] = nama
    if sorted(hasil.values()) != ["negatif", "netral", "positif"]:
        raise SystemExit(f"Label model harus tepat 3 kelas berbeda, tapi dapat: {hasil}")
    return hasil


def prediksi(texts, tokenizer, model, device, batch, max_len):
    import torch

    semua = []
    model.eval()
    with torch.no_grad():
        for i in range(0, len(texts), batch):
            potong = texts[i:i + batch]
            enc = tokenizer(potong, padding=True, truncation=True,
                            max_length=max_len, return_tensors="pt").to(device)
            probs = torch.softmax(model(**enc).logits, dim=-1).cpu().tolist()
            semua.extend(probs)
            print(f"  {min(i + batch, len(texts)):,}/{len(texts):,}", end="\r")
    print()
    return semua


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=MODEL_NAME)
    ap.add_argument("--batch", type=int, default=32)
    ap.add_argument("--max-len", type=int, default=128)
    args = ap.parse_args()

    if not DATA_FILE.exists():
        raise SystemExit(f"File sampel tidak ditemukan: {DATA_FILE}")
    df = pd.read_csv(DATA_FILE, dtype=str, keep_default_na=False, encoding="utf-8")
    if "comment_text_clean" not in df.columns:
        raise SystemExit(f"Kolom comment_text_clean tidak ada. Kolom: {list(df.columns)}")
    texts = df["comment_text_clean"].tolist()
    print(f"Sampel: {len(texts):,} komentar | model: {args.model}")

    import torch
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"Device: {device}")
    tokenizer = AutoTokenizer.from_pretrained(args.model)
    model = AutoModelForSequenceClassification.from_pretrained(args.model).to(device)

    id2nama = petakan_label(model.config.id2label)
    print("Pemetaan label:", {k: id2nama[k] for k in sorted(id2nama)})

    probs = prediksi(texts, tokenizer, model, device, args.batch, args.max_len)

    out = pd.DataFrame({"row_id": range(len(df)), "comment_text_clean": texts})
    for idx, nama in id2nama.items():
        out[f"p_{nama}"] = [round(p[idx], 4) for p in probs]
    kolom_p = [f"p_{n}" for n in ("negatif", "netral", "positif")]
    out["model_label"] = out[kolom_p].idxmax(axis=1).str.replace("p_", "", regex=False)
    out["model_conf"] = out[kolom_p].max(axis=1)

    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(OUT_FILE, index=False, encoding="utf-8")
    print(f"\nTersimpan: {OUT_FILE}")
    print("Sebaran prediksi model:")
    print(out["model_label"].value_counts().to_string())


if __name__ == "__main__":
    sys.exit(main())
    