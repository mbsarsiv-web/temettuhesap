import os
import sys
import re
import json
import time
import base64
import requests

GITHUB_TOKEN = os.environ.get("GH_TOKEN")
REPO = "mbsarsiv-web/temettuhesap"
DOSYA = "temettu_veri.json"

IS_URL = "https://www.isyatirim.com.tr/_layouts/15/IsYatirim.Website/StockInfo/CompanyInfoAjax.aspx/GetSermayeArttirimlari"
IS_SAYFA = "https://www.isyatirim.com.tr/tr-tr/analiz/hisse/Sayfalar/Temettu-Ve-Sermaye-Artirimi-Tarihcesi.aspx"
TV_URL = "https://scanner.tradingview.com/turkey/scan"


def log(*a):
    print(*a, flush=True)


# ---------------------------------------------------------------- yardimcilar
def tr_upper(s):
    return str(s).replace("i", "İ").replace("ı", "I").upper().replace("İ", "I")


def parse_sayi(val):
    """'1,2345' / '1.234,56' / 1.5 / '0.4930' -> float"""
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
    """Her tur tarih bicimini (YYYYMMDD int, 'dd.mm.yyyy' str) dondurur. Bulamazsa (0, '')."""
    if val is None:
        return 0, ""

    # Sayisal epoch (milisaniye): -62135596800000 = 0001-01-01 = bos tarih
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


# ---------------------------------------------------------------- fiyatlar
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


# ---------------------------------------------------------------- is yatirim
def is_yatirim_satirlari(session):
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Accept-Language": "tr-TR,tr;q=0.9,en;q=0.8",
        "Content-Type": "application/json; charset=UTF-8",
        "X-Requested-With": "XMLHttpRequest",
        "Origin": "https://www.isyatirim.com.tr",
        "Referer": IS_SAYFA,
    })

    # Once sayfaya girip cerez al (bazi sunucular bunu ister)
    try:
        session.get(IS_SAYFA, timeout=20)
    except Exception as e:
        log("Sayfa on-yukleme uyarisi:", repr(e))

    payload = {
        "hisseKodu": "",
        "hisseTanimKodu": "04",
        "yil": 0,
        "zaman": "HEPSI",
        "endeksKodu": "09",
        "sektorKodu": "",
    }

    r = session.post(IS_URL, json=payload, timeout=60)
    log("Is Yatirim HTTP durum:", r.status_code)
    if r.status_code != 200:
        log("Yanit govdesi (ilk 500):", r.text[:500])
        return []

    js = r.json()
    data = js
    if isinstance(js, dict):
        data = js.get("value") or js.get("d") or js
    if isinstance(data, str):
        data = json.loads(data)
    if isinstance(data, dict):
        # {"d": {"results": [...]}} gibi durumlar
        for v in data.values():
            if isinstance(v, list):
                data = v
                break
    if not isinstance(data, list):
        log("Beklenmeyen veri yapisi:", str(data)[:300])
        return []

    log("Is Yatirim ham satir sayisi:", len(data))
    if data:
        log("Ornek satir anahtarlari:", list(data[0].keys()))
        log("Ornek satir:", json.dumps(data[0], ensure_ascii=False)[:600])
    return data


def satiri_coz(satir):
    """Bir satirdan (kod, tarih_int, tarih_str, hisse_basi_brut_tl) cikarir.

    Is Yatirim alanlari:
      SHHE_HS_KODU / HISSE_KODU    -> hisse kodu
      SHHE_TARIH                   -> temettu tarihi (TARIH yedek)
      SHHE_NAKIT_TM_ORAN           -> brut nakit temettu orani (% , 1 TL nominal uzerinden)
      SHHE_NAKIT_TM_ORAN_NET       -> net oran (KULLANILMAZ)
      SHHE_NAKIT_TM_TUTAR          -> toplam dagitilan tutar (hisse basi DEGIL)
    Hisse basi brut TL = SHHE_NAKIT_TM_ORAN / 100
    """
    kod = str(satir.get("SHHE_HS_KODU") or satir.get("HISSE_KODU") or "").strip().upper()

    t_int, t_str = parse_tarih(satir.get("SHHE_TARIH"))
    if t_int <= 0:
        t_int, t_str = parse_tarih(satir.get("TARIH"))

    oran = parse_sayi(satir.get("SHHE_NAKIT_TM_ORAN"))
    hb = oran / 100.0 if oran > 0 else 0.0

    return kod, t_int, t_str, hb


def temettuleri_hazirla(satirlar, fiyatlar):
    gecmis = {}
    for satir in satirlar:
        if not isinstance(satir, dict):
            continue
        kod, t_int, t_str, hb = satiri_coz(satir)
        if not kod or hb <= 0 or t_int <= 0:
            continue
        gecmis.setdefault(kod, []).append({"date_val": t_int, "tarih": t_str, "tutar": hb})

    sonuc = {}
    for kod, liste in gecmis.items():
        if fiyatlar and kod not in fiyatlar:
            continue
        son = max(liste, key=lambda x: x["date_val"])
        sonuc[kod] = {
            "tarih": son["tarih"],
            "hisse_basi_tl": round(son["tutar"], 4),
            "fiyat": round(fiyatlar.get(kod, 0.0), 4),
        }
    return sonuc


# ---------------------------------------------------------------- github
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
    log("GitHub yazma durumu:", p.status_code)
    if p.status_code not in (200, 201):
        log(p.text[:500])
        return False
    return True


# ---------------------------------------------------------------- ana akis
def main():
    session = requests.Session()
    fiyatlar = fiyatlari_cek(session)

    try:
        satirlar = is_yatirim_satirlari(session)
    except Exception as e:
        log("Is Yatirim HATA:", repr(e))
        satirlar = []

    veri = temettuleri_hazirla(satirlar, fiyatlar)
    log("Hazirlanan hisse sayisi:", len(veri))

    if not veri:
        # Bos veriyle mevcut dosyanin uzerine YAZMIYORUZ.
        log("HATA: Hic temettu verisi uretilemedi. Mevcut JSON korunuyor.")
        sys.exit(1)

    if not github_a_yaz(veri):
        sys.exit(1)
    log("Tamamlandi.")


if __name__ == "__main__":
    main()
