"""
Test script buat cek token & chat_id Telegram bot udah bener/belum.

Cara pakai:
1. Pastikan credentials.py udah ada di folder yang sama, isi
   TELEGRAM_BOT_TOKEN dan TELEGRAM_CHAT_ID.
2. Jalanin: python test_telegram.py
3. Kalau berhasil, HP kamu bakal dapet notif dari bot-nya.
"""

import requests

try:
    from credentials import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
except ImportError:
    print("❌ credentials.py gak ketemu di folder ini.")
    print("   Pastikan file credentials.py ada dan isinya:")
    print('   TELEGRAM_BOT_TOKEN = "..."')
    print('   TELEGRAM_CHAT_ID = "..."')
    exit(1)

if not TELEGRAM_BOT_TOKEN or TELEGRAM_BOT_TOKEN.startswith("ISI_"):
    print("❌ TELEGRAM_BOT_TOKEN masih kosong / belum diisi di credentials.py")
    exit(1)

if not TELEGRAM_CHAT_ID or str(TELEGRAM_CHAT_ID).startswith("ISI_"):
    print("❌ TELEGRAM_CHAT_ID masih kosong / belum diisi di credentials.py")
    exit(1)

print("🔍 Testing koneksi ke Telegram Bot API...")
print(f"   Token  : {TELEGRAM_BOT_TOKEN[:10]}...{TELEGRAM_BOT_TOKEN[-4:]}")
print(f"   ChatID : {TELEGRAM_CHAT_ID}")
print()

# --- 1) Cek token valid dengan getMe ---
try:
    resp = requests.get(
        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getMe",
        timeout=10,
    )
    data = resp.json()
    if not data.get("ok"):
        print("❌ Token SALAH atau bot gak valid.")
        print(f"   Response: {data}")
        exit(1)
    bot_username = data["result"]["username"]
    print(f"✅ Token valid! Bot kamu: @{bot_username}")
except Exception as e:
    print(f"❌ Gagal connect ke Telegram API: {e}")
    print("   Cek koneksi internet kamu.")
    exit(1)

# --- 2) Coba kirim pesan test ke chat_id ---
print("\n📤 Mengirim pesan test...")
try:
    resp = requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
        json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": "✅ Test berhasil! Kalau kamu lihat pesan ini, "
                    "notif captcha alert dari script scraping siap dipakai. 🎉",
        },
        timeout=10,
    )
    data = resp.json()
    if data.get("ok"):
        print("✅ BERHASIL! Cek Telegram kamu, harusnya udah ada pesan masuk.")
    else:
        print("❌ Gagal kirim pesan.")
        print(f"   Response: {data}")
        error_desc = data.get("description", "")
        if "chat not found" in error_desc.lower():
            print("\n   💡 Kemungkinan penyebab: CHAT_ID salah, atau kamu")
            print("      belum pernah chat bot-nya duluan (harus /start dulu")
            print("      atau kirim pesan apa aja ke bot sebelum bot bisa")
            print("      kirim balik ke kamu).")
except Exception as e:
    print(f"❌ Error saat kirim pesan: {e}")