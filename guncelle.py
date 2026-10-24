import requests
import pandas as pd
import json
import time

# 1. BİST hisse kodlarının tam listesini TradingView üzerinden çekiyoruz
url_tv = "https://scanner.tradingview.com/turkey/scan"
payload = {
    "columns": ["name"],
    "filter": [{"left": "type", "operation": "in_range", "right": ["stock"]}],
    "range": [0, 1500]
}

try:
    res = requests.post(url_tv, json=payload, timeout=10)
    tv_data = res.json()
    tum_hisseler = [item['d'][0] for item in tv_data['data']]
    print(f"Toplam {len(tum_hisseler)} hisse kodu bulundu.")
except Exception as e:
    print(f"Hisse listesi alınırken hata: {e}")
    tum_hisseler = ["AKSA", "ANHYT", "DOAS", "ENJSA", "FROTO", "ISMEN", "TUPRS", "TTRAK"]

temettu_verileri = {}

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "tr-TR,tr;q=0.8,en-US;q=0.5,en;q=0.3"
}

print("İş Yatırım temettü taraması başlıyor...")

# 2. Hisseleri tek tek gezip İş Yatırım'dan güncel temettü bilgisini alıyoruz
for hisse in tum_hisseler:
    url = f"https://www.isyatirim.com.tr/tr-tr/analiz/hisse/Sayfalar/sirket-karti.aspx?hisse={hisse}"
    try:
        r = requests.get(url, headers=headers, timeout=10)
        tablolar = pd.read_html(r.text, decimal=',', thousands='.')
        for tablo in tablolar:
            if 'Hisse Başı TL' in tablo.columns and 'Dağ. Tarihi' in tablo.columns:
                en_guncel = tablo.iloc[0]
                temettu_verileri[hisse] = {
                    "tarih": str(en_guncel['Dağ. Tarihi']),
                    "hisse_basi_tl": float(en_guncel['Hisse Başı TL'])
                }
                print(f"{hisse} - Temettü bulundu: {en_guncel['Hisse Başı TL']} TL")
                break
    except Exception:
        pass 
        
    time.sleep(0.3) 

# 3. Web arayüzünün kullanacağı JSON dosyasını oluşturuyoruz
with open('temettu_veri.json', 'w', encoding='utf-8') as f:
    json.dump(temettu_verileri, f, ensure_ascii=False, indent=4)

print("İşlem tamamlandı. Veriler temettu_veri.json dosyasına yazıldı.")
