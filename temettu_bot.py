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

# NOT: Filtreleme (Yoğunluk >= 0,50 / Son 10 Yıl / Verim >= %3) bu botta YAPILMAZ.
# Hisse listesi ve filtreler "TEMETTÜ ANALİZ" uygulamasının kaynağından, hesaplama
# uygulaması (index.html) içinde uygulanır. Bot, fiyat ve hisse başı temettü verisini üretir.

IS_URL = "https://www.isyatirim.com.tr/_layouts/15/IsYatirim.Website/StockInfo/CompanyInfoAjax.aspx/GetSermayeArttirimlari"
IS_SAYFA = "https://www.isyatirim.com.tr/tr-tr/analiz/hisse/Sayfalar/Temettu-Ve-Sermaye-Artirimi-Tarihcesi.aspx"
TV_URL = "https://scanner.tradingview.com/turkey/scan"
FIYAT_GECMISI_URL = ("https://www.isyatirim.com.tr/_layouts/15/Isyatirim.Website/Common/Data.aspx/HisseTekil"
                     "?hisse=%s&startdate=%s&enddate=%s")
GAS_URL = "https://script.google.com/macros/s/AKfycbxo9u41oj1aXxgkuE7gTnHdeG_mzT41o3WmaYhhqNNUJi_NTDzLOmSBSnHAhfsvhOiK/exec"

YEDEK_FIYAT_BUTCE_SN = 300   # Yedek fiyat kaynakları için toplam süre sınırı


def log(*a):
    print(*a, flush=True)

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

# ----------------------------------------------------------------------
# FİYAT KAYNAKLARI
# ----------------------------------------------------------------------
def _tv_tara(session, turler):
    payload = {
        "columns": ["name", "close"],
        "filter": [{"left": "type", "operation": "in_range", "right": turler}],
        "range": [0, 1500],
    }
    sonuc = {}
    try:
        r = session.post(TV_URL, json=payload, timeout=20)
        js = r.json()
        for item in js.get("data", []):
            kod = str(item["d"][0]).replace("BIST:", "").strip().upper()
            fiyat = item["d"][1]
            if fiyat:
                sonuc[kod] = float(fiyat)
    except Exception as e:
        log("TradingView (%s) HATA:" % ",".join(turler), repr(e))
    return sonuc

def fiyatlari_cek(session):
    fiyatlar = _tv_tara(session, ["stock"])
    log("TradingView hisse fiyat sayisi:", len(fiyatlar))

    # Bazı kodlar 'stock' dışında bir türde olabilir (ör. fon/DR): ayrı ayrı denenir
    for tur in (["fund"], ["dr"]):
        ek = _tv_tara(session, tur)
        yeni = 0
        for k, v in ek.items():
            if k not in fiyatlar:
                fiyatlar[k] = v
                yeni += 1
        if ek:
            log("TradingView ek tur %s: +%d" % (tur[0], yeni))
    return fiyatlar

def yahoo_fiyat(kod):
    url = "https://query2.finance.yahoo.com/v8/finance/chart/%s.IS?interval=1d&range=5d" % kod
    try:
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=12)
        if r.status_code != 200:
            return 0.0
        res = r.json().get("chart", {}).get("result") or []
        if not res:
            return 0.0
        meta = res[0].get("meta", {})
        p = meta.get("regularMarketPrice")
        if p and float(p) > 0:
            return float(p)
        kapanislar = (res[0].get("indicators", {}).get("quote") or [{}])[0].get("close") or []
        for v in reversed(kapanislar):
            if v:
                return float(v)
    except Exception:
        pass
    return 0.0

