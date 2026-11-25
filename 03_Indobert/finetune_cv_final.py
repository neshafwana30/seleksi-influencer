#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fine-tune IndoBERTweet buat 2 skema (binary & 3class), diadaptasi dari 02_finetune_cv.ipynb
versi lama, disesuaikan sama data hasil gabung_final_training.py (03_Indobert/Data/<skema>/).

PERBEDAAN PENTING vs notebook lama:
  1. Baris sintesis/dummy TIDAK PUNYA kolom 'fold' (cuma di-assign sebelum ada augmentasi).
     Makanya di tiap fold CV, baris yang dijadiin HELD-OUT VALIDATION cuma baris 'asli'
     (pakai kolom fold yg asli), sedangkan baris sintesis/dummy SELALU masuk ke training di
     setiap fold -- supaya metrik CV ngukur kemampuan model di komentar ASLI, bukan di
     kalimat buatan sendiri.
  2. `test.csv` sekarang itu HOLD-OUT SUNGGUHAN yang udah dipisah dari awal (ga pernah
     disentuh augmentasi). Jadi CV (5-fold) di sini cuma buat development/tuning; hasil akhir
     yang dilaporkan di skripsi adalah SATU model final (dilatih pakai SELURUH train.csv)
     dievaluasi SEKALI ke test.csv -- bukan rata-rata 5 fold kaya notebook lama.
  3. class_weights dihitung ULANG dari distribusi train.csv yang baru (bukan pakai angka
     config.json lama yang udah obsolete krn datanya beda).

Model: indolem/indobertweet-base-uncased (sama kaya sebelumnya). max_len=56 (dipertahankan
biar kompatibel/comparable sama baseline S0/S1).

