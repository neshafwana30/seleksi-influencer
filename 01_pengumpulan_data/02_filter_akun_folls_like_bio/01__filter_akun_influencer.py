import os
import polars as pl
import ollama
from ddgs import DDGS

# ==============================================================================
# MEMORI LOKAL / KNOWLEDGE BASE INFLUENCER KESEHATAN INDONESIA
# (Bisa kamu tambah sewaktu-waktu tanpa perlu akses internet)
# ==============================================================================
# ==============================================================================
# MEMORI LOKAL / KNOWLEDGE BASE INFLUENCER KESEHATAN INDONESIA (CURATED)
# ==============================================================================
HEALTH_INFLUENCERS_MEMORY = [
    # --- Dokter Umum & Edukator Populer ---
    "tirta", "tirtacipeng", "dr. tirta",
    "ayman", "aymanalts", "dr. ayman",
    "clarin hayes", "clarinhayes", "dr. clarin",
    "kevin mak", "drkevinmak", "dr. kevin",
    "gia pratama", "giapratamamd", "dr. gia",
    "alip hildan", "doklip", "qonita", "doklip universe",
    "asa ibrahim", "dr. asa",
    
    # --- Skincare & Estetika (Medical) ---
    "zie", "drzie", "yessicatania", "dr. yessica",
    "richard lee", "dr.richard_lee", "dr. richard",
    "danar", "dr_danar", "danar wicaksono",
    "kamila jaidi", "dr. kamila",
    "ekles", "dr_ekles", "dr. ekles",
    "abelina", "abelina_md", "dr. abelina",
    "kamilah", "dr. kamilah",
    
    # --- Gizi, Diet & Lifestyle ---
    "dion haryadi", "dionharyadi", "dr. dion",
    "hans tandra", "dr. hans",
    "santi", "susanti", "dr. santi",

    # --- Dokter Spesialis Kandungan (ObGyn) & Seksologi ---
    "boyke", "wishdrboyke", "drboyke",
    "yassin bintang", "dr. yassin",
    "amira", "amiradokter", "dr. amira",
    "nisa fathoni", "dr. nisa",
    "boy abidin", "dr. boy abidin",

    # --- Dokter Spesialis Anak (Pediatric) ---
    "mesty", "mestyariotedjo", "mesty ariotedjo",
    "meta hanindita", "dr. meta",
    "citra amelinda", "dr. citra",
    "k.s. diniari", "dr. dini",

    # --- Penyakit Dalam & Spesialis Lainnya ---
    "decsa", "dokterdecsa", "decsa medika",
    "vito damay", "vitodamay", "dr. vito",
    "ikeda", "dr ikeda", "ikedaputri",
    
    # --- Nakes Lainnya (Perawat, Bidan, Apoteker) ---
    "rizal do", "afrumed", "ners rizal",
    "bidan novel", "bidannovel",
    "apt", "apoteker" # Kata kunci umum pendukung
]

def is_in_health_memory(display_name, signature):
    """Fungsi cocokologi dengan memori lokal"""
    text_combined = f"{display_name} {signature}".lower()
    for key in HEALTH_INFLUENCERS_MEMORY:
        if key in text_combined:
            return True, f"Terdeteksi di memori lokal sebagai influencer kesehatan terpercaya ({key})."
    return False, ""


def search_internet_targeted(query):
    """Pencarian Google terarah khusus kualifikasi influencer kesehatan TikTok"""
    search_query = f'"{query}" influencer edukator kesehatan tiktok indonesia'
    print(f"   🌐 [Google Search] Cocokologi web: {search_query}...")
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(search_query, max_results=3))
            if not results:
                # Retry dengan query lebih fleksibel jika query pertama kosong
                results = list(ddgs.text(f"{query} dokter edukator kesehatan indonesia", max_results=3))
            
            if not results:
                return "Tidak ditemukan data pencarian relevan di internet."
            
            snippets = [f"- {r['title']}: {r['body']}" for r in results]
            return "\n".join(snippets)
    except Exception as e:
        return f"Gagal akses internet: {e}"