def isyatirim_fiyat(kod):
    bit = time.strftime("%d-%m-%Y", time.gmtime())
    bas = time.strftime("%d-%m-%Y", time.gmtime(time.time() - 25 * 86400))
    hdr = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Accept": "application/json, text/plain, */*",
        "Referer": IS_SAYFA,
    }
    for sembol in (kod + ".E", kod):
        try:
            r = requests.get(FIYAT_GECMISI_URL % (sembol, bas, bit), headers=hdr, timeout=12)
            if r.status_code != 200:
                continue
            js = r.json()
            rows = js.get("value") if isinstance(js, dict) else js
            if not rows:
                continue
            en_yeni, fiyat = 0, 0.0
            for row in rows:
                if not isinstance(row, dict):
                    continue
                ti = 0
                for k, v in row.items():
                    if "TARIH" in str(k).upper():
                        ti, _ = parse_tarih(v)
                        break
                p = 0.0
                for k, v in row.items():
                    if "KAPANIS" in str(k).upper():
                        p = parse_sayi(v)
                        break
                if p <= 0:
                    for k in ("HGDG_AOF", "AOF"):
                        if k in row:
                            p = parse_sayi(row[k])
                            break
                if p > 0 and ti >= en_yeni:
                    en_yeni, fiyat = ti, p
            if fiyat > 0:
                return fiyat
        except Exception:
            continue
    return 0.0

def eksik_fiyatlari_tamamla(kodlar, fiyatlar, oncelik=()):
    # Temettü Analiz listesindeki hisseler önce denenir (süre sınırı dolarsa onlar kaçmasın)
    eksik = sorted((k for k in kodlar if k not in fiyatlar), key=lambda k: (k not in oncelik, k))
    if not eksik:
        return
    log("Fiyati eksik hisse sayisi:", len(eksik), "->", ", ".join(eksik[:40]))
    baslangic = time.time()
    bulunan = 0
    for kod in eksik:
        if time.time() - baslangic > YEDEK_FIYAT_BUTCE_SN:
            log("Yedek fiyat suresi doldu.")
            break
        p = yahoo_fiyat(kod)
        kaynak = "yahoo"
        if p <= 0:
            p = isyatirim_fiyat(kod)
            kaynak = "isyatirim"
        if p > 0:
            fiyatlar[kod] = p
            bulunan += 1
            log("  yedek fiyat: %s = %s (%s)" % (kod, p, kaynak))
        time.sleep(0.3)
    kalan = [k for k in eksik if k not in fiyatlar]
    log("Yedek kaynaklarla bulunan:", bulunan, "| hala fiyati olmayan:", ", ".join(kalan) if kalan else "yok")

# ----------------------------------------------------------------------
# "TEMETTÜ ANALİZ" kaynağındaki hisse kodları
# ----------------------------------------------------------------------
def gas_kodlari(session):
    try:
        r = session.get(GAS_URL + "?t=%d" % int(time.time()), timeout=120)
        js = r.json()
        tablo = js.get("anaTablo") or []
        kodlar = set()
        for satir in tablo[1:]:
            if satir and satir[0] is not None:
                k = str(satir[0]).strip().upper()
                if k:
                    kodlar.add(k)
        log("Temettu Analiz kaynagindaki hisse sayisi:", len(kodlar))
        return kodlar
    except Exception as e:
        log("Temettu Analiz kaynagi okunamadi (yedek kullanilmayacak):", repr(e))
        return set()

# ----------------------------------------------------------------------
# İş Yatırım temettü verisi
# ----------------------------------------------------------------------
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

def temettu_kayitlari(satirlar):
    ham = {}
    for satir in satirlar:
        if not isinstance(satir, dict):
            continue
        kod, t_int, t_str, hb = satiri_coz(satir)
        if not kod or hb <= 0 or t_int <= 0:
            continue
        ham.setdefault(kod, {})[(t_int, round(hb, 6))] = t_str
    return ham

def veriyi_hazirla(ham, fiyatlar, gas):
    sonuc = {}
    atlanan = 0

    for kod, kayitlar in ham.items():
        fiyat = fiyatlar.get(kod, 0.0)
        if fiyat <= 0 and kod not in gas:
            atlanan += 1
            continue

        yillik = {}
        for (t_int, hb), t_str in kayitlar.items():
            yillik.setdefault(t_int // 10000, []).append((t_int, hb, t_str))

        son_yil = max(yillik.keys())
        odemeler = sorted(yillik[son_yil])
        toplam = sum(o[1] for o in odemeler)

        sonuc[kod] = {
            "tarih": odemeler[-1][2],
            "hisse_basi_tl": round(toplam, 4),
            "fiyat": round(fiyat, 4),
            "yil": son_yil,
            "odeme_sayisi": len(odemeler),
            "tarihler": [o[2] for o in odemeler],
        }

    # Temettü kaydı olmayan ama Temettü Analiz listesinde bulunan hisseler (yalnızca fiyat)
    sadece_fiyat = 0
    for kod in gas:
        if kod in sonuc:
            continue
        fiyat = fiyatlar.get(kod, 0.0)
        if fiyat > 0:
            sonuc[kod] = {
                "tarih": "",
                "hisse_basi_tl": 0.0,
                "fiyat": round(fiyat, 4),
                "yil": 0,
                "odeme_sayisi": 0,
                "tarihler": [],
            }
            sadece_fiyat += 1

    log("Fiyati olmadigi icin atlanan (listede olmayan) hisse:", atlanan)
    log("Yalnizca fiyat eklenen hisse:", sadece_fiyat)
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

    ham = temettu_kayitlari(satirlar)
    gas = gas_kodlari(session)

    hedef = set(ham.keys()) | gas
    eksik_fiyatlari_tamamla(hedef, fiyatlar, gas)

    veri = veriyi_hazirla(ham, fiyatlar, gas)
    log("Hazirlanan hisse sayisi:", len(veri))

    if not veri:
        log("HATA: Hic temettu verisi uretilemedi.")
        sys.exit(1)

    if not github_a_yaz(veri):
        sys.exit(1)
    log("Tamamlandi.")

if __name__ == "__main__":
    main()
