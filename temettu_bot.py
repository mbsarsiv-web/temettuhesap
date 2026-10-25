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

# --- Filtre ayarlari ---
YIL_PENCERESI = 10        # en fazla kac tamamlanmis yil geriye bakilacak
YOGUNLUK_ESIGI = 0.7      # temettu yogunlugu bunun altindaysa hisse elenir (7/10 = 0.7)
MIN_DEGERLENDIRME_YILI = 1  # halka arzdan bu yana en az kac tamamlanmis yil gecmis olmali
HALKA_ARZ_DOSYA = "halka_arz.json"  # {"KOD": halka_arz_yili} - yalnizca son 10 yildaki arzlar yeterli

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


def halka_arz_yillarini_yukle():
    """halka_arz.json: {"ABCDE": 2020, ...}. '_' ile baslayan anahtarlar yok sayilir."""
    try:
        with open(HALKA_ARZ_DOSYA, "r", encoding="utf-8") as f:
            ham = json.load(f)
    except FileNotFoundError:
        log("%s bulunamadi: tum hisseler %d yildan eski kabul edilecek." % (HALKA_ARZ_DOSYA, YIL_PENCERESI))
        return {}
    except Exception as e:
        log("%s okunamadi:" % HALKA_ARZ_DOSYA, repr(e))
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
    log("Halka arz yili tanimli hisse sayisi:", len(sonuc))
    return sonuc


def temettuleri_hazirla(satirlar, fiyatlar, halka_arz=None):
    """
    1) Ayni hisse + ayni tarih + ayni oran tekrar eden satirlar teke indirilir.
    2) Ayni takvim yilindaki tum temettuler TOPLANIR.
    3) Gosterilen tutar = hissenin en guncel temettu yilindaki toplam (hisse basi brut TL).
    4) TEMETTU YOGUNLUGU filtresi:
         degerlendirme araligi = [max(halka_arz_yili, bu_yil-10) ... bu_yil-1]  (tamamlanmis yillar)
         yogunluk = (aralikta temettu verilen farkli yil sayisi) / (aralikteki yil sayisi)
         yogunluk < YOGUNLUK_ESIGI ise hisse elenir.
       Halka arz yili bilinmeyen hisse 10 yildan eski sayilir (aralik = son 10 yil).
    """
    halka_arz = halka_arz or {}
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
    elenen = 0
    kontrol = []   # halka arz yili tanimsiz ama yeni arz olabilecek, elenen hisseler
    for kod, kayitlar in ham.items():
        if fiyatlar and kod not in fiyatlar:
            continue

        yillik = {}
        for (t_int, hb), t_str in kayitlar.items():
            yillik.setdefault(t_int // 10000, []).append((t_int, hb, t_str))

        arz_yili = halka_arz.get(kod)
        bas = max(arz_yili or 0, pencere_basi)
        degerlendirilen = son_tam_yil - bas + 1
        if degerlendirilen < MIN_DEGERLENDIRME_YILI:
            elenen += 1
            continue

        temettu_yili = len([y for y in yillik if bas <= y <= son_tam_yil])
        yogunluk = temettu_yili / float(degerlendirilen)

        if round(yogunluk, 4) < YOGUNLUK_ESIGI:
            elenen += 1
            if arz_yili is None and min(yillik) >= pencere_basi:
                kontrol.append(kod)
            continue

        son_yil = max(yillik.keys())
        odemeler = sorted(yillik[son_yil])
        toplam = sum(o[1] for o in odemeler)

        sonuc[kod] = {
            "tarih": odemeler[-1][2],
            "hisse_basi_tl": round(toplam, 4),
            "fiyat": round(fiyatlar.get(kod, 0.0), 4),
            "yil": son_yil,
            "odeme_sayisi": len(odemeler),
            "tarihler": [o[2] for o in odemeler],
            "arz_yili": arz_yili,
            "degerlendirilen_yil": degerlendirilen,
            "temettu_yili_sayisi": temettu_yili,
            "yogunluk": round(yogunluk, 2),
        }

    log("Yogunluk esigi %.2f nedeniyle elenen hisse: %d" % (YOGUNLUK_ESIGI, elenen))
    if kontrol:
        log("KONTROL ET - halka arz yili tanimsiz, ilk temettusu son %d yilda ve elenen hisseler "
            "(yeni arz ise halka_arz.json'a ekleyin): %s" % (YIL_PENCERESI, ", ".join(sorted(kontrol))))
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

    veri = temettuleri_hazirla(satirlar, fiyatlar, halka_arz_yillarini_yukle())
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
