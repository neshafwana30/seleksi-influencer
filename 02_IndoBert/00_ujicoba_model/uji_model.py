# Instal dulu di terminal VS Code:
# pip install torch transformers pyyaml

import unicodedata
import re

import yaml
import torch
from transformers import AutoTokenizer, AutoModelForSequenceClassification

MODEL = "Aardiiiiy/indobertweet-base-Indonesian-sentiment-analysis"
MAX_LENGTH = 512
TOLERANCE = 0.01
OUTPUT_FILE = "test_widget_lengkap.txt"

# Salin persis dari model card (blok widget saja)
WIDGET_YAML = """
widget:
  - text: >-
      Gapapa kalian gak tahu band Indo ini. Tapi jangan becanda. Karena mereka
      berani menyanyikan dengan lantang bagaimana aktivis ditikam, diracun,
      dikursilitrikkan, dan dibunuh di udara. Orang-orang yang berkorban nyawa
      supaya kalian menikmati hari ini sambil ngetwit tanpa khawatir
    example_title: Example 1
    output:
      - label: Negative
        score: 0.2964
      - label: Neutral
        score: 0.067
      - label: Positive
        score: 0.6969
  - text: >-
      Selama ada kelompok yg ingin jd mesias, selama itu jg govt punya
      justifikasi but bikin banyak aturan = celah korup/power abuse. Keadilan
      adalah deregulasi.
    example_title: Example 2
    output:
      - label: Negative
        score: 0.971
      - label: Neutral
        score: 0.0165
      - label: Positive
        score: 0.126
  - text: >-
      saat pendukungmu oke😹 gas ✌🏽oke😹 gas ✌🏽tapi kamu malah ketawa 🤣 itu
      ga respek 😠banget wok jangan lupa makan siang 😁geratisnya wok😋😹✌🏽
    example_title: Example 3
    output:
      - label: Negative
        score: 0.6457
      - label: Neutral
        score: 0.048
      - label: Positive
        score: 0.3063
  - text: >-
      Infoin loker wfh/freelance untuk mahasiswa dong, pengin bangget buat
      tambahan uang jajan di kos
    example_title: Example 4
    output:
      - label: Negative
        score: 0.0544
      - label: Neutral
        score: 0.6973
      - label: Positive
        score: 0.2482
  - text: >-
      Cari kerja sekarang tuh susah. Anaknya Presiden aja mesti dicariin kerjaan
      sama bapaknya
    example_title: Example 5
    output:
      - label: Negative
        score: 0.9852
      - label: Neutral
        score: 0.0116
      - label: Positive
        score: 0.0032
  - text: >-
      Komisi Penyiaran Indonesia (KPI) meminta agar tayangan televisi
      menampilkan citra positif Polri secara edukatif dan akurat. Hal ini
      disampaikan ketua KPI Pusat Ubaidillah dalam sebuah diskusi panel
    example_title: Example 6
    output:
      - label: Neutral
        score: 0.9932
      - label: Positive
        score: 0.0063
      - label: Negative
        score: 0.0005
  - text: >-
      Jgnkan tweet becandaan.. kadang tweet normal yg gue baca 'oh menarik' trs
      gue like/retweet, trs gue tinggal tidur, BESOKNYA ITU TWEET DIRUJAK. Gue
      jadi mikir, ini emang gue yang merasa semua hal menarik dan semua org bisa
      aja bener.. ATAU.. SEMUA ORANG jadi sensitif
    example_title: Example 7
    output:
      - label: Negative
        score: 0.5531
      - label: Neutral
        score: 0.4426
      - label: Positive
        score: 0.0043
"""

# ============================================================
# LOAD DATA & MODEL
# ============================================================
widget_data = yaml.safe_load(WIDGET_YAML)["widget"]
print(f"Jumlah contoh widget: {len(widget_data)}")

print("Loading model...")
tok = AutoTokenizer.from_pretrained(MODEL)
model = AutoModelForSequenceClassification.from_pretrained(MODEL).eval()
id2label = model.config.id2label
print("id2label:", id2label)
print("-" * 70)


def prediksi(teks):
    enc = tok(teks, return_tensors="pt", truncation=True, max_length=MAX_LENGTH)
    with torch.no_grad():
        p = model(**enc).logits.softmax(dim=-1)[0]
    return {id2label[i]: p[i].item() for i in range(len(p))}


def variasi(teks):
    return {
        "asli": teks,
        "NFKC": unicodedata.normalize("NFKC", teks),
        "spasi_rapi": re.sub(r"\s+", " ", teks).strip(),
    }


# ============================================================
# TEST
# ============================================================
baris_output = []
hitung_label_ok = 0
hitung_skor_ok = 0

for item in widget_data:
    nama = item["example_title"]
    teks = item["text"]
    widget = {o["label"]: o["score"] for o in item["output"]}
    label_widget = max(widget, key=widget.get)
    skor_widget = widget[label_widget]
    jumlah = sum(widget.values())
    n_token = len(tok(teks)["input_ids"])

    if abs(jumlah - 1.0) <= 0.005:
        cek_jumlah = "OK"
    else:
        cek_jumlah = f"TIDAK 1.0 (jumlah={jumlah:.4f}, kemungkinan typo di widget)"

    baris_output.append(
        f"{nama} | token={n_token} | widget: {label_widget} {skor_widget} | jumlah probabilitas: {cek_jumlah}"
    )

    for nama_var, t in variasi(teks).items():
        p = prediksi(t)
        label_lokal = max(p, key=p.get)
        skor_lokal = p[label_widget]

        label_ok = label_lokal == label_widget
        skor_ok = abs(skor_lokal - skor_widget) <= TOLERANCE
        if nama_var == "asli":
            hitung_label_ok += int(label_ok)
            hitung_skor_ok += int(skor_ok)

        detail = ", ".join(f"{k}={v:.4f}" for k, v in p.items())
        baris_output.append(
            f"   [{nama_var}] lokal {label_lokal} {skor_lokal:.4f} | "
            f"label {'OK' if label_ok else 'BEDA'} | skor {'OK' if skor_ok else 'BEDA'} | {detail}"
        )
    baris_output.append("")

total = len(widget_data)
baris_output.append("RINGKASAN (varian asli)")
baris_output.append(f"Label cocok : {hitung_label_ok}/{total}")
baris_output.append(f"Skor cocok  : {hitung_skor_ok}/{total} (toleransi {TOLERANCE})")
baris_output.append(f"Model       : {MODEL}")
baris_output.append(f"max_length  : {MAX_LENGTH}")

print("\n".join(baris_output))

with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
    f.write("\n".join(baris_output) + "\n")

print(f"\nHasil disimpan ke {OUTPUT_FILE}")