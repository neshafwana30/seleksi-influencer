import os
import polars as pl
import ollama
from ddgs import DDGS

def search_internet_for_doctor(query):
    """Mencari informasi tambahan di internet untuk akun yang meragukan"""
    print(f"   🌐 [Internet Search] Verifikasi akun di web: {query}...")
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(f"{query} kesehatan tiktok indonesia", max_results=3))
            if not results:
                return "Tidak ditemukan informasi relevan di internet."
            
            snippets = [f"- {r['title']}: {r['body']}" for r in results]
            return "\n".join(snippets)
    except Exception as e:
        return f"Gagal akses internet: {e}"


def check_bio_hybrid(signature, display_name):
    text_to_check = f"Name: {display_name} | Bio: {signature}"
    
    # PROMPT STAGE 1: Perluasan Indikator Valid & Aturan Bio Singkat
    prompt_stage1 = f"""
    Tugas: Evaluasi profil media sosial berikut untuk memfilter akun EDUKATOR / KREATOR KONTEN KESEHATAN INDONESIA.

    KRITERIA VALID (DILOLOSKAN):
    1. INDIVIDUAL & TIM: Dokter, nakes, mahasiswa kedokteran, edukator kesehatan, health creator, atau tim/kolaborasi edukasi kesehatan (misal: "dokter rame-rame", tim edukasi nakes).
    2. HOST / CREATOR HEALTH: Kreator/Host yang fokus pada konten kesehatan, wellness, skincare, atau Health & Personal Care.
    3. GAYA BAHASA & HUMOR: Menggunakan bahasa gaul, humor lokal, istilah "doklip", "dok", "kadang dokter kadang pasien", dll.
    4. Merek/link skincare, endorse, atau reservasi klinik pribadi TETAP VALID.

    KRITERIA RAGU (WAJIB LEMPAR KE GOOGLE / JAWAB "RAGU"):
    1. Bio sangat singkat yang HANYA berisi kontak bisnis/endorse/email/WA (contoh: "Business inquiry: email@gmail.com").
    2. Nama/Bio menggunakan bahasa Inggris standar tanpa penjelasan spesifik apakah dia kreator asal Indonesia.

    KRITERIA TIDAK VALID (LANGSUNG DITOLAK):
    1. INSTITUSI / PERUSAHAAN MURNI: Rumah sakit, klinik, PT/CV, instansi pemerintah (contoh: Kemenkes RI), atau brand/pabrik produk.
    2. BAHASA ASING NON-INGGRIS: Bio 100% menggunakan bahasa Arab, Spanyol, Mandarin, dll. TANPA ada unsur Indonesia/lokal.
    3. TOKO ONLINE MURNI: Akun jualan baju, makanan, judi online, atau olshop non-kesehatan yang tidak punya unsur edukasi/kreator.

    PILIH SALAH SATU KEPUTUSAN:
    - "VALID" : Jika yakin ini edukator/kreator kesehatan/host personal care Indonesia.
    - "TIDAK VALID" : Jika murni instansi/perusahaan, toko online non-kreator, atau akun luar negeri.
    - "RAGU" : Jika bionya hanya kontak endorse/bisnis singkat, atau belum jelas konteks Indonesianya.

    Format Balasan (WAJIB PERSIS):
    KEPUTUSAN: [VALID / TIDAK VALID / RAGU]
    ALASAN: [1 kalimat singkat]

    Profil: "{text_to_check}"
    """

    try:
        # Step 1: Cek Lokal via Ollama
        response = ollama.chat(
            model='llama3.1',
            messages=[{'role': 'user', 'content': prompt_stage1}],
            options={'temperature': 0.0}
        )
        content = response['message']['content'].strip()
        
        status = "TIDAK VALID"
        if "KEPUTUSAN: VALID" in content.upper():
            status = "VALID"
        elif "KEPUTUSAN: RAGU" in content.upper():
            status = "RAGU"

        reason = content.split("ALASAN:")[-1].strip() if "ALASAN:" in content.upper() else content

        # Step 2: Cek Internet Khusus yang "RAGU"
        if status == "RAGU":
            search_context = search_internet_for_doctor(display_name)
            
            prompt_stage2 = f"""
            Tugas: Tentukan apakah profil berikut milik Edukator / Nakes / Health Creator Indonesia berdasarkan data internet.

            Profil: "{text_to_check}"
            Hasil Google:
            {search_context}

            Aturan:
            - Jika dari hasil pencarian terbukti dia adalah kreator kesehatan/host/edukator/dokter Indonesia, jawab "VALID".
            - Jika ternyata akun toko murni non-kreator, instansi/perusahaan, atau dari luar negeri, jawab "TIDAK VALID".

            Format Balasan:
            KEPUTUSAN: [VALID / TIDAK VALID]
            ALASAN: [1 kalimat berdasarkan data internet]
            """

            response_web = ollama.chat(
                model='llama3.1',
                messages=[{'role': 'user', 'content': prompt_stage2}],
                options={'temperature': 0.0}
            )
            content_web = response_web['message']['content'].strip()
            
            is_valid_final = "VALID" in content_web.upper() and "TIDAK VALID" not in content_web.upper()
            web_reason = content_web.split("ALASAN:")[-1].strip() if "ALASAN:" in content_web.upper() else content_web
            
            return is_valid_final, f"[Cek Web] {web_reason}", "INTERNET_VERIFIED"

        return (status == "VALID"), reason, "LOCAL_DIRECT"

    except Exception as e:
        return False, f"Error system: {e}", "ERROR"


