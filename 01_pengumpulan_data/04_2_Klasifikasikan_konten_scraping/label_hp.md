# AKSES DARI WiFi BEDA dengan NGROK

## 🌐 APA ITU NGROK?

**ngrok** = tools yang bikin **public URL** untuk local server kamu.

**Tanpa ngrok:**
```
HP WiFi A → TIDAK BISA connect
Laptop WiFi B → BISA connect lokal
```

**Dengan ngrok:**
```
HP WiFi A → BISA connect (public URL)
Laptop WiFi B → BISA connect (public URL)
Di cafe, di rumah, dimana saja → BISA
```

---

## 📝 STEP 1: INSTALL PYNGROK

```bash
pip install pyngrok
```

Check if installed:
```bash
python -c "import pyngrok; print('✅ ngrok ready!')"
```

---

## 🚀 STEP 2: RUN DENGAN NGROK

```bash
python label_app_mobile.py --tunnel
```

Output:
```
======================================================================
MOBILE LABELING APP - SWIPE GESTURE
======================================================================

✅ Total videos: 2000
Already labeled: 500
Remaining: 1500

🌐 Starting Flask server...

🔗 Setting up ngrok tunnel...

======================================================================
🌐 NGROK TUNNEL ACTIVE!
======================================================================

📱 Public URL: https://abc1234def5678.ngrok.io

   Copy & paste ke HP (WiFi beda juga bisa):
   https://abc1234def5678.ngrok.io

======================================================================
```

---

## 📱 STEP 3: BUKA DI HP

**Copy URL dari terminal**, terus:

1. **Buka Chrome/Safari di HP**
2. **Paste:** `https://abc1234def5678.ngrok.io`
3. **Enter**

Done! Bisa akses dari WiFi beda manapun.

---

## ⚡ TIPS

### Tunnel expire kalo server stop
- Kalo tutup Flask, ngrok juga stop
- Buka lagi = URL baru
- Jadi copy URL setiap kali jalanin

### Ada warning dari browser?
- Chrome/Safari kadang warning "Unsafe"
- Click "Advanced" → "Proceed anyway"
- Normal, kalo pakai ngrok free tier

### Multiple devices?
- Bisa buka 1 URL di beberapa HP sekaligus
- Tapi save file agak risky (conflict)
- Recommended: 1 orang 1 kali

### Kecepatan lambat?
- Normal, ngrok tunnel ada latency
- Swipe/click masih lancar kok
- Kalo perlu faster → upgrade ngrok (paid)

---

## 🔧 TWO MODES

### MODE 1: SAME WiFi (Faster)
```bash
python label_app_mobile.py
```
- Cepat (no tunnel overhead)
- Butuh: HP & Laptop same WiFi
- Recommended kalo bisa

### MODE 2: BEDA WiFi (Flexible)
```bash
python 01_pengumpulan_data\04_2_Klasifikasikan_konten_scraping\02_2_label_manual_hp.py --tunnel
```
- Bisa dari mana saja
- Slightly slower (normal aja)
- Recommended untuk flexibility

---

## 📊 COMPARISON

| Aspek | Mode 1 | Mode 2 |
|-------|--------|--------|
| Speed | Cepat | Normal |
| WiFi | Harus sama | Beda juga OK |
| Setup | Mudah | 1 line (--tunnel) |
| URL | 192.168.x.x:5000 | https://xxx.ngrok.io |

---

## 🐛 TROUBLESHOOT

### "ModuleNotFoundError: pyngrok"
```bash
pip install pyngrok
```

### "Address already in use :5000"
- Ada program lain pakai port 5000
- Close semua Flask/Django
- Atau edit `label_app_mobile.py` ganti port (advanced)

### "Failed to connect ngrok"
- Check internet laptop OK?
- Reboot ngrok: close & run lagi
- Kalo masih error, fallback ke mode 1

### "Page not loading di HP"
- Copy URL exact dari terminal (jangan ketik manual)
- Check HP internet OK?
- Try refresh halaman
- Pastikan HTTPS (penting!)

---

## 🎯 QUICK START

```bash
# 1. Install ngrok (1x doang)
pip install pyngrok

# 2. Run dengan tunnel
python label_app_mobile.py --tunnel

# 3. Copy URL dari output
# Contoh: https://abc1234def5678.ngrok.io

# 4. Paste di HP browser
# Done! Labeling dimulai

# 5. Kalo mau pause
# Tekan Ctrl+C di terminal
# Data saved otomatis ke labeled_videos.csv

# 6. Resume nanti
# python label_app_mobile.py --tunnel
# (URL baru tapi data lanjut)
```

---

## 💾 IMPORTANT

- **File tersimpan di laptop** → `labeled_videos.csv`
- **HP cuma interface saja**
- Gak ada data yang disimpan di HP
- Bisa close HP kapan saja, resume dari laptop

---

## 🤔 FAQ

**Q: Ngrok free / paid?**
- Free: OK untuk use case ini (1 tunnel, 40 koneksi/menit)
- Paid: Unlimited (tp overkill)

**Q: URL bisa disimpan?**
- Gak, URL berubah setiap kali jalanin
- Copy dari terminal setiap kali

**Q: Bisa share URL ke orang lain?**
- Bisa, tapi data shared
- Jangan-jangan conflict saat save
- Recommended: 1 orang 1 session

**Q: Bisa pakai solution lain?**
- Cloud Deploy (Heroku, Railway) - lebih ribet
- Port Forwarding - security risk
- CloudFlare Tunnel - lebih kompleks

Ngrok = simplest & safest solution ✅

---

**Enjoy labeling dari mana saja!** 🚀