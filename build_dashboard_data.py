#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
대시보드 데이터 생성기 (GitHub Actions 용, 하루 4회)
====================================================
FRED(금리·유가·환율) + DART(한국 보유종목 당일 공시)를 조회해
dashboard_data.json 으로 저장한다. 대시보드 HTML 이 이 파일을 읽어 표시한다.

한국 보유종목은 키움 자동조회 없이 holdings.json(코드+이름)을 그대로 읽는다.
holdings.json은 대시보드의 "한국 보유종목" 입력칸에서 "holdings.json 복사" 버튼으로
복사해 붙여넣어 관리한다.

환경변수(GitHub Secrets):
  FRED_API_KEY
  DART_API_KEY

출력: dashboard_data.json
"""

import os
import json
import datetime
import contextlib
import io

import requests

KST = datetime.timezone(datetime.timedelta(hours=9))
OUT = "dashboard_data.json"
HOLDINGS_PATH = os.environ.get("HOLDINGS_PATH", "holdings.json")

MACRO = [
    ("DGS30", "미국채 30년물", "pct"),
    ("DGS10", "미국채 10년물", "pct"),
    ("DGS2",  "미국채 2년물",  "pct"),
    ("DFF",   "연준 기준금리", "pct"),
    ("DCOILWTICO",   "WTI 유가", "usd"),
    ("DCOILBRENTEU", "브렌트유",  "usd"),
    ("DEXKOUS", "원/달러", "won"),
    ("VIXCLS",  "VIX",    "pt"),
]


def fred_latest2(series_id):
    r = requests.get(
        "https://api.stlouisfed.org/fred/series/observations",
        params={"series_id": series_id, "api_key": os.environ["FRED_API_KEY"],
                "file_type": "json", "sort_order": "desc", "limit": 12},
        timeout=20)
    r.raise_for_status()
    out = []
    for o in r.json().get("observations", []):
        v = o.get("value", ".")
        if v not in (".", "", None):
            try: out.append(float(v))
            except ValueError: pass
        if len(out) >= 2:
            break
    return out


def build_macro():
    rows = []
    for sid, name, unit in MACRO:
        try:
            d = fred_latest2(sid)
            cur = d[0] if d else None
            prev = d[1] if len(d) >= 2 else None
            rows.append({"name": name, "unit": unit, "cur": cur, "prev": prev})
        except Exception as e:
            rows.append({"name": name, "unit": unit, "cur": None, "prev": None, "err": str(e)})
    return rows


def load_kr_holdings():
    if not os.path.exists(HOLDINGS_PATH):
        return []
    with open(HOLDINGS_PATH, encoding="utf-8") as f:
        data = json.load(f)
    out = []
    for row in data:
        code = str(row.get("code", "")).strip()
        name = str(row.get("name", "")).strip()
        if code:
            out.append({"code": code, "name": name})
    return out


def build_dart(holdings):
    """한국 보유종목의 최근(어제~오늘) DART 공시를 조회.
    새벽 시간대에 실행돼도 전날 공시까지 잡히도록 범위를 넓힌다."""
    key = os.environ.get("DART_API_KEY")
    if not key or not holdings:
        return []
    try:
        import OpenDartReader
    except Exception:
        return []
    dart = OpenDartReader(key)
    today = datetime.datetime.now(KST).date()
    start = today - datetime.timedelta(days=1)
    name_by = {h["code"]: h["name"] for h in holdings}
    rows = []
    for h in holdings:
        code = h["code"]
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                df = dart.list(code, start=start.strftime("%Y%m%d"),
                               end=today.strftime("%Y%m%d"))
        except Exception:
            continue
        if df is None or len(df) == 0:
            continue
        items = []
        for _, r in df.iterrows():
            items.append({
                "report": r.get("report_nm", ""),
                "url": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={r.get('rcept_no','')}",
                "dt": str(r.get("rcept_dt", "")),
            })
        if items:
            rows.append({"code": code, "name": name_by.get(code, code), "items": items})
    return rows


def main():
    holdings = load_kr_holdings()
    data = {
        "updated": datetime.datetime.now(KST).strftime("%Y-%m-%d %H:%M KST"),
        "macro": build_macro(),
        "dart": build_dart(holdings),
        "kr_holdings": holdings,
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"OK {OUT} 저장: 금리 {len(data['macro'])} / 보유종목 {len(holdings)} / 공시종목 {len(data['dart'])}")


if __name__ == "__main__":
    main()
