#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
보유종목 목록 갱신 (로컬 전용)
==============================
한국 IP 환경(로컬 PC)에서 키움 REST API 로 전 계좌 보유종목을 조회해
holdings.json 으로 저장한다. 이 파일을 GitHub 저장소에 올리면,
disclosure_notifier.py 가 매일 그 목록으로 공시를 조회한다.

※ 키움 조회는 한국 IP 에서만 통과(8050 회피)되므로 반드시 로컬에서 실행.
※ 종목이 바뀌었을 때(매매 후)만 다시 실행해 holdings.json 을 갱신하면 된다.

실행 (Windows PowerShell):
  pip install requests
  $env:KIWOOM_APPKEY_1="..."; $env:KIWOOM_SECRETKEY_1="..."
  $env:KIWOOM_APPKEY_2="..."; $env:KIWOOM_SECRETKEY_2="..."
  $env:KIWOOM_APPKEY_3="..."; $env:KIWOOM_SECRETKEY_3="..."
  python update_holdings.py
"""

import os
import re
import sys
import json

import requests

OUT_PATH = "holdings.json"


def kiwoom_host() -> str:
    return (os.environ.get("KIWOOM_HOST") or "https://api.kiwoom.com").rstrip("/")


def get_credentials() -> list[tuple[str, str]]:
    creds: list[tuple[str, str]] = []
    i = 1
    while True:
        ak = os.environ.get(f"KIWOOM_APPKEY_{i}")
        sk = os.environ.get(f"KIWOOM_SECRETKEY_{i}")
        if ak and sk:
            creds.append((ak, sk))
            i += 1
        else:
            break
    if not creds and os.environ.get("KIWOOM_APPKEY") and os.environ.get("KIWOOM_SECRETKEY"):
        creds.append((os.environ["KIWOOM_APPKEY"], os.environ["KIWOOM_SECRETKEY"]))
    if not creds:
        raise RuntimeError("키움 키가 없습니다. KIWOOM_APPKEY_1/KIWOOM_SECRETKEY_1 ... 를 설정하세요.")
    return creds


def get_token(appkey: str, secretkey: str) -> str:
    r = requests.post(
        f"{kiwoom_host()}/oauth2/token",
        headers={"Content-Type": "application/json;charset=UTF-8"},
        data=json.dumps({
            "grant_type": "client_credentials",
            "appkey": appkey,
            "secretkey": secretkey,
        }),
        timeout=15,
    )
    r.raise_for_status()
    d = r.json()
    token = d.get("token") or d.get("access_token")
    if not token:
        raise RuntimeError(f"토큰 발급 실패: {d}")
    return token


def get_holdings(token: str) -> list[dict]:
    url = f"{kiwoom_host()}/api/dostk/acnt"
    headers = {
        "Content-Type": "application/json;charset=UTF-8",
        "authorization": f"Bearer {token}",
        "api-id": "kt00018",
        "cont-yn": "N",
        "next-key": "",
    }
    body = {"qry_tp": "1", "dmst_stex_tp": "KRX"}

    rows_out: list[dict] = []
    while True:
        r = requests.post(url, headers=headers, data=json.dumps(body), timeout=15)
        r.raise_for_status()
        data = r.json()
        for row in _extract_rows(data):
            code = _clean_code(row.get("stk_cd", ""))
            name = (row.get("stk_nm") or "").strip()
            qty = _to_int(row.get("rmnd_qty") or row.get("trde_able_qty"))
            if code and qty > 0:
                rows_out.append({"code": code, "name": name})
        if r.headers.get("cont-yn") == "Y" and r.headers.get("next-key"):
            headers["cont-yn"] = "Y"
            headers["next-key"] = r.headers["next-key"]
        else:
            break
    return rows_out


def _extract_rows(data: dict) -> list[dict]:
    for key in ("acnt_evlt_remn_indv_tot", "stk_acnt_evlt_prst", "output", "output1"):
        v = data.get(key)
        if isinstance(v, list) and v and isinstance(v[0], dict) and "stk_cd" in v[0]:
            return v
    for v in data.values():
        if isinstance(v, list) and v and isinstance(v[0], dict) and "stk_cd" in v[0]:
            return v
    return []


def _clean_code(raw: str) -> str:
    m = re.search(r"\d{6}", str(raw))
    return m.group(0) if m else ""


def _to_int(v) -> int:
    try:
        return int(str(v).replace(",", "").replace("+", "").strip() or 0)
    except ValueError:
        return 0


def main():
    creds = get_credentials()
    print(f"연결된 키움 계좌(키) 수: {len(creds)}개")

    merged: dict[str, str] = {}  # code -> name
    for idx, (ak, sk) in enumerate(creds, start=1):
        try:
            token = get_token(ak, sk)
            holds = get_holdings(token)
            print(f" - 계좌 {idx}: {len(holds)}종목")
            for h in holds:
                merged.setdefault(h["code"], h["name"])
        except Exception as e:
            print(f" - 계좌 {idx} 조회 실패: {e}", file=sys.stderr)

    if not merged:
        print("보유종목이 없습니다. holdings.json 을 갱신하지 않습니다.")
        sys.exit(1)

    holdings = [{"code": c, "name": n} for c, n in sorted(merged.items())]
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(holdings, f, ensure_ascii=False, indent=2)

    print(f"\n✅ {OUT_PATH} 저장 완료 — 합산 {len(holdings)}종목")
    print("   " + ", ".join(f"{h['name']}({h['code']})" for h in holdings))
    print(f"\n이제 {OUT_PATH} 파일을 GitHub 저장소에 올리세요 (Add file → Upload files).")


if __name__ == "__main__":
    main()
