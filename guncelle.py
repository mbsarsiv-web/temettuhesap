import requests
import pandas as pd
import json
import time

# 1. TradingView'den hisse kodlarını ve GÜNCEL FİYATLARI çekiyoruz
url_tv = "https://scanner.tradingview.com/turkey/scan"
payload = {
    "columns": ["name", "close"],
    "filter": [{"left": "type", "operation": "in_range", "right": ["stock"]}],
    "range": [0, 1500]
}

tv_fiyatlar = {}
try:
    res = requests.post(url_tv, json=payload, timeout=10)
    tv_data = res.json()
    for item in tv_data['data']:
        kod = item['d'][0].replace("BIST:", "")
        fiyat = item['d'][1]
        tv_fiyatlar[kod] = fiyat
    tum_hisseler = list(tv_fiyatlar.keys())
    print(f"Toplam {len(tum_hisseler)} hisse kodu ve canlı fiyatı bulundu.")
except Exception as e:
    print(f"Hisse listesi alınırken hata: {e}")
    tum_hisseler = ["AKSA", "ANHYT", "DOAS", "ENJSA", "FROTO", "ISMEN", "TUPRS", "TTRAK"]

temettu_verileri = {}

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

# 2. İş Yatırım'dan temettüleri bulup fiyatlar ile birleştiriyoruz
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
                    "hisse_basi_tl": float(en_guncel['Hisse Başı TL']),
                    "fiyat": tv_fiyatlar.get(hisse, 0) # Fiyatı buraya ekledik!
                }
                break
    except Exception:
        pass 
    time.sleep(0.3) 

# 3. Her şeyi JSON'a kaydediyoruz
with open('temettu_veri.json', 'w', encoding='utf-8') as f:
    json.dump(temettu_verileri, f, ensure_ascii=False, indent=4)

print("İşlem tamamlandı. Veriler temettu_veri.json dosyasına yazıldı.")
