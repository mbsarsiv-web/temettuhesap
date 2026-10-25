import os
import sys
import re
import json
import time
import base64
import requests

GITHUB_TOKEN = os.environ.get("GH_TOKEN")
REPO = "mbsarsiv-web/temettu-bot"
DOSYA = "temettu_veri.json"

# --- Filtre ayarlari ---
YIL_PENCERESI = 10          # Son 10 Yıl filtresi
YOGUNLUK_ESIGI = 0.5        # >= 0.50 (Orta) temettü yoğunluğu
MIN_DEGERLENDIRME_YILI = 1  # Halka arzdan bu yana en az kac tamamlanmis yil gecmis olmali
MIN_ORTALAMA_VERIM = 3.0    # >= %3 Ortalama Temettü Verimi
FIYAT_GECMISI_URL = ("https://www.isyatirim.com.tr/_layouts/15/Isyatirim.Website/Common/Data.aspx/HisseTekil"
                     "?hisse=%s&startdate=%s&enddate=%s")
TV_ARZ_ALAN_ADAYLARI = ["ipo_offer_date_first", "ipo_offer_date_last", "ipo_date",
                        "listing_date", "first_trading_date"] 
TAHMINI_ARZ_YILI_KULLAN = True  
IZLE = ["MACKO"]                
OTOMATIK_ZAMAN_ASIMI = 8     
OTOMATIK_BUTCE_SN = 240      
HALKA_ARZ_DOSYA = "halka_arz.json"  

IS_URL = "https://www.isyatirim.com.tr/_layouts/15/IsYatirim.Website/StockInfo/CompanyInfoAjax.aspx/GetSermayeArttirimlari"
IS_SAYFA = "https://www.isyatirim.com.tr/tr-tr/analiz/hisse/Sayfalar/Temettu-Ve-Sermaye-Artirimi-Tarihcesi.aspx"
TV_URL = "https://scanner.tradingview.com/turkey/scan"


def log(*a):
    print(*a, flush=True)

def tr_upper(s):
    return str(s).replace("i", "İ").replace("ı", "I").upper().replace("İ", "I")

def parse_sayi(val):
    if val is None:
        return 0.0
    if isinstance(val, bool):
        return 0.0
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).strip().replace("%", "").replace("TL", "").replace(" ", "")
    if s == "":
        return 0.0
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return 0.0