def check_bio_hybrid(signature, display_name):
    text_to_check = f"Name: {display_name} | Bio: {signature}"
    
    # 1. CEK MEMORI LOKAL TERLEBIH DAHULU (FAST PATH)
    in_memory, memory_reason = is_in_health_memory(display_name, signature)
    if in_memory:
        return True, memory_reason, "MEMORY_MATCH"

    # 2. CEK BIO KOSONG
    if not signature or str(signature).strip() in ("", '""', "null", "None"):
        return False, "Bio kosong atau informasi terlalu minim.", "LOCAL_DIRECT"

    # 3. PROMPT STAGE 1: EVALUASI KONTEKS LOKAL
    prompt_stage1 = f"""
    Tugas: Evaluasi profil media sosial berikut untuk mengidentifikasi apakah pemilik akun adalah EDUKATOR / DOKTER / NAKES / KREATOR KONTEN KESEHATAN MANUSIA INDONESIA.

    ANALISIS UTAMA:

    1. UJI PROFESI / SPESIALISASI MEDIS MANUSIA:
       - Apakah kata "Dokter/Dok/Dr" merujuk pada KESEHATAN MANUSIA / MEDIS ASLI?
       - BILA METAFORA / NON-MEDIS (misal: "Dokter Sepatu" -> reparasi sepatu, "Dokter HP" -> servis HP, "Dokter Mobil" -> bengkel): WAJIB JAWAB "TIDAK VALID".
       - BILA GELAR AKADEMIS NON-MEDIS (Doktor S3 Ekonomi/Hukum/dll): WAJIB JAWAB "TIDAK VALID".

    2. UJI KONTEN EDUKASI KESEHATAN:
       - Jika akun merupakan clipper/reposter/tim edukasi, TETAPI fokus materinya adalah EDUKASI KESEHATAN MANUSIA yang valid -> BISA DIANGGAP "VALID" atau "RAGU" untuk dicek Google.
       - Jika clipper/bot spam non-kesehatan atau olshop/jasa non-medis murni -> JAWAB "TIDAK VALID".

    3. UJI BAHASA & KEWARGANEGARAAN:
       - Bio full bahasa asing non-Inggris (Arab/Spanyol/dll) tanpa konteks Indonesia -> JAWAB "TIDAK VALID".
       - Bio bahasa Inggris atau kasual yang belum jelas lokasi/kredensialnya -> WAJIB JAWAB "RAGU" AGAR DICEK GOOGLE.

    PILIH SALAH SATU KEPUTUSAN:
    - "VALID" : Jika terbukti kuat merupakan dokter/nakes/edukator kesehatan manusia Indonesia.
    - "TIDAK VALID" : Jika metafora (reparasi sepatu/HP), dokter non-medis, jasa non-kesehatan, atau luar negeri.
    - "RAGU" : Jika bionya meragukan dan butuh konfirmasi via Google Search.

    Format Balasan (WAJIB PERSIS):
    KEPUTUSAN: [VALID / TIDAK VALID / RAGU]
    ALASAN: [1 kalimat analisis kontekstual]

    Profil: "{text_to_check}"
    """

    try:
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

        # 4. STAGE 2: PENCARIAN GOOGLE TERARAH JIKA "RAGU"
        if status == "RAGU":
            search_context = search_internet_targeted(display_name)
            
            prompt_stage2 = f"""
            Tugas: Tentukan apakah profil berikut milik Influencer / Edukator / Dokter Kesehatan MANUSIA di Indonesia berdasarkan data pencarian web.

            Profil: "{text_to_check}"
            Hasil Google Search:
            {search_context}

            ATURAN EVALUASI GOOGLE:
            1. Jika dari hasil Google terbukti dia adalah influencer/kreator/dokter/nakes edukasi kesehatan asal Indonesia, jawab "VALID".
            2. Jika hasil Google menunjukkan dia berasal dari luar negeri, jasa reparasi/non-medis, atau tidak ada bukti bahwa dia edukator kesehatan Indonesia, jawab "TIDAK VALID".

            Format Balasan:
            KEPUTUSAN: [VALID / TIDAK VALID]
            ALASAN: [1 kalimat berdasarkan bukti Google]
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
    print("🎬 === MEMULAI PENYARINGAN HYBRID (MEMORY + CONTEXT AI + TARGETED GOOGLE) ===")

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
    file_final = r"01_pengumpulan_data\02_filter_akun_folls_like_bio\metadata_akun_filtered_reasoning.csv"

    filter_valid_influencers(file_mentah, file_final)