CATATAN SOAL TESTING: bagian yang murni logic (pemisahan fold, hitung class weight, mapping
label) sudah ditest lewat `test_finetune_cv_final.py` tanpa butuh GPU/download model. Bagian
yang beneran panggil IndoBERTweet (tokenizer + training) BELUM bisa ditest di sini karena
environment ini ga ada akses ke huggingface hub (network allowlist) dan ga ada GPU -- jadi
WAJIB kamu coba jalanin sendiri di mesin yang ada GPU + akses internet dulu sebelum dipakai
buat laporan beneran.
"""
import gc
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score, classification_report, confusion_matrix, f1_score

# ============================ KONFIGURASI ============================
SCHEMES = ['binary', '3class']
EPOCHS = 4
LR = 2e-5
BATCH_TRAIN = 16       # turunkan ke 8 kalau CUDA out of memory
BATCH_EVAL = 32
MAX_LEN = 56
WEIGHT_DECAY = 0.01
WARMUP_RATIO = 0.1
EARLY_STOP_PATIENCE = 2
VAL_SIZE_INTERNAL = 0.1   # dipecah dari bagian "remain" tiap fold, buat early stopping
RUN_CV = True              # False -> skip 5-fold CV, langsung ke model final doang (lebih cepet,
                           # tapi ga dapet metrik CV per fold -- cocok kalau CV-nya udah pernah
                           # dijalanin sebelumnya dan sekarang cuma mau liat train vs test)
SAVE_MODELS = False        # True -> simpan model fold terbaik + model final ke models/<skema>/
SEED = 42
MODEL_NAME = 'indolem/indobertweet-base-uncased'

LABEL2ID = {
    'binary': {'non-negatif': 0, 'negatif': 1},
    '3class': {'negatif': 0, 'netral': 1, 'positif': 2},
}
LABEL_COL = {'binary': 'label_name', '3class': 'label_final'}
# =====================================================================


def find_repo_root():
    current = Path.cwd()
    for _ in range(6):
        if (current / '01_pengumpulan_data').is_dir() and (current / '03_Indobert').is_dir():
            return current
        if current.parent == current:
            break
        current = current.parent
    return None


def load_train_test(repo_root, scheme):
    """Load train.csv & test.csv buat 1 skema, mapping label ke label_id (int), dan pastikan
    kolom 'fold' ada (string, blank buat baris non-asli)."""
    data_dir = repo_root / '03_Indobert' / 'Data' / scheme
    train_path = data_dir / 'train.csv'
    test_path = data_dir / 'test.csv'
    for p in (train_path, test_path):
        if not p.exists():
            raise FileNotFoundError(f'{p} tidak ditemukan. Jalankan gabung_final_training.py dulu.')

    train_df = pd.read_csv(train_path, dtype=str, keep_default_na=False, encoding='utf-8')
    test_df = pd.read_csv(test_path, dtype=str, keep_default_na=False, encoding='utf-8')

    label_col = LABEL_COL[scheme]
    label2id = LABEL2ID[scheme]

    for nama, df in [('train', train_df), ('test', test_df)]:
        required = {'text', 'sumber', label_col}
        missing = required - set(df.columns)
        if missing:
            raise ValueError(f'{scheme}/{nama}.csv kurang kolom: {missing}')
        tidak_dikenal = set(df[label_col].unique()) - set(label2id.keys())
        if tidak_dikenal:
            raise ValueError(f'{scheme}/{nama}.csv punya label tak dikenal: {tidak_dikenal}')
        df['label_id'] = df[label_col].map(label2id)

    if 'fold' not in train_df.columns:
        train_df['fold'] = ''

    return train_df, test_df


def compute_class_weights(label_ids, n_classes):
    """Formula 'balanced' standar: weight_c = n_samples / (n_classes * count_c).
    Kelas yg jarang (misal negatif) dapet bobot loss lebih besar."""
    label_ids = np.asarray(label_ids)
    counts = np.array([(label_ids == c).sum() for c in range(n_classes)])
    if (counts == 0).any():
        raise ValueError(f'Ada kelas dengan 0 anggota: counts={counts.tolist()}')
    n_samples = len(label_ids)
    weights = n_samples / (n_classes * counts)
    return weights


def split_fold_for_cv(df, fold_value, label_col='label_id', seed=42, val_size=VAL_SIZE_INTERNAL):
    """1 iterasi CV: pisahin held-out validation (cuma baris asli dgn fold == fold_value) dari
    sisanya (baris asli fold lain + SEMUA sintesis/dummy, yg lalu dipecah lagi 90/10 buat
    early-stopping internal).

    Return: (train_sub_df, val_internal_df, held_out_df)
    """
    held_out_mask = (df['sumber'] == 'asli') & (df['fold'] == str(fold_value))
    held_out_df = df[held_out_mask].reset_index(drop=True)
    remain_df = df[~held_out_mask].reset_index(drop=True)

    if len(held_out_df) == 0:
        raise ValueError(f'Ga ada baris asli dengan fold == {fold_value}. Cek kolom fold di train.csv.')

    train_sub_df, val_internal_df = train_test_split(
        remain_df, test_size=val_size, random_state=seed, stratify=remain_df[label_col],
    )
    return train_sub_df.reset_index(drop=True), val_internal_df.reset_index(drop=True), held_out_df


def available_folds(train_df):
    """Daftar nilai fold yg valid (cuma dari baris asli, exclude blank)."""
    folds = train_df.loc[train_df['sumber'] == 'asli', 'fold']
    folds = sorted({f for f in folds if f.strip() != ''}, key=int)
    return folds


# ====================== Bagian yang butuh torch/transformers ======================
# Import di sini (bukan di atas) biar logic di atas tetap bisa ditest tanpa torch terinstall.
def _lazy_import_ml():
    import torch
    from torch.utils.data import Dataset
    from transformers import (
        AutoModelForSequenceClassification, AutoTokenizer, EarlyStoppingCallback,
        Trainer, TrainingArguments,
    )
    return torch, Dataset, AutoModelForSequenceClassification, AutoTokenizer, EarlyStoppingCallback, Trainer, TrainingArguments


def build_dataset_class():
    torch, Dataset, *_ = _lazy_import_ml()

    class CommentDataset(Dataset):
        def __init__(self, texts, labels, tokenizer, max_len):
            enc = tokenizer(
                list(texts), truncation=True, padding='max_length',
                max_length=max_len, return_tensors='pt',
            )
            self.input_ids = enc['input_ids']
            self.attention_mask = enc['attention_mask']
            self.labels = torch.tensor(np.asarray(labels), dtype=torch.long)

        def __len__(self):
            return len(self.labels)

        def __getitem__(self, idx):
            return {
                'input_ids': self.input_ids[idx],
                'attention_mask': self.attention_mask[idx],
                'labels': self.labels[idx],
            }

    return CommentDataset


def build_weighted_trainer_class():
    torch, _, _, _, _, Trainer, _ = _lazy_import_ml()

    class WeightedTrainer(Trainer):
        def __init__(self, *args, class_weights=None, **kwargs):
            super().__init__(*args, **kwargs)
            self.class_weights = class_weights

        def compute_loss(self, model, inputs, return_outputs=False, **kwargs):
            labels = inputs.pop('labels')
            outputs = model(**inputs)
            logits = outputs.logits
            weight = self.class_weights.to(logits.device) if self.class_weights is not None else None
            loss_fct = torch.nn.CrossEntropyLoss(weight=weight)
            loss = loss_fct(logits.view(-1, model.config.num_labels), labels.view(-1))
            return (loss, outputs) if return_outputs else loss

    return WeightedTrainer


def make_compute_metrics(label_names):
    def compute_metrics(eval_pred):
        logits, labels = eval_pred
        preds = np.argmax(logits, axis=-1)
        out = {
            'accuracy': accuracy_score(labels, preds),
            'f1_macro': f1_score(labels, preds, average='macro', zero_division=0),
        }
        per_class = f1_score(labels, preds, average=None, zero_division=0,
                              labels=list(range(len(label_names))))
        for name, f1_val in zip(label_names, per_class):
            out[f'f1_{name}'] = f1_val
        return out
    return compute_metrics


def train_one_model(train_sub_df, val_df, label_names, class_weight_vec, tokenizer,
                     model_name, max_len, out_dir, seed_offset=0):
    """Latih 1 model IndoBERTweet. Return (trainer, model) biar bisa dipakai predict lagi."""
    torch, _, AutoModelForSequenceClassification, _, EarlyStoppingCallback, _, TrainingArguments = _lazy_import_ml()
    CommentDataset = build_dataset_class()
    WeightedTrainer = build_weighted_trainer_class()

    device = 'cuda' if torch.cuda.is_available() else 'cpu'
    n_labels = len(label_names)

    train_ds = CommentDataset(train_sub_df['text'], train_sub_df['label_id'], tokenizer, max_len)
    val_ds = CommentDataset(val_df['text'], val_df['label_id'], tokenizer, max_len)

    model = AutoModelForSequenceClassification.from_pretrained(model_name, num_labels=n_labels)

    total_steps = (len(train_sub_df) // BATCH_TRAIN + 1) * EPOCHS
    warmup_steps = int(total_steps * WARMUP_RATIO)
    args = TrainingArguments(
        output_dir=str(out_dir / 'checkpoints'),
        num_train_epochs=EPOCHS,
        per_device_train_batch_size=BATCH_TRAIN,
        per_device_eval_batch_size=BATCH_EVAL,
        learning_rate=LR,
        weight_decay=WEIGHT_DECAY,
        warmup_steps=warmup_steps,
        eval_strategy='epoch',
        save_strategy='epoch',
        save_total_limit=1,
        load_best_model_at_end=True,
        metric_for_best_model='f1_macro',
        greater_is_better=True,
        fp16=(device == 'cuda'),
        logging_steps=50,
        seed=SEED + seed_offset,
        report_to='none',
        disable_tqdm=False,
    )

    trainer = WeightedTrainer(
        model=model,
        args=args,
        train_dataset=train_ds,
        eval_dataset=val_ds,
        compute_metrics=make_compute_metrics(label_names),
        class_weights=class_weight_vec,
        callbacks=[EarlyStoppingCallback(early_stopping_patience=EARLY_STOP_PATIENCE)],
    )
    trainer.train()
    return trainer


def evaluate_on(trainer, eval_df, label_names, tokenizer, max_len):
    CommentDataset = build_dataset_class()
    ds = CommentDataset(eval_df['text'], eval_df['label_id'], tokenizer, max_len)
    pred_out = trainer.predict(ds)
    preds = np.argmax(pred_out.predictions, axis=-1)
    labels_true = eval_df['label_id'].astype(int).to_numpy()
    report = classification_report(labels_true, preds, target_names=label_names,
                                    output_dict=True, zero_division=0)
    cm = confusion_matrix(labels_true, preds, labels=list(range(len(label_names))))
    return report, cm.tolist()


def main():
    torch, *_ = _lazy_import_ml()

    REPO_ROOT = find_repo_root()
    if REPO_ROOT is None:
        raise FileNotFoundError(
            "Nggak ketemu folder root repo (butuh '01_pengumpulan_data' dan '03_Indobert' "
            "keduanya ada sebagai subfolder). Jalankan dari dalam folder repo."
        )

    MODELS_DIR = REPO_ROOT / '03_Indobert' / 'models'
    RESULTS_DIR = REPO_ROOT / '03_Indobert' / 'Data' / 'hasil_cv'
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    torch.manual_seed(SEED)
    np.random.seed(SEED)

    device = 'cuda' if torch.cuda.is_available() else 'cpu'
    print('Device:', device)
    if device == 'cuda':
        print('GPU   :', torch.cuda.get_device_name(0))
    else:
        print('PERHATIAN: tidak terdeteksi GPU. Training akan sangat lambat di CPU.')

    _, _, AutoModelForSequenceClassification, AutoTokenizer, _, _, _ = _lazy_import_ml()

    all_fold_results = []
    all_fold_reports = {}
    final_results = {}

    for scheme in SCHEMES:
        label2id = LABEL2ID[scheme]
        label_names = [name for name, _ in sorted(label2id.items(), key=lambda kv: kv[1])]
        n_labels = len(label_names)

        train_df, test_df = load_train_test(REPO_ROOT, scheme)
        tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)

        print(f'\n{"=" * 70}\n  SKEMA: {scheme}  ({n_labels} kelas)\n{"=" * 70}')
        print(f'  train total: {len(train_df)}  (asli={len(train_df[train_df.sumber == "asli"])}, '
              f'sintesis/dummy={len(train_df[train_df.sumber != "asli"])})')
        print(f'  test total : {len(test_df)}  (semua asli, hold-out sungguhan)')

        folds = available_folds(train_df)
        print(f'  fold tersedia (dari baris asli): {folds}')

        # ------------------------- CV (buat development/tuning) -------------------------
        if not RUN_CV:
            print('  RUN_CV=False -> skip 5-fold CV, langsung ke model final.')
        for fold in (folds if RUN_CV else []):
            t0 = time.time()
            train_sub_df, val_internal_df, held_out_df = split_fold_for_cv(
                train_df, fold, seed=SEED + int(fold),
            )
            class_weight_vec_np = compute_class_weights(train_sub_df['label_id'], n_labels)
            class_weight_vec = torch.tensor(class_weight_vec_np, dtype=torch.float)

            out_dir = MODELS_DIR / scheme / f'cv_fold_{fold}'
            trainer = train_one_model(
                train_sub_df, val_internal_df, label_names, class_weight_vec, tokenizer,
                MODEL_NAME, MAX_LEN, out_dir, seed_offset=int(fold),
            )

            report, cm = evaluate_on(trainer, held_out_df, label_names, tokenizer, MAX_LEN)
            row = {
                'scheme': scheme, 'fold': fold,
                'n_train': len(train_sub_df), 'n_val_internal': len(val_internal_df),
                'n_held_out_asli': len(held_out_df),
                'accuracy': report['accuracy'], 'f1_macro': report['macro avg']['f1-score'],
            }
            for name in label_names:
                row[f'f1_{name}'] = report[name]['f1-score']
                row[f'precision_{name}'] = report[name]['precision']
                row[f'recall_{name}'] = report[name]['recall']
            all_fold_results.append(row)
            all_fold_reports[(scheme, fold)] = {
                'report': report, 'confusion_matrix': cm, 'label_names': label_names,
            }
            print(f'  [fold {fold}] f1_macro={row["f1_macro"]:.3f} | '
                  + ' | '.join(f'f1_{n}={row[f"f1_{n}"]:.3f}' for n in label_names)
                  + f' | waktu={(time.time() - t0) / 60:.1f} menit')

            if SAVE_MODELS:
                out_dir.mkdir(parents=True, exist_ok=True)
                trainer.save_model(str(out_dir))
                tokenizer.save_pretrained(str(out_dir))

            del trainer
            gc.collect()
            if device == 'cuda':
                torch.cuda.empty_cache()

        # ------------------------- Model FINAL (evaluasi sekali ke test.csv) -------------------------
        print(f'\n  --- Melatih model FINAL {scheme} (seluruh train.csv) ---')
        class_weight_vec_np = compute_class_weights(train_df['label_id'], n_labels)
        class_weight_vec = torch.tensor(class_weight_vec_np, dtype=torch.float)

        train_sub_df, val_internal_df = train_test_split(
            train_df, test_size=VAL_SIZE_INTERNAL, random_state=SEED, stratify=train_df['label_id'],
        )
        out_dir_final = MODELS_DIR / scheme / 'final'
        trainer_final = train_one_model(
            train_sub_df.reset_index(drop=True), val_internal_df.reset_index(drop=True),
            label_names, class_weight_vec, tokenizer, MODEL_NAME, MAX_LEN, out_dir_final,
        )
        report_test, cm_test = evaluate_on(trainer_final, test_df, label_names, tokenizer, MAX_LEN)
        print(f'  [FINAL -> test.csv] accuracy={report_test["accuracy"]:.3f} '
              f'f1_macro={report_test["macro avg"]["f1-score"]:.3f}')
        for name in label_names:
            print(f'    f1_{name} = {report_test[name]["f1-score"]:.3f}')

        # Evaluasi juga ke TRAIN penuh -- buat bandingin train vs test (cek overfitting:
        # kalau train jauh lebih tinggi drpd test, model ngapalin training set).
        print(f'  --- Evaluasi model final ke TRAIN penuh (cek overfitting) ---')
        report_train, cm_train = evaluate_on(trainer_final, train_df, label_names, tokenizer, MAX_LEN)
        print(f'  [FINAL -> train.csv] accuracy={report_train["accuracy"]:.3f} '
              f'f1_macro={report_train["macro avg"]["f1-score"]:.3f}')

        final_results[scheme] = {
            'label_names': label_names,
            'train': {'report': report_train, 'confusion_matrix': cm_train},
            'test': {'report': report_test, 'confusion_matrix': cm_test},
        }

        if SAVE_MODELS:
            out_dir_final.mkdir(parents=True, exist_ok=True)
            trainer_final.save_model(str(out_dir_final))
            tokenizer.save_pretrained(str(out_dir_final))

        del trainer_final, tokenizer
        gc.collect()
        if device == 'cuda':
            torch.cuda.empty_cache()

    # ------------------------- Simpan hasil -------------------------
    if all_fold_results:
        results_df = pd.DataFrame(all_fold_results)
        results_df.to_csv(RESULTS_DIR / 'cv_results_per_fold.csv', index=False)

        metric_cols = [c for c in results_df.columns if c.startswith(('accuracy', 'f1_', 'precision_', 'recall_'))]
        summary = results_df.groupby('scheme')[metric_cols].agg(['mean', 'std'])
        summary.to_csv(RESULTS_DIR / 'cv_results_summary.csv')
    else:
        print('RUN_CV=False -> ga ada hasil CV yg disimpan (cv_results_per_fold.csv/summary.csv dilewat).')

    with open(RESULTS_DIR / 'cv_reports_detail.json', 'w', encoding='utf-8') as f:
        json.dump({f'{s}_fold{i}': v for (s, i), v in all_fold_reports.items()}, f, indent=2, ensure_ascii=False)

    with open(RESULTS_DIR / 'final_train_vs_test.json', 'w', encoding='utf-8') as f:
        json.dump(final_results, f, indent=2, ensure_ascii=False)

    print('\nTersimpan di 03_Indobert/Data/hasil_cv/:')
    print(' - cv_results_per_fold.csv     (metrik CV per fold, held-out cuma dari baris asli)')
    print(' - cv_results_summary.csv      (mean ± std per skema, dari CV)')
    print(' - cv_reports_detail.json      (classification_report + confusion matrix lengkap per fold)')
    print(' - final_train_vs_test.json    (HASIL AKHIR: model full-train dievaluasi ke train.csv DAN test.csv,')
    print('                                buat dibandingin lewat tampilkan_laporan_perbandingan.py)')


if __name__ == '__main__':
    main()