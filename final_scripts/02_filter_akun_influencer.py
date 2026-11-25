"""
02 - Filter Akun Influencer Kesehatan (Followers + Bio)
=========================================================
Tahap 3.1.2.2 (Seleksi Akun Influencer) pada proposal TA.

Alur:
1. filter_valid_influencers()
   Dari metadata profil mentah (output 01_scraping_akun_dokter.py): buang akun
   gagal/private, filter followers >= 10.000, lalu validasi tiap akun sebagai
   individu nakes/edukator kesehatan Indonesia memakai pendekatan hybrid
   (knowledge base lokal -> LLM lokal/Ollama -> pencarian web DuckDuckGo
   untuk kasus ragu).
2. extract_passed_influencers()
   Menyaring hanya akun berstatus PASSED_AI dan membuang kolom-kolom audit.
3. extract_usernames()
   Mengekstrak kolom username menjadi file .txt untuk tahap scraping video id.

Konsolidasi dari:
- 01_pengumpulan_data/02_filter_akun_folls_like_bio/01__filter_akun_influencer.py
- 01_pengumpulan_data/02_filter_akun_folls_like_bio/02_passed_only.py
- 01_pengumpulan_data/02_filter_akun_folls_like_bio/03_uname_only.py

Catatan: 00_test_api_connection.py (cek koneksi Ollama) TIDAK diikutkan karena
hanya utilitas debug, bukan bagian pipeline data.

Dicek terpakai/tidaknya: 03_videoid_perakun/01_get_videoid.py meng-hardcode
"usernames_only.txt" sebagai input -> dipastikan file ini yang jadi output
rantai final (bukan tiktok_influencer.txt yang ternyata orphan/tidak terpakai).

CATATAN EKSEKUSI: filter_valid_influencers() memanggil LLM lokal (Ollama)
+ pencarian web per akun -- lumayan lama kalau datanya ratusan baris. Semua
fungsi di sini otomatis skip kalau output-nya sudah ada di final_scripts/output/.
"""

import csv
from pathlib import Path

import ollama
import polars as pl
from ddgs import DDGS

# -------------------------------------------------------------------------
# KONFIGURASI PATH
# -------------------------------------------------------------------------
FINAL_OUTPUT_ROOT = Path(__file__).resolve().parent / "output"
OUTPUT_DIR = FINAL_OUTPUT_ROOT / "02_filter_akun_influencer"

PROFILE_METADATA_RAW = FINAL_OUTPUT_ROOT / "01_scraping_akun_dokter" / "metadata_akun_mentah.csv"  # input
FILTERED_WITH_REASONING = OUTPUT_DIR / "metadata_akun_tervalidasi.csv"  # output step 1
PASSED_ONLY = OUTPUT_DIR / "metadata_akun_lolos_filter.csv"             # output step 2
USERNAMES_OUTPUT = OUTPUT_DIR / "username_influencer_terpilih.txt"      # output step 3

MIN_FOLLOWERS = 10_000
OLLAMA_MODEL = "llama3.1"

# -------------------------------------------------------------------------
# KNOWLEDGE BASE LOKAL - INFLUENCER KESEHATAN INDONESIA YANG SUDAH DIKENAL
# -------------------------------------------------------------------------
HEALTH_INFLUENCERS_MEMORY = [
    "tirta", "tirtacipeng", "dr. tirta",
    "ayman", "aymanalts", "dr. ayman",
    "clarin hayes", "clarinhayes", "dr. clarin",
    "kevin mak", "drkevinmak", "dr. kevin",
    "gia pratama", "giapratamamd", "dr. gia",
    "alip hildan", "doklip", "qonita", "doklip universe",
    "asa ibrahim", "dr. asa",
    "zie", "drzie", "yessicatania", "dr. yessica",
    "richard lee", "dr.richard_lee", "dr. richard",
    "danar", "dr_danar", "danar wicaksono",
    "kamila jaidi", "dr. kamila",
    "ekles", "dr_ekles", "dr. ekles",
    "abelina", "abelina_md", "dr. abelina",
    "kamilah", "dr. kamilah",
    "dion haryadi", "dionharyadi", "dr. dion",
    "hans tandra", "dr. hans",
    "santi", "susanti", "dr. santi",
    "boyke", "wishdrboyke", "drboyke",
    "yassin bintang", "dr. yassin",
    "amira", "amiradokter", "dr. amira",
    "nisa fathoni", "dr. nisa",
    "boy abidin", "dr. boy abidin",
    "mesty", "mestyariotedjo", "mesty ariotedjo",
    "meta hanindita", "dr. meta",
    "citra amelinda", "dr. citra",
    "k.s. diniari", "dr. dini",
    "decsa", "dokterdecsa", "decsa medika",
    "vito damay", "vitodamay", "dr. vito",
    "ikeda", "dr ikeda", "ikedaputri",
    "rizal do", "afrumed", "ners rizal",
    "bidan novel", "bidannovel",
    "apt", "apoteker",
]


