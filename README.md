# TradingBot

한국투자증권(KIS) Open API 기반 국내 주식 자동매매 봇. ORB(오프닝 레인지 돌파) + VWAP 전략으로 신호를 판단하고, 카카오톡으로 체결/오류 알림을 보냅니다.

## 구성 파일

| 파일 | 역할 |
|---|---|
| [auto_trading_bot.py](auto_trading_bot.py) | 메인 자동매매 봇. Windows 작업 스케줄러로 5분 간격 실행하는 것을 전제로 동작하며, 실행마다 한 번 상태를 체크하고 종료함 |
| [kis_interactive_query.py](kis_interactive_query.py) | 터미널에서 KIS API 키/계좌를 직접 입력해 실시간 ORB+VWAP 신호를 조회하는 보조 스크립트 |
| [kakao_setup.py](kakao_setup.py) | 카카오톡 "나에게 보내기" 최초 인증용 스크립트 (최초 1회만 실행) |
| [kakao_watchdog.py](kakao_watchdog.py) | 매매봇이 예정된 시간에 실행되지 않거나 오류를 남기면 카카오톡으로 알림을 보내는 워치독 |

## 전략 개요

- 초기 박스: 09:00~09:30
- 진입: 종가가 초기 박스 고가 돌파 + VWAP 위 (롱 전용, 종목당 하루 1회)
- 손절 -1.5% / 익절 +1.0%
- 15:20까지 미청산 시 강제 시장가 청산

## 안전장치

- 기본값은 모의투자. 실전 전환은 `auto_trading_bot.py`의 CONFIG에서 `IS_MOCK=False`와 `CONFIRM_LIVE_TRADING=True`를 모두 바꿔야만 활성화됨
- 일일 손실이 서킷브레이커 기준을 넘으면 신규 진입 중단 (기존 포지션 청산은 정상 수행)
- 모든 주문 시도는 `Order/` 하위 로그에 기록됨

## 사전 준비

1. 한국투자증권 계좌 + [KIS Developers](https://apiportal.koreainvestment.com) App Key/App Secret 발급 (모의투자용 우선 권장)
2. [Kakao Developers](https://developers.kakao.com)에서 REST API 키 발급 및 카카오 로그인(talk_message 동의항목) 설정
3. 의존 패키지 설치

```bash
pip install requests pandas numpy
```

## 설정 파일 (직접 생성 필요, 저장소에는 포함되지 않음)

민감정보가 담기는 아래 파일들은 `.gitignore`에 포함되어 저장소에 올라가지 않습니다. 각자 로컬에서 직접 만들어야 합니다.

- `kis_config.json` — KIS App Key/Secret, 계좌번호
- `kis_token_cache.json` — KIS 접근 토큰 캐시 (자동 생성)
- `kakao_token.json` — 카카오 인증 토큰 (`kakao_setup.py` 최초 실행 시 자동 생성)
- `user_config.json` — 사용자별 설정
- `Key.txt` — 기타 API 키/계좌 메모

## 실행

```bash
python auto_trading_bot.py
```

Windows 작업 스케줄러에 5분 간격으로 등록해 장중 내내 반복 실행하는 방식으로 사용합니다. 워치독은 별도 트리거(10~15분 간격)로 `kakao_watchdog.py`를 등록합니다.

## 주의사항

실전 매매 전환 전 KIS Developers 공식 문서에서 TR_ID 등 API 스펙을 반드시 재확인하고, 모의투자로 충분히 검증한 뒤 사용하세요.
