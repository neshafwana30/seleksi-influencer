import re

# Path ke file txt kamu
file_path = r"01_pengumpulan_data\01_scrapping_informasi_akun\list_dokter_raw.txt"
output_path = r"01_pengumpulan_data\01_scrapping_informasi_akun\list_dokter.txt"

usernames = set()  # Menggunakan set agar duplikat otomatis tereliminasi

# Pola regex untuk mendeteksi "Avatar for username" atau "@username"
pattern_avatar = re.compile(r"Avatar for\s+([^\s\n]+)", re.IGNORECASE)
pattern_at = re.compile(r"@([^\s\n]+)")

try:
    with open(file_path, "r", encoding="utf-8") as file:
        for line in file:
            line = line.strip()
            
            # 1. Cek pola "Avatar for ..."
            match_avatar = pattern_avatar.search(line)
            if match_avatar:
                uname = match_avatar.group(1).lower()
                usernames.add(uname)
                continue
            
            # 2. Cek pola "@..."
            match_at = pattern_at.search(line)
            if match_at:
                uname = match_at.group(1).lower()
                usernames.add(uname)

    # Simpan hasil pembersihan ke file baru tanpa duplicate
    with open(output_path, "w", encoding="utf-8") as out_file:
        for username in sorted(usernames):
            out_file.write(f"{username}\n")

    print(f"Berhasil mengekstrak {len(usernames)} username unik (bebas duplikat)!")
    print(f"Hasil disimpan di: {output_path}")

except FileNotFoundError:
    print(f"File tidak ditemukan di path: {file_path}.")