def _is_in_health_memory(display_name: str, signature: str) -> tuple[bool, str]:
    text_combined = f"{display_name} {signature}".lower()
    for key in HEALTH_INFLUENCERS_MEMORY:
        if key in text_combined:
            return True, f"Terdeteksi di memori lokal sebagai influencer kesehatan terpercaya ({key})."
    return False, ""


def _search_internet_targeted(query: str) -> str:
    search_query = f'"{query}" asal negara profil dokter influencer kesehatan tiktok indonesia'
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(search_query, max_results=3))
            if not results:
                results = list(ddgs.text(f"{query} dokter nakes pembuat konten bahasa indonesia", max_results=3))
            if not results:
                return "Tidak ditemukan data pencarian relevan di internet."
            return "\n".join(f"- {r['title']}: {r['body']}" for r in results)
    except Exception as e:
        return f"Gagal akses DuckDuckGo: {e}"


def _check_bio_hybrid(signature: str, display_name: str) -> tuple[bool, str, str]:
    """Validasi bio: knowledge base lokal -> LLM lokal -> web search (jika ragu)."""
    text_to_check = f"Name: {display_name} | Bio: {signature}"

    in_memory, memory_reason = _is_in_health_memory(display_name, signature)
    if in_memory:
        return True, memory_reason, "MEMORY_MATCH"

    if not signature or str(signature).strip() in ("", '""', "null", "None"):
        return False, "Bio kosong atau informasi terlalu minim.", "LOCAL_DIRECT"

    prompt_stage1 = f"""
        Tugas: Evaluasi profil media sosial berikut untuk mengidentifikasi apakah pemilik akun adalah EDUKATOR / DOKTER / NAKES / KREATOR KONTEN KESEHATAN MANUSIA INDONESIA yang bersifat PERORANGAN (INDIVIDU).

        ANALISIS UTAMA:

        1. UJI ENTITAS (INDIVIDU VS INSTITUSI/BRAND/APLIKASI) -> PALING KRUSIAL:
        - Target UTAMA adalah KREATOR PERORANGAN.
        - BILA INSTITUSI PEMERINTAH / LEMBAGA (misal: "Badan Gizi Nasional", "Kemenkes", "Dinas Kesehatan", "Puskesmas", "BPOM"): WAJIB JAWAB "TIDAK VALID".
        - BILA APLIKASI / PERUSAHAAN / STARTUP (misal: "Alodokter", "Halodoc", "KlikDokter", "Siloam", "Media Medis"): WAJIB JAWAB "TIDAK VALID".
        - BILA RUMAH SAKIT / KLINIK / BRAND SKINCARE (yang bukan akun personal): WAJIB JAWAB "TIDAK VALID".

        2. UJI PROFESI / SPESIALISASI MEDIS MANUSIA:
        - Apakah kata "Dokter/Dok/Dr" merujuk pada KESEHATAN MANUSIA / MEDIS ASLI?
        - BILA METAFORA / NON-MEDIS (misal: "Dokter Sepatu" -> reparasi sepatu, "Dokter HP" -> servis HP, "Dokter Mobil" -> bengkel): WAJIB JAWAB "TIDAK VALID".
        - BILA GELAR AKADEMIS NON-MEDIS (Doktor S3 Ekonomi/Hukum/dll): WAJIB JAWAB "TIDAK VALID".

        3. UJI KONTEN EDUKASI KESEHATAN:
        - Jika akun membagikan edukasi kesehatan secara individu/perorangan -> BISA DIANGGAP "VALID" atau "RAGU" untuk dicek Google.
        - Jika bot spam, olshop murni non-kesehatan, atau clipper tanpa nilai edukasi -> WAJIB JAWAB "TIDAK VALID".

        4. UJI BAHASA & KEWARGANEGARAAN:
        - Bio full bahasa asing non-Inggris (Arab/Spanyol/dll) tanpa konteks Indonesia -> WAJIB JAWAB "TIDAK VALID".
        - Bio bahasa Inggris atau kasual yang belum jelas lokasi/kredensialnya -> WAJIB JAWAB "RAGU" AGAR DICEK GOOGLE.

        PILIH SALAH SATU KEPUTUSAN:
        - "VALID" : Jika terbukti kuat merupakan INDIVIDU / PERORANGAN dokter/nakes/edukator kesehatan Indonesia.
        - "TIDAK VALID" : Jika institusi pemerintah, aplikasi, rumah sakit, klinik, brand, metafora (reparasi), atau jasa non-kesehatan.
        - "RAGU" : Jika bionya meragukan (perorangan atau bukan) dan butuh konfirmasi via Google.

        Format Balasan (WAJIB PERSIS):
        KEPUTUSAN: [VALID / TIDAK VALID / RAGU]
        ALASAN: [1 kalimat analisis kontekstual]

        Profil: "{text_to_check}"
        """

    try:
        response = ollama.chat(
            model=OLLAMA_MODEL,
            messages=[{"role": "user", "content": prompt_stage1}],
            options={"temperature": 0.0},
        )
        content = response["message"]["content"].strip()

        status = "TIDAK VALID"
        if "KEPUTUSAN: VALID" in content.upper():
            status = "VALID"
        elif "KEPUTUSAN: RAGU" in content.upper():
            status = "RAGU"

        reason = content.split("ALASAN:")[-1].strip() if "ALASAN:" in content.upper() else content

        if status == "RAGU":
            search_context = _search_internet_targeted(display_name)

            prompt_stage2 = f"""
            Tugas: Tentukan apakah profil berikut milik Influencer / Edukator / Dokter Kesehatan MANUSIA yang berasal dari / berdomisili di INDONESIA dan membuat konten BERBAHASA INDONESIA.

            Profil: "{text_to_check}"
            Hasil Google Search:
            {search_context}

            ATURAN EVALUASI GOOGLE (WAJIB DIIKUTI):
            1. UJI ASAL NEGARA & BAHASA: Jika hasil Google membuktikan dia berasal dari LUAR NEGERI (misal: Malaysia, Amerika, Inggris, dll) atau bukan pembuat konten berbahasa Indonesia, WAJIB jawab "TIDAK VALID".
            2. UJI ENTITAS: Jika dia ternyata institusi pemerintah, aplikasi, atau brand (bukan individu), jawab "TIDAK VALID".
            3. Jika hasil Google terbukti bahwa dia adalah EDUKATOR PERORANGAN asal INDONESIA yang membuat konten kesehatan, jawab "VALID".

            Format Balasan:
            KEPUTUSAN: [VALID / TIDAK VALID]
            ALASAN: [1 kalimat berdasarkan bukti Google]
            """

            response_web = ollama.chat(
                model=OLLAMA_MODEL,
                messages=[{"role": "user", "content": prompt_stage2}],
                options={"temperature": 0.0},
            )
            content_web = response_web["message"]["content"].strip()

            is_valid_final = "VALID" in content_web.upper() and "TIDAK VALID" not in content_web.upper()
            web_reason = content_web.split("ALASAN:")[-1].strip() if "ALASAN:" in content_web.upper() else content_web

            return is_valid_final, f"[Cek Web] {web_reason}", "INTERNET_VERIFIED"

        return (status == "VALID"), reason, "LOCAL_DIRECT"

    except Exception as e:
        return False, f"Error system: {e}", "ERROR"


