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

def parse_date_int(val):
    if not val: return 0
    s = str(val).strip()
    m = re.search(r'Date\(([-0-9]+)\)', s)
    if m: 
        t = time.gmtime(int(m.group(1)) // 1000)
        return t.tm_year * 10000 + t.tm_mon * 100 + t.tm_mday
    m_iso = re.search(r'(\d{4})-(\d{2})-(\d{2})', s)
    if m_iso: return int(m_iso.group(1))*10000 + int(m_iso.group(2))*100 + int(m_iso.group(3))
    m2 = re.search(r'(\d{2})[\./-](\d{2})[\./-](\d{4})', s)
    if m2: return int(m2.group(3))*10000 + int(m2.group(2))*100 + int(m2.group(1))
    return 0

def format_tarih(val):
    s = str(val).strip()
    m = re.search(r'Date\(([-0-9]+)\)', s)
    if m: return time.strftime('%d.%m.%Y', time.gmtime(int(m.group(1))/1000))
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

    # endeksKodu kısıtlaması kaldırılarak BIST'teki TÜM hisseler hedeflendi
    api_url = "https://www.isyatirim.com.tr/_layouts/15/IsYatirim.Website/StockInfo/CompanyInfoAjax.aspx/GetSermayeArttirimlari"
    payload_is = {"hisseKodu": "", "hisseTanimKodu": "04", "yil": 0, "zaman": "HEPSI", "endeksKodu": "", "sektorKodu": ""}
    
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
                        ku = str(k).upper()
                        if not kod and "KOD" in ku: kod = v
                        if not tarih and "TARIH" in ku: tarih = v
                
                if not kod or not tarih: continue
                kod = str(kod).strip().upper()

                hb = 0.0
                for k, v in satir.items():
                    ku = str(k).upper().replace('İ', 'I').replace('Ü', 'U')
                    
                    # Exact Substring Match: Sütun ön eklerini (SHHE_) umursamadan doğrudan veriyi çeker
                    if "PAY_BASINA_BRUT_TEMETTU" in ku or "HISSE_BASI" in ku:
                        val = parse_turkce_sayi(v)
                        if val > hb: hb = val
                        
                    elif "NAKIT_TEMETTU_ORANI" in ku and "NET" not in ku:
                        val = parse_turkce_sayi(v) / 100.0
                        if val > hb: hb = val
                
                if hb > 0:
                    date_val = parse_date_int(tarih)
                    if date_val > 0:
                        if kod not in gecmis_veriler:
                            gecmis_veriler[kod] = []
                        gecmis_veriler[kod].append({"tarih": tarih, "date_val": date_val, "tutar": hb})
            
            current_date_int = time.gmtime().tm_year * 10000 + time.gmtime().tm_mon * 100 + time.gmtime().tm_mday
            
            for kod, liste in gecmis_veriler.items():
                if len(fiyatlar) > 0 and kod not in fiyatlar: continue
                
                # Sadece mevcut yıl ve geçmiş tarihlere izin ver
                gecerli_liste = [x for x in liste if x["date_val"] <= (current_date_int + 200)]
                if not gecerli_liste:
                    gecerli_liste = liste
                
                son_temettu = max(gecerli_liste, key=lambda x: x["date_val"])
                
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
    data = {"message": "otomatik güncelleme: endekskodu sifirlandi, sutun aramasi genisletildi", "content": encoded}
    if sha: data["sha"] = sha
    
    requests.put(api_url_gh, headers=auth_headers, json=data)

if __name__ == "__main__":
    main()
