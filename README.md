# 보유종목 공시 아침 알림 (키움 REST API + DART + 카톡)

키움 계좌의 **실제 보유종목을 자동 조회**해서, 그 종목들의 **DART 신규 공시**를
매일 아침 **카카오톡**(또는 메일 경유)으로 보내주는 자동화입니다. GitHub Actions로
돌아가므로 PC를 켜둘 필요가 없습니다.

```
키움 kt00018(보유종목) → DART 공시조회 → 카카오톡 '나에게 보내기'
```

---

## 1. 키움 REST API 신청

1. https://openapi.kiwoom.com 접속 → 로그인 → **API 사용신청**
2. **App Key / Secret Key** 발급
3. 발급된 두 키를 메모 (잔고조회는 **실전계좌**여야 실제 보유분이 나옵니다. 모의는 `KIWOOM_HOST=https://mockapi.kiwoom.com`)

> 사용 TR: `au10001`(토큰발급), `kt00018`(계좌평가잔고내역)

## 2. DART OpenAPI 키 발급

1. https://opendart.fss.or.kr → **인증키 신청/관리**에서 무료 발급
2. 개인 키는 하루 호출 한도가 넉넉해서 보유종목 수십 개 정도는 문제없습니다.

## 3. 발송 방식 선택

### (A) 카카오톡 직접 — `SEND_METHOD=kakao` (기본)
1. https://developers.kakao.com 에서 앱 생성 → **REST API 키** 확보
2. [카카오 로그인] 활성화, 동의항목에서 **카카오톡 메시지 전송(talk_message)** 추가
3. 최초 1회 인가코드 → 토큰 교환으로 **refresh_token** 확보:
   ```bash
   # 1) 브라우저에서 인가코드 받기 (talk_message scope)
   #   https://kauth.kakao.com/oauth/authorize?response_type=code&client_id={REST_API_KEY}&redirect_uri=https://localhost&scope=talk_message
   # 2) 받은 code 로 토큰 교환
   curl -X POST "https://kauth.kakao.com/oauth/token" \
     -d "grant_type=authorization_code" \
     -d "client_id={REST_API_KEY}" \
     -d "redirect_uri=https://localhost" \
     -d "code={받은_인가코드}"
   ```
   응답의 `refresh_token` 을 저장 (약 2개월 유효, 자동 갱신됨).

### (B) 이메일 경유(기존 Make.com 재활용) — `SEND_METHOD=email`
- 스크립트가 본인 Gmail로 메일 발송 → 기존 **Make.com Gmail→카톡 시나리오**가 전달.
- Gmail은 **앱 비밀번호**를 만들어 `SMTP_PASS` 로 사용.
- Make 시나리오 필터를 제목 `[공시알림]` 으로 잡으면 깔끔합니다.

## 4. GitHub 저장소에 올리고 Secrets 등록

저장소 → **Settings → Secrets and variables → Actions → New repository secret** 으로
아래를 등록 (사용하는 방식의 것만):

| 공통 | 카카오 방식 | 이메일 방식 |
|---|---|---|
| `KIWOOM_APPKEY` | `KAKAO_REST_API_KEY` | `SMTP_USER` |
| `KIWOOM_SECRETKEY` | `KAKAO_REFRESH_TOKEN` | `SMTP_PASS` (앱 비번) |
| `DART_API_KEY` | `SEND_METHOD` = `kakao` | `MAIL_TO` |
| `KIWOOM_HOST` (모의일 때만) | | `SEND_METHOD` = `email` |

## 5. 실행

- 자동: 워크플로우가 **평일 오전 8시(KST)** 자동 실행 (`cron: 0 23 * * 0-4`, UTC 기준).
  - 시간 바꾸려면 `.github/workflows/daily-disclosure.yml` 의 cron 수정.
- 수동: Actions 탭 → **daily-disclosure → Run workflow** 로 즉시 테스트.
- 로컬 테스트:
  ```bash
  pip install -r requirements.txt
  export KIWOOM_APPKEY=... KIWOOM_SECRETKEY=... DART_API_KEY=... SEND_METHOD=kakao ...
  python disclosure_notifier.py
  ```

---

## ⚠️ 처음 1회 꼭 확인할 것

키움 `kt00018` 응답의 **보유종목 리스트 필드명**은 계정/버전에 따라 다를 수 있습니다.
스크립트는 `acnt_evlt_remn_indv_tot` 를 우선 찾고, 없으면 `stk_cd`를 가진 배열을
자동 탐색하도록 방어적으로 짰습니다. 그래도 못 찾으면 **응답 전체를 로그로 출력**하니,
첫 실행 로그를 보고 실제 필드명(보유수량 키 등)을 `get_holdings()` /
`_extract_holding_rows()` 에 맞춰주면 됩니다.

> 본 도구는 정보 정리용입니다. 투자 판단·매매는 반드시 공식 HTS/MTS에서 확인하세요.
> App Key·Secret·토큰은 절대 코드/공개 저장소에 직접 넣지 말고 Secrets로만 관리하세요.
