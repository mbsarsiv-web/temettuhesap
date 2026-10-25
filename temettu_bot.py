import os
import requests
import json
import base64
import re
import time

GITHUB_TOKEN = os.environ.get("GH_TOKEN")
REPO = "mbsarsiv-web/temettuhesap"

def parse_turkce_sayi(val):
    if val is None or str(val).strip() == "": return 0.0
    if isinstance(val, (int, float)): return float(val)
    s = str(val).strip()
    if ',' in s and '.' in s:
        if s.rfind(',') > s.rfind('.'): s = s.replace('.', '').replace(',', '.')
        else: s = s.replace(',', '')
    elif ',' in s: s = s.replace(',', '.')
    try: return float(s)
    except ValueError: return 0.0

def parse_date_ts(val):
    s = str(val).strip()
    m = re.search(r'Date\(([-0-9]+)\)', s)
    if m: return int(m.group(1)) // 1000
    m_iso = re.search(r'(\d{4})-(\d{2})-(\d{2})', s)
    if m_iso:
        try: return time.mktime((int(m_iso.group(1)), int(m_iso.group(2)), int(m_iso.group(3)), 0, 0, 0, 0, 0, -1))
        except: pass
    m2 = re.search(r'(\d{2})[\./-](\d{2})[\./-](\d{4})', s)
    if m2:
        try: return time.mktime((int(m2.group(3)), int(m2.group(2)), int(m2.group(1)), 0, 0, 0, 0, 0, -1))
        except: pass
    return 0

def format_tarih(val):
    s = str(val).strip()
    m = re.search(r'Date\(([-0-9]+)\)', s)
    if m: return time.strftime('%d.%m.%Y', time.gmtime(int(m.group(1))//1000))
    m_iso = re.search(r'(\d{4})-(\d{2})-(\d{2})', s)
    if m_iso: return f"{m_iso.group(3)}.{m_iso.group(2)}.{m_iso.group(1)}"
    return s

def main():
    session = requests.Session()
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json",
        "X-Requested-With": "XMLHttpRequest",
        "Content-Type": "application/json; charset=utf-8"
    }
    session.headers.update(headers)

    tv_url = "https://scanner.tradingview.com/turkey/scan"
    payload_tv = {"columns": ["name", "close"], "filter": [{"left": "type", "operation": "in_range", "right": ["stock"]}], "range": [0, 1500]}
    fiyatlar = {}
    try:
        tv_res = session.post(tv_url, json=payload_tv, timeout=10).json()
        if 'data' in tv_res:
            fiyatlar = {item['d'][0].replace("BIST:", ""): item['d'][1] for item in tv_res['data']}
    except Exception:
        pass 

    api_url = "https://www.isyatirim.com.tr/_layouts/15/IsYatirim.Website/StockInfo/CompanyInfoAjax.aspx/GetSermayeArttirimlari"
    payload_is = {"hisseKodu": "", "hisseTanimKodu": "04", "yil": 0, "zaman": "HEPSI", "endeksKodu": "09", "sektorKodu": ""}
    
    temettu_verileri = {}
    try:
        res = session.post(api_url, json=payload_is, timeout=20)
        if res.status_code == 200:
            js = res.json()
            data = js.get("value") or js.get("d") or js
            if isinstance(data, str): data = json.loads(data)
            
            gecmis_veriler = {}
            for satir in data:
                kod = satir.get("SHHE_HS_KOD") or satir.get("HISSE_KODU") or ""
                tarih = satir.get("SHHE_TARIH") or satir.get("TARIH") or ""
                
                if not kod or not tarih:
                    for k, v in satir.items():
                        ku = str(k).upper().replace('İ', 'I')
                        if not kod and "KOD" in ku: kod = v
                        if not tarih and "TARIH" in ku: tarih = v
                
                if not kod or not tarih: continue
                kod = str(kod).strip().upper()

                hb_brut = 0.0
                hb_net = 0.0
                
                # Aşama 1: Doğrudan Hisse Başı TL sütununu ara
                for k, v in satir.items():
                    ku = str(k).upper().replace('İ', 'I').replace('Ü', 'U')
                    val = parse_turkce_sayi(v)
                    if val <= 0: continue
                    
                    if ku == "PAY_BASINA_BRUT_TEMETTU" or ku == "HISSE_BASI_TL":
                        hb_brut = max(hb_brut, val)
                    elif ku == "PAY_BASINA_NET_TEMETTU":
                        hb_net = max(hb_net, val)
                
                # Aşama 2: Eğer doğrudan bulamazsa, Nakit Temettü Oranı üzerinden 100'e bölerek hesapla
                if hb_brut == 0 and hb_net == 0:
                    for k, v in satir.items():
                        ku = str(k).upper().replace('İ', 'I').replace('Ü', 'U')
                        val = parse_turkce_sayi(v)
                        if val <= 0: continue
                        
                        if "NAKIT" in ku and "ORAN" in ku and "TEMETTU" in ku:
                            if "NET" in ku: hb_net = max(hb_net, val / 100.0)
                            else: hb_brut = max(hb_brut, val / 100.0)
                
                secilen_hb = hb_brut if hb_brut > 0 else hb_net
                
                if secilen_hb > 0:
                    if kod not in gecmis_veriler:
                        gecmis_veriler[kod] = []
                    gecmis_veriler[kod].append({"tarih": tarih, "tutar": secilen_hb})
            
            current_ts = time.time()
            limit_ts = current_ts + (60 * 86400) # Bugünden itibaren en fazla 60 gün sonrasını kabul et
            
            for kod, liste in gecmis_veriler.items():
                # Uzak gelecekteki tahminleri filtrele
                gecerli_tarihler = [x for x in liste if parse_date_ts(x["tarih"]) <= limit_ts]
                if not gecerli_tarihler:
                    gecerli_tarihler = liste
                    
                son_temettu = max(gecerli_tarihler, key=lambda x: parse_date_ts(x["tarih"]))
                
                temettu_verileri[kod] = {
                    "tarih": format_tarih(son_temettu["tarih"]),
                    "hisse_basi_tl": round(son_temettu["tutar"], 4),
                    "fiyat": fiyatlar.get(kod, 0.0)
                }
    except Exception:
        pass

    if not GITHUB_TOKEN: return

    api_url_gh = f"https://api.github.com/repos/{REPO}/contents/temettu_veri.json"
    auth_headers = {"Authorization": f"Bearer {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}
    
    get_res = requests.get(api_url_gh, headers=auth_headers)
    sha = get_res.json().get('sha', '') if get_res.status_code == 200 else ""
    
    encoded = base64.b64encode(json.dumps(temettu_verileri, indent=4).encode('utf-8')).decode('utf-8')
    data = {"message": "otomatik güncelleme: uzak gelecek tahminleri ve oran filtresi", "content": encoded}
    if sha: data["sha"] = sha
    
    requests.put(api_url_gh, headers=auth_headers, json=data)

if __name__ == "__main__":
    main()
