import os
from dotenv import load_dotenv
import google.generativeai as genai

# Load variabel dari file .env
load_dotenv()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# Konfigurasi Google Gemini
genai.configure(api_key=GEMINI_API_KEY)

def test_working_models():
    candidate_models = [
        'gemini-2.0-flash', 
        'gemini-3.5-flash',
        'gemini-2.0-flash-lite',
        'gemini-pro'
    ]
    
    prompt = """
    Tolong jawab 5 pertanyaan singkat ini secara langsung:
    1. Apa ibu kota Indonesia?
    2. Berapa hasil dari 15 dikali 4?
    3. Siapa penemu bola lampu pijar?
    4. Apa kepanjangan dari AI?
    5. Sebutkan 3 warna primer!
    6. apakah jualan atau afiliate termasuk ke dalam komersial account?
    """
    
    print("Mulai mengetes model mana yang benar-benar bisa generate teks...\n")
    
    for model_name in candidate_models:
        print(f"Mengetes model: {model_name}...")
        try:
            model = genai.GenerativeModel(model_name)
            response = model.generate_content(prompt)
            
            print(f"✅ BERHASIL! Model '{model_name}' bisa dipakai.")
            print(f"Balasan AI:\n{response.text.strip()}\n")
            
            print(f"👉 KESIMPULAN: Gunakan '{model_name}' di kode utamamu.")
            
        except Exception as e:
            print(f"❌ GAGAL. Error: {e}\n")

if __name__ == "__main__":
    test_working_models()