#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
보유종목 공시 아침 알림 (GitHub Actions 용)
============================================
1) holdings.json 에서 보유종목 목록을 읽는다 (로컬에서 update_holdings.py 로 생성)
2) DART OpenAPI로 해당 종목들의 최근(어제~오늘) 공시를 조회
3) 카카오톡 '나에게 보내기' 또는 이메일(Make.com 경유)로 발송

키움 조회는 한국 IP 에서만 통과되므로(해외 IP/지정단말기 8050 회피), 키움 호출은
로컬의 update_holdings.py 가 담당하고, 이 스크립트는 키움을 호출하지 않는다.

환경변수(GitHub Secrets 권장):
  # DART
  DART_API_KEY
  # 발송 방식 선택: "kakao" | "email"  (기본 kakao)
  SEND_METHOD
  # 카카오 (SEND_METHOD=kakao 일 때)
  KAKAO_REST_API_KEY, KAKAO_REFRESH_TOKEN, KAKAO_CLIENT_SECRET(시크릿 ON 시)
  # 이메일 (SEND_METHOD=email 일 때)
  SMTP_USER, SMTP_PASS, MAIL_TO   (Gmail이면 앱 비밀번호 사용)
"""

import os
import re
import sys
import json
import datetime
import smtplib
from email.mime.text import MIMEText
from email.header import Header

import requests

KST = datetime.timezone(datetime.timedelta(hours=9))


# ----------------------------------------------------------------------
# 1. 보유종목 목록 로드 (holdings.json)
# ----------------------------------------------------------------------
# 키움 조회는 한국 IP 에서만 통과되므로(8050 회피), 로컬에서 update_holdings.py 로
# holdings.json 을 만들어 저장소에 올려둔다. 여기서는 그 파일만 읽는다.
HOLDINGS_PATH = os.environ.get("HOLDINGS_PATH", "holdings.json")


def load_holdings() -> list[dict]:
    """holdings.json 을 읽어 [{'code':'005930','name':'삼성전자'}, ...] 반환."""
    if not os.path.exists(HOLDINGS_PATH):
        raise FileNotFoundError(
            f"{HOLDINGS_PATH} 가 없습니다. 로컬에서 update_holdings.py 를 실행해 생성 후 저장소에 올려주세요."
        )
    with open(HOLDINGS_PATH, encoding="utf-8") as f:
        data = json.load(f)
    holdings = []
    for row in data:
        code = str(row.get("code", "")).strip()
        name = str(row.get("name", "")).strip()
        if code:
            holdings.append({"code": code, "name": name})
    return holdings


# ----------------------------------------------------------------------
# 2. DART OpenAPI — 종목별 최근 공시 조회
# ----------------------------------------------------------------------
import contextlib
import io


@contextlib.contextmanager
def _silence_stdout():
    """OpenDartReader 가 찍는 'status:013 조회된 데이타가 없습니다' 등을 숨긴다."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        yield


def get_disclosures(codes: list[str]) -> dict[str, list[dict]]:
    """
    OpenDartReader 로 어제~오늘 공시를 종목별로 조회.
    반환: {'005930': [{'name':..., 'report':..., 'no':..., 'dt':...}, ...]}
    ETF/우선주 등 DART 조회가 안 되는 종목은 조용히 건너뛴다.
    """
    import OpenDartReader

    dart = OpenDartReader(os.environ["DART_API_KEY"])
    today = datetime.datetime.now(KST).date()
    start = today - datetime.timedelta(days=1)  # 전일 마감~오늘 아침 공시 커버

    result: dict[str, list[dict]] = {}
    skipped: list[str] = []
    for code in codes:
        try:
            with _silence_stdout():
                df = dart.list(code, start=start.strftime("%Y%m%d"),
                               end=today.strftime("%Y%m%d"))
        except Exception:  # ETF/우선주 등 DART 미등록 → 조용히 스킵
            skipped.append(code)
            continue
        if df is None or len(df) == 0:
            continue
        items = []
        for _, row in df.iterrows():
            items.append({
                "name": row.get("corp_name", ""),
                "report": row.get("report_nm", ""),
                "no": row.get("rcept_no", ""),
                "dt": str(row.get("rcept_dt", "")),
            })
        if items:
            result[code] = items
    if skipped:
        print(f"   (DART 미등록 종목 {len(skipped)}개 건너뜀: {', '.join(skipped)})")
    return result


