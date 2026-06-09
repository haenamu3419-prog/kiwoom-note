#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
미국주 보유종목 뉴스 아침 알림
==============================
Finnhub 무료 API 로 지정한 미국 종목들의 현재가(전일대비)와 최근 주요뉴스를
모아 카카오톡으로 발송한다. 종목 리스트는 us_tickers.json 에서 읽는다.

환경변수(GitHub Secrets 권장):
  FINNHUB_API_KEY
  ANTHROPIC_API_KEY          (뉴스 한글 번역용. 없으면 영문 그대로)
  SEND_METHOD                ("kakao" | "email", 기본 kakao)
  KAKAO_REST_API_KEY, KAKAO_REFRESH_TOKEN, KAKAO_CLIENT_SECRET(시크릿 ON 시)
  SMTP_USER, SMTP_PASS, MAIL_TO   (email 방식일 때)

us_tickers.json 예시:
  ["AAPL", "MSFT", "NVDA", "TSLA"]
"""

import os
import json
import time
import smtplib
import datetime
from email.mime.text import MIMEText
from email.header import Header

import requests

KST = datetime.timezone(datetime.timedelta(hours=9))
BASE = "https://finnhub.io/api/v1"
TICKERS_PATH = os.environ.get("US_TICKERS_PATH", "us_tickers.json")
NEWS_PER_STOCK = 2        # 종목당 보여줄 뉴스 개수
NEWS_LOOKBACK_DAYS = 2    # 최근 며칠 뉴스


def load_tickers() -> list[str]:
    if not os.path.exists(TICKERS_PATH):
        raise FileNotFoundError(
            f"{TICKERS_PATH} 가 없습니다. 예: [\"AAPL\",\"MSFT\",\"NVDA\"] 형식으로 만들어 올려주세요."
        )
    with open(TICKERS_PATH, encoding="utf-8") as f:
        data = json.load(f)
    return [str(t).strip().upper() for t in data if str(t).strip()]


def get_quote(symbol: str) -> dict | None:
    try:
        r = requests.get(f"{BASE}/quote",
                         params={"symbol": symbol, "token": os.environ["FINNHUB_API_KEY"]},
                         timeout=15)
        r.raise_for_status()
        d = r.json()
        if d.get("c"):  # 현재가
            return {"price": d["c"], "change_pct": d.get("dp", 0)}
    except Exception:
        pass
    return None


def get_news(symbol: str) -> list[dict]:
    today = datetime.datetime.now(KST).date()
    start = today - datetime.timedelta(days=NEWS_LOOKBACK_DAYS)
    try:
        r = requests.get(f"{BASE}/company-news",
                         params={"symbol": symbol,
                                 "from": start.strftime("%Y-%m-%d"),
                                 "to": today.strftime("%Y-%m-%d"),
                                 "token": os.environ["FINNHUB_API_KEY"]},
                         timeout=15)
        r.raise_for_status()
        arts = r.json()
        if isinstance(arts, list):
            # 최신순 정렬 후 상위 N개
            arts.sort(key=lambda a: a.get("datetime", 0), reverse=True)
            return arts[:NEWS_PER_STOCK]
    except Exception:
        pass
    return []


def translate_headlines(headlines: list[str]) -> list[str]:
    """영문 헤드라인 리스트를 Claude API 로 한 번에 한글 번역. 실패 시 원문 반환."""
    if not headlines or not os.environ.get("ANTHROPIC_API_KEY"):
        return headlines
    numbered = "\n".join(f"{i+1}. {h}" for i, h in enumerate(headlines))
    prompt = (
        "다음 영문 주식 뉴스 헤드라인들을 자연스러운 한국어로 번역해줘. "
        "번역문만, 입력과 같은 번호 순서로, 한 줄에 하나씩 출력해. 설명·따옴표 없이.\n\n"
        + numbered
    )
    try:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": os.environ["ANTHROPIC_API_KEY"],
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            data=json.dumps({
                "model": "claude-haiku-4-5-20251001",
                "max_tokens": 1500,
                "messages": [{"role": "user", "content": prompt}],
            }),
            timeout=30,
        )
        r.raise_for_status()
        text = "".join(b.get("text", "") for b in r.json().get("content", []))
        lines = [l.strip() for l in text.strip().splitlines() if l.strip()]
        cleaned = []
        for l in lines:
            head = l.split(".", 1)[0]
            cleaned.append(l.split(". ", 1)[1] if head.isdigit() and ". " in l else l)
        return cleaned if len(cleaned) == len(headlines) else headlines
    except Exception:
        return headlines


def build_message(tickers: list[str]) -> str:
    today = datetime.datetime.now(KST).strftime("%Y-%m-%d (%a)")

    # 1) 종목별 시세·뉴스 수집 + 헤드라인 모으기
    per_stock = []          # [{sym, quote, news:[{headline,url}]}]
    all_headlines = []      # 번역용 헤드라인 모음
    for sym in tickers:
        q = get_quote(sym)
        news = get_news(sym)
        items = []
        for a in news:
            h = (a.get("headline") or "").strip()
            if h:
                items.append({"headline": h, "url": a.get("url", "")})
                all_headlines.append(h)
        per_stock.append({"sym": sym, "quote": q, "news": items})
        time.sleep(0.3)     # 무료 티어 rate limit 여유

    # 2) 헤드라인 일괄 한글 번역 (실패 시 원문)
    translated = translate_headlines(all_headlines)
    tmap = dict(zip(all_headlines, translated))

    # 3) 메시지 조립
    lines = [f"🇺🇸 [{today}] 미국주 보유종목 뉴스", ""]
    for s in per_stock:
        q = s["quote"]
        if q:
            arrow = "▲" if q["change_pct"] > 0 else ("▼" if q["change_pct"] < 0 else "－")
            lines.append(f"▶ {s['sym']}  ${q['price']:.2f}  {arrow}{abs(q['change_pct']):.1f}%")
        else:
            lines.append(f"▶ {s['sym']}")
        if s["news"]:
            for it in s["news"]:
                kr = tmap.get(it["headline"], it["headline"])
                lines.append(f"  · {kr}")
                if it["url"]:
                    lines.append(f"    {it['url']}")
        else:
            lines.append("  · 최근 뉴스 없음")
        lines.append("")

    return "\n".join(lines).strip()


# ----------------------------------------------------------------------
# 발송 (기존 구조 재사용)
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

    template = {"object_type": "text", "text": text[:2000], "link": {}}
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
    msg["Subject"] = Header("[미국주] 보유종목 뉴스", "utf-8")
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
    print("1) 종목 리스트 로드…")
    tickers = load_tickers()
    print(f"   {len(tickers)}종목: {', '.join(tickers)}")
    if not tickers:
        print("종목이 없어 종료합니다.")
        return

    print("2) Finnhub 시세·뉴스 조회…")
    message = build_message(tickers)
    print("------ 발송 내용 ------")
    print(message)
    print("----------------------")
    print("3) 발송…")
    send(message)


if __name__ == "__main__":
    main()
