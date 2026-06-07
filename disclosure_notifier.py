#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
보유종목 공시 아침 알림
========================
1) 키움 REST API(kt00018)로 실제 보유종목을 자동 조회
2) DART OpenAPI로 해당 종목들의 최근(어제~오늘) 공시를 조회
3) 카카오톡 '나에게 보내기' 또는 이메일(Make.com 경유)로 발송

환경변수(GitHub Secrets 권장):
  # 키움
  KIWOOM_APPKEY, KIWOOM_SECRETKEY
  KIWOOM_HOST          (선택, 기본 https://api.kiwoom.com / 모의는 https://mockapi.kiwoom.com)
  # DART
  DART_API_KEY
  # 발송 방식 선택: "kakao" | "email"  (기본 kakao)
  SEND_METHOD
  # 카카오 (SEND_METHOD=kakao 일 때)
  KAKAO_REST_API_KEY, KAKAO_REFRESH_TOKEN
  # 이메일 (SEND_METHOD=email 일 때)
  SMTP_USER, SMTP_PASS, MAIL_TO   (Gmail이면 앱 비밀번호 사용)

주의: 키움 kt00018 응답의 정확한 필드명은 계정/시점에 따라 다를 수 있어,
      처음 1회는 RAW 응답 키를 로그로 찍어 확인하도록 방어적으로 작성됨.
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
# 1. 키움 REST API — 접근토큰 발급 + 보유종목 조회
# ----------------------------------------------------------------------
def kiwoom_host() -> str:
    return os.environ.get("KIWOOM_HOST", "https://api.kiwoom.com").rstrip("/")


def get_credentials() -> list[tuple[str, str]]:
    """
    여러 계좌의 (appkey, secretkey) 쌍을 환경변수에서 수집.
    - KIWOOM_APPKEY_1 / KIWOOM_SECRETKEY_1, _2, _3 ... (계좌별)
    - 또는 단일 KIWOOM_APPKEY / KIWOOM_SECRETKEY (계좌 1개)
    """
    creds: list[tuple[str, str]] = []
    # 번호형 (_1, _2, ...) 우선 수집
    i = 1
    while True:
        ak = os.environ.get(f"KIWOOM_APPKEY_{i}")
        sk = os.environ.get(f"KIWOOM_SECRETKEY_{i}")
        if ak and sk:
            creds.append((ak, sk))
            i += 1
        else:
            break
    # 단일형 폴백
    if not creds and os.environ.get("KIWOOM_APPKEY") and os.environ.get("KIWOOM_SECRETKEY"):
        creds.append((os.environ["KIWOOM_APPKEY"], os.environ["KIWOOM_SECRETKEY"]))
    if not creds:
        raise RuntimeError("키움 키가 없습니다. KIWOOM_APPKEY_1/KIWOOM_SECRETKEY_1 ... 또는 "
                           "KIWOOM_APPKEY/KIWOOM_SECRETKEY 를 설정하세요.")
    return creds


def get_kiwoom_token(appkey: str, secretkey: str) -> str:
    """au10001 접근토큰 발급."""
    url = f"{kiwoom_host()}/oauth2/token"
    payload = {
        "grant_type": "client_credentials",
        "appkey": appkey,
        "secretkey": secretkey,
    }
    r = requests.post(
        url,
        headers={"Content-Type": "application/json;charset=UTF-8"},
        data=json.dumps(payload),
        timeout=15,
    )
    r.raise_for_status()
    data = r.json()
    # 키움은 token 키 이름이 'token' 으로 내려옴 (응답 변동 대비 폴백 포함)
    token = data.get("token") or data.get("access_token")
    if not token:
        raise RuntimeError(f"토큰 발급 실패: {data}")
    return token


def get_holdings(token: str) -> list[dict]:
    """
    kt00018 계좌평가잔고내역요청.
    반환: [{'code': '005930', 'name': '삼성전자', 'qty': 10}, ...]
    """
    url = f"{kiwoom_host()}/api/dostk/acnt"
    headers = {
        "Content-Type": "application/json;charset=UTF-8",
        "authorization": f"Bearer {token}",
        "api-id": "kt00018",
        "cont-yn": "N",
        "next-key": "",
    }
    body = {
        "qry_tp": "1",          # 1:합산, 2:개별
        "dmst_stex_tp": "KRX",  # 국내거래소구분
    }

    holdings: list[dict] = []
    while True:
        r = requests.post(url, headers=headers, data=json.dumps(body), timeout=15)
        r.raise_for_status()
        data = r.json()

        # --- 보유종목 배열 찾기 (필드명 방어적 탐색) ---
        rows = _extract_holding_rows(data)
        for row in rows:
            code = _clean_code(row.get("stk_cd", ""))
            qty = _to_int(row.get("rmnd_qty") or row.get("hldg_qty") or row.get("trde_able_qty"))
            name = (row.get("stk_nm") or "").strip()
            if code and qty > 0:
                holdings.append({"code": code, "name": name, "qty": qty})

        # --- 연속조회 처리 ---
        cont_yn = r.headers.get("cont-yn", "N")
        next_key = r.headers.get("next-key", "")
        if cont_yn == "Y" and next_key:
            headers["cont-yn"] = "Y"
            headers["next-key"] = next_key
        else:
            break

    # 중복 종목 합치기
    merged: dict[str, dict] = {}
    for h in holdings:
        if h["code"] in merged:
            merged[h["code"]]["qty"] += h["qty"]
        else:
            merged[h["code"]] = h
    return list(merged.values())


def collect_all_holdings() -> list[dict]:
    """모든 계좌(키 쌍)를 돌며 보유종목을 합산."""
    creds = get_credentials()
    print(f"   연결된 키움 계좌(키) 수: {len(creds)}개")
    all_holdings: list[dict] = []
    for idx, (ak, sk) in enumerate(creds, start=1):
        try:
            token = get_kiwoom_token(ak, sk)
            h = get_holdings(token)
            print(f"   - 계좌 {idx}: {len(h)}종목")
            all_holdings.extend(h)
        except Exception as e:
            print(f"   - 계좌 {idx} 조회 실패: {e}", file=sys.stderr)

    # 계좌 간 동일 종목 합치기
    merged: dict[str, dict] = {}
    for h in all_holdings:
        if h["code"] in merged:
            merged[h["code"]]["qty"] += h["qty"]
        else:
            merged[h["code"]] = dict(h)
    return list(merged.values())


def _extract_holding_rows(data: dict) -> list[dict]:
    """
    kt00018 응답에서 개별 종목 리스트를 찾는다.
    문서상 'acnt_evlt_remn_indv_tot' 가 유력하나, 계정/버전에 따라 다를 수 있어
    응답 안에서 stk_cd 를 가진 list[dict] 를 자동 탐색한다.
    """
    # 1순위: 알려진 키
    for key in ("acnt_evlt_remn_indv_tot", "stk_acnt_evlt_prst", "output", "output1"):
        v = data.get(key)
        if isinstance(v, list) and v and isinstance(v[0], dict) and "stk_cd" in v[0]:
            return v
    # 2순위: 응답 전체에서 stk_cd 포함 list 자동 탐색
    for v in data.values():
        if isinstance(v, list) and v and isinstance(v[0], dict) and "stk_cd" in v[0]:
            return v
    # 못 찾으면 디버깅용으로 키 구조를 로그
    print("⚠️  보유종목 리스트를 자동 탐색하지 못했습니다. 응답 최상위 키:",
          list(data.keys()), file=sys.stderr)
    print("   전체 응답:", json.dumps(data, ensure_ascii=False)[:2000], file=sys.stderr)
    return []


def _clean_code(raw: str) -> str:
    """'A005930' / '005930_AL' 등에서 6자리 숫자만 추출."""
    m = re.search(r"\d{6}", str(raw))
    return m.group(0) if m else ""


def _to_int(v) -> int:
    try:
        return int(str(v).replace(",", "").replace("+", "").strip() or 0)
    except ValueError:
        return 0


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
    print("1) 키움 보유종목 조회 (전 계좌)…")
    holdings = collect_all_holdings()
    print(f"   합산 {len(holdings)}종목:", ", ".join(f"{h['name']}({h['code']})" for h in holdings))

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