def parse_tarih(val):
    if val is None:
        return 0, ""

    if isinstance(val, (int, float)) and not isinstance(val, bool):
        if abs(val) > 1e11:
            t = time.gmtime(val / 1000.0)
            if t.tm_year < 1990:
                return 0, ""
            return (t.tm_year * 10000 + t.tm_mon * 100 + t.tm_mday,
                    "%02d.%02d.%04d" % (t.tm_mday, t.tm_mon, t.tm_year))
        return 0, ""

    s = str(val).strip()
    if not s:
        return 0, ""

    m = re.search(r"Date\((-?\d+)\)", s)
    if m:
        t = time.gmtime(int(m.group(1)) // 1000)
        if t.tm_year < 1990:
            return 0, ""
        return (t.tm_year * 10000 + t.tm_mon * 100 + t.tm_mday,
                "%02d.%02d.%04d" % (t.tm_mday, t.tm_mon, t.tm_year))

    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return y * 10000 + mo * 100 + d, "%02d.%02d.%04d" % (d, mo, y)

    m = re.search(r"(\d{1,2})[./-](\d{1,2})[./-](\d{4})", s)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return y * 10000 + mo * 100 + d, "%02d.%02d.%04d" % (d, mo, y)

    if re.fullmatch(r"\d{8}", s):
        y, mo, d = int(s[:4]), int(s[4:6]), int(s[6:])
        return y * 10000 + mo * 100 + d, "%02d.%02d.%04d" % (d, mo, y)

    return 0, ""

def fiyatlari_cek(session):
    payload = {
        "columns": ["name", "close"],
        "filter": [{"left": "type", "operation": "in_range", "right": ["stock"]}],
        "range": [0, 1500],
    }
    fiyatlar = {}
    try:
        r = session.post(TV_URL, json=payload, timeout=20)
        js = r.json()
        for item in js.get("data", []):
            kod = str(item["d"][0]).replace("BIST:", "").strip().upper()
            fiyat = item["d"][1]
            if fiyat:
                fiyatlar[kod] = float(fiyat)
        log("TradingView fiyat sayisi:", len(fiyatlar))
    except Exception as e:
        log("TradingView HATA:", repr(e))
    return fiyatlar

def _tv_yil(v):
    if v is None or isinstance(v, bool):
        return None
    bu_yil = time.gmtime().tm_year
    if isinstance(v, (int, float)):
        x = float(v)
        if x > 1e11:
            x /= 1000.0
        if x <= 0:
            return None
        y = time.gmtime(x).tm_year
    else:
        ti, _ = parse_tarih(v)
        y = ti // 10000 if ti else 0
    return y if 1980 <= y <= bu_yil else None

def tv_arz_yillari(session):
    url = TV_URL
    taban = [{"left": "type", "operation": "in_range", "right": ["stock"]}]
    calisan = []
    for kolon in TV_ARZ_ALAN_ADAYLARI:
        try:
            r = session.post(url, json={"columns": ["name", kolon], "filter": taban, "range": [0, 5]},
                             timeout=20)
            if r.status_code == 200 and r.json().get("data") is not None:
                calisan.append(kolon)
        except Exception as e:
            pass

    if not calisan:
        return {}

    sonuc = {}
    try:
        r = session.post(url, json={"columns": ["name"] + calisan, "filter": taban, "range": [0, 1500]},
                         timeout=30)
        for item in r.json().get("data", []):
            kod = str(item["d"][0]).replace("BIST:", "").strip().upper()
            yillar = [y for y in (_tv_yil(v) for v in item["d"][1:]) if y]
            if yillar:
                sonuc[kod] = min(yillar)
    except Exception as e:
        log("TradingView halka arz verisi HATA:", repr(e))
    return sonuc

def is_yatirim_satirlari(session):
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Accept-Language": "tr-TR,tr;q=0.9,en;q=0.8",
        "Content-Type": "application/json; charset=UTF-8",
        "X-Requested-With": "XMLHttpRequest",
        "Origin": "https://www.isyatirim.com.tr",
        "Referer": IS_SAYFA,
    })

    for deneme in range(3):
        try:
            session.get(IS_SAYFA, timeout=30)
        except Exception:
            pass

        payload = {
            "hisseKodu": "",
            "hisseTanimKodu": "04",
            "yil": 0,
            "zaman": "HEPSI",
            "endeksKodu": "09",
            "sektorKodu": "",
        }

        try:
            log(f"Is Yatirim'dan veri cekiliyor (Deneme {deneme + 1}/3)...")
            r = session.post(IS_URL, json=payload, timeout=120)
            if r.status_code == 200:
                js = r.json()
                data = js
                if isinstance(js, dict):
                    data = js.get("value") or js.get("d") or js
                if isinstance(data, str):
                    data = json.loads(data)
                if isinstance(data, dict):
                    for v in data.values():
                        if isinstance(v, list):
                            data = v
                            break
                if isinstance(data, list) and len(data) > 0:
                    return data
            else:
                log(f"Is Yatirim HTTP Hata: {r.status_code}")
        except requests.exceptions.RequestException as e:
            log(f"Is Yatirim HATA: {repr(e)}")
        
        time.sleep(5)

    return []

def satiri_coz(satir):
    kod = str(satir.get("SHHE_HS_KODU") or satir.get("HISSE_KODU") or "").strip().upper()

    t_int, t_str = parse_tarih(satir.get("SHHE_TARIH"))
    if t_int <= 0:
        t_int, t_str = parse_tarih(satir.get("TARIH"))

    oran = parse_sayi(satir.get("SHHE_NAKIT_TM_ORAN"))
    hb = oran / 100.0 if oran > 0 else 0.0

    return kod, t_int, t_str, hb

_OTO = {"ardisik_hata": 0, "devre_disi": False, "baslangic": None}

