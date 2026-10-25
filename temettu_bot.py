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
    s = str(val).strip()
    if not s:
        return 0, ""

    m = re.search(r"Date\((-?\d+)\)", s)
    if m:
        t = time.gmtime(int(m.group(1)) // 1000)
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
    """Bir satirdan (kod, tarih_int, tarih_str, hisse_basi_brut_tl) cikarir."""
    kod = ""
    tarihler = []      # (anahtar, deger)
    hb = 0.0           # hisse basi brut TL
    oran = 0.0         # nakit temettu orani (% nominal)

    for k, v in satir.items():
        ku = tr_upper(k)

        if not kod and (ku.endswith("HS_KOD") or ku.endswith("HISSE_KODU")
                        or ku.endswith("HISSE_KOD") or ku == "KOD" or ku.endswith("_KOD")):
            if isinstance(v, str) and v.strip():
                kod = v.strip().upper()
            continue

        if "TARIH" in ku:
            tarihler.append((ku, v))
            continue

        if hb == 0.0 and ("PAY_BASINA_BRUT_TEMETTU" in ku or ku.endswith("HISSE_BASI_TL")
                          or ("BRUT" in ku and "TEMETTU" in ku and "ORAN" not in ku
                              and "NET" not in ku and "TOPLAM" not in ku)):
            hb = parse_sayi(v)
            continue

        if oran == 0.0 and ku.endswith("NAKIT_TEMETTU_ORANI") and "NET" not in ku:
            oran = parse_sayi(v)

    # Oncelik: key'i TARIH ile biten, sonra icinde TARIH gecen ilk gecerli tarih
    tarihler.sort(key=lambda kv: (0 if kv[0].endswith("TARIH") else 1))
    t_int, t_str = 0, ""
    for _, v in tarihler:
        ti, ts = parse_tarih(v)
        if ti > 0:
            t_int, t_str = ti, ts
            break

    if hb <= 0 and oran > 0:
        hb = oran / 100.0      # nominal 1 TL uzerinden oran -> hisse basi TL

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
