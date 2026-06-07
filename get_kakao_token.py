#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
카카오 refresh_token 1회용 발급 헬퍼
====================================
실행하면 인증 URL을 띄워주고, 브라우저에서 받은 code 를 붙여넣으면
refresh_token 을 출력합니다. 출력된 값을 GitHub Secret(KAKAO_REFRESH_TOKEN)에 등록하세요.

사전 준비 (developers.kakao.com):
  1) 내 애플리케이션 → 앱 생성 → [앱 키]의 REST API 키 확보
  2) [카카오 로그인] 활성화 ON
  3) [카카오 로그인] → Redirect URI 에 아래 REDIRECT_URI 와 동일하게 등록
  4) [동의항목] → '카카오톡 메시지 전송(talk_message)' 사용 ON
  5) 본인(앱 소유) 카카오계정으로 로그인하여 진행
"""

import sys
import webbrowser
import urllib.parse
import requests

# ── 여기 값을 채우세요 ────────────────────────────────
REST_API_KEY = "여기에_REST_API_키"
REDIRECT_URI = "https://example.com/oauth"   # 카카오 로그인에 등록한 것과 '완전히' 동일해야 함
# 클라이언트 시크릿이 '활성화 ON'이면 코드를 넣으세요. OFF면 빈 문자열("") 유지.
CLIENT_SECRET = ""
# ─────────────────────────────────────────────────────


def main():
    if REST_API_KEY.startswith("여기에"):
        print("먼저 파일 상단의 REST_API_KEY 와 REDIRECT_URI 를 채워주세요.")
        sys.exit(1)

    # 1) 인가코드 받기
    auth_url = (
        "https://kauth.kakao.com/oauth/authorize?"
        + urllib.parse.urlencode({
            "response_type": "code",
            "client_id": REST_API_KEY,
            "redirect_uri": REDIRECT_URI,
            "scope": "talk_message",
        })
    )
    print("\n[1] 아래 URL을 브라우저에서 열고 본인 카카오계정으로 로그인/동의하세요.")
    print("    (페이지가 안 떠도 OK — 주소창의 ?code=... 값만 복사하면 됩니다)\n")
    print(auth_url, "\n")
    try:
        webbrowser.open(auth_url)
    except Exception:
        pass

    code = input("[2] 리다이렉트된 주소의 code= 뒤 값을 붙여넣으세요: ").strip()

    # 2) 토큰 교환
    payload = {
        "grant_type": "authorization_code",
        "client_id": REST_API_KEY,
        "redirect_uri": REDIRECT_URI,
        "code": code,
    }
    if CLIENT_SECRET:
        payload["client_secret"] = CLIENT_SECRET
    res = requests.post(
        "https://kauth.kakao.com/oauth/token",
        data=payload,
        timeout=15,
    )
    data = res.json()
    if "refresh_token" not in data:
        print("\n❌ 실패:", data)
        print("  - code 만료(약 10분)/재사용 여부, redirect_uri 일치 여부를 확인하세요.")
        sys.exit(1)

    print("\n✅ 발급 완료. 아래 값을 GitHub Secret 에 등록하세요.")
    print("   KAKAO_REST_API_KEY =", REST_API_KEY)
    print("   KAKAO_REFRESH_TOKEN =", data["refresh_token"])
    if CLIENT_SECRET:
        print("   KAKAO_CLIENT_SECRET =", CLIENT_SECRET, "  (시크릿 ON 사용 중)")
    print("\n   (access_token 은 매 실행마다 자동 갱신되므로 저장 불필요)")


if __name__ == "__main__":
    main()
