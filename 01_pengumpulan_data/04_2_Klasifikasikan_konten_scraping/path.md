# 🎯 SCRIPTS DENGAN PATH YANG BENAR

## ✅ FILES YANG SUDAH DIBIKIN (GUNAKAN YANG "_FIXED" VERSION)

```
DOWNLOAD DARI OUTPUT FOLDER INI:

✅ classify_health_content_FIXED.py  ← Gunakan INI! (bukan yang lama)
✅ prepare_sample_FIXED.py           ← Gunakan INI! (bukan yang lama)
✅ label_app.py                      ← Pakai yang ini (path sudah standard local)
✅ validate_accuracy.py              ← Pakai yang ini (path sudah standard local)
✅ PATH_STRUCTURE.md                 ← Dokumentasi path
✅ LABELING_WORKFLOW.md              ← Dokumentasi workflow
```

---

## 📁 FOLDER STRUCTURE

```
01_pengumpulan_data/
│
├── 04_1_mendapatkan_metadata_akun/
│   └── metadata_video.csv          ← INPUT KAMU (3090+ videos)
│
└── 04_2_Klasifikasikan_konten_scraping/  ← COPY SEMUA SCRIPT KE SINI
    ├── classify_health_content_FIXED.py
    ├── prepare_sample_FIXED.py
    ├── label_app.py
    ├── validate_accuracy.py
    │
    └── (Output files akan di-generate di sini)
        ├── video_classified.csv
        ├── health_contents_only.csv
        ├── videos_to_label.csv
        ├── labeled_videos.csv
        ├── accuracy_report.json
        └── confusion_matrix.csv
```

---

## ⚡ QUICK START (3 STEPS)

### STEP 1: Copy Scripts
```bash
# Download 4 files dari folder outputs:
- classify_health_content_FIXED.py
- prepare_sample_FIXED.py
- label_app.py
- validate_accuracy.py

# Copy ke folder:
01_pengumpulan_data/04_2_Klasifikasikan_konten_scraping/
```

### STEP 2: Run Scripts
```bash
# Navigate ke working directory
cd 01_pengumpulan_data/04_2_Klasifikasikan_konten_scraping/

# Run dalam order:
python prepare_sample_FIXED.py          # Extract 1000 sample
python classify_health_content_FIXED.py  # Classify all videos
python label_app.py                      # Manual labeling
python validate_accuracy.py              # Check accuracy
```

### STEP 3: Check Output
```bash
# Verify files di folder 04_2:
video_classified.csv         ← Semua videos dengan label
health_contents_only.csv     ← Hanya health (untuk scraping komentar)
labeled_videos.csv          ← Manual labels (dari labeling app)
accuracy_report.json        ← Accuracy metrics
```

---

## 🔑 KEY DIFFERENCES (FIXED vs LAMA)

### classify_health_content_FIXED.py
```python
# FIXED VERSION - Path otomatis dari relative location
BASE_DIR = Path(__file__).parent
INPUT_DIR = BASE_DIR.parent / "04_1_mendapatkan_metadata_akun"
CONFIG = {
    'input_file': str(INPUT_DIR / 'metadata_video.csv'),
    'output_file': str(OUTPUT_DIR / 'video_classified.csv'),
}
```

### prepare_sample_FIXED.py
```python
# FIXED VERSION - Path otomatis
BASE_DIR = Path(__file__).parent
INPUT_DIR = BASE_DIR.parent / "04_1_mendapatkan_metadata_akun"
CONFIG = {
    'input_file': str(INPUT_DIR / 'metadata_video.csv'),
    'output_file': str(OUTPUT_DIR / 'videos_to_label.csv'),
}
```

### label_app.py & validate_accuracy.py
```python
# Standard version - uses local files
# Tidak perlu ganti path, semua files di folder yang sama
CONFIG = {
    'input_file': 'videos_to_label.csv',     # Local
    'output_file': 'labeled_videos.csv',     # Local
}
```

---

## ✅ VERIFICATION CHECKLIST

Sebelum run, pastikan:

