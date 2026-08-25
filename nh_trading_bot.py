# -*- coding: utf-8 -*-
"""
NH투자증권(나무Plug) API 연동 - ORB+VWAP 자동매매 (KIS 봇과 완전히 별도)
=====================================================================
⚠️ 이 파일은 auto_trading_bot.py(KIS)와 전혀 무관한 별도의 병행 개발 파일입니다.
   기존 KIS 매매봇은 이 파일과 상관없이 그대로 계속 작동합니다.

⚠️ fetch_today_minute_bars()가 아직 실제 API 응답으로 검증된 적이 없습니다
   (분봉 조회 스니펫이 SDK에 없어서 명세만 보고 구현함). 평일 정규장에 결과를
   먼저 확인하기 전까지는 nh_config.json의 enable_auto_orders를 true로 켜지 마세요.

실행 결과는 Log/trading_log_NH_YYYY_MM_DD.txt(화면 출력 전체)와
Order/order_log_NH_YYYY_MM_DD.csv(실제 체결된 주문만)에 KIS 봇과 같은 폴더에
남습니다(파일명에 NH가 붙어 구분됨).

[사전 준비 - 아직 안 하셨다면]
1. https://www.nhplug.com/intro 에서 앱키/앱시크릿 발급 신청
2. pip install nhplug --break-system-packages
3. 아래 NH_CONFIG_PATH 파일을 만들어서 앱키/시크릿 저장

[nh_config.json 형식 예시]
{
  "app_key": "발급받은_APP_KEY",
  "app_secret": "발급받은_APP_SECRET",
  "account_no": "모의투자 계좌번호",
  "is_mock": true,
  "enable_auto_orders": false,
  "capital_per_ticker_krw": 1000000,
  "circuit_breaker_krw": 500000
}
※ enable_auto_orders를 true로 직접 바꾸기 전까지는, python nh_trading_bot.py를
  실행해도 실제 주문은 절대 나가지 않고(dry_run 고정) 신호만 출력합니다.
"""

import os
import json
import time
import requests
from datetime import datetime, timedelta, time as dtime
from zoneinfo import ZoneInfo
import pandas as pd
import numpy as np

BASE_DIR = r"C:\TradingBot"
NH_CONFIG_PATH = os.path.join(BASE_DIR, "nh_config.json")
NH_STATE_PATH = os.path.join(BASE_DIR, "nh_position_state.json")
WATCHLIST_PATH = os.path.join(BASE_DIR, "watchlist.json")
# KIS 봇(auto_trading_bot.py)과 같은 Log/, Order/ 폴더를 그대로 씀 - 파일명에만 NH를 붙여서 구분
LOG_DIR = os.path.join(BASE_DIR, "Log")
ORDER_DIR = os.path.join(BASE_DIR, "Order")
RUN_LOCK_PATH = os.path.join(BASE_DIR, "nh_trading_bot.lock")
# 카카오톡 "나에게 보내기" 알림도 KIS 봇과 같은 개인 토큰 파일을 공유해서 씀 (계정 하나라 재설정 불필요)
KAKAO_TOKEN_PATH = os.path.join(BASE_DIR, "kakao_token.json")

MOCK_BASE_URL = "https://moapi.nhplug.com:8443"
REAL_BASE_URL = "https://api.nhplug.com:8443"

MARKET_OPEN = dtime(9, 0)
MARKET_CLOSE = dtime(15, 30)

# 해외(미국) 정규장: 뉴욕 시간 09:30~16:00 기준. zoneinfo로 뉴욕 현지시각을 직접 계산해서
# 서머타임(EDT/EST) 전환을 하드코딩 없이 자동으로 반영함 (ict_strategy_bot.py와 동일한 방식).
US_MARKET_OPEN_ET = dtime(9, 30)
US_MARKET_CLOSE_ET = dtime(16, 0)
US_FORCE_CLOSE_BEFORE_MIN = 10

# 해외주식 API(나무Plug)는 /gbstock/... 네임스페이스를 씀. fc_sec_trd_nat_cd: 200=미국.
US_NAT_CD = "200"
US_STATE_PATH = os.path.join(BASE_DIR, "nh_us_position_state.json")
# 나무Plug에는 국내(/krstock/quote/v1/period)처럼 검증된 해외 분봉 조회 API 샘플이 없어서
# (공식 저장소 PLUG-OpenAPI/nhplug-sdk의 snippets/gbstock/에 current_price만 있고 분봉 조회는 없음),
# 확인된 "현재가 조회"(/gbstock/quote/v1/current)만으로 5분마다 스냅샷을 쌓아 자체 분봉을 만듦.
# -> 미검증 엔드포인트를 추측해서 쓰는 것보다 안전한 접근.
US_PRICE_HISTORY_PATH = os.path.join(BASE_DIR, "nh_us_price_history.json")


def get_us_market_status():
    """현재 시각 기준 미국 정규장이 열려있는지 뉴욕 현지시각으로 직접 판단.
    반환값: (열림여부(bool), 강제청산시각_지남여부(bool), 뉴욕현지시각(datetime))"""
    now_et = datetime.now(ZoneInfo("America/New_York"))
    is_weekday_et = now_et.weekday() < 5
    is_open = is_weekday_et and (US_MARKET_OPEN_ET <= now_et.time() <= US_MARKET_CLOSE_ET)
    force_close_dt = (datetime.combine(now_et.date(), US_MARKET_CLOSE_ET)
                       - timedelta(minutes=US_FORCE_CLOSE_BEFORE_MIN))
    past_force_close = now_et.time() >= force_close_dt.time()
    return is_open, past_force_close, now_et

try:
    from nhplug import call
    NHPLUG_AVAILABLE = True
except ImportError:
    NHPLUG_AVAILABLE = False
    print("[알림] nhplug 패키지가 설치되어 있지 않습니다.")
    print("       pip install nhplug --break-system-packages 로 설치해주세요.")


class TeeOutput:
    """print() 출력을 화면과 로그 파일에 동시에 씀 (auto_trading_bot.py의 TeeOutput과 동일한 방식)"""
    def __init__(self, filepath):
        import sys
        self.terminal = sys.stdout
        self.log = open(filepath, "a", encoding="utf-8")

    def write(self, message):
        self.terminal.write(message)
        self.log.write(message)
        self.log.flush()

    def flush(self):
        self.terminal.flush()
        self.log.flush()


def _guard_duplicate_run(min_interval_sec: int = 60) -> bool:
    """직전 실행 시작 후 min_interval_sec가 안 지났으면 중복 실행으로 보고 건너뜀.
    Windows 작업 스케줄러가 같은 5분 트리거를 1~2초 간격으로 두 번 발화하는 현상이
    관측되어(원인 불명) 추가한 방어 코드 - 정상적인 5분 간격 실행은 전혀 막지 않음.
    반환값 True = 정상 진행, False = 방금 막 실행돼서 이번 건 건너뛰어야 함"""
    now = datetime.now()
    if os.path.exists(RUN_LOCK_PATH):
        try:
            with open(RUN_LOCK_PATH, "r", encoding="utf-8") as f:
                last = datetime.fromisoformat(f.read().strip())
            if (now - last).total_seconds() < min_interval_sec:
                return False
        except Exception:
            pass  # 락 파일이 손상됐어도 정상 실행을 막으면 안 됨
    with open(RUN_LOCK_PATH, "w", encoding="utf-8") as f:
        f.write(now.isoformat())
    return True


