"""오피넷 유가 수집 (GitHub Actions에서 실행).

- 시도별 평균가격(avgSidoPrice.do): 모든 시도·모든 유종을 받아 날짜별로 쌓음
- 지역 최저가 주유소(lowTop10.do): config.json의 지역에서 싼 주유소 목록 (실패하면 건너뜀)
결과: data/prices.json
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

    db["updated"] = now.strftime("%Y-%m-%d %H:%M")
    os.makedirs(os.path.dirname(DATA), exist_ok=True)
    with open(DATA, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, separators=(",", ":"))
    print(f"{today}: 시도 {len(day)}곳, 최저가 주유소 {len(cheapest)}곳 저장")


if __name__ == "__main__":
    main()
