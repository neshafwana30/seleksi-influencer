#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tampilin laporan hasil evaluasi IndoBERTweet secara rapi, dari file
`03_Indobert/Data/hasil_cv/final_train_vs_test.json` (hasil finetune_cv_final.py).

Nampilin buat tiap skema (binary & 3class):
  1. Tabel precision/recall/F1-score/support per kelas (ini yg dimaksud "kaya confusion
     matrix tapi ada precision/recall/F1" -- fungsi python-nya classification_report dari
     sklearn, di sini cuma dirapiin jadi tabel) -- SISI BERDAMPINGAN train vs test, biar
     langsung kelihatan beda performanya.
  2. Confusion matrix asli (jumlah prediksi x label asli) buat train & test.
  3. Ringkasan gap accuracy/F1-macro train vs test -- gap besar = sinyal overfitting.

Bisa dipanggil langsung (baca dari path default) atau import function-nya buat dipakai di
notebook/script lain.
"""
import json
from pathlib import Path

import pandas as pd


def find_repo_root():
    current = Path.cwd()
    for _ in range(6):
        if (current / '01_pengumpulan_data').is_dir() and (current / '03_Indobert').is_dir():
            return current
        if current.parent == current:
            break
        current = current.parent
    return None


def muat_hasil(path):
    if not path.exists():
        raise FileNotFoundError(
            f'{path} tidak ditemukan. Jalankan finetune_cv_final.py dulu sampai selesai.'
        )
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def tabel_classification_report(report, label_names):
    """report = dict hasil sklearn.metrics.classification_report(..., output_dict=True).
    Dirapiin jadi DataFrame: baris = kelas (+ accuracy/macro avg/weighted avg),
    kolom = precision/recall/f1-score/support."""
    baris = []
    for name in label_names:
        r = report[name]
        baris.append({
            'kelas': name,
            'precision': r['precision'],
            'recall': r['recall'],
            'f1-score': r['f1-score'],
            'support': int(r['support']),
        })
    for ringkasan_key, label_tampil in [('macro avg', 'macro avg'), ('weighted avg', 'weighted avg')]:
        r = report[ringkasan_key]
        baris.append({
            'kelas': label_tampil,
            'precision': r['precision'],
            'recall': r['recall'],
            'f1-score': r['f1-score'],
            'support': int(r['support']),
        })
    baris.append({
        'kelas': 'accuracy', 'precision': '', 'recall': '', 'f1-score': report['accuracy'],
        'support': int(report['macro avg']['support']),
    })
    df = pd.DataFrame(baris).set_index('kelas')
    return df


def tabel_perbandingan_train_test(report_train, report_test, label_names):
    """Precision/recall/F1 per kelas, train & test berdampingan, plus kolom gap (test - train)
    buat F1 -- gap negatif besar = overfitting (test jauh lebih jelek drpd train)."""
    baris = []
    for name in label_names + ['macro avg', 'weighted avg']:
        rt, rv = report_train[name], report_test[name]
        baris.append({
            'kelas': name,
            'precision_train': rt['precision'], 'precision_test': rv['precision'],
            'recall_train': rt['recall'], 'recall_test': rv['recall'],
            'f1_train': rt['f1-score'], 'f1_test': rv['f1-score'],
            'gap_f1 (test - train)': rv['f1-score'] - rt['f1-score'],
        })
    baris.append({
        'kelas': 'accuracy',
        'precision_train': '', 'precision_test': '', 'recall_train': '', 'recall_test': '',
        'f1_train': report_train['accuracy'], 'f1_test': report_test['accuracy'],
        'gap_f1 (test - train)': report_test['accuracy'] - report_train['accuracy'],
    })
    df = pd.DataFrame(baris).set_index('kelas')
    return df


def tabel_confusion_matrix(cm, label_names):
    """cm = list of list (hasil sklearn confusion_matrix). Baris = label asli, kolom = prediksi."""
    idx = [f'asli: {n}' for n in label_names]
    cols = [f'prediksi: {n}' for n in label_names]
    return pd.DataFrame(cm, index=idx, columns=cols)


def cetak_laporan_skema(scheme, data, float_fmt='{:.3f}'):
    label_names = data['label_names']
    report_train = data['train']['report']
    report_test = data['test']['report']
    cm_train = data['train']['confusion_matrix']
    cm_test = data['test']['confusion_matrix']

    pd.set_option('display.float_format', float_fmt.format)

    print('\n' + '=' * 70)
    print(f'SKEMA: {scheme.upper()}')
    print('=' * 70)

    print('\n-- Classification report TRAIN --')
    print(tabel_classification_report(report_train, label_names).to_string())

    print('\n-- Classification report TEST --')
    print(tabel_classification_report(report_test, label_names).to_string())

    print('\n-- Perbandingan TRAIN vs TEST (gap F1 negatif besar = overfitting) --')
    print(tabel_perbandingan_train_test(report_train, report_test, label_names).to_string())

    print('\n-- Confusion matrix TRAIN --')
    print(tabel_confusion_matrix(cm_train, label_names).to_string())

    print('\n-- Confusion matrix TEST --')
    print(tabel_confusion_matrix(cm_test, label_names).to_string())

    gap_acc = report_test['accuracy'] - report_train['accuracy']
    gap_f1 = report_test['macro avg']['f1-score'] - report_train['macro avg']['f1-score']
    print(f'\nGap accuracy (test - train): {gap_acc:+.3f}')
    print(f'Gap F1-macro (test - train): {gap_f1:+.3f}')
    if gap_f1 < -0.1:
        print('  -> PERHATIAN: gap > 0.1, kemungkinan model overfit ke training set.')
    else:
        print('  -> gap wajar, model keliatan generalize dengan oke.')


def main():
    REPO_ROOT = find_repo_root()
    if REPO_ROOT is None:
        raise FileNotFoundError(
            "Nggak ketemu folder root repo (butuh '01_pengumpulan_data' dan '03_Indobert' "
            "keduanya ada sebagai subfolder). Jalankan dari dalam folder repo."
        )
    path = REPO_ROOT / '03_Indobert' / 'Data' / 'hasil_cv' / 'final_train_vs_test.json'
    hasil = muat_hasil(path)
    for scheme, data in hasil.items():
        cetak_laporan_skema(scheme, data)


if __name__ == '__main__':
    main()