# -------------------------------------------------------------------------
# STEP 1 - Filter followers + validasi bio hybrid
# -------------------------------------------------------------------------
def filter_valid_influencers(input_csv: Path = PROFILE_METADATA_RAW,
                              output_csv: Path = FILTERED_WITH_REASONING) -> None:
    if output_csv.exists():
        print(f"[Step 1] Sudah ada -> {output_csv} (skip, tidak diproses ulang)")
        return

    if not input_csv.exists():
        raise FileNotFoundError(f"File input tidak ditemukan: {input_csv}")

    df = pl.read_csv(input_csv, infer_schema=False, truncate_ragged_lines=True, ignore_errors=True)

    gagal_text = "Gagal ditarik / Kemungkinan Private Ketat"
    df_valid = df.filter(
        (pl.col("unique_id") != gagal_text)
        & (pl.col("scraping_status").str.contains("(?i)public"))
    )

    df_valid = df_valid.with_columns(
        pl.col("num_followers").str.replace_all(r"\D", "").cast(pl.Int64, strict=False).fill_null(0)
    ).filter(pl.col("num_followers") >= MIN_FOLLOWERS)

    print(f"[Step 1] Akun masuk kualifikasi followers >= {MIN_FOLLOWERS}: {df_valid.height} baris")

    ai_results, ai_reasons, method_used = [], [], []
    signatures = df_valid["signature"].to_list()
    display_names = df_valid["nickname"].to_list()

    for i in range(len(df_valid)):
        is_valid, reason, method = _check_bio_hybrid(signatures[i], display_names[i])
        ai_results.append("PASSED_AI" if is_valid else "REJECTED_AI")
        ai_reasons.append(reason)
        method_used.append(method)

        if (i + 1) % 10 == 0 or (i + 1) == len(df_valid):
            print(f"   Terproses {i + 1}/{len(df_valid)} akun...")

    df_final = df_valid.with_columns([
        pl.Series("final_status", ai_results),
        pl.Series("ai_reason", ai_reasons),
        pl.Series("check_method", method_used),
    ])

    output_csv.parent.mkdir(parents=True, exist_ok=True)
    df_final.write_csv(output_csv)
    print(f"[Step 1] Tersimpan -> {output_csv}")


