import os
import requests
import json
import time
import base64
from bs4 import BeautifulSoup

# Şifre artık açıkta değil, GitHub Secrets kasasından çekiliyor
GITHUB_TOKEN = os.environ.get("GH_TOKEN")
REPO = "mbsarsiv-web/temettuhesap"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
    "Cache-Control": "no-cache",
    "Connection": "keep-alive"
}

def get_session():
    s = requests.Session()
    s.headers.update(HEADERS)
    return s

def fetch_with_retry(session, url):
    for attempt in range(1, 6):
        try:
            resp = session.get(url, timeout=15)
            if resp.status_code == 200: return resp.text
        except Exception: pass
        time.sleep(2)
    return None

def main():
    session = get_session()
    
    # 1. TradingView'den canlı fiyatlar
    tv_url = "https://scanner.tradingview.com/turkey/scan"
    payload = {"columns": ["name", "close"], "filter": [{"left": "type", "operation": "in_range", "right": ["stock"]}], "range": [0, 1500]}
    
    try:
        tv_res = requests.post(tv_url, json=payload, timeout=10).json()
        fiyatlar = {item['d'][0].replace("BIST:", ""): item['d'][1] for item in tv_res['data']}
    except Exception:
        fiyatlar = {}

    temettu_verileri = {}
    
    # 2. İş Yatırım'dan Session taktiğiyle veri çekimi
    for kod in fiyatlar.keys():
        html = fetch_with_retry(session, f"https://www.isyatirim.com.tr/tr-tr/analiz/hisse/Sayfalar/sirket-karti.aspx?hisse={kod}")
        if html:
            soup = BeautifulSoup(html, 'html.parser')
            ths = soup.find_all('th')
            h_idx, t_idx = -1, -1
            for i, th in enumerate(ths):
                if 'Hisse Başı TL' in th.text: h_idx = i
                if 'Dağ. Tarihi' in th.text: t_idx = i
                
            if h_idx != -1 and t_idx != -1:
                try:
                    tr = ths[0].find_parent('table').find('tbody').find('tr')
                    if tr:
                        tds = tr.find_all('td')
                        tarih = tds[t_idx].text.strip()
                        hisse_basi = tds[h_idx].text.strip().replace(',', '.')
                        if hisse_basi and hisse_basi != '-':
                            temettu_verileri[kod] = {"tarih": tarih, "hisse_basi_tl": float(hisse_basi), "fiyat": fiyatlar[kod]}
                except Exception: pass
        time.sleep(0.5)

    # 3. GitHub API ile doğrudan deponuza yazma
    if not GITHUB_TOKEN:
        print("HATA: GH_TOKEN bulunamadı.")
        return

    api_url = f"https://api.github.com/repos/{REPO}/contents/temettu_veri.json"
    auth_headers = {"Authorization": f"Bearer {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}
    
    get_res = requests.get(api_url, headers=auth_headers)
    sha = get_res.json().get('sha', '') if get_res.status_code == 200 else ""
    
    encoded = base64.b64encode(json.dumps(temettu_verileri, indent=4).encode('utf-8')).decode('utf-8')
    data = {"message": "otomatik güncelleme: session bypass", "content": encoded}
    if sha: data["sha"] = sha
    
    requests.put(api_url, headers=auth_headers, json=data)

if __name__ == "__main__":
    main()