def _setup_nh_logging():
    """화면 출력을 Log/trading_log_NH_YYYY_MM_DD.txt에도 남김.
    -- main()에서만 호출함(import 시점엔 안 함) --
    nh_strategy_test.py/nh_order_test.py처럼 이 모듈을 라이브러리로만 가져다 쓰는
    스크립트까지 로그 파일이 생기면 혼란스러워서, 실제 봇 진입점(main)에서만 켬."""
    import sys
    os.makedirs(LOG_DIR, exist_ok=True)
    today_str = datetime.now().strftime("%Y_%m_%d")
    log_path = os.path.join(LOG_DIR, f"trading_log_NH_{today_str}.txt")
    try:
        sys.stdout = TeeOutput(log_path)
        sys.stderr = sys.stdout
    except Exception as e:
        print(f"[경고] 로그 파일 연결 실패({e}), 화면 출력만 진행합니다.")


def _kakao_refresh_access_token(token_data):
    """-- auto_trading_bot.py의 동일 함수를 그대로 가져옴(순수 인프라 코드라 로직 변형 없음) --
    kakao_token.json은 KIS 봇과 공유하는 개인 계정 토큰이라 이 로직도 그대로 재사용함."""
    url = "https://kauth.kakao.com/oauth/token"
    data = {
        "grant_type": "refresh_token",
        "client_id": token_data["rest_api_key"],
        "refresh_token": token_data["refresh_token"],
    }
    res = requests.post(url, data=data, timeout=10)
    if res.status_code != 200:
        return None
    result = res.json()
    token_data["access_token"] = result["access_token"]
    expires_in = result.get("expires_in", 21599)
    token_data["access_token_expire_at"] = (datetime.now() + timedelta(seconds=expires_in)).isoformat()
    with open(KAKAO_TOKEN_PATH, "w", encoding="utf-8") as f:
        json.dump(token_data, f, ensure_ascii=False, indent=2)
    return token_data


def send_kakao_message(text: str):
    """체결 알림을 카카오톡(나에게 보내기)으로 전송. 실패해도 절대 예외를 던지지 않음.
    -- auto_trading_bot.py의 send_kakao_message()와 동일(공유 개인 토큰, 순수 인프라 코드) --
    kakao_token.json이 없으면(카카오 연동 안 한 경우) 조용히 건너뜀(에러 아님)."""
    if not os.path.exists(KAKAO_TOKEN_PATH):
        return
    try:
        with open(KAKAO_TOKEN_PATH, "r", encoding="utf-8") as f:
            token_data = json.load(f)

        expire_at = datetime.fromisoformat(token_data["access_token_expire_at"])
        if datetime.now() >= expire_at - timedelta(minutes=5):
            refreshed = _kakao_refresh_access_token(token_data)
            if refreshed is None:
                print("  [알림] 카카오 토큰 갱신 실패, 이번 알림은 건너뜁니다.")
                return
            token_data = refreshed

        url = "https://kapi.kakao.com/v2/api/talk/memo/default/send"
        headers = {"Authorization": f"Bearer {token_data['access_token']}"}
        template = {
            "object_type": "text",
            "text": text,
            "link": {"web_url": "https://developers.kakao.com", "mobile_web_url": "https://developers.kakao.com"},
        }
        res = requests.post(url, headers=headers, data={"template_object": json.dumps(template)}, timeout=10)
        if res.status_code != 200:
            print(f"  [알림] 카카오톡 전송 실패: {res.status_code} {res.text}")
    except Exception as e:
        print(f"  [알림] 카카오톡 전송 중 오류(무시하고 계속 진행): {e}")


NH_REASON_LABELS_KR = {
    "orb_vwap_breakout": "박스+VWAP 동시 돌파 매수",
    "take_profit": "익절 (목표 수익 도달)",
    "stop_loss": "손절 (손실 제한)",
    "nh_order_test_diagnostic": "진단 스크립트(nh_order_test.py) 테스트 매수",
}


def log_order_nh(action: str, ticker: str, price, shares: int, reason: str, extra: str = ""):
    """실제 체결된 주문을 Order/order_log_NH_YYYY_MM_DD.csv에 기록 + 카카오톡 알림.
    -- auto_trading_bot.py의 log_order() 구조를 참고함(그대로 복사 아님) --
    기록/알림 실패가 매매 상태 처리를 막으면 안 되므로 절대 예외를 위로 던지지 않음"""
    is_mock = True
    try:
        cfg = load_nh_config()
        if cfg:
            is_mock = bool(cfg.get("is_mock", True))
    except Exception:
        pass

    today_str = datetime.now().strftime("%Y_%m_%d")
    order_log_path = os.path.join(ORDER_DIR, f"order_log_NH_{today_str}.csv")
    row = pd.DataFrame([{
        "timestamp": datetime.now().isoformat(), "action": action, "ticker": ticker,
        "price": price, "shares": shares, "reason": reason, "extra": extra,
        "mode": "MOCK" if is_mock else "REAL",
    }])
    try:
        os.makedirs(ORDER_DIR, exist_ok=True)
        if os.path.exists(order_log_path):
            # dtype=str로 안 박으면 "005930" 같은 종목코드가 숫자로 오인식되어
            # 앞자리 0이 사라짐(예: 5930으로 저장) - pandas의 자동 타입추론 함정
            existing = pd.read_csv(order_log_path, dtype={"ticker": str})
            row = pd.concat([existing, row], ignore_index=True)
        row.to_csv(order_log_path, index=False, encoding="utf-8-sig")
    except Exception as e:
        print(f"  [경고] {os.path.basename(order_log_path)} 기록 실패(무시하고 계속 진행): {e}")

    mode_label = "모의" if is_mock else "실전"
    action_label = {"BUY": "매수", "SELL": "매도"}.get(action, action)
    reason_kr = NH_REASON_LABELS_KR.get(reason, reason)
    msg = (f"[NH-{mode_label}] {action_label} 체결\n"
           f"종목: {ticker}\n"
           f"가격: {price:,.0f}원 x {shares}주\n"
           f"사유: {reason_kr}")
    if extra:
        extra_display = extra
        if extra.startswith("pnl="):
            try:
                val = float(extra.split("=", 1)[1])
                extra_display = f"손익: {val:+,.0f}원"
            except ValueError:
                pass
        msg += f"\n{extra_display}"
    send_kakao_message(msg)


