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

# NOT: Filtreleme (Yoğunluk / Dönem / Verim) bu botta YAPILMAZ.
# Hisse listesi ve filtreler "TEMETTÜ ANALİZ" uygulamasının kaynağından, hesaplama
# uygulaması (index.html) içinde uygulanır. Bot, fiyat ve hisse başı temettü verisini üretir.
#
# Her hisse için tarihli temettü geçmişi ("temettuler") ve bedelsiz bölünme
# kayıtları ("bolunmeler") yazılır. index.html, geçmiş yıllardaki lot sayısını
# bu verilerle hesaplar (güncel lot ÷ o tarihten sonraki bedelsiz çarpanları).
#
# BÖLÜNME KAYNAĞI ÖNCELİĞİ:
#   1) İş Yatırım (tam tarihli, en güvenilir)
#   2) Temettü Analiz (GAS) tablosundaki "Bedelsiz" sütunu (İş Yatırım'da kaydı olmayan hisseler için)
# İki kaynak aynı hissede ASLA birleştirilmez (çift sayımı önlemek için).

IS_URL = "https://www.isyatirim.com.tr/_layouts/15/IsYatirim.Website/StockInfo/CompanyInfoAjax.aspx/GetSermayeArttirimlari"
IS_SAYFA = "https://www.isyatirim.com.tr/tr-tr/analiz/hisse/Sayfalar/Temettu-Ve-Sermaye-Artirimi-Tarihcesi.aspx"
TV_URL = "https://scanner.tradingview.com/turkey/scan"
FIYAT_GECMISI_URL = ("https://www.isyatirim.com.tr/_layouts/15/Isyatirim.Website/Common/Data.aspx/HisseTekil"
                     "?hisse=%s&startdate=%s&enddate=%s")
GAS_URL = "https://script.google.com/macros/s/AKfycbxo9u41oj1aXxgkuE7gTnHdeG_mzT41o3WmaYhhqNNUJi_NTDzLOmSBSnHAhfsvhOiK/exec"

YEDEK_FIYAT_BUTCE_SN = 300   # Yedek fiyat kaynakları için toplam süre sınırı
GECMIS_BASLANGIC_YIL = 2018  # Tarihli temettü / bölünme kayıtları bu yıldan itibaren tutulur


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

def sayi_metni(x):
    """Gereksiz sıfırları atılmış sayı metni (JSON'u küçük tutmak için)."""
    return ("%.6f" % x).rstrip("0").rstrip(".")

_TR_HARITA = str.maketrans("İıŞşĞğÜüÖöÇç", "IISSGGUUOOCC")

def norm(s):
    """Türkçe harfleri sadeleştirip büyük harfe çevirir (başlık/alan adı karşılaştırmaları için)."""
    return str(s).translate(_TR_HARITA).upper().strip()

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

def sektorleri_cek(session):
    """TradingView'dan sektör (sector) ve alt sektör (industry). Fiyat sorgusundan AYRI yapılır."""
    sonuc = {}
    for kolonlar in (["name", "sector", "industry"], ["name", "sector"]):
        payload = {
            "columns": kolonlar,
            "filter": [{"left": "type", "operation": "in_range", "right": ["stock"]}],
            "range": [0, 1500],
        }
        try:
            r = session.post(TV_URL, json=payload, timeout=20)
            if r.status_code != 200:
                log("TradingView sektor HTTP:", r.status_code, kolonlar)
                continue
            for item in r.json().get("data", []):
                d = item["d"]
                kod = str(d[0]).replace("BIST:", "").strip().upper()
                sektor = d[1] if len(d) > 1 else None
                sanayi = d[2] if len(d) > 2 else None
                if sektor:
                    sonuc[kod] = {"sektor": str(sektor), "sanayi": str(sanayi) if sanayi else ""}
            if sonuc:
                break
        except Exception as e:
            log("TradingView sektor HATA:", repr(e))
    log("Sektor bilgisi alinan hisse sayisi:", len(sonuc))
    return sonuc

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
# "TEMETTÜ ANALİZ" kaynağındaki hisse kodları + "Bedelsiz" sütunu
# ----------------------------------------------------------------------
def gas_tarih_int(val, yil):
    """Satırdaki tam tarih varsa onu, yalnızca yıl varsa o yılın 30 Haziran'ını döndürür."""
    t, _ = parse_tarih(val)
    if t > 0:
        return t
    return yil * 10000 + 630