- [ ] Input file ada: `01_pengumpulan_data/04_1_mendapatkan_metadata_akun/metadata_video.csv`
- [ ] File punya columns: username, video_id, video_url, description
- [ ] Folder target ada: `01_pengumpulan_data/04_2_Klasifikasikan_konten_scraping/`
- [ ] Script di-download yang versi "_FIXED": 
  - [ ] classify_health_content_FIXED.py
  - [ ] prepare_sample_FIXED.py
- [ ] Dependencies installed: `pip install pandas ollama flask`
- [ ] Ollama running: `ollama serve` (di terminal lain)
- [ ] Ollama model ready: `ollama pull qwen2.5:7b` (jika belum ada)

---

## 🚀 COMPLETE WORKFLOW

```
1. PREPARE SAMPLE
   python prepare_sample_FIXED.py
   ✅ Output: videos_to_label.csv (1000 videos untuk dilabel)
   ⏱️ Time: 1 menit

2. CLASSIFY ALL VIDEOS
   python classify_health_content_FIXED.py
   ✅ Output: video_classified.csv (semua 3090+ videos dengan label)
   ⏱️ Time: 2-3 jam

3. MANUAL LABELING (Validation)
   python label_app.py
   ✅ Output: labeled_videos.csv (1000 manual labels)
   ⏱️ Time: 2-3 jam (dapat dilakukan bertahap)

4. VALIDATE MODEL
   python validate_accuracy.py
   ✅ Output: accuracy_report.json (akurasi model)
   ⏱️ Time: 1 menit

5. DECISION
   Accuracy ≥ 85%? → Continue with model
   Accuracy < 85%? → Improve model and retry
```

---

## 📊 OUTPUT FILES EXPLAINED

### video_classified.csv (MAIN OUTPUT)
```
Columns:
- username: @ali, @dinna, etc
- video_id: TikTok video ID
- video_url: TikTok URL
- description: Video description
- is_health: True/False (model prediction)
- confidence: 0.0-1.0 (model confidence)
- method: ollama_success / error / empty
- classified_at: Timestamp
```

### health_contents_only.csv (UNTUK SCRAPING KOMENTAR)
```
Same as above, tapi hanya rows where is_health=True
→ Gunakan ini untuk scraping comments di step berikutnya
→ ~60-70% dari total videos (2000-2500 videos)
```

### labeled_videos.csv (DARI MANUAL LABELING)
```
Columns:
- username, video_id, video_url, description
- manual_label: 'health' atau 'not_health' (dari user)
- label_timestamp: Waktu labeling
```

### accuracy_report.json (VALIDATION RESULT)
```json
{
  "metrics": {
    "accuracy": 0.873,
    "precision": 0.892,
    "recall": 0.851,
    "f1": 0.871
  },
  "confusion_matrix": {
    "TP": 850,
    "FP": 60,
    "FN": 50,
    "TN": 40
  }
}
```

---

## 🛠️ TROUBLESHOOTING

### "File not found: metadata_video.csv"
```
✅ Solution:
- Check file exists: 01_pengumpulan_data/04_1_mendapatkan_metadata_akun/metadata_video.csv
- Run script dari: 01_pengumpulan_data/04_2_Klasifikasikan_konten_scraping/
- Jika tetap error, check PATH_STRUCTURE.md
```

### "ModuleNotFoundError: No module named 'flask'"
```
✅ Solution:
pip install flask pandas
```

### "Ollama not running"
```
✅ Solution:
# Terminal lain, jalankan:
ollama serve
```

### "Model qwen2.5:7b not found"
```
✅ Solution:
ollama pull qwen2.5:7b
```

---

## 📞 SUMMARY

| Task | Script | Input | Output |
|------|--------|-------|--------|
| Extract sample | prepare_sample_FIXED.py | metadata_video.csv | videos_to_label.csv |
| Classify all | classify_health_content_FIXED.py | metadata_video.csv | video_classified.csv |
| Manual label | label_app.py | videos_to_label.csv | labeled_videos.csv |
| Validate | validate_accuracy.py | labeled_videos.csv + video_classified.csv | accuracy_report.json |

---

## ✨ PENTING!

✅ **Gunakan FIXED versions** untuk classify_health_content dan prepare_sample  
✅ **Semua script di folder 04_2** (working directory)  
✅ **Input dari folder 04_1** (metadata_video.csv)  
✅ **Output ke folder 04_2** (semua generated files)  

Good luck! 🚀
