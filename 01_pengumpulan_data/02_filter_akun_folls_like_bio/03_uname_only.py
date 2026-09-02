import os
import csv

input_csv = r"01_pengumpulan_data\02_filter_akun_folls_like_bio\metadata_akun_passed_final.csv"
output_txt = r"01_pengumpulan_data\02_filter_akun_folls_like_bio\usernames_only.txt"

def main():
    # Pastikan file CSV-nya benar-benar ada
    if not os.path.exists(input_csv):
        print(f"[!] File tidak ditemukan di path: {os.path.abspath(input_csv)}")
        print("[!] Coba cek apakah foldernya atau namanya ada yang salah.")
        return

    usernames = []
    
    with open(input_csv, mode='r', encoding='utf-8-sig') as file: # pakai utf-8-sig supaya aman dari BOM Excel
        reader = csv.DictReader(file)
        
        # Cek apa aja nama kolom yang ada di CSV (buat antisipasi kalau bukan 'unique_id')
        print(f"[i] Kolom yang terdeteksi di CSV: {reader.fieldnames}")
        
        for row in reader:
            # Cari kolom yang mirip-mirip username / unique_id
            uname = (
                row.get('unique_id') or 
                row.get('uniqueId') or 
                row.get('username') or 
                row.get('UserName')
            )
            
            if uname:
                usernames.append(uname.strip())

    # Hilangkan duplikat
    usernames = list(dict.fromkeys(usernames))

    if not usernames:
        print("[!] Warning: Tidak ada username yang berhasil diekstrak. Cek isi file CSV-mu!")
        return

    # Buat folder output jika belum ada
    os.makedirs(os.path.dirname(output_txt), exist_ok=True)

    with open(output_txt, mode='w', encoding='utf-8') as f:
        for uname in usernames:
            f.write(f"{uname}\n")

    print(f"\n[SUKSES] Berhasil mengambil {len(usernames)} username!")
    print(f"[SUKSES] Disimpan ke: {os.path.abspath(output_txt)}")

if __name__ == "__main__":
    main()