def gas_bedelsiz_tara(tablo, sonuc):
    """Başlığında KOD, (YIL/TARIH) ve BEDEL(SIZ) geçen herhangi bir tablodan bedelsiz oranlarını ekler.
    sonuc: {kod: {tarih_int: oran_yuzde}}"""
    if not isinstance(tablo, list) or len(tablo) < 2 or not isinstance(tablo[0], list):
        return 0
    bas = [norm(x) for x in tablo[0]]
    kod_i = next((i for i, b in enumerate(bas) if b == "KOD"), None)
    bd_i = next((i for i, b in enumerate(bas) if "BEDEL" in b), None)
    tar_i = next((i for i, b in enumerate(bas) if b in ("YIL", "TARIH")), None)
    if kod_i is None or bd_i is None or tar_i is None:
        return 0
    eklenen = 0
    for satir in tablo[1:]:
        if not isinstance(satir, list) or len(satir) <= max(kod_i, bd_i, tar_i):
            continue
        kod = str(satir[kod_i] or "").strip().upper()
        oran = parse_sayi(satir[bd_i])
        if not kod or oran <= 0:
            continue
        m = re.search(r"(19|20)\d\d", str(satir[tar_i]))
        if not m:
            continue
        t = gas_tarih_int(satir[tar_i], int(m.group(0)))
        d = sonuc.setdefault(kod, {})
        d[t] = max(d.get(t, 0.0), oran)
        eklenen += 1
    return eklenen