def filter_valid_influencers(input_csv, final_csv):
    print("🎬 === MEMULAI PENYARINGAN HYBRID (PERLUASAN EDUKATOR + WEB SEARCH) ===")

    if not os.path.exists(input_csv):
        print(f"❌ File input tidak ditemukan: {input_csv}")
        return

    df = pl.read_csv(
        input_csv,
        infer_schema=False,
        truncate_ragged_lines=True,
        ignore_errors=True,
    )

    gagal_text = "Gagal ditarik / Kemungkinan Private Ketat"
    df_valid = df.filter(
        (pl.col("unique_id") != gagal_text)
        & (pl.col("scraping_status").str.contains("(?i)public"))
    )

    df_valid = df_valid.with_columns(
        pl.col("num_followers").str.replace_all(r"\D", "").cast(pl.Int64, strict=False).fill_null(0)
    ).filter(pl.col("num_followers") >= 10000)

    print(f"📊 Total akun masuk kualifikasi awal: {df_valid.height} baris.")

    ai_results = []
    ai_reasons = []
    method_used = []

    signatures = df_valid["signature"].to_list()
    display_names = df_valid["nickname"].to_list()

    for i in range(len(df_valid)):
        sig = signatures[i]
        name = display_names[i]
        
        is_valid, reason, method = check_bio_hybrid(sig, name)
        
        ai_results.append("PASSED_AI" if is_valid else "REJECTED_AI")
        ai_reasons.append(reason)
        method_used.append(method)

        if (i + 1) % 10 == 0 or (i + 1) == len(df_valid):
            print(f"   Terproses {i + 1}/{len(df_valid)} akun...")

    df_final = df_valid.with_columns(
        [
            pl.Series("final_status", ai_results),
            pl.Series("ai_reason", ai_reasons),
            pl.Series("check_method", method_used)
        ]
    )

    df_final.write_csv(final_csv)
    print(f"\n💾 Penyaringan selesai! File disimpan ke: {final_csv}")


if __name__ == "__main__":
    file_mentah = r"01_pengumpulan_data\01_scrapping_informasi_akun\metadata_akun_raw.csv"
    file_final = r"01_pengumpulan_data\02_filter_akun_folls_like_bio\metadata_akun_filtered_1.csv"

    filter_valid_influencers(file_mentah, file_final)