def ilk_islem_yili_bul(kod, baslangic_yili):
    if _OTO["devre_disi"]:
        return None
    if _OTO["baslangic"] is None:
        _OTO["baslangic"] = time.time()
    if time.time() - _OTO["baslangic"] > OTOMATIK_BUTCE_SN:
        _OTO["devre_disi"] = True
        return None

    bas = "01-01-%d" % baslangic_yili
    bit = time.strftime("%d-%m-%Y", time.gmtime())
    hdr = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Accept": "application/json, text/plain, */*",
        "Referer": IS_SAYFA,
    }

    def hata(mesaj):
        _OTO["ardisik_hata"] += 1
        if _OTO["ardisik_hata"] >= 5:
            _OTO["devre_disi"] = True

    for sembol in (kod + ".E", kod):
        try:
            r = requests.get(FIYAT_GECMISI_URL % (sembol, bas, bit), headers=hdr, timeout=OTOMATIK_ZAMAN_ASIMI)
        except Exception:
            hata("istek hatasi")
            return None
        if r.status_code != 200:
            hata("HTTP hatasi")
            return None
        try:
            js = r.json()
        except Exception:
            hata("JSON hatasi")
            return None

        _OTO["ardisik_hata"] = 0
        rows = js.get("value") if isinstance(js, dict) else js
        if not rows:
            continue

        en_kucuk = 0
        for row in rows:
            for k, v in row.items():
                if "TARIH" in str(k).upper():
                    ti, _ = parse_tarih(v)
                    if ti > 0 and (en_kucuk == 0 or ti < en_kucuk):
                        en_kucuk = ti
                    break
        if en_kucuk == 0:
            continue
        yil = en_kucuk // 10000
        return yil if yil > baslangic_yili else None
    return None

def halka_arz_yillarini_yukle():
    try:
        with open(HALKA_ARZ_DOSYA, "r", encoding="utf-8") as f:
            ham = json.load(f)
    except Exception:
        return {}

    sonuc = {}
    for k, v in ham.items():
        if str(k).startswith("_"):
            continue
        try:
            y = int(str(v).strip()[-4:]) if len(str(v).strip()) > 4 else int(v)
        except ValueError:
            continue
        if 1900 < y < 2100:
            sonuc[str(k).strip().upper()] = y
    return sonuc

def temettuleri_hazirla(satirlar, fiyatlar, halka_arz=None, arz_bulucu=None, tv_arz=None):
    halka_arz = halka_arz or {}
    tv_arz = tv_arz or {}
    bu_yil = time.gmtime().tm_year
    son_tam_yil = bu_yil - 1
    pencere_basi = bu_yil - YIL_PENCERESI

    ham = {}
    for satir in satirlar:
        if not isinstance(satir, dict):
            continue
        kod, t_int, t_str, hb = satiri_coz(satir)
        if not kod or hb <= 0 or t_int <= 0:
            continue
        ham.setdefault(kod, {})[(t_int, round(hb, 6))] = t_str

    sonuc = {}
    elenen_yogunluk = 0
    elenen_verim = 0

    for kod, kayitlar in ham.items():
        if fiyatlar and kod not in fiyatlar:
            continue

        yillik = {}
        for (t_int, hb), t_str in kayitlar.items():
            yillik.setdefault(t_int // 10000, []).append((t_int, hb, t_str))

        arz_yili = halka_arz.get(kod)
        arz_kaynagi = "dosya" if arz_yili else None
        if arz_yili is None and kod in tv_arz:
            arz_yili = tv_arz[kod]
            arz_kaynagi = "tradingview"

        def degerlendir(arz):
            bas = max(arz or 0, pencere_basi)
            deg = son_tam_yil - bas + 1
            ty = len([y for y in yillik if bas <= y <= son_tam_yil])
            yog = (ty / float(deg)) if deg > 0 else 0.0
            return bas, deg, ty, yog

        bas, degerlendirilen, temettu_yili, yogunluk = degerlendir(arz_yili)
        gecti = degerlendirilen >= MIN_DEGERLENDIRME_YILI and round(yogunluk, 4) >= YOGUNLUK_ESIGI

        if not gecti and arz_yili is None and min(yillik) >= pencere_basi and arz_bulucu:
            bulunan = arz_bulucu(kod, pencere_basi)
            if bulunan:
                arz_yili = bulunan
                arz_kaynagi = "otomatik"
                bas, degerlendirilen, temettu_yili, yogunluk = degerlendir(arz_yili)
                gecti = (degerlendirilen >= MIN_DEGERLENDIRME_YILI and round(yogunluk, 4) >= YOGUNLUK_ESIGI)
            elif TAHMINI_ARZ_YILI_KULLAN:
                arz_yili = min(yillik)
                arz_kaynagi = "tahmin"
                bas, degerlendirilen, temettu_yili, yogunluk = degerlendir(arz_yili)
                gecti = (degerlendirilen >= MIN_DEGERLENDIRME_YILI and round(yogunluk, 4) >= YOGUNLUK_ESIGI)

        # 1. Filtre: Yoğunluk
        if not gecti:
            elenen_yogunluk += 1
            continue

        son_yil = max(yillik.keys())
        odemeler = sorted(yillik[son_yil])
        toplam = sum(o[1] for o in odemeler)
        fiyat = fiyatlar.get(kod, 0.0)

        # 2. Filtre: Ortalama Verim (%3 Sınırı)
        toplam_temettu_pencere = sum(sum(o[1] for o in yillik[y]) for y in yillik if y >= bas)
        temettu_veren_yil_sayisi = len([y for y in yillik if y >= bas])
        
        if temettu_veren_yil_sayisi > 0 and fiyat > 0:
            ortalama_temettu_tl = toplam_temettu_pencere / temettu_veren_yil_sayisi
            ortalama_verim = (ortalama_temettu_tl / fiyat) * 100
        else:
            ortalama_verim = 0.0

        if ortalama_verim < MIN_ORTALAMA_VERIM:
            elenen_verim += 1
            continue

        sonuc[kod] = {
            "tarih": odemeler[-1][2],
            "hisse_basi_tl": round(toplam, 4),
            "fiyat": round(fiyat, 4),
            "yil": son_yil,
            "odeme_sayisi": len(odemeler),
            "tarihler": [o[2] for o in odemeler],
            "arz_yili": arz_yili,
            "arz_kaynagi": arz_kaynagi,
            "degerlendirilen_yil": degerlendirilen,
            "temettu_yili_sayisi": temettu_yili,
            "yogunluk": round(yogunluk, 2),
            "ortalama_verim": round(ortalama_verim, 2)
        }

    log("Yoğunluk sınırına (<0.50) takılan hisse:", elenen_yogunluk)
    log("Ortalama verim sınırına (<%3) takılan hisse:", elenen_verim)
    return sonuc

def github_a_yaz(veri):
    if not GITHUB_TOKEN:
        log("GH_TOKEN yok, GitHub'a yazilamadi.")
        return False

    url = "https://api.github.com/repos/%s/contents/%s" % (REPO, DOSYA)
    h = {"Authorization": "Bearer " + GITHUB_TOKEN, "Accept": "application/vnd.github+json"}

    sha = ""
    g = requests.get(url, headers=h, timeout=30)
    if g.status_code == 200:
        sha = g.json().get("sha", "")

    icerik = json.dumps(veri, indent=2, ensure_ascii=False, sort_keys=True)
    govde = {
        "message": "otomatik guncelleme: %d hisse temettu verisi" % len(veri),
        "content": base64.b64encode(icerik.encode("utf-8")).decode("utf-8"),
    }
    if sha:
        govde["sha"] = sha

    p = requests.put(url, headers=h, json=govde, timeout=30)
    if p.status_code not in (200, 201):
        log(p.text[:500])
        return False
    return True

def main():
    session = requests.Session()
    fiyatlar = fiyatlari_cek(session)
    
    satirlar = is_yatirim_satirlari(session)

    if not satirlar:
        log("HATA: Is Yatirim'dan gecerli satir alinamadi.")
        sys.exit(1)

    tv_arz = tv_arz_yillari(session)
    veri = temettuleri_hazirla(satirlar, fiyatlar, halka_arz_yillarini_yukle(), ilk_islem_yili_bul, tv_arz)
    log("Hazirlanan hisse sayisi:", len(veri))

    if not veri:
        log("HATA: Hic temettu verisi uretilemedi.")
        sys.exit(1)

    if not github_a_yaz(veri):
        sys.exit(1)
    log("Tamamlandi.")

if __name__ == "__main__":
    main()