def load_nh_config():
    if not os.path.exists(NH_CONFIG_PATH):
        print(f"[최초 설정 필요] {NH_CONFIG_PATH} 파일이 없습니다.")
        default = {"app_key": "여기에_발급받은_APP_KEY_입력", "app_secret": "여기에_APP_SECRET_입력",
                   "account_no": "여기에_계좌번호_입력", "is_mock": True}
        os.makedirs(BASE_DIR, exist_ok=True)
        with open(NH_CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(default, f, ensure_ascii=False, indent=2)
        print("빈 설정 파일을 만들었습니다. 메모장으로 열어서 앱키/시크릿/계좌번호를 채워주세요.")
        return None
    with open(NH_CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def get_current_price(ticker: str, market_cd: str = "KRX"):
    """국내주식 현재가 조회 (README에서 확인된 정확한 사용법 그대로)
    market_cd: KRX(코스피/코스닥 통합) 등"""
    if not NHPLUG_AVAILABLE:
        print("[오류] nhplug 패키지 미설치로 조회 불가")
        return None
    try:
        result = call("/krstock/quote/v1/currentPrice", {"iem_cd": ticker, "market_cd": market_cd})
        return result
    except Exception as e:
        print(f"[오류] 시세 조회 실패: {e}")
        return None


# ============================================================
# 해외(미국) 주식 - 나무Plug /gbstock/... API
# 아래 함수들의 요청 필드는 공식 저장소(github.com/PLUG-OpenAPI/nhplug-sdk)
# snippets/gbstock/*.py 샘플로 확인된 필드만 사용함. 매도 주문(order/v1/sell)만
# 공식 샘플이 없어서 매수(order/v1/buy)와 대칭 구조로 추정한 것이니, 실거래(enable_auto_orders)
# 전에 반드시 모의투자 환경에서 매도 체결까지 직접 확인해주세요.
# ============================================================
def get_us_current_price(ticker: str):
    """해외주식 현재가 조회 (POST /gbstock/quote/v1/current).
    ⚠️ nhplug-sdk 공식 예제(current_price.py)에서 확인된 정확한 스펙: iem_cd 딱 하나만 보냄.
    이전에 fc_sec_trd_nat_cd(국가코드)를 추가로 넣었었는데, 공식 스펙엔 이 필드가 아예 없어서
    오히려 모든 종목이 "IGW40019 종목코드를 확인해주세요" 오류로 거부되고 있었음. 제거함.
    rate_limit(IGW42903)은 순간적인 초과라 짧게 대기 후 재시도함.

    ⚠️ 나무Plug 공식 답변(2026-08-23 확인): 모의투자(moapi) 환경은 시세 정보 자체를
    제공하지 않으며, 시세는 반드시 실거래(운영, api.nhplug.com) 서버로 조회해야 함.
    그래서 이 함수 "안에서만" 일시적으로 NHPLUG_BASE_URL을 운영 서버로 바꿔서 조회하고,
    끝나면(성공/실패/예외 어떤 경우든) 반드시 원래 값(호출 전 설정, 보통 모의투자)으로
    복원함 - try/finally라 도중에 예외가 나도 복원이 보장됨.
    주문(order_us_buy/order_us_sell 등)은 이 함수와 무관하게 항상 그 시점의
    NHPLUG_BASE_URL을 그대로 쓰므로, 이 함수 호출 전후로 계속 모의투자 서버를 씀."""
    if not NHPLUG_AVAILABLE:
        return None

    original_base_url = os.environ.get("NHPLUG_BASE_URL")
    os.environ["NHPLUG_BASE_URL"] = REAL_BASE_URL
    try:
        max_attempts = 3
        for attempt in range(1, max_attempts + 1):
            try:
                payload = {"iem_cd": ticker}
                print(f"  [진단] get_us_current_price 요청값(운영 서버로 조회): ticker={ticker!r}, "
                      f"payload={payload!r}, APP_KEY 앞8자리={os.environ.get('NHPLUG_APP_KEY', '(없음)')[:8]}")
                return call("/gbstock/quote/v1/current", payload)
            except Exception as e:
                is_rate_limit = "IGW42903" in str(e) or "rate_limit" in str(e).lower()
                if is_rate_limit and attempt < max_attempts:
                    wait_sec = 1.5 * attempt
                    print(f"  [경고] 해외 시세 조회 rate_limit, {wait_sec:.1f}초 대기 후 재시도 "
                          f"({attempt}/{max_attempts - 1})")
                    time.sleep(wait_sec)
                    continue
                print(f"  [오류] 해외 시세 조회 실패: {e}")
                return None
    finally:
        # 예외가 나든 정상 반환이든 항상 실행됨 - 원래 환경(보통 모의투자)으로 반드시 복원
        if original_base_url is None:
            os.environ.pop("NHPLUG_BASE_URL", None)
        else:
            os.environ["NHPLUG_BASE_URL"] = original_base_url


def get_us_buyable_amount(act_no: str, ticker: str, price: float = None) -> dict:
    """해외주식 매수가능금액/수량 조회 (POST /gbstock/inquiry/v1/buyableAmount, pcs_dit=1/2)"""
    input_0 = {
        "act_no": act_no, "pcs_dit": "2",  # 2: 매수가능수량
        "fc_sec_trd_nat_cd": US_NAT_CD, "iem_cd": ticker,
        "wtm_cur_knd_cd": "2", "oss_orr_knd_cd": "1",
        "ahi_nmn_pr_tp_cd": "00" if price is not None else "03",
    }
    if price is not None:
        input_0["fc_orr_uit_pr"] = price
    try:
        return call("/gbstock/inquiry/v1/buyableAmount", input_0)
    except Exception as e:
        print(f"  [오류] 해외 매수가능수량 조회 실패: {e}")
        return {}


def get_us_sellable_quantity(act_no: str, ticker: str) -> dict:
    """해외주식 매도가능수량 조회 (같은 buyableAmount API를 pcs_dit=3으로 호출, 공식 샘플 확인됨)"""
    try:
        return call("/gbstock/inquiry/v1/buyableAmount", {
            "act_no": act_no, "pcs_dit": "3",
            "fc_sec_trd_nat_cd": US_NAT_CD, "iem_cd": ticker,
            "wtm_cur_knd_cd": "2", "oss_orr_knd_cd": "1", "ahi_nmn_pr_tp_cd": "03",
        })
    except Exception as e:
        print(f"  [오류] 해외 매도가능수량 조회 실패: {e}")
        return {}


def order_us_buy(act_no: str, iem_cd: str, orr_qty: int, price: float = None, dry_run: bool = True) -> dict:
    """해외주식 매수 주문 (POST /gbstock/order/v1/buy, 공식 샘플로 확인된 필드)"""
    input_0 = {
        "act_no": act_no, "fc_sec_trd_nat_cd": US_NAT_CD, "iem_cd": iem_cd,
        "orr_qty": orr_qty, "ahi_nmn_pr_tp_cd": "00" if price is not None else "03",
        "wtm_cur_knd_cd": "2",
    }
    if price is not None:
        input_0["fc_orr_uit_pr"] = price
    if dry_run:
        return {"dry_run": True, "Input_0": input_0}
    if not NHPLUG_AVAILABLE:
        return {"success": False, "msg1": "nhplug 미설치"}
    try:
        result = call("/gbstock/order/v1/buy", input_0)
        return {"success": True, "result": result}
    except Exception as e:
        print(f"  [주문 거부됨] 해외 매수: {e}")
        return {"success": False, "msg1": str(e)}


def order_us_sell(act_no: str, iem_cd: str, orr_qty: int, price: float = None, dry_run: bool = True) -> dict:
    """⚠️ 미검증: 해외주식 매도 주문. 공식 샘플에 매도 주문이 없어서 매수(order/v1/buy)와
    대칭 구조(/gbstock/order/v1/sell, 동일 필드)로 추정해 구현함. 실거래 전 반드시
    모의투자 환경에서 매도 체결이 정상적으로 되는지 직접 확인해주세요."""
    input_0 = {
        "act_no": act_no, "fc_sec_trd_nat_cd": US_NAT_CD, "iem_cd": iem_cd,
        "orr_qty": orr_qty, "ahi_nmn_pr_tp_cd": "00" if price is not None else "03",
        "wtm_cur_knd_cd": "2",
    }
    if price is not None:
        input_0["fc_orr_uit_pr"] = price
    if dry_run:
        return {"dry_run": True, "Input_0": input_0}
    if not NHPLUG_AVAILABLE:
        return {"success": False, "msg1": "nhplug 미설치"}
    try:
        result = call("/gbstock/order/v1/sell", input_0)
        return {"success": True, "result": result}
    except Exception as e:
        print(f"  [주문 거부됨] 해외 매도: {e}")
        return {"success": False, "msg1": str(e)}


def load_us_price_history() -> dict:
    if os.path.exists(US_PRICE_HISTORY_PATH):
        try:
            with open(US_PRICE_HISTORY_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_us_price_history(history: dict):
    with open(US_PRICE_HISTORY_PATH, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False)


def append_us_price_snapshot(ticker: str, price: float, volume: float = None):
    """검증된 현재가 조회 API로 5분마다(Task Scheduler 주기) 스냅샷을 쌓아서
    자체적으로 분봉을 구성함. 나무Plug 해외 분봉 조회 API가 공식적으로 검증되지
    않았기 때문에, 검증된 현재가 API만으로 안전하게 시계열을 만드는 방식."""
    history = load_us_price_history()
    today_et = datetime.now(ZoneInfo("America/New_York")).date().isoformat()
    key = f"{ticker}:{today_et}"
    if key not in history:
        history[key] = []
    history[key].append({"ts": datetime.now().isoformat(), "price": price, "volume": volume})
    # 하루치만 유지 (오래된 날짜 키는 정리)
    history = {k: v for k, v in history.items() if k.endswith(today_et)}
    save_us_price_history(history)
    return history[key]


def build_us_bars_from_history(ticker: str, bucket_minutes: int = 5) -> pd.DataFrame:
    """쌓인 현재가 스냅샷을 bucket_minutes 단위로 묶어 OHLCV 분봉으로 변환.
    스냅샷 개수가 적은 하루 초반에는 봉 개수가 적을 수밖에 없음(자연스러운 현상)."""
    today_et = datetime.now(ZoneInfo("America/New_York")).date().isoformat()
    history = load_us_price_history()
    snapshots = history.get(f"{ticker}:{today_et}", [])
    if not snapshots:
        return pd.DataFrame()
    df = pd.DataFrame(snapshots)
    df["datetime"] = pd.to_datetime(df["ts"])
    df = df.set_index("datetime").sort_index()
    ohlc = df["price"].resample(f"{bucket_minutes}min").ohlc()
    vol = df["volume"].resample(f"{bucket_minutes}min").sum() if df["volume"].notna().any() else None
    ohlc = ohlc.dropna(subset=["open"]).reset_index()
    ohlc.columns = ["datetime", "open", "high", "low", "close"]
    ohlc["volume"] = vol.reindex(ohlc["datetime"]).values if vol is not None else 0.0
    return ohlc


def order_cash_buy(act_no: str, iem_cd: str, orr_qty: int, orr_pr: int = None, dry_run: bool = True) -> dict:
    """국내주식 현금 매수 주문 (POST /krstock/order/v1/cashBuy)
    -- nhplug-sdk 공식 예제(order_cash_buy.py)에서 확인된 정확한 스펙 그대로 사용 --
    act_no: 계좌번호, iem_cd: 종목코드 6자리(예: 005930), orr_qty: 주문수량
    orr_pr: 지정가일 때 원 단위 정수가, None이면 시장가 주문
    dry_run=True면 실제 전송 없이 요청 내용만 반환 (기본값, 안전을 위해)"""
    is_market = orr_pr is None
    input_0 = {
        "act_no": act_no,
        "iem_cd": iem_cd,
        "orr_qty": orr_qty,
        "nmn_pr_tp_cd": "05" if is_market else "01",
        "orr_cnd_dit_cd": "00",
        "ssl_nmn_pr_dit_cd": "00",
        "rmt_mkt_cd": "KRX",
        "sor_mkt_sli_yn": "N",
    }
    if not is_market:
        input_0["orr_pr"] = orr_pr
    if dry_run:
        return {"dry_run": True, "Input_0": input_0}
    if not NHPLUG_AVAILABLE:
        return {"error": "nhplug 패키지 미설치"}
    return call("/krstock/order/v1/cashBuy", input_0)


def order_cash_sell(act_no: str, iem_cd: str, orr_qty: int, orr_pr: int = None, dry_run: bool = True) -> dict:
    """국내주식 현금 매도 주문 (POST /krstock/order/v1/cashSell)
    -- nhplug-sdk 공식 예제(order_cash_sell.py)에서 확인된 정확한 스펙 그대로 사용 --
    필드 구조는 매수와 동일, 엔드포인트만 cashSell로 다름"""
    is_market = orr_pr is None
    input_0 = {
        "act_no": act_no,
        "iem_cd": iem_cd,
        "orr_qty": orr_qty,
        "nmn_pr_tp_cd": "05" if is_market else "01",
        "orr_cnd_dit_cd": "00",
        "ssl_nmn_pr_dit_cd": "00",
        "rmt_mkt_cd": "KRX",
        "sor_mkt_sli_yn": "N",
    }
    if not is_market:
        input_0["orr_pr"] = orr_pr
    if dry_run:
        return {"dry_run": True, "Input_0": input_0}
    if not NHPLUG_AVAILABLE:
        return {"error": "nhplug 패키지 미설치"}
    return call("/krstock/order/v1/cashSell", input_0)


def order_overseas_buy(act_no: str, iem_cd: str, orr_qty: int, price: float = None,
                        nat_cd: str = "200", dry_run: bool = True) -> dict:
    """해외주식 매수 주문 (POST /gbstock/order/v1/buy)
    -- nhplug-sdk 공식 예제(snippets/gbstock/order_buy/order_buy.py) 그대로 참고 --
    act_no: 해외거래 계좌번호, iem_cd: 티커(예: AAPL), nat_cd: 200=미국 070=일본 120=홍콩 160=상해 170=심천
    price 지정 시 지정가(00), 생략 시 시장가(03). wtm_cur_knd_cd=2(원화)는 매수에만 필요한 필드
    dry_run=True면 실제 전송 없이 요청 내용만 반환 (기본값, 안전을 위해)"""
    input_0 = {
        "act_no": act_no,
        "fc_sec_trd_nat_cd": nat_cd,
        "iem_cd": iem_cd,
        "orr_qty": orr_qty,
        "ahi_nmn_pr_tp_cd": "00" if price is not None else "03",
        "wtm_cur_knd_cd": "2",
    }
    if price is not None:
        input_0["fc_orr_uit_pr"] = price
    if dry_run:
        return {"dry_run": True, "Input_0": input_0}
    if not NHPLUG_AVAILABLE:
        return {"error": "nhplug 패키지 미설치"}
    return call("/gbstock/order/v1/buy", input_0)


def order_overseas_sell(act_no: str, iem_cd: str, orr_qty: int, price: float = None,
                         nat_cd: str = "200", dry_run: bool = True) -> dict:
    """해외주식 매도 주문 (POST /gbstock/order/v1/sell)
    -- nhplug-sdk 저장소에는 아직 매도 예제 스니펫이 없어서, 공식 명세(docs/gbstock/openapi.json,
       scripts/fetch_docs.py로 www.nhplug.com에서 직접 내려받아 확인)를 기준으로 구현함 --
    매수와 필드는 거의 동일하지만 wtm_cur_knd_cd(증거금통화종류코드)는 매도 요청에는 없는 필드라 제외함
    dry_run=True면 실제 전송 없이 요청 내용만 반환 (기본값, 안전을 위해)"""
    input_0 = {
        "act_no": act_no,
        "fc_sec_trd_nat_cd": nat_cd,
        "iem_cd": iem_cd,
        "orr_qty": orr_qty,
        "ahi_nmn_pr_tp_cd": "00" if price is not None else "03",
    }
    if price is not None:
        input_0["fc_orr_uit_pr"] = price
    if dry_run:
        return {"dry_run": True, "Input_0": input_0}
    if not NHPLUG_AVAILABLE:
        return {"error": "nhplug 패키지 미설치"}
    return call("/gbstock/order/v1/sell", input_0)


def get_balance(act_no: str) -> dict:
    """국내주식 잔고 조회 (POST /krstock/inquiry/v1/balance)
    -- nhplug-sdk 공식 예제(balance.py)에서 확인된 정확한 스펙 그대로 사용 --
    반환값의 Output_0: 예수금(dca), 총자산(tot_aet_amt) 등 계좌 요약
    반환값의 Output_1: 보유 종목 리스트"""
    if not NHPLUG_AVAILABLE:
        return {"error": "nhplug 패키지 미설치"}
    return call("/krstock/inquiry/v1/balance", {
        "act_no": act_no,
        "bnc_bse_cd": "5",
        "ltg_aot_dit_cd": "9",
        "aet_bse": "2",
        "qut_dit_cd": "UNT",
    })


def get_buyable_quantity(act_no: str, iem_cd: str, price: int = None) -> dict:
    """국내주식 매수가능수량 조회 (POST /krstock/inquiry/v1/buyableQuantity)
    -- nhplug-sdk 공식 예제(buyable_quantity.py)에서 확인된 정확한 스펙 그대로 사용 --
    price 지정 시 그 가격 기준(보통가), 생략 시 시장가 기준"""
    if not NHPLUG_AVAILABLE:
        return {"error": "nhplug 패키지 미설치"}
    input_0 = {
        "ost_dit_cd": "1",  # 1.현금
        "act_no": act_no,
        "iem_cd": iem_cd,
        "nmn_pr_tp_cd": "01" if price is not None else "05",
    }
    if price is not None:
        input_0["orr_pr"] = price
    return call("/krstock/inquiry/v1/buyableQuantity", input_0)


def get_sellable_quantity(act_no: str, iem_cd: str, cfd_lon_cd: str = "00", lon_dt: str = None) -> dict:
    """국내주식 매도가능수량 조회 (POST /krstock/inquiry/v1/sellableQuantity)
    -- nhplug-sdk 공식 예제(sellable_quantity.py)에서 확인된 정확한 스펙 그대로 사용 --
    ⚠️ 명세상 iem_cd는 선택이지만 실제로는 필수(없으면 rsp_cd 10006 에러)
    cfd_lon_cd: 00.일반거래(기본값, 저희 매매봇은 이것만 씀) 01~04는 신용거래 관련
    반환값 Output_0.sll_pbl_qty = 매도가능수량, bnc_qty = 보유수량"""
    if not NHPLUG_AVAILABLE:
        return {"error": "nhplug 패키지 미설치"}
    input_0 = {
        "act_no": act_no,
        "iem_cd": iem_cd,
        "cfd_lon_cd": cfd_lon_cd,
    }
    if lon_dt:
        input_0["lon_dt"] = lon_dt
    return call("/krstock/inquiry/v1/sellableQuantity", input_0)


def fetch_today_minute_bars(iem_cd: str, market_cd: str = "KRX", xtick: str = "5",
                             array_cnt: str = "120") -> pd.DataFrame:
    """오늘 분봉 데이터 조회 (POST /krstock/quote/v1/period, gubun=5 분봉)
    ⚠️ nhplug-sdk 저장소에는 이 엔드포인트의 예제 스니펫이 없어서 공식 명세(docs/krstock/openapi.json,
       scripts/fetch_docs.py로 www.nhplug.com에서 직접 내려받아 확인)만 보고 구현함.
       명세 자체에도 "Output_0는 Array로 선언됐지만 예시 응답은 Object"라는 경고가 있으므로
       실전에 쓰기 전에 반드시 실제 응답으로 한 번 검증해야 함.
    ⚠️ xtick 기본값을 "1"(1분봉)에서 "5"(5분봉)로 변경함. 1분봉×120개=2시간치뿐이라,
       장 시작(09:00)~박스구간(09:00~09:30) 데이터가 11시 넘어가면 조회범위에서 밀려나
       박스를 영영 못 만드는 문제가 있었음. 5분봉×120개=10시간치라 장 마감까지 항상 커버함.
    반환: datetime, open, high, low, close, volume 컬럼의 DataFrame (과거->현재 순 정렬)"""
    if not NHPLUG_AVAILABLE:
        print("[오류] nhplug 패키지 미설치로 조회 불가")
        return pd.DataFrame()

    # rate_limit(IGW42903)은 순간적인 호출량 초과라 잠깐 쉬었다 재시도하면 대부분 풀림.
    # business(00007) 등 서버 측 오류는 재시도해도 계속 실패할 가능성이 높으므로 바로 포기함.
    max_attempts = 3
    result = None
    for attempt in range(1, max_attempts + 1):
        try:
            result = call("/krstock/quote/v1/period", {
                "market_cd": market_cd,
                "iem_cd": iem_cd,
                "gubun": "5",           # 5=분봉
                "xtick": xtick,         # 분 단위 (예: "1"=1분봉)
                "today_cls_code": "1",  # 당일만조회
                "array_cnt": array_cnt,
                "fake_tick": "1",       # 거래량0봉 제외
            })
            break
        except Exception as e:
            is_rate_limit = "IGW42903" in str(e) or "rate_limit" in str(e).lower()
            if is_rate_limit and attempt < max_attempts:
                wait_sec = 1.0 * attempt  # 1초, 2초로 점점 늘려가며 대기
                print(f"[경고] 분봉 조회 rate_limit, {wait_sec:.0f}초 대기 후 재시도 "
                      f"({attempt}/{max_attempts - 1})")
                time.sleep(wait_sec)
                continue
            print(f"[오류] 분봉 조회 실패: {e}")
            return pd.DataFrame()

    if result is None:
        return pd.DataFrame()

    rows = result.get("Output_0") or []
    if isinstance(rows, dict):  # 명세 경고대로 Object 단건으로 올 수도 있음
        rows = [rows]
    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df["datetime"] = pd.to_datetime(df["qry_date"] + df["qry_time"], format="%Y%m%d%H%M%S")
    df["open"] = df["stck_oprc"].astype(float)
    df["high"] = df["stck_hgpr"].astype(float)
    df["low"] = df["stck_lwpr"].astype(float)
    df["close"] = df["stck_prpr"].astype(float)
    df = df.sort_values("datetime").reset_index(drop=True)
    # acml_vol(누적거래량)은 하루 누적치라, 봉별 거래량은 직전 봉과의 차분으로 구함
    acml_vol = df["acml_vol"].astype(float)
    df["volume"] = acml_vol.diff().fillna(acml_vol)
    df.loc[df["volume"] < 0, "volume"] = 0  # 장중 누적치 리셋 등 이상치 방어
    return df[["datetime", "open", "high", "low", "close", "volume"]]


def calc_vwap(df: pd.DataFrame):
    """VWAP 계산 (auto_trading_bot.py의 calc_vwap과 동일한 방식: 누적거래대금/누적거래량)"""
    cum_vol = df["volume"].cumsum()
    cum_vp = (df["close"] * df["volume"]).cumsum()
    return cum_vp / cum_vol.replace(0, np.nan)


def check_orb_vwap_signal(df: pd.DataFrame, orb_minutes: int = 30) -> dict:
    """박스(오프닝레인지) 상단 + VWAP 계산해서 진입조건 충족 여부 판단
    -- auto_trading_bot.py의 process_ticker() 진입조건 로직 구조를 참고함(그대로 복사 아님) --
    진입조건: 현재가가 박스상단(오프닝레인지 고가)과 VWAP을 모두 상회
    반환: {"box_forming": True} (박스 형성 중) 또는
          {"box_forming": False, "orb_high", "vwap", "close", "entry_signal"}"""
    if df.empty:
        return {"box_forming": True}

    df = df.copy()
    df["vwap"] = calc_vwap(df)

    # ⚠️ 예전엔 .normalize() + Timedelta 조합에서 NumPy DeprecationWarning이 발생했음.
    # 날짜만 뽑아서 Timestamp를 새로 만드는 방식으로 교체해 경고 없이 동일한 결과를 얻음.
    first_date = df["datetime"].iloc[0].date()
    open_dt = pd.Timestamp(first_date) + pd.Timedelta(hours=9)
    range_end = open_dt + pd.Timedelta(minutes=orb_minutes)
    opening = df[df["datetime"] < range_end]

    if opening.empty or df["datetime"].max() < range_end:
        return {"box_forming": True}

    orb_high = float(opening["high"].max())
    latest = df.iloc[-1]
    entry_signal = bool(latest["close"] > orb_high and latest["close"] > latest["vwap"])
    return {
        "box_forming": False,
        "orb_high": orb_high,
        "vwap": float(latest["vwap"]),
        "close": float(latest["close"]),
        "high": float(latest["high"]),
        "low": float(latest["low"]),
        "entry_signal": entry_signal,
    }


def check_exit_signal(latest_low: float, latest_high: float, entry_price: float,
                       stop_pct: float = 0.02, tp_trigger_pct: float = 0.03):
    """손절/익절 조건 체크 (auto_trading_bot.py process_ticker() 청산조건 로직 구조 참고)
    반환: "stop_loss" | "take_profit" | None"""
    stop_price = entry_price * (1 - stop_pct)
    if latest_low <= stop_price:
        return "stop_loss"
    high_ret = (latest_high - entry_price) / entry_price
    if high_ret >= tp_trigger_pct:
        return "take_profit"
    return None


def process_ticker_nh(iem_cd: str, act_no: str, state: dict, capital_krw: int = 1_000_000,
                       orb_minutes: int = 30, stop_pct: float = 0.02, tp_trigger_pct: float = 0.03,
                       dry_run: bool = True) -> dict:
    """종목 1개에 대해 박스+VWAP 전략을 한 번 평가하고, 조건 충족 시 주문까지 시도함
    -- auto_trading_bot.py의 process_ticker() 전체 구조(진입/청산 상태기계)를 참고함(그대로 복사 아님) --
    state: 종목코드별 포지션 상태를 담는 dict. 호출하는 쪽에서 계속 들고 있어야 함
           (예: {"005930": {"in_position": False, "day_traded": False}})
    dry_run=True(기본값)면 실제 주문 없이 신호와 주문 payload만 반환.
    실거래를 원하면 이 함수를 호출하는 사람이 명시적으로 dry_run=False로 바꿔야 함."""
    try:
        pos = state.setdefault(iem_cd, {"in_position": False, "day_traded": False})
        df = fetch_today_minute_bars(iem_cd)
        if df.empty:
            return {"ticker": iem_cd, "status": "no_data"}

        sig = check_orb_vwap_signal(df, orb_minutes=orb_minutes)
        if sig.get("box_forming"):
            return {"ticker": iem_cd, "status": "box_forming"}

        if not pos["in_position"] and not pos["day_traded"]:
            if not sig["entry_signal"]:
                return {"ticker": iem_cd, "status": "waiting", **sig}
            shares = int(capital_krw // sig["close"])
            if shares <= 0:
                return {"ticker": iem_cd, "status": "insufficient_capital", **sig}
            order_result = order_cash_buy(act_no, iem_cd, shares, dry_run=dry_run)
            if not dry_run:
                pos.update({"in_position": True, "day_traded": True,
                            "entry_price": sig["close"], "shares": shares})
            return {"ticker": iem_cd, "status": "entry_signal", "order_result": order_result, **sig}

        if pos["in_position"]:
            exit_reason = check_exit_signal(sig["low"], sig["high"], pos["entry_price"],
                                             stop_pct, tp_trigger_pct)
            if not exit_reason:
                return {"ticker": iem_cd, "status": "holding", **sig}
            order_result = order_cash_sell(act_no, iem_cd, pos["shares"], dry_run=dry_run)
            if not dry_run:
                pos.update({"in_position": False})
            return {"ticker": iem_cd, "status": f"exit_signal:{exit_reason}",
                    "order_result": order_result, **sig}

        return {"ticker": iem_cd, "status": "done_for_today", **sig}
    except Exception as e:
        print(f"[오류] {iem_cd} 전략 평가 중 예외 발생: {e}")
        return {"ticker": iem_cd, "status": "error", "error": str(e)}


def process_ticker_nh_us(ticker: str, act_no: str, state: dict, capital_usd: float = 400,
                          orb_bars: int = 6, stop_pct: float = 0.02, tp_trigger_pct: float = 0.03,
                          dry_run: bool = True) -> dict:
    """해외(미국) 종목 1개에 대해 박스+VWAP 전략을 한 번 평가.
    process_ticker_nh()와 동일한 상태기계 구조지만, 분봉 데이터를 국내처럼 API에서 직접
    받는 대신 검증된 현재가 API로 쌓은 자체 스냅샷 분봉(build_us_bars_from_history)을 씀.
    orb_bars=6: 5분 단위 스냅샷이므로 6개(=30분)가 쌓여야 오프닝 레인지 형성 완료로 봄.
    capital_usd: 국내(원화)와 자금 단위가 달라서 별도 달러 한도를 씀(환율 미반영, 의도된 제한)."""
    try:
        pos = state.setdefault(ticker, {"in_position": False, "day_traded": False})

        price_data = get_us_current_price(ticker)
        if not price_data:
            return {"ticker": ticker, "status": "no_data"}
        # Output_0 필드명은 공식 chk_current_price.py 샘플에서 구체적으로 확정되지 않아
        # 흔한 후보들을 순서대로 시도함 (해외주식 현재가 응답의 일반적인 필드명 패턴)
        o0 = price_data.get("Output_0", {}) if isinstance(price_data, dict) else {}
        price_raw = o0.get("prpr") or o0.get("last") or o0.get("iem_prpr")
        if price_raw is None:
            print(f"  {ticker}: 현재가 응답에서 가격 필드를 못 찾음 (응답 키: {list(o0.keys())})")
            return {"ticker": ticker, "status": "no_data"}
        price = float(price_raw)
        append_us_price_snapshot(ticker, price)

        df = build_us_bars_from_history(ticker)
        if len(df) < orb_bars:
            return {"ticker": ticker, "status": "box_forming",
                    "bars_so_far": len(df), "bars_needed": orb_bars}

        sig = check_orb_vwap_signal(df, orb_minutes=orb_bars * 5)
        if sig.get("box_forming"):
            return {"ticker": ticker, "status": "box_forming"}

        if not pos["in_position"] and not pos["day_traded"]:
            if not sig["entry_signal"]:
                return {"ticker": ticker, "status": "waiting", **sig}
            shares = int(capital_usd // sig["close"])
            if shares <= 0:
                return {"ticker": ticker, "status": "insufficient_capital", **sig}
            order_result = order_us_buy(act_no, ticker, shares, dry_run=dry_run)
            if not dry_run and order_result.get("success"):
                pos.update({"in_position": True, "day_traded": True,
                            "entry_price": sig["close"], "shares": shares})
            return {"ticker": ticker, "status": "entry_signal", "order_result": order_result, **sig}

        if pos["in_position"]:
            exit_reason = check_exit_signal(sig["low"], sig["high"], pos["entry_price"],
                                             stop_pct, tp_trigger_pct)
            if not exit_reason:
                return {"ticker": ticker, "status": "holding", **sig}
            order_result = order_us_sell(act_no, ticker, pos["shares"], dry_run=dry_run)
            if not dry_run and order_result.get("success"):
                pos.update({"in_position": False})
            return {"ticker": ticker, "status": f"exit_signal:{exit_reason}",
                    "order_result": order_result, **sig}

        return {"ticker": ticker, "status": "done_for_today", **sig}
    except Exception as e:
        print(f"[오류] {ticker} 해외 전략 평가 중 예외 발생: {e}")
        return {"ticker": ticker, "status": "error", "error": str(e)}


def load_nh_watchlist() -> list:
    """국내 종목코드 리스트 로드. watchlist.json(KIS 봇과 공유하는 종목 스크리닝 결과)이
    있으면 그걸 재사용하고(나중에 KIS-NH 비교를 위해 같은 종목군으로 맞춤), 없으면
    삼성전자 하나만 기본값으로 씀."""
    if os.path.exists(WATCHLIST_PATH):
        try:
            with open(WATCHLIST_PATH, "r", encoding="utf-8") as f:
                wl = json.load(f)
            tickers = list(wl.get("kr_tickers", {}).keys())
            if tickers:
                return tickers
        except Exception as e:
            print(f"[알림] watchlist.json 로드 실패({e}), 기본 종목으로 대체합니다.")
    return ["005930"]


def load_nh_us_watchlist() -> list:
    """해외(미국) 종목 티커 리스트 로드. watchlist.json의 'us_tickers' 키를
    ict_strategy_bot.py와 동일한 형식으로 공유해서 씀 (예: {"AAPL": "NAS", ...}).
    없으면 빈 리스트를 반환하고 해외장은 건너뜀."""
    if not os.path.exists(WATCHLIST_PATH):
        return []
    try:
        with open(WATCHLIST_PATH, "r", encoding="utf-8") as f:
            wl = json.load(f)
        us_tickers = list(wl.get("us_tickers", {}).keys())
        if not us_tickers:
            print("[알림] watchlist.json에 'us_tickers'가 없어 해외장 종목이 없습니다.")
        return us_tickers
    except Exception as e:
        print(f"[알림] watchlist.json 해외 종목 로드 실패({e})")
        return []


def load_nh_state(tickers: list, state_path: str = None) -> dict:
    """포지션 상태 로드 + 날짜가 바뀌면 일일 카운터만 리셋(청산 안 된 포지션은 유지).
    -- auto_trading_bot.py의 load_state() 구조를 참고함(그대로 복사 아님) --
    state_path: 생략 시 국내(NH_STATE_PATH), 해외는 US_STATE_PATH를 넘겨서 상태를 완전히 분리함."""
    path = state_path or NH_STATE_PATH
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            state = json.load(f)
    else:
        state = {}

    today_str = datetime.now().date().isoformat()
    if state.get("date") != today_str:
        old_positions = state.get("positions", {})
        state["date"] = today_str
        state["daily_pnl"] = 0
        state["positions"] = {}
        for t in tickers:
            prev = old_positions.get(t, {})
            if prev.get("in_position"):
                state["positions"][t] = prev  # 청산 안 된 포지션은 유지, 다음 청산신호에 맡김
            else:
                state["positions"][t] = {"in_position": False, "day_traded": False}

    for t in tickers:
        state["positions"].setdefault(t, {"in_position": False, "day_traded": False})
    state.setdefault("daily_pnl", 0)
    return state


def save_nh_state(state: dict):
    with open(NH_STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def main():
    _setup_nh_logging()

    if not _guard_duplicate_run():
        print(f"[중복 실행 감지] {datetime.now()} - 직전 실행 후 얼마 안 지나 이번 실행은 건너뜁니다. "
              f"(Windows 작업 스케줄러가 같은 트리거를 짧은 간격으로 두 번 발화하는 현상 방어)")
        return

    # 워치독: 5분마다 반복 실행되는 스케줄러 특성상, 네트워크 호출이 멈추는 등
    # 어떤 이유로든 제한시간 안에 안 끝나면 강제 종료해서 python.exe가 계속
    # 쌓여 다음 트리거를 막는 사고를 방지함 (auto_trading_bot.py의 워치독 참고)
    import threading
    def _watchdog_force_exit():
        print("\n[경고] 실행 시간이 240초를 초과해 강제 종료합니다. (멈춤 방지 안전장치)")
        import sys
        sys.stdout.flush()
        os._exit(1)
    _watchdog = threading.Timer(240, _watchdog_force_exit)
    _watchdog.daemon = True
    _watchdog.start()

    print("=" * 60)
    print(f" NH투자증권(나무Plug) ORB+VWAP 자동매매 - {datetime.now()}")
    print("=" * 60)

    if not NHPLUG_AVAILABLE:
        return

    cfg = load_nh_config()
    if cfg is None:
        return
    if cfg.get("app_key", "").startswith("여기에"):
        print("[알림] nh_config.json에 아직 실제 앱키/시크릿을 안 넣으셨습니다.")
        return
    account_no = cfg.get("account_no", "")
    if not account_no or account_no.startswith("여기에"):
        print("[설정 오류] nh_config.json에 account_no가 설정되지 않았습니다.")
        return

    os.environ["NHPLUG_APP_KEY"] = cfg["app_key"]
    os.environ["NHPLUG_APP_SECRET"] = cfg["app_secret"]
    if cfg.get("is_mock", True):
        os.environ["NHPLUG_BASE_URL"] = MOCK_BASE_URL
        print("[모드] 모의투자(moapi)로 접속합니다.")
    else:
        print("[모드] ⚠️ 실전(운영) 서버로 접속합니다.")

    # 이중 안전장치: enable_auto_orders를 사람이 직접 true로 바꾸기 전까지는
    # 신호가 떠도 절대 실제 주문을 내지 않음(dry_run 고정) - auto_trading_bot.py의
    # IS_MOCK + CONFIRM_LIVE_TRADING 이중 게이트 구조를 참고함.
    enable_auto_orders = bool(cfg.get("enable_auto_orders", False))
    dry_run = not enable_auto_orders
    if dry_run:
        print("[안전장치] enable_auto_orders=false 라 신호만 평가하고 실제 주문은 내지 않습니다.")
    else:
        print("[안전장치 해제됨] enable_auto_orders=true - 신호 발생 시 실제로 주문이 나갑니다.")

    capital_per_ticker = int(cfg.get("capital_per_ticker_krw", 1_000_000))
    circuit_breaker = int(cfg.get("circuit_breaker_krw", 500_000))
    capital_per_ticker_usd = float(cfg.get("capital_per_ticker_usd", 400))

    now = datetime.now()
    is_weekday = now.weekday() < 5
    in_market_hours = MARKET_OPEN <= now.time() <= MARKET_CLOSE
    us_open, us_force_closed, us_now_et = get_us_market_status()

    if not (is_weekday and in_market_hours) and not us_open:
        print(f"\n국내/해외 모두 장 시간이 아닙니다. 종료합니다. (참고: 현재 뉴욕시각 {us_now_et.strftime('%H:%M')})")
        return

    if is_weekday and in_market_hours:
        tickers = load_nh_watchlist()
        state = load_nh_state(tickers)

        print(f"\n[국내장 시간대 - {len(tickers)}종목 처리]")
        for idx, ticker in enumerate(tickers):
            if state["daily_pnl"] <= -circuit_breaker:
                print(f"  {ticker}: 서킷브레이커 발동(오늘 손실 {state['daily_pnl']:,.0f}원) - 신규 진입 중단")
                break
            if idx > 0:
                # 종목 사이에 텀을 둬서 API 호출이 한꺼번에 몰리지 않도록 함 (rate_limit 방지)
                time.sleep(0.5)
            print(f"\n[{ticker}]")
            try:
                r = process_ticker_nh(ticker, account_no, state["positions"], capital_krw=capital_per_ticker,
                                       dry_run=dry_run)
                print(f"  상태: {r.get('status')}"
                      + (f" | 박스상단 {r['orb_high']:,.0f} VWAP {r['vwap']:,.0f} 현재가 {r['close']:,.0f}"
                         if "orb_high" in r else ""))
                if r.get("order_result"):
                    print(f"  주문결과: {r['order_result']}")

                status = str(r.get("status", ""))
                if not dry_run and status == "entry_signal":
                    pos = state["positions"][ticker]
                    log_order_nh("BUY", ticker, r["close"], pos["shares"], "orb_vwap_breakout")
                elif not dry_run and status.startswith("exit_signal"):
                    pos = state["positions"][ticker]
                    pnl = (r["close"] - pos["entry_price"]) * pos["shares"]
                    state["daily_pnl"] += pnl
                    reason = status.split(":", 1)[1] if ":" in status else status
                    log_order_nh("SELL", ticker, r["close"], pos["shares"], reason, extra=f"pnl={pnl:.0f}")
                    print(f"  손익: {pnl:+,.0f}원")
            except Exception as e:
                print(f"  [오류] {e}")

        save_nh_state(state)
        print(f"\n오늘 NH 국내 누적손익: {state['daily_pnl']:+,.0f}원")

    if us_open:
        us_tickers = load_nh_us_watchlist()
        if us_tickers:
            us_state = load_nh_state(us_tickers, state_path=US_STATE_PATH)
            print(f"\n[해외(미국) {len(us_tickers)}종목 처리 - 뉴욕시각 {us_now_et.strftime('%H:%M')}]")
            for idx, ticker in enumerate(us_tickers):
                if us_state["daily_pnl"] <= -circuit_breaker:
                    print(f"  {ticker}: 서킷브레이커 발동(오늘 손실 {us_state['daily_pnl']:,.0f}) - 신규 진입 중단")
                    break
                if idx > 0:
                    time.sleep(1.5)  # NH 해외 API가 KIS보다 rate_limit에 더 민감해서 딜레이를 늘림
                print(f"\n[{ticker}]")
                try:
                    r = process_ticker_nh_us(ticker, account_no, us_state["positions"],
                                              capital_usd=capital_per_ticker_usd, dry_run=dry_run)
                    print(f"  상태: {r.get('status')}"
                          + (f" | 박스상단 {r['orb_high']:,.2f} VWAP {r['vwap']:,.2f} 현재가 {r['close']:,.2f}"
                             if "orb_high" in r else ""))
                    if r.get("order_result"):
                        print(f"  주문결과: {r['order_result']}")

                    status = str(r.get("status", ""))
                    if not dry_run and status == "entry_signal":
                        pos = us_state["positions"][ticker]
                        log_order_nh("BUY", ticker, r["close"], pos["shares"], "orb_vwap_breakout",
                                     extra="market=overseas")
                    elif not dry_run and status.startswith("exit_signal"):
                        pos = us_state["positions"][ticker]
                        pnl = (r["close"] - pos["entry_price"]) * pos["shares"]
                        us_state["daily_pnl"] += pnl
                        reason = status.split(":", 1)[1] if ":" in status else status
                        log_order_nh("SELL", ticker, r["close"], pos["shares"], reason,
                                     extra=f"pnl={pnl:.2f}|market=overseas")
                        print(f"  손익: {pnl:+,.2f}")
                except Exception as e:
                    print(f"  [오류] {e}")

            # 해외 포지션은 국내와 다른 파일에 저장 (종목코드 체계가 겹칠 수 있어 상태 분리)
            with open(US_STATE_PATH, "w", encoding="utf-8") as f:
                json.dump(us_state, f, ensure_ascii=False, indent=2)
            print(f"\n오늘 NH 해외 누적손익: {us_state['daily_pnl']:+,.2f}")

    _watchdog.cancel()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback
        traceback.print_exc()
