import requests
from bs4 import BeautifulSoup
import pandas as pd
import json
import time

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

# 1. İş Yatırım sayfasından işlem gören TÜM hisse kodlarını otomatik alıyoruz
ana_url = "https://www.isyatirim.com.tr/tr-tr/analiz/hisse/Sayfalar/sirket-karti.aspx"
r = requests.get(ana_url, headers=headers)
soup = BeautifulSoup(r.content, 'html.parser')
hisse_secici = soup.find('select', id='ddlHisse')
tum_hisseler = [opt.get('value') for opt in hisse_secici.find_all('option') if opt.get('value')]

temettu_verileri = {}
print(f"Toplam {len(tum_hisseler)} hisse bulundu. Temettü taraması başlıyor...")

# 2. Tüm hisseleri tek tek gezip güncel temettü bilgisini ayıklıyoruz
for hisse in tum_hisseler:
    url = f"https://www.isyatirim.com.tr/tr-tr/analiz/hisse/Sayfalar/sirket-karti.aspx?hisse={hisse}"
    try:
        tablolar = pd.read_html(requests.get(url, headers=headers).text, decimal=',', thousands='.')
        for tablo in tablolar:
            if 'Hisse Başı TL' in tablo.columns and 'Dağ. Tarihi' in tablo.columns:
                en_guncel = tablo.iloc[0] # İlk satır en güncel/gelecek temettüdür
                temettu_verileri[hisse] = {
                    "tarih": str(en_guncel['Dağ. Tarihi']),
                    "hisse_basi_tl": float(en_guncel['Hisse Başı TL'])
                }
                break
    except Exception:
        pass # Temettüsü olmayan veya veri hatası veren hisseleri sessizce atlıyoruz
    
    # Sunucu tarafından engellenmemek için her hissede yarım saniye bekliyoruz
    time.sleep(0.5)

# 3. Çekilen tüm veriyi web arayüzünün kullanacağı JSON dosyasına yazıyoruz
with open('temettu_veri.json', 'w', encoding='utf-8') as f:
    json.dump(temettu_verileri, f, ensure_ascii=False, indent=4)

print("İşlem tamamlandı. Tüm piyasa tarandı ve temettu_veri.json dosyasına yazıldı.")