# ----------------------------------------------------------------------
# 3. 메시지 구성
# ----------------------------------------------------------------------
def build_message(holdings: list[dict], disclosures: dict[str, list[dict]]) -> str:
    today = datetime.datetime.now(KST).strftime("%Y-%m-%d (%a)")
    name_by_code = {h["code"]: h["name"] for h in holdings}

    if not disclosures:
        return f"📋 [{today}] 보유종목 공시 알림\n\n신규 공시가 없습니다. (보유 {len(holdings)}종목 확인)"

    lines = [f"📋 [{today}] 보유종목 신규 공시", ""]
    for code, items in disclosures.items():
        nm = name_by_code.get(code, code)
        lines.append(f"▶ {nm} ({code})")
        for it in items:
            url = f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={it['no']}"
            lines.append(f"  · {it['report']}")
            lines.append(f"    {url}")
        lines.append("")
    return "\n".join(lines).strip()


# ----------------------------------------------------------------------
# 4. 발송 — 카카오 '나에게 보내기' 또는 이메일
# ----------------------------------------------------------------------
def send_kakao(text: str) -> None:
    """카카오 '나에게 보내기'. refresh_token 으로 access_token 갱신 후 전송."""
    # 4-1. access_token 갱신
    refresh_payload = {
        "grant_type": "refresh_token",
        "client_id": os.environ["KAKAO_REST_API_KEY"],
        "refresh_token": os.environ["KAKAO_REFRESH_TOKEN"],
    }
    # 클라이언트 시크릿이 ON 이면 함께 전송 (OFF 면 secret 미설정 → 생략)
    if os.environ.get("KAKAO_CLIENT_SECRET"):
        refresh_payload["client_secret"] = os.environ["KAKAO_CLIENT_SECRET"]
    tok = requests.post(
        "https://kauth.kakao.com/oauth/token",
        data=refresh_payload,
        timeout=15,
    )
    tok.raise_for_status()
    access_token = tok.json()["access_token"]

    # 4-2. 메모(나에게 보내기) 전송
    # link 를 비워 두면 메시지 클릭 시 엉뚱한 등록 도메인으로 튀지 않는다.
    # (공시 URL 은 본문 텍스트에 그대로 들어가므로 버튼 링크는 불필요)
    template = {
        "object_type": "text",
        "text": text[:2000],  # 카카오 텍스트 길이 제한 대비
        "link": {},
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
    """Gmail SMTP 로 본인에게 메일 발송 → Make.com 시나리오가 카톡 전달."""
    user = os.environ["SMTP_USER"]
    msg = MIMEText(text, "plain", "utf-8")
    msg["Subject"] = Header("[공시알림] 보유종목 신규 공시", "utf-8")
    msg["From"] = user
    msg["To"] = os.environ.get("MAIL_TO", user)

    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=20) as s:
        s.login(user, os.environ["SMTP_PASS"])
        s.send_message(msg)
    print("✅ 이메일 발송 완료")


def send(text: str) -> None:
    method = os.environ.get("SEND_METHOD", "kakao").lower()
    if method == "email":
        send_email(text)
    else:
        send_kakao(text)


# ----------------------------------------------------------------------
# main
# ----------------------------------------------------------------------
def main() -> None:
    print("1) 보유종목 목록 로드 (holdings.json)…")
    holdings = load_holdings()
    print(f"   {len(holdings)}종목:", ", ".join(f"{h['name']}({h['code']})" for h in holdings))

    if not holdings:
        print("보유종목이 없어 종료합니다.")
        return

    print("2) DART 공시 조회…")
    codes = [h["code"] for h in holdings]
    disclosures = get_disclosures(codes)
    print(f"   공시 있는 종목 {len(disclosures)}개")

    print("3) 메시지 발송…")
    message = build_message(holdings, disclosures)
    print("------ 발송 내용 ------")
    print(message)
    print("----------------------")
    send(message)


if __name__ == "__main__":
    main()
