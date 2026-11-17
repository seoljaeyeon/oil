"""오피넷 유가 수집 (GitHub Actions에서 실행).

- 시도별 평균가격(avgSidoPrice.do): 모든 시도·모든 유종을 받아 날짜별로 쌓음
- 지역 최저가 주유소(lowTop10.do): config.json의 지역에서 싼 주유소 목록 (실패하면 건너뜀)
- 내 주변 주유소용: config.json의 station_regions(예: 전북) 시·군별 싼 주유소를 좌표와 함께 모음
결과: data/prices.json, data/stations.json
"""
import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

BASE = "https://www.opinet.co.kr/api/"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data", "prices.json")
STATIONS = os.path.join(ROOT, "data", "stations.json")
# 오피넷 좌표(KATEC) → 위경도(WGS84)
KATEC = "+proj=tmerc +lat_0=38 +lon_0=128 +k=0.9999 +x_0=400000 +y_0=600000 +ellps=bessel +units=m +towgs84=-115.80,474.99,674.11,1.16,-2.31,-1.63,6.43 +no_defs"
KST = timezone(timedelta(hours=9))
FUELS = {"B027": "휘발유", "D047": "경유", "B034": "고급휘발유", "K015": "LPG", "C004": "등유"}


def call(api, **params):
    key = os.environ.get("OPINET_KEY", "").strip()
    if not key:
        sys.exit("OPINET_KEY 시크릿이 없어요. 저장소 Settings > Secrets and variables > Actions 에 등록하세요.")
    # 문서 기준 파라미터는 certkey. 예전 방식(code)도 같이 보내 둠
    q = {"out": "json", "certkey": key, "code": key, **params}
    url = BASE + api + "?" + urllib.parse.urlencode(q)
    req = urllib.request.Request(url, headers={"User-Agent": "oil-price-tracker (personal, GitHub Actions)"})
    with urllib.request.urlopen(req, timeout=30) as r:
        body = r.read().decode("utf-8")
    data = json.loads(body)
    oil = (data.get("RESULT") or {}).get("OIL")
    if oil is None:
        raise ValueError(f"{api} 응답에 OIL 목록이 없어요: {body[:200]}")
    return oil if isinstance(oil, list) else [oil]


def num(v):
    try:
        return round(float(str(v).replace(",", "")), 2)
    except (TypeError, ValueError):
        return None


def main():
    cfg = json.load(open(os.path.join(ROOT, "config.json"), encoding="utf-8"))
    now = datetime.now(KST)
    today = now.strftime("%Y-%m-%d")

    db = {"history": [], "cheapest": [], "regions": {}}
    if os.path.exists(DATA):
        db = json.load(open(DATA, encoding="utf-8"))

    # 1) 시도별 평균 (모든 시도, 모든 유종)
    rows = call("avgSidoPrice.do")
    day = {}
    for r in rows:
        name = str(r.get("SIDONM", "")).strip()
        prod = str(r.get("PRODCD", "")).strip()
        price = num(r.get("PRICE"))
        if not name or prod not in FUELS or price is None:
            continue
        day.setdefault(name, {})[prod] = price
        db["regions"][name] = str(r.get("SIDOCD", "")).strip()
    if not day:
        sys.exit("평균가격을 하나도 못 받았어요.")

    hist = [h for h in db.get("history", []) if h.get("date") != today]
    hist.append({"date": today, "avg": day})
    hist.sort(key=lambda h: h["date"])
    db["history"] = hist[-1100:]  # 약 3년치

    # 2) 지역 최저가 주유소 (선택 기능)
    region = cfg.get("region", "전북")
    code = next((c for n, c in db["regions"].items() if region in n), None)
    cheapest = []
    if code:
        for prod in dict.fromkeys([cfg.get("fuel", "B027"), "B027", "D047"]):
            try:
                st = call("lowTop10.do", prodcd=prod, area=code, cnt=cfg.get("cheapest_count", 10))
                for s in st:
                    cheapest.append({
                        "fuel": prod,
                        "name": s.get("OS_NM"),
                        "price": num(s.get("PRICE")),
                        "addr": s.get("NEW_ADR") or s.get("VAN_ADR") or s.get("ADDR") or "",
                        "brand": s.get("POLL_DIV_CD") or s.get("POLL_DIV_CO") or "",
                    })
            except Exception as e:  # 키 권한이 없거나 형식이 다르면 평균가격만 저장
                print(f"최저가 주유소({prod}) 건너뜀: {e}")
    if cheapest:
        db["cheapest"] = cheapest
        db["cheapest_region"] = region

    # 3) 내 주변 주유소용 목록 (시·군별 싼 주유소 + 좌표)
    try:
        collect_stations(cfg, db["regions"], now)
    except Exception as e:
        print(f"주변 주유소 수집 건너뜀: {e}")

    db["updated"] = now.strftime("%Y-%m-%d %H:%M")
    os.makedirs(os.path.dirname(DATA), exist_ok=True)
    with open(DATA, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, separators=(",", ":"))
    print(f"{today}: 시도 {len(day)}곳, 최저가 주유소 {len(cheapest)}곳 저장")


def collect_stations(cfg, regions, now):
    from pyproj import Transformer
    tr = Transformer.from_crs(KATEC, "EPSG:4326", always_xy=True)
    fuels = list(dict.fromkeys([cfg.get("fuel", "B027"), "B027", "D047"]))
    areas = []
    for want in cfg.get("station_regions", [cfg.get("region", "전북")]):
        sido = next((c for n, c in regions.items() if want in n), None)
        if not sido:
            continue
        try:  # 시·군 코드 (4자리)
            subs = call("areaCode.do", area=sido)
            codes = [(str(a.get("AREA_CD")), str(a.get("AREA_NM"))) for a in subs if a.get("AREA_CD")]
        except Exception as e:
            print(f"{want} 시·군 코드 못 받음, 도 전체로 검색: {e}")
            codes = []
        areas += codes or [(sido, want)]
    stations = {}
    calls = 0
    for code, area_nm in areas:
        for prod in fuels:
            try:
                rows = call("lowTop10.do", prodcd=prod, area=code, cnt=20)
                calls += 1
            except Exception as e:
                print(f"{area_nm} {prod} 건너뜀: {e}")
                continue
            for r in rows:
                uid = r.get("UNI_ID")
                x, y = num(r.get("GIS_X_COOR")), num(r.get("GIS_Y_COOR"))
                price = num(r.get("PRICE"))
                if not uid or x is None or y is None or price is None:
                    continue
                st = stations.get(uid)
                if not st:
                    lng, lat = tr.transform(x, y)
                    st = stations[uid] = {
                        "id": uid, "name": r.get("OS_NM"), "area": area_nm,
                        "addr": r.get("NEW_ADR") or r.get("VAN_ADR") or "",
                        "brand": r.get("POLL_DIV_CD") or r.get("POLL_DIV_CO") or "",
                        "lat": round(lat, 6), "lng": round(lng, 6), "p": {},
                    }
                st["p"][prod] = price
    if not stations:
        print("주변 주유소: 받은 데이터 없음 (기존 파일 유지)")
        return
    out = {"updated": now.strftime("%Y-%m-%d %H:%M"), "count": len(stations), "stations": list(stations.values())}
    with open(STATIONS, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    print(f"주변 주유소 {len(stations)}곳 저장 (API {calls}회)")


if __name__ == "__main__":
    main()
