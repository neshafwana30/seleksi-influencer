import os
import polars as pl

def extract_passed_influencers(input_audit_csv, output_clean_csv):
    print("🎬 === MEMPROSES DATA AKUN PASSED ONLY ===")

    if not os.path.exists(input_audit_csv):
        print(f"❌ File input tidak ditemukan: {input_audit_csv}")
        return

    # Read data dari file audit hasil filter AI
    df = pl.read_csv(
        input_audit_csv,
        infer_schema=False,
        truncate_ragged_lines=True,
        ignore_errors=True,
    )

    total_audit = df.height
    print(f"📊 Total akun di file audit: {total_audit} baris")

    # Filter hanya yang lolos AI (PASSED_AI)
    df_passed = df.filter(pl.col("final_status") == "PASSED_AI")

    # Buang kolom-kolom evaluasi AI
    columns_to_drop = ["is_valid_ai", "ai_reason", "final_status"]
    df_clean = df_passed.drop([col for col in columns_to_drop if col in df_passed.columns])

    print(f"✅ Total akun lolos kualifikasi murni: {df_clean.height} baris")

    # Simpan ke CSV bersih
    df_clean.write_csv(output_clean_csv)
    print(f"💾 File berhasil disimpan ke: {output_clean_csv}")
    print("=========================================\n")


if __name__ == "__main__":
    file_audit = r"01_pengumpulan_data\02_filter_akun_folls_like_bio\metadata_akun_filtered_reasoning.csv"
    file_clean = r"01_pengumpulan_data\02_filter_akun_folls_like_bio\metadata_akun_passed_final.csv"

    extract_passed_influencers(file_audit, file_clean)