def gas_verisi(session):
    """(hisse_kodlari, {kod: {tarih_int: bedelsiz_oran}}) döndürür."""
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

        bol = {}
        if isinstance(js, dict):
            log("GAS tablo anahtarlari:", ", ".join(str(k) for k in js.keys()))
            for anahtar, deger in js.items():
                if anahtar == "anaTablo":
                    continue
                if isinstance(deger, list) and deger and isinstance(deger[0], list):
                    n = gas_bedelsiz_tara(deger, bol)
                    log("GAS '%s' tablosu basliklari: %s | bedelsiz satiri: %d" %
                        (anahtar, [str(x) for x in deger[0]], n))
        log("GAS'ta bedelsiz kaydi olan hisse sayisi:", len(bol))
        return kodlar, bol
    except Exception as e:
        log("Temettu Analiz kaynagi okunamadi (yedek kullanilmayacak):", repr(e))
        return set(), {}

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

        # ÇÖZÜM: "09" (BİST TÜM) kodu geri eklendi, Tip ("") boş bırakılarak tüm bedelsiz ve temettüler dahil edildi.
        payload = {
            "hisseKodu": "",
            "hisseTanimKodu": "",
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

def kod_tarih(satir):
    kod = str(satir.get("SHHE_HS_KODU") or satir.get("HISSE_KODU") or "").strip().upper()
    t_int, t_str = parse_tarih(satir.get("SHHE_TARIH"))
    if t_int <= 0:
        t_int, t_str = parse_tarih(satir.get("TARIH"))
    return kod, t_int, t_str

def satiri_coz(satir):
    kod, t_int, t_str = kod_tarih(satir)
    oran = parse_sayi(satir.get("SHHE_NAKIT_TM_ORAN"))
    hb = oran / 100.0 if oran > 0 else 0.0
    return kod, t_int, t_str, hb

def alan_sinifi(k):
    """İş Yatırım satırındaki alan adının hangi sütuna karşılık geldiğini tahmin eder.
    Sermaye Artırımları tablosu: Bölünme Sonrası Sermaye | Tarih | Bedelli Oran | Bedelli Nom. Tutar |
    Diğer (*) | Bedelsiz IK Oran | Bedelsiz Temettü Oran | Nakit Tem. Brüt | Nakit Temettü | Hisse Başı Brüt"""
    n = norm(k)
    if "BDLI" in n or "BEDELLI" in n:
        return "bedelli"
    if "NAKIT" in n or "HS_BRUT" in n or "HISSE_BASI" in n:
        return "nakit"
    if "BDLZ" in n or "BEDELSIZ" in n:
        return "bedelsiz"
    if "DIGER" in n or "_DGR" in n or "DGR_" in n or "_DIG_" in n:
        return "diger"
    if "SERMAYE" in n:
        return "sermaye"
    return ""

def bedelsiz_orani(satir):
    """Satırdaki toplam bedelsiz oranı (%): Bedelsiz IK + Bedelsiz Temettü + Diğer (*) sütunları.
    (FROTO 07.05.2025 bedelsizi %900 'Diğer' sütunundadır.)
    Hazır bir 'toplam' alan varsa, alt alanların toplamıyla karşılaştırılıp büyük olan alınır."""
    toplam, alt = [], []
    for k, v in satir.items():
        sinif = alan_sinifi(k)
        n = norm(k)
        val = parse_sayi(v)
        if val <= 0:
            continue
        if sinif == "bedelsiz" and "ORAN" in n:
            if any(x in n for x in ("_IK_", "_TM_")):
                alt.append(val)
            else:
                toplam.append(val)
        elif sinif == "diger":
            alt.append(val)
    return max(max(toplam) if toplam else 0.0, sum(alt))

def sermaye_degeri(satir):
    """'Bölünme Sonrası Sermaye (TL)' değeri (bulunamazsa 0)."""
    degerler = [parse_sayi(v) for k, v in satir.items() if alan_sinifi(k) == "sermaye"]
    return max(degerler) if degerler else 0.0

def bedelli_durumu(satir):
    """(bedelli alanı var mı, bedelli değer > 0 mı)"""
    var, pozitif = False, False
    for k, v in satir.items():
        if alan_sinifi(k) == "bedelli":
            var = True
            if parse_sayi(v) > 0:
                pozitif = True
    return var, pozitif

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

def bolunme_kayitlari(satirlar):
    """{kod: {tarih_int: bedelsiz_oran_yuzde}} - temettü olsun olmasın tüm bedelsiz kayıtları.

    1) Sütunlardan okunan oran (Bedelsiz IK + Bedelsiz Temettü + Diğer).
    2) Çapraz kontrol / yedek: 'Bölünme Sonrası Sermaye' bir önceki tarihe göre arttıysa ve satırda bedelli
       artırım yoksa, oran sermaye artışından türetilir (sütun adı tanınmasa bile bölünme kaçmaz)."""
    sonuc = {}
    sermaye = {}   # {kod: {tarih: sermaye}}
    bedelli = {}   # {kod: {tarih: (alan_var, pozitif)}}
    if satirlar and isinstance(satirlar[0], dict):
        log("Ornek satir alanlari:", ", ".join(sorted(str(k) for k in satirlar[0].keys())))
        log("Alan siniflari:", ", ".join("%s=%s" % (k, alan_sinifi(k) or "-") for k in sorted(satirlar[0].keys())))

    for satir in satirlar:
        if not isinstance(satir, dict):
            continue
        kod, t_int, _ = kod_tarih(satir)
        if not kod or t_int <= 0:
            continue
        sm = sermaye_degeri(satir)
        if sm > 0:
            d = sermaye.setdefault(kod, {})
            d[t_int] = max(d.get(t_int, 0.0), sm)
        var, poz = bedelli_durumu(satir)
        eski = bedelli.setdefault(kod, {}).get(t_int, (False, False))
        bedelli[kod][t_int] = (eski[0] or var, eski[1] or poz)
        oran = bedelsiz_orani(satir)
        if oran > 0:
            d = sonuc.setdefault(kod, {})
            d[t_int] = max(d.get(t_int, 0.0), oran)

    sutundan = len(sonuc)
    turetilen = 0
    for kod, sm in sermaye.items():
        onceki = 0.0
        for t in sorted(sm):
            cap = sm[t]
            if onceki > 0 and cap > onceki * 1.0001:
                liste_oran = sonuc.get(kod, {}).get(t, 0.0)
                oran_cap = (cap / onceki - 1.0) * 100.0
                var, poz = bedelli.get(kod, {}).get(t, (False, False))
                if liste_oran <= 0:
                    if var and not poz:
                        sonuc.setdefault(kod, {})[t] = round(oran_cap, 4)
                        turetilen += 1
                        log("  sermayeden turetilen bedelsiz: %s %d %%%.2f" % (kod, t, oran_cap))
                elif abs(liste_oran - oran_cap) > max(1.0, liste_oran * 0.02):
                    log("  UYARI oran farki: %s %d sutun=%%%.2f sermaye=%%%.2f" % (kod, t, liste_oran, oran_cap))
            if cap > 0:
                onceki = cap

    log("Is Yatirim bedelsiz bolunme kaydi olan hisse: %d (sutundan %d, sermayeden eklenen kayit %d)" %
        (len(sonuc), sutundan, turetilen))
    for ornek in ("FROTO", "ISMEN", "AKSA", "TUPRS"):
        if ornek in sonuc:
            log("  %s bedelsiz: %s" % (ornek, ", ".join("%d:%%%.2f" % (t, o) for t, o in sorted(sonuc[ornek].items()))))
    return sonuc

def bolunmeleri_birlestir(isy, gas_bol):
    """İş Yatırım'da kaydı olan hissede yalnızca İş Yatırım kullanılır; olmayanlarda GAS kullanılır.
    (Aynı bölünmenin iki kaynaktan çift sayılmasını önler.)"""
    sonuc = {k: dict(v) for k, v in isy.items() if v}
    gas_eklenen = 0
    for kod, d in gas_bol.items():
        if kod not in sonuc and d:
            sonuc[kod] = dict(d)
            gas_eklenen += 1
    log("Bolunme kaynagi: Is Yatirim %d hisse, Temettu Analiz (ek) %d hisse" % (len(sonuc) - gas_eklenen, gas_eklenen))
    return sonuc

def gecmis_metinleri(kod, kayitlar, bolunmeler):
    """JSON'a yazılacak sıkıştırılmış metinler: 'YYYYMMDD:deger,YYYYMMDD:deger'."""
    alt = GECMIS_BASLANGIC_YIL * 10000
    tem = sorted(k for k in (kayitlar or {}) if k[0] >= alt)
    bol = sorted((t, o) for t, o in (bolunmeler.get(kod) or {}).items() if t >= alt)
    return (",".join("%d:%s" % (t, sayi_metni(hb)) for t, hb in tem),
            ",".join("%d:%s" % (t, sayi_metni(o)) for t, o in bol))

def veriyi_hazirla(ham, fiyatlar, gas, sektorler=None, bolunmeler=None):
    sektorler = sektorler or {}
    bolunmeler = bolunmeler or {}
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
        tem_m, bol_m = gecmis_metinleri(kod, kayitlar, bolunmeler)

        sonuc[kod] = {
            "tarih": odemeler[-1][2],
            "hisse_basi_tl": round(toplam, 4),
            "fiyat": round(fiyat, 4),
            "yil": son_yil,
            "odeme_sayisi": len(odemeler),
            "tarihler": [o[2] for o in odemeler],
            "sektor": sektorler.get(kod, {}).get("sektor", ""),
            "sanayi": sektorler.get(kod, {}).get("sanayi", ""),
            "temettuler": tem_m,
            "bolunmeler": bol_m,
        }

    # Temettü kaydı olmayan ama Temettü Analiz listesinde bulunan hisseler (yalnızca fiyat)
    sadece_fiyat = 0
    for kod in gas:
        if kod in sonuc:
            continue
        fiyat = fiyatlar.get(kod, 0.0)
        if fiyat > 0:
            _, bol_m = gecmis_metinleri(kod, {}, bolunmeler)
            sonuc[kod] = {
                "tarih": "",
                "hisse_basi_tl": 0.0,
                "fiyat": round(fiyat, 4),
                "yil": 0,
                "odeme_sayisi": 0,
                "tarihler": [],
                "sektor": sektorler.get(kod, {}).get("sektor", ""),
                "sanayi": sektorler.get(kod, {}).get("sanayi", ""),
                "temettuler": "",
                "bolunmeler": bol_m,
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
    isy_bol = bolunme_kayitlari(satirlar)
    gas, gas_bol = gas_verisi(session)
    bolunmeler = bolunmeleri_birlestir(isy_bol, gas_bol)

    hedef = set(ham.keys()) | gas
    eksik_fiyatlari_tamamla(hedef, fiyatlar, gas)

    sektorler = sektorleri_cek(session)

    veri = veriyi_hazirla(ham, fiyatlar, gas, sektorler, bolunmeler)
    log("Hazirlanan hisse sayisi:", len(veri))
    for ornek in ("FROTO", "ISMEN", "AKSA", "TUPRS"):
        if ornek in veri:
            log("  %s bolunmeler: %s" % (ornek, veri[ornek].get("bolunmeler") or "yok"))

    if not veri:
        log("HATA: Hic temettu verisi uretilemedi.")
        sys.exit(1)

    if not github_a_yaz(veri):
        sys.exit(1)
    log("Tamamlandi.")

if __name__ == "__main__":
    main()