# -------------------------------------------------------------------------
# STEP 2 - Ambil hanya yang PASSED_AI
# -------------------------------------------------------------------------
def extract_passed_influencers(input_csv: Path = FILTERED_WITH_REASONING,
                                output_csv: Path = PASSED_ONLY) -> None:
    if output_csv.exists():
        print(f"[Step 2] Sudah ada -> {output_csv} (skip, tidak diproses ulang)")
        return

    if not input_csv.exists():
        raise FileNotFoundError(f"File input tidak ditemukan: {input_csv}")

    df = pl.read_csv(input_csv, infer_schema=False, truncate_ragged_lines=True, ignore_errors=True)
    total_audit = df.height

    df_passed = df.filter(pl.col("final_status") == "PASSED_AI")
    columns_to_drop = ["is_valid_ai", "ai_reason", "final_status"]
    df_clean = df_passed.drop([c for c in columns_to_drop if c in df_passed.columns])

    print(f"[Step 2] {df_clean.height}/{total_audit} akun lolos kualifikasi akhir")

    output_csv.parent.mkdir(parents=True, exist_ok=True)
    df_clean.write_csv(output_csv)
    print(f"[Step 2] Tersimpan -> {output_csv}")


# -------------------------------------------------------------------------
# STEP 3 - Ekstrak username ke .txt untuk tahap scraping video id
# -------------------------------------------------------------------------
def extract_usernames(input_csv: Path = PASSED_ONLY,
                       output_txt: Path = USERNAMES_OUTPUT) -> list[str]:
    if output_txt.exists():
        print(f"[Step 3] Sudah ada -> {output_txt} (skip, tidak diproses ulang)")
        with open(output_txt, "r", encoding="utf-8") as f:
            return [line.strip() for line in f if line.strip()]

    if not input_csv.exists():
        raise FileNotFoundError(f"File input tidak ditemukan: {input_csv}")

    usernames = []
    with open(input_csv, mode="r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            uname = row.get("unique_id") or row.get("uniqueId") or row.get("username") or row.get("UserName")
            if uname:
                usernames.append(uname.strip())

    usernames = list(dict.fromkeys(usernames))  # dedupe, preserve order

    if not usernames:
        print("[Step 3] Warning: tidak ada username yang berhasil diekstrak.")
        return []

    output_txt.parent.mkdir(parents=True, exist_ok=True)
    with open(output_txt, mode="w", encoding="utf-8") as f:
        for uname in usernames:
            f.write(f"{uname}\n")

    print(f"[Step 3] {len(usernames)} username tersimpan -> {output_txt}")
    return usernames


# -------------------------------------------------------------------------
# MAIN
# -------------------------------------------------------------------------
def main():
    filter_valid_influencers()
    extract_passed_influencers()
    extract_usernames()


if __name__ == "__main__":
    main()
