#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
미국 채권 금리 아침 알림
========================
FRED(미 세인트루이스 연준) 무료 API 로 미국채 10년물·2년물·연준 기준금리를
조회해, 전일 대비 변화와 장단기 금리차(10Y-2Y)까지 카카오톡으로 발송한다.

환경변수(GitHub Secrets 권장):
  FRED_API_KEY
  SEND_METHOD                ("kakao" | "email", 기본 kakao)
  KAKAO_REST_API_KEY, KAKAO_REFRESH_TOKEN, KAKAO_CLIENT_SECRET(시크릿 ON 시)
  SMTP_USER, SMTP_PASS, MAIL_TO   (email 방식일 때)
"""

import os
import json
import smtplib
import datetime
from email.mime.text import MIMEText
from email.header import Header

import requests

KST = datetime.timezone(datetime.timedelta(hours=9))

# 조회할 시리즈: (FRED 시리즈ID, 표시이름)
SERIES = [
    ("DGS30", "미국채 30년물"),
    ("DGS10", "미국채 10년물"),
    ("DGS2",  "미국채 2년물"),
    ("DFF",   "연준 기준금리(FFR)"),
]


# ----------------------------------------------------------------------
# 1. FRED 금리 조회
# ----------------------------------------------------------------------
def fetch_latest_two(series_id: str) -> list[tuple[str, float]]:
    """해당 시리즈의 가장 최근 유효값 2개를 [(날짜, 값), ...] 최신순으로 반환."""
    r = requests.get(
        "https://api.stlouisfed.org/fred/series/observations",
        params={
            "series_id": series_id,
            "api_key": os.environ["FRED_API_KEY"],
            "file_type": "json",
            "sort_order": "desc",      # 최신순
            "limit": 10,               # 결측(.) 대비 여유분
        },
        timeout=20,
    )
    r.raise_for_status()
    obs = r.json().get("observations", [])
    out: list[tuple[str, float]] = []
    for o in obs:
        v = o.get("value", ".")
        if v not in (".", "", None):    # FRED 결측치는 '.'
            try:
                out.append((o["date"], float(v)))
            except ValueError:
                continue
        if len(out) >= 2:
            break
    return out


# ----------------------------------------------------------------------
# 2. 메시지 구성
# ----------------------------------------------------------------------
def build_message() -> str:
    today = datetime.datetime.now(KST).strftime("%Y-%m-%d (%a)")
    lines = [f"📈 [{today}] 미국 채권 금리", ""]

    latest: dict[str, float] = {}
    for sid, name in SERIES:
        data = fetch_latest_two(sid)
        if not data:
            lines.append(f"▶ {name}: 데이터 없음")
            continue
        cur_date, cur = data[0]
        latest[sid] = cur
        if len(data) >= 2:
            prev = data[1][1]
            diff = cur - prev
            bp = diff * 100  # %p → bp
            arrow = "▲" if bp > 0 else ("▼" if bp < 0 else "－")
            lines.append(f"▶ {name}: {cur:.2f}%  {arrow}{abs(bp):.0f}bp")
        else:
            lines.append(f"▶ {name}: {cur:.2f}%")
        lines.append(f"   ({cur_date} 기준)")

    # 장단기 금리차 (10Y - 2Y)
    if "DGS10" in latest and "DGS2" in latest:
        spread = (latest["DGS10"] - latest["DGS2"]) * 100  # bp
        state = "역전" if spread < 0 else "정상"
        lines.append("")
        lines.append(f"〰 10Y-2Y 금리차: {spread:+.0f}bp ({state})")

    return "\n".join(lines).strip()


# ----------------------------------------------------------------------
# 3. 발송 (기존 구조 재사용)
# ----------------------------------------------------------------------
def send_kakao(text: str) -> None:
    refresh_payload = {
        "grant_type": "refresh_token",
        "client_id": os.environ["KAKAO_REST_API_KEY"],
        "refresh_token": os.environ["KAKAO_REFRESH_TOKEN"],
    }
    if os.environ.get("KAKAO_CLIENT_SECRET"):
        refresh_payload["client_secret"] = os.environ["KAKAO_CLIENT_SECRET"]
    tok = requests.post("https://kauth.kakao.com/oauth/token",
                        data=refresh_payload, timeout=15)
    tok.raise_for_status()
    access_token = tok.json()["access_token"]

    template = {
        "object_type": "text",
        "text": text[:2000],
        "link": {"web_url": "https://fred.stlouisfed.org/series/DGS10",
                 "mobile_web_url": "https://fred.stlouisfed.org/series/DGS10"},
    }
    res = requests.post(
        "https://kapi.kakao.com/v2/api/talk/memo/default/send",
        headers={"Authorization": f"Bearer {access_token}"},
        data={"template_object": json.dumps(template, ensure_ascii=False)},
        timeout=15,
    )
    res.raise_for_status()
    print("✅ 카카오 발송 완료")


def send_email(text: str) -> None:
    user = os.environ["SMTP_USER"]
    msg = MIMEText(text, "plain", "utf-8")
    msg["Subject"] = Header("[금리알림] 미국 채권 금리", "utf-8")
    msg["From"] = user
    msg["To"] = os.environ.get("MAIL_TO", user)
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=20) as s:
        s.login(user, os.environ["SMTP_PASS"])
        s.send_message(msg)
    print("✅ 이메일 발송 완료")


def send(text: str) -> None:
    if os.environ.get("SEND_METHOD", "kakao").lower() == "email":
        send_email(text)
    else:
        send_kakao(text)


def main() -> None:
    print("1) FRED 금리 조회…")
    message = build_message()
    print("------ 발송 내용 ------")
    print(message)
    print("----------------------")
    print("2) 발송…")
    send(message)


if __name__ == "__main__":
    main()
