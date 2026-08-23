"""
국내 2종목(0193L0, 0197X0) ORB+VWAP 자동매매 봇
=====================================================================

[중요 - 반드시 읽어주세요]
이 프로그램은 실제로 매수/매도 "주문"을 KIS 서버에 전송합니다.
모의투자 모드가 기본값이며, 실전 전환은 아래 CONFIG에서 두 곳을
직접 고쳐야만 가능하도록 이중 안전장치를 걸어뒀습니다.

[동작 방식 - "5분마다 체크"를 원하신 대로 구현]
이 스크립트는 실행될 때마다 "한 번 확인하고 끝나는" 방식입니다.
Windows 작업 스케줄러에서 이 스크립트를 5분 간격으로 반복 실행하도록
등록하면, 장중 내내 5분마다 자동으로 상황을 체크하고 필요시 주문을 냅니다.
(설정 방법은 별도 안내 문서 setup_scheduler_trading.md 참고)

포지션 상태는 position_state.json 파일에 저장되어, 스크립트가 매번
새로 실행되어도 "지금 포지션을 들고 있는지, 오늘 이미 매매했는지"를
기억합니다.

[적용 전략 - 그리드서치로 검증된 파라미터]
- 초기 박스: 09:00~09:30 (30분)
- 진입: 종가가 초기 박스 고가 돌파 + VWAP 위 (롱 전용, 하루 종목당 1회)
- 손절: -1.5%
- 익절: +1.0% 도달 시 시장가 매도
- 15:20까지 청산 안 되면 강제 시장가 청산

[안전장치]
1. 기본값은 모의투자입니다. 실전 전환은 CONFIG의 IS_MOCK=False,
   CONFIRM_LIVE_TRADING=True 두 곳을 모두 바꿔야만 활성화됩니다.
2. 일일 손실이 CIRCUIT_BREAKER_KRW를 넘으면, 그날은 신규 진입을
   멈춥니다 (기존 포지션 청산은 정상 수행).
3. 모든 주문 시도는 order_log.csv에 기록됩니다.
4. 종목당 하루 최대 1회 진입으로 제한됩니다.

[※ 반드시 확인하실 것]
아래 TR_ID(TTTC0802U 등)는 KIS Open API의 일반적인 패턴에 따라
작성했지만, 실전 전환 전 반드시 KIS Developers 공식 문서
(apiportal.koreainvestment.com)에서 정확한 TR_ID를 재확인하세요.
잘못된 TR_ID로 실전 주문을 넣으면 의도하지 않은 주문이 나갈 수 있습니다.
모의투자로 최소 몇 주간 충분히 검증한 뒤 실전으로 넘어가세요.
"""

import os
import sys

# ============================================================
# 로그 연결을 그 무엇보다도 가장 먼저 함 (표준 라이브러리 os, sys만 사용)
# pandas/numpy/requests 같은 외부 라이브러리 import가 스케줄러 실행 환경에서
# 실패하더라도(경로/권한 문제 등) 그 사실 자체가 로그에 반드시 남도록 하기 위함
# ============================================================
class TeeOutput:
    """print()의 출력을 화면과 로그 파일에 동시에 씀"""
    def __init__(self, filepath):
        self.terminal = sys.stdout
        self.log = open(filepath, "a", encoding="utf-8")

    def write(self, message):
        self.terminal.write(message)
        self.log.write(message)
        self.log.flush()

    def flush(self):
        self.terminal.flush()
        self.log.flush()


from datetime import datetime as _dt, timedelta as _timedelta

BASE_DIR = r"C:\TradingBot"
LOG_DIR = os.path.join(BASE_DIR, "Log")
ORDER_DIR = os.path.join(BASE_DIR, "Order")


def _trading_day_str(dt_obj=None):
    """'거래일' 문자열을 반환. 한국시간 자정이 아니라 새벽 5시를 하루의 경계로 삼음.
    -> 미국장(22:30~05:00, 자정을 넘김)이 통째로 '시작한 날짜' 하나로 묶이고,
       그 사이에 로그/CSV 파일이 둘로 쪼개지거나 포지션 상태가 잘못 리셋되는 걸 방지함.
    (국내장은 09:00~15:30로 애초에 자정을 안 넘기므로 이 보정의 영향을 안 받음)"""
    d = dt_obj or _dt.now()
    return (d - _timedelta(hours=5)).strftime("%Y_%m_%d")


_today_str = _trading_day_str()
_log_path = os.path.join(LOG_DIR, f"trading_log_{_today_str}.txt")
try:
    os.makedirs(LOG_DIR, exist_ok=True)
    sys.stdout = TeeOutput(_log_path)
    sys.stderr = sys.stdout
except Exception as _e:
    print(f"[경고] 로그 파일 연결 실패({_e}), 화면 출력만 진행합니다.")

print(f"\n{'='*60}")
print(f" 실행 시각: {_dt.now()}")
print(f" 파이썬 실행 경로: {sys.executable}")
print(f" 파이썬 버전: {sys.version}")
SCRIPT_VERSION_TAG = "2026-07-30-v1 (Git인코딩수정+국내계좌차단)"
print(f" 스크립트 버전: {SCRIPT_VERSION_TAG}")

try:
    import json
    import io
    import csv
    import itertools
    import glob
    import time as time_module
    from datetime import datetime, time, date, timedelta
    import pandas as pd
    import numpy as np
    import requests
    print(" 필수 라이브러리(json, pandas, numpy, requests) 로드 완료")
except Exception as _import_err:
    import traceback
    print(f"\n[치명적 오류] 라이브러리 로드 실패: {_import_err}")
    print(" 이 파이썬 실행파일에 pandas/numpy/requests가 설치되어 있는지 확인하세요.")
    print(f" 예: {sys.executable} -m pip install pandas numpy requests")
    traceback.print_exc()
    sys.exit(1)


# ============================================================
# CONFIG - 안전장치 (직접 확인하고 설정하세요)
# ============================================================

# 안전장치: 어떤 이유로든 제한시간 안에 안 끝나면 강제 종료.
# 이렇게 해야 python.exe가 무한정 멈춰서 다음 5분 트리거를 막는 사고를 방지함.
# 매매 체크(기본 모드)는 4분, 스크리닝/재최적화는 종목이 많아 오래 걸릴 수 있어 20분으로 넉넉히.
import threading

# 일요일 22:00~22:09 사이에 "인수 없이" 실행되면, 그 회차만 자동으로 주간(--weekly) 작업으로 전환됨.
# (스케줄러에 --weekly용 트리거를 따로 안 둬도, 5분마다 도는 트리거 하나만으로 매주 자동 수행됨)
WEEKLY_AUTO_TRIGGER_WEEKDAY = 6   # 0=월요일 ... 6=일요일
WEEKLY_AUTO_TRIGGER_START = time(22, 0)
WEEKLY_AUTO_TRIGGER_END = time(22, 9)
WEEKLY_MARKER_PATH = os.path.join(BASE_DIR, "last_weekly_run.txt")


def _is_weekly_auto_window_now():
    n = _dt.now()
    return n.weekday() == WEEKLY_AUTO_TRIGGER_WEEKDAY and WEEKLY_AUTO_TRIGGER_START <= n.time() <= WEEKLY_AUTO_TRIGGER_END


def _weekly_already_ran_today():
    if not os.path.exists(WEEKLY_MARKER_PATH):
        return False
    try:
        with open(WEEKLY_MARKER_PATH, "r", encoding="utf-8") as f:
            saved_date = f.read().strip()
        return saved_date == _dt.now().strftime("%Y-%m-%d")
    except Exception:
        return False


def _mark_weekly_ran_today():
    with open(WEEKLY_MARKER_PATH, "w", encoding="utf-8") as f:
        f.write(_dt.now().strftime("%Y-%m-%d"))


_explicit_long_mode = any(a in sys.argv for a in ("--screen", "--optimize", "--weekly"))
_auto_weekly_now = ("--screen" not in sys.argv and "--optimize" not in sys.argv and "--weekly" not in sys.argv
                     and _is_weekly_auto_window_now() and not _weekly_already_ran_today())
_is_long_running_mode = _explicit_long_mode or _auto_weekly_now
_watchdog_timeout = 1200 if _is_long_running_mode else 240
def _watchdog_force_exit():
    print(f"\n[경고] 실행 시간이 {_watchdog_timeout}초를 초과해 강제 종료합니다. (멈춤 방지 안전장치)")
    sys.stdout.flush()
    os._exit(1)
_watchdog = threading.Timer(_watchdog_timeout, _watchdog_force_exit)
_watchdog.daemon = True
_watchdog.start()

KIS_CONFIG_PATH = os.path.join(BASE_DIR, "kis_config.json")
STATE_PATH = os.path.join(BASE_DIR, "position_state.json")
USER_CONFIG_PATH = os.path.join(BASE_DIR, "user_config.json")

# ⚠️ 실전 전환하려면 이 두 줄을 반드시 함께 바꿔야 합니다 (이중 안전장치)
# 이 두 값은 실수로 바뀌는 걸 막기 위해 일부러 코드 안에만 두었습니다 (외부 설정파일로 안 뺌)
IS_MOCK = True                    # False로 바꾸면 실전투자 (신중하게!)
CONFIRM_LIVE_TRADING = False      # True로 바꿔야 실전 주문이 실제로 나감


# ============================================================
# 개인 설정 (user_config.json) - 코드가 바뀌어도 계속 유지되는 값들
# ============================================================
DEFAULT_USER_CONFIG = {
    "account_no": "여기에_본인_계좌번호_입력",   # 예: "12345678-01"
    "capital_per_ticker_krw": 500_000,
    "circuit_breaker_krw": 30_000,
}


def load_user_config():
    """user_config.json이 없으면 기본 템플릿을 만들고 안내 후 종료.
    있으면 그 값을 읽어서 반환. 이렇게 하면 auto_trading_bot.py를 새 버전으로
    덮어써도 이 파일은 그대로 남아 계좌번호 등을 다시 입력할 필요가 없음."""
    if not os.path.exists(USER_CONFIG_PATH):
        os.makedirs(BASE_DIR, exist_ok=True)
        with open(USER_CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_USER_CONFIG, f, ensure_ascii=False, indent=2)
        print(f"[최초 설정 필요] {USER_CONFIG_PATH} 파일을 새로 만들었습니다.")
        print("메모장으로 열어서 account_no(계좌번호) 등을 본인 값으로 수정한 뒤 다시 실행해주세요.")
        sys.exit(1)

    with open(USER_CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    # 새 항목이 추가된 버전이어도 기존 파일에 없는 키는 기본값으로 채움
    for k, v in DEFAULT_USER_CONFIG.items():
        cfg.setdefault(k, v)
    return cfg


_user_cfg = load_user_config()
ACCOUNT_NO = _user_cfg["account_no"]
CAPITAL_PER_TICKER_KRW = _user_cfg["capital_per_ticker_krw"]
CIRCUIT_BREAKER_KRW = _user_cfg["circuit_breaker_krw"]

# 기본 종목 리스트 (watchlist.json이 없을 때 사용되는 폴백값)
_DEFAULT_TICKERS = {
    "0193L0": {"account_prdt_cd": "01"},
    "0197X0": {"account_prdt_cd": "01"},
}
_DEFAULT_US_TICKERS = {
    "TQQQ": {"exchange": "AMEX"},
    "SQQQ": {"exchange": "AMEX"},
    "SOXL": {"exchange": "AMEX"},
    "SOXS": {"exchange": "AMEX"},
    "SPXL": {"exchange": "AMEX"},
    "SPXS": {"exchange": "AMEX"},
}

WATCHLIST_PATH = os.path.join(BASE_DIR, "watchlist.json")


def _is_market_open_moment():
    """지금이 국내장(09:00~09:04) 또는 미국장(22:30~22:34 KST) 시작 직후인지 판단.
    이 시점에만 종목/파라미터 상세 목록을 로그에 남기고, 그 외 5분 주기에는
    생략해서 로그가 매번 길게 반복되는 걸 방지함."""
    now = datetime.now().time()
    kr_open_window = time(9, 0) <= now <= time(9, 4, 59)
    us_open_window = time(22, 30) <= now <= time(22, 34, 59)
    return kr_open_window or us_open_window


_SHOW_SESSION_START_LOG = _is_market_open_moment()


def load_watchlist(verbose=True):
    """kis_stock_screener.py가 생성한 watchlist.json이 있으면 그걸 사용하고,
    없거나 읽기 실패하면 기본 종목 리스트로 안전하게 대체함.
    종목코드가 비어있거나(NaN 등) 이상한 항목은 여기서도 한 번 더 걸러냄.
    verbose=False면 정상 로드 시의 상세 출력은 생략함(경고/에러는 항상 출력)."""
    if not os.path.exists(WATCHLIST_PATH):
        print(f"[알림] {WATCHLIST_PATH} 가 없어 기본 종목 리스트를 사용합니다.")
        return dict(_DEFAULT_TICKERS), dict(_DEFAULT_US_TICKERS)

    try:
        with open(WATCHLIST_PATH, "r", encoding="utf-8") as f:
            wl = json.load(f)
        kr = wl.get("kr_tickers", {})
        us = wl.get("us_tickers", {})

        def _clean(d, required_key):
            cleaned = {}
            for k, v in d.items():
                if not isinstance(k, str) or not k.strip() or k.strip().lower() == "nan":
                    continue
                if not isinstance(v, dict) or required_key not in v:
                    continue
                cleaned[k.strip()] = v
            return cleaned

        kr_clean = _clean(kr, "account_prdt_cd")
        us_clean = _clean(us, "exchange")

        if not kr_clean and not us_clean:
            print("[경고] watchlist.json 내용이 비어있거나 전부 무효해서 기본 리스트를 사용합니다.")
            return dict(_DEFAULT_TICKERS), dict(_DEFAULT_US_TICKERS)

        if verbose:
            print(f"[watchlist.json 적용] 생성시각={wl.get('generated_at','?')}, "
                  f"국내 {len(kr_clean)}종목, 미국 {len(us_clean)}종목")
        # 둘 중 하나가 비어있으면 그 시장은 기본값으로 보완 (완전히 매매 없이 비워두지 않도록)
        if not kr_clean:
            print("[알림] watchlist에 유효한 국내 종목이 없어 국내는 기본 리스트를 사용합니다.")
            kr_clean = dict(_DEFAULT_TICKERS)
        if not us_clean:
            print("[알림] watchlist에 유효한 미국 종목이 없어 미국은 기본 리스트를 사용합니다.")
            us_clean = dict(_DEFAULT_US_TICKERS)
        return kr_clean, us_clean

    except Exception as e:
        print(f"[경고] watchlist.json 읽기 실패({e}), 기본 종목 리스트를 사용합니다.")
        return dict(_DEFAULT_TICKERS), dict(_DEFAULT_US_TICKERS)


TICKERS, US_TICKERS = load_watchlist(verbose=_SHOW_SESSION_START_LOG)

US_MARKET_OPEN_ET = time(9, 30)   # 미국 동부시간 정규장 개장
US_FORCE_CLOSE_ET = time(15, 50)  # 정규장 마감 10분 전 강제청산

# 전략 파라미터 (기본값 - strategy_params.json이 없을 때 사용됨)
_DEFAULT_ORB_MINUTES = 30
_DEFAULT_STOP_PCT = 0.015
_DEFAULT_TP_TRIGGER_PCT = 0.010
_DEFAULT_STRATEGY = {"orb_minutes": _DEFAULT_ORB_MINUTES, "stop_pct": _DEFAULT_STOP_PCT,
                     "tp_trigger_pct": _DEFAULT_TP_TRIGGER_PCT}

STRATEGY_PARAMS_PATH = os.path.join(BASE_DIR, "strategy_params.json")


def _validate_strategy_entry(entry):
    """상식적인 범위를 벗어나면 기본값으로 대체 (실전에 이상한 값이 들어가는 것 방지)"""
    orb_minutes = entry.get("orb_minutes", _DEFAULT_ORB_MINUTES)
    stop_pct = entry.get("stop_pct", _DEFAULT_STOP_PCT)
    tp_trigger_pct = entry.get("tp_trigger_pct", _DEFAULT_TP_TRIGGER_PCT)
    if not (5 <= orb_minutes <= 120):
        orb_minutes = _DEFAULT_ORB_MINUTES
    if not (0.003 <= stop_pct <= 0.10):
        stop_pct = _DEFAULT_STOP_PCT
    if not (0.001 <= tp_trigger_pct <= 0.10):
        tp_trigger_pct = _DEFAULT_TP_TRIGGER_PCT
    return {"orb_minutes": orb_minutes, "stop_pct": stop_pct, "tp_trigger_pct": tp_trigger_pct}


def load_strategy_params(verbose=True):
    """weekly_optimizer.py가 생성한 strategy_params.json(종목별 개별 파라미터)을 읽어서
    {종목코드: {orb_minutes, stop_pct, tp_trigger_pct}} 딕셔너리로 반환.
    없거나 이상하면 전 종목에 기존 검증된 기본값을 적용.
    tp_max_pct(익절 상한)는 백테스트 전용 개념이라 실전 매매 로직에서는 사용하지 않음
    (실전은 트리거 도달 즉시 시장가 매도).
    verbose=False면 정상 로드 시의 종목별 상세 출력은 생략함(경고/에러는 항상 출력)."""
    if not os.path.exists(STRATEGY_PARAMS_PATH):
        print(f"[알림] {STRATEGY_PARAMS_PATH} 가 없어 전 종목에 기본 전략 파라미터를 사용합니다.")
        return {}, dict(_DEFAULT_STRATEGY)

    try:
        with open(STRATEGY_PARAMS_PATH, "r", encoding="utf-8") as f:
            p = json.load(f)

        default_entry = _validate_strategy_entry(p.get("default", {}))
        per_ticker_raw = p.get("per_ticker", {})

        per_ticker = {}
        for ticker, entry in per_ticker_raw.items():
            if not isinstance(ticker, str) or not ticker.strip():
                continue
            if not isinstance(entry, dict):
                continue
            per_ticker[ticker.strip()] = _validate_strategy_entry(entry)

        if verbose:
            print(f"[strategy_params.json 적용] 생성시각={p.get('generated_at','?')}, "
                  f"종목별 파라미터 {len(per_ticker)}건 로드")
            for ticker, entry in per_ticker.items():
                note = per_ticker_raw.get(ticker, {}).get("note", "")
                print(f"  {ticker}: 초기박스={entry['orb_minutes']}분, 손절={entry['stop_pct']*100:.1f}%, "
                      f"익절트리거={entry['tp_trigger_pct']*100:.1f}%  ({note})")
        return per_ticker, default_entry

    except Exception as e:
        print(f"[경고] strategy_params.json 읽기 실패({e}), 전 종목에 기본 전략 파라미터를 사용합니다.")
        return {}, dict(_DEFAULT_STRATEGY)


PER_TICKER_STRATEGY, DEFAULT_STRATEGY = load_strategy_params(verbose=_SHOW_SESSION_START_LOG)


def get_strategy_for(ticker):
    """해당 종목의 전략 파라미터를 반환 (없으면 기본값)"""
    return PER_TICKER_STRATEGY.get(ticker, DEFAULT_STRATEGY)


FORCE_CLOSE_TIME = time(15, 20)
MARKET_OPEN = time(9, 0)

KIS_BASE_URL_REAL = "https://openapi.koreainvestment.com:9443"
KIS_BASE_URL_MOCK = "https://openapivts.koreainvestment.com:29443"
TR_ID_MINUTE = "FHKST03010200"
TR_ID_BUY_REAL, TR_ID_SELL_REAL = "TTTC0802U", "TTTC0801U"   # ⚠️ 실전 전 재확인 필요
TR_ID_BUY_MOCK, TR_ID_SELL_MOCK = "VTTC0802U", "VTTC0801U"   # ⚠️ 실전 전 재확인 필요

# 해외주식 주문 TR_ID (⚠️ 실전 전 KIS Developers 문서에서 반드시 재확인)
TR_ID_US_BUY_REAL, TR_ID_US_SELL_REAL = "TTTT1002U", "TTTT1001U"
TR_ID_US_BUY_MOCK, TR_ID_US_SELL_MOCK = "VTTT1002U", "VTTT1001U"
OVERSEAS_ORDER_PATH = "/uapi/overseas-stock/v1/trading/order"

try:
    import yfinance as yf
except ImportError:
    yf = None  # 국내종목만 쓸 경우 없어도 동작하도록


# ============================================================
# 상태 관리
# ============================================================
def load_state():
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            state = json.load(f)
    else:
        state = {}

    # ⚠️ 자정(00:00) 기준이 아니라 새벽 5시 기준으로 '거래일'을 판단함.
    # 미국장(22:30~05:00)이 한국시간 자정을 넘기는데, 자정 기준으로 하면
    # 그 세션 도중에 '새 거래일'로 오인해서 포지션 상태가 잘못 리셋될 수 있음.
    today_str = (datetime.now() - timedelta(hours=5)).date().isoformat()
    if state.get("date") != today_str:
        # 날짜가 바뀌어도 포지션(positions/us_positions)은 절대 그냥 지우지 않음.
        # 만약 전날 청산 안 된 포지션이 남아있으면, process_ticker/process_us_ticker가
        # 이를 감지해서 비상 매도부터 시도한 뒤에 초기화해야 하기 때문.
        # 여기서는 "하루 단위 카운터"(일일손익, day_traded 여부)만 리셋함.
        old_positions = state.get("positions", {})
        old_us_positions = state.get("us_positions", {})

        state["date"] = today_str
        state["daily_pnl"] = 0
        state["daily_pnl_us"] = 0
        state["orb_high"] = {}
        state["fx_rate"] = None  # 매일 환율 새로 조회하도록

        state["positions"] = {}
        for t in TICKERS:
            prev = old_positions.get(t, {})
            if prev.get("in_position"):
                # 청산 안 된 포지션은 그대로 유지 (day_traded도 True 유지해서 재진입 안 하게)
                state["positions"][t] = prev
                state["positions"][t]["carried_over"] = True
            else:
                state["positions"][t] = {"in_position": False, "day_traded": False}

        state["us_positions"] = {}
        for t in US_TICKERS:
            prev = old_us_positions.get(t, {})
            if prev.get("in_position"):
                state["us_positions"][t] = prev  # session 필드는 process_us_ticker가 알아서 비교/처리함
            else:
                state["us_positions"][t] = {"in_position": False, "day_traded": False, "session": None}

    state.setdefault("daily_pnl_us", 0)
    state.setdefault("us_positions", {t: {"in_position": False, "day_traded": False, "session": None} for t in US_TICKERS})
    return state


def save_state(state):
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


# ============================================================
# 카카오톡 "나에게 보내기" 알림 (kakao_setup.py로 최초 인증 필요)
# ============================================================
KAKAO_TOKEN_PATH = os.path.join(BASE_DIR, "kakao_token.json")


def _kakao_refresh_access_token(token_data):
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
    # 카카오는 refresh_token 자체도 가끔 같이 갱신해줌
    if "refresh_token" in result:
        token_data["refresh_token"] = result["refresh_token"]
    with open(KAKAO_TOKEN_PATH, "w", encoding="utf-8") as f:
        json.dump(token_data, f, ensure_ascii=False, indent=2)
    return token_data


def send_kakao_message(text):
    """체결 알림 등을 카카오톡(나에게 보내기)으로 전송. 실패해도 절대 예외를 던지지 않음
    (알림 실패가 실제 매매 로직을 막으면 안 되므로)."""
    if not os.path.exists(KAKAO_TOKEN_PATH):
        return  # 카카오 연동을 안 하신 경우, 조용히 건너뜀 (에러 아님)

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


REASON_LABELS_KR = {
    "orb_vwap_breakout": "박스+VWAP 동시 돌파 매수",
    "take_profit": "익절 (목표 수익 도달)",
    "stop_loss": "손절 (손실 제한)",
    "time_close": "장마감 강제청산",
    "emergency_carryover_close": "이월 포지션 비상매도",
}


def _append_csv_row_safe(path, row_df):
    """CSV에 한 줄을 추가하되, 기존 파일의 헤더(컬럼 구성)가 지금 쓰려는 것과 다르면
    (예: 코드 업데이트로 컬럼이 늘어난 경우) 전체 파일을 새 스키마로 안전하게 재작성함.
    -> 헤더와 실제 데이터의 컬럼 수가 어긋나서 CSV가 깨지는 걸 방지."""
    if not os.path.exists(path):
        row_df.to_csv(path, mode="a", index=False, header=True, encoding="utf-8-sig")
        return

    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            existing_header = f.readline().strip().split(",")
    except Exception:
        existing_header = []

    if existing_header == list(row_df.columns):
        # 스키마가 같으면 그냥 이어붙이기(가장 흔한 정상 케이스, 빠름)
        row_df.to_csv(path, mode="a", index=False, header=False, encoding="utf-8-sig")
        return

    # 스키마가 다름 -> 기존 파일을 최대한 읽어서(형식이 깨져있어도) 새 스키마로 합쳐 재작성
    print(f"  [알림] {os.path.basename(path)}의 기존 컬럼 구성이 달라져서, 전체를 새 형식으로 정리합니다.")
    old_rows = []
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            reader = csv.reader(f)
            old_header = next(reader)
            for r in reader:
                if len(r) == len(old_header):
                    old_rows.append(dict(zip(old_header, r)))
                # 컬럼 수가 안 맞는 예전 깨진 줄은 컬럼명 순서대로 최대한 매칭 시도
                elif len(r) > 0:
                    old_rows.append(dict(zip(old_header[:len(r)] + [f"_extra{i}" for i in range(len(r)-len(old_header))], r)))
    except Exception as e:
        print(f"  [경고] 기존 {os.path.basename(path)} 읽기 중 문제 발생({e}), 기존 내용은 최대한 보존하며 진행합니다.")

    old_df = pd.DataFrame(old_rows) if old_rows else pd.DataFrame(columns=row_df.columns)
    combined = pd.concat([old_df, row_df], ignore_index=True, sort=False)
    # 새로 정의한 컬럼 순서를 기준으로 정리 (예전에만 있던 임시 컬럼은 맨 뒤로)
    ordered_cols = list(row_df.columns) + [c for c in combined.columns if c not in row_df.columns]
    combined = combined[ordered_cols]
    combined.to_csv(path, index=False, encoding="utf-8-sig")


def log_order(action, ticker, price, shares, reason, extra=""):
    """로그 기록 실패가 매매 상태 처리를 막지 않도록, 절대 예외를 위로 던지지 않음"""
    # 자정이 아닌 새벽 5시 기준 거래일 사용 -> 미국장이 자정을 넘겨도 CSV가 하나로 유지됨
    today_str = _trading_day_str()
    order_log_path = os.path.join(ORDER_DIR, f"order_log_{today_str}.csv")

    reason_kr = REASON_LABELS_KR.get(reason, reason)  # 모르는 코드가 들어와도 원문 그대로 표시(누락 방지)

    row = pd.DataFrame([{
        "timestamp": datetime.now().isoformat(), "action": action, "ticker": ticker,
        "price": price, "shares": shares, "reason": reason, "reason_kr": reason_kr, "extra": extra,
        "mode": "MOCK" if IS_MOCK else "REAL",
    }])

    # 카카오톡 알림 (기록 성공/실패와 무관하게, 체결 자체는 이미 일어난 사실이므로 항상 시도)
    mode_label = "모의" if IS_MOCK else "실전"
    action_label = {"BUY": "매수", "SELL": "매도", "BUY_US": "매수(미국)", "SELL_US": "매도(미국)"}.get(action, action)
    price_str = f"{price:,.2f}" if "US" in action else f"{price:,.0f}원"

    # 종목명 조회: 미국이면 US_TICKERS, 국내면 TICKERS에서 찾음 (watchlist.json에서 로드된 정보)
    ticker_dict = US_TICKERS if "US" in action else TICKERS
    ticker_name = ticker_dict.get(ticker, {}).get("name", "")
    ticker_display = f"{ticker}({ticker_name})" if ticker_name and ticker_name != ticker else ticker

    msg = (f"[{mode_label}] {action_label} 체결\n"
           f"종목: {ticker_display}\n"
           f"가격: {price_str} x {shares}주\n"
           f"사유: {reason_kr}")
    if extra:
        # extra는 보통 "pnl=1234" 또는 "pnl_krw=1234" 형식 -> 사람이 읽기 쉬운 손익 표시로 변환
        extra_display = extra
        if extra.startswith("pnl_krw="):
            try:
                val = float(extra.split("=", 1)[1])
                extra_display = f"손익: {val:+,.0f}원"
            except ValueError:
                pass
        elif extra.startswith("pnl="):
            try:
                val = float(extra.split("=", 1)[1])
                extra_display = f"손익: {val:+,.0f}원"
            except ValueError:
                pass
        msg += f"\n{extra_display}"
    send_kakao_message(msg)

    for attempt in range(3):
        try:
            os.makedirs(ORDER_DIR, exist_ok=True)
            _append_csv_row_safe(order_log_path, row)
            return
        except PermissionError as e:
            print(f"  [경고] {os.path.basename(order_log_path)} 쓰기 실패(다른 프로그램이 열어둔 상태일 수 있음), 재시도 {attempt+1}/3: {e}")
            time_module.sleep(1.0)
        except Exception as e:
            print(f"  [경고] {os.path.basename(order_log_path)} 쓰기 중 예상치 못한 오류(무시하고 계속 진행): {e}")
            return
    print(f"  [경고] {os.path.basename(order_log_path)} 기록에 최종 실패했습니다. 주문 자체는 정상 처리됐을 수 있으니 "
          f"position_state.json으로 실제 포지션 상태를 확인하세요. (누락된 로그: {action} {ticker} {price} x{shares})")


# ============================================================
# KIS API 기초 함수
# ============================================================
def load_kis_config():
    if not os.path.exists(KIS_CONFIG_PATH):
        raise SystemExit(f"{KIS_CONFIG_PATH} 가 없습니다. kis_interactive_query.py를 먼저 실행해 App Key를 저장하세요.")
    with open(KIS_CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def get_base_url():
    return KIS_BASE_URL_MOCK if IS_MOCK else KIS_BASE_URL_REAL


TOKEN_CACHE_PATH = os.path.join(BASE_DIR, "kis_token_cache.json")


def get_token(cfg):
    # 캐시된 토큰이 있고 아직 유효하면 재사용 (1분당 1회 발급 제한 대응)
    if os.path.exists(TOKEN_CACHE_PATH):
        try:
            with open(TOKEN_CACHE_PATH, "r", encoding="utf-8") as f:
                cache = json.load(f)
            expire_at = datetime.fromisoformat(cache["expire_at"])
            if cache.get("is_mock") == IS_MOCK and datetime.now() < expire_at:
                return cache["access_token"]
        except Exception:
            pass  # 캐시 파일이 손상됐으면 무시하고 새로 발급

    url = get_base_url() + "/oauth2/tokenP"
    headers = {"content-type": "application/json"}
    body = {"grant_type": "client_credentials", "appkey": cfg["appkey"], "appsecret": cfg["appsecret"]}
    res = requests.post(url, headers=headers, data=json.dumps(body), timeout=10)
    if res.status_code != 200:
        print(f"[토큰 발급 오류] status_code={res.status_code}")
        print(f"[서버 응답 원문] {res.text}")
        print(f"[접속 시도한 URL] {url}")
        print(f"[사용한 appkey 앞 6자리] {cfg['appkey'][:6]}...")
        print(f"[스크립트의 IS_MOCK 설정] {IS_MOCK}")
        print(f"[kis_config.json의 is_mock 값] {cfg.get('is_mock')}")
        if "EGW00133" in res.text:
            print("[알림] 토큰 발급은 1분당 1회로 제한됩니다. 1분 후 다시 시도해주세요.")
        res.raise_for_status()

    result = res.json()
    token = result["access_token"]
    # KIS가 응답으로 알려주는 실제 만료시간(expires_in, 초 단위)을 사용.
    # 혹시 없으면 보수적으로 12시간만 유효한 것으로 간주(기존 23시간은 실제보다 길어 만료 에러의 원인이 됐음)
    expires_in_sec = result.get("expires_in")
    if expires_in_sec:
        # 안전마진으로 5분 일찍 만료된 것으로 처리
        expire_at = datetime.now() + timedelta(seconds=int(expires_in_sec) - 300)
    else:
        expire_at = datetime.now() + timedelta(hours=12)
    with open(TOKEN_CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump({"access_token": token, "expire_at": expire_at.isoformat(), "is_mock": IS_MOCK}, f)
    return token


def get_hashkey(cfg, body):
    # ⚠️ 주문 시 KIS는 hashkey 헤더를 요구합니다. 실전 전 공식문서로 정확한 스펙 재확인 필요.
    url = get_base_url() + "/uapi/hashkey"
    headers = {"content-type": "application/json", "appkey": cfg["appkey"], "appsecret": cfg["appsecret"]}
    res = requests.post(url, headers=headers, data=json.dumps(body), timeout=10)
    res.raise_for_status()
    return res.json().get("HASH")


def fetch_today_data(cfg, token, ticker):
    """오늘 09:00부터 지금까지의 5분봉을 전부 가져옴"""
    base_url = get_base_url()
    now = datetime.now()
    checkpoints = pd.date_range(
        start=now.replace(hour=9, minute=30, second=0, microsecond=0), end=now, freq="60min"
    )
    if len(checkpoints) == 0:
        checkpoints = [now]

    frames = []
    for cp in checkpoints:
        url = base_url + "/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice"
        headers = {"content-type": "application/json; charset=utf-8", "authorization": f"Bearer {token}",
                   "appkey": cfg["appkey"], "appsecret": cfg["appsecret"], "tr_id": TR_ID_MINUTE, "custtype": "P"}
        params = {"FID_ETC_CLS_CODE": "", "FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": ticker,
                  "FID_INPUT_HOUR_1": cp.strftime("%H%M%S"), "FID_PW_DATA_INCU_YN": "N"}
        res = requests.get(url, headers=headers, params=params, timeout=10)
        if res.status_code == 200:
            data = res.json()
            rows = data.get("output2", [])
            if rows:
                df = pd.DataFrame(rows)
                df["datetime"] = pd.to_datetime(df["stck_bsop_date"]+df["stck_cntg_hour"], format="%Y%m%d%H%M%S")
                df["open"]=df["stck_oprc"].astype(float); df["high"]=df["stck_hgpr"].astype(float)
                df["low"]=df["stck_lwpr"].astype(float); df["close"]=df["stck_prpr"].astype(float)
                df["volume"]=df["cntg_vol"].astype(float)
                frames.append(df[["datetime","open","high","low","close","volume"]])
        time_module.sleep(1.5)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames).drop_duplicates(subset="datetime").sort_values("datetime").reset_index(drop=True)


def place_order(cfg, token, ticker, side, qty, price=0):
    """국내 종목 매수/매도. price=0이면 시장가."""
    if not IS_MOCK and not CONFIRM_LIVE_TRADING:
        raise RuntimeError("실전 주문 이중 안전장치가 해제되지 않았습니다. CONFIG를 확인하세요.")

    base_url = get_base_url()
    tr_id = (TR_ID_BUY_MOCK if IS_MOCK else TR_ID_BUY_REAL) if side == "buy" else \
            (TR_ID_SELL_MOCK if IS_MOCK else TR_ID_SELL_REAL)

    body = {
        "CANO": ACCOUNT_NO.split("-")[0], "ACNT_PRDT_CD": ACCOUNT_NO.split("-")[1],
        "PDNO": ticker, "ORD_DVSN": "01" if price == 0 else "00",  # 01=시장가, 00=지정가 ⚠️재확인 필요
        "ORD_QTY": str(qty), "ORD_UNPR": str(int(price)),
    }
    hashkey = get_hashkey(cfg, body)
    headers = {
        "content-type": "application/json; charset=utf-8", "authorization": f"Bearer {token}",
        "appkey": cfg["appkey"], "appsecret": cfg["appsecret"], "tr_id": tr_id,
        "custtype": "P", "hashkey": hashkey,
    }
    url = base_url + "/uapi/domestic-stock/v1/trading/order-cash"

    if IS_MOCK or CONFIRM_LIVE_TRADING:
        res = requests.post(url, headers=headers, data=json.dumps(body), timeout=10)
        if res.status_code != 200:
            print(f"  [국내주문 HTTP 오류] status_code={res.status_code}")
            print(f"  [서버 응답 원문] {res.text}")
            if "EGW00123" in res.text:
                print("  [알림] 토큰이 만료됐습니다. 캐시를 삭제하니 다음 주기에는 새 토큰으로 재시도됩니다.")
                if os.path.exists(TOKEN_CACHE_PATH):
                    os.remove(TOKEN_CACHE_PATH)
            return {"rt_cd": "1", "msg1": f"HTTP {res.status_code}", "success": False}
        result = res.json()
        if result.get("rt_cd") != "0":
            print(f"  [국내주문 거부됨] msg_cd={result.get('msg_cd')} msg1={result.get('msg1')}")
            if result.get("msg_cd") == "EGW00123":
                print("  [알림] 토큰이 만료됐습니다. 캐시를 삭제하니 다음 주기에는 새 토큰으로 재시도됩니다.")
                if os.path.exists(TOKEN_CACHE_PATH):
                    os.remove(TOKEN_CACHE_PATH)
            result["success"] = False
        else:
            print(f"  [주문결과] {result.get('msg1')}")
            result["success"] = True
        return result
    else:
        print(f"  [DRY-RUN, 실제 주문 안 나감] {side} {ticker} {qty}주 @ {price}")
        return {"rt_cd": "0", "msg1": "dry-run (실전 미확정 상태)", "success": True}


def place_us_order(cfg, token, ticker, exchange, side, qty, price=0):
    """미국 종목 매수/매도. price=0이면 시장가로 취급(다만 해외주식은 시장가 미지원 거래소가 많아
    안전하게 현재가 근접 지정가를 권장 - 아래 caller에서 price를 반드시 넘기도록 처리함)"""
    if not IS_MOCK and not CONFIRM_LIVE_TRADING:
        raise RuntimeError("실전 주문 이중 안전장치가 해제되지 않았습니다.")

    base_url = get_base_url()
    tr_id = (TR_ID_US_BUY_MOCK if IS_MOCK else TR_ID_US_BUY_REAL) if side == "buy" else \
            (TR_ID_US_SELL_MOCK if IS_MOCK else TR_ID_US_SELL_REAL)

    body = {
        "CANO": ACCOUNT_NO.split("-")[0], "ACNT_PRDT_CD": ACCOUNT_NO.split("-")[1],
        "OVRS_EXCG_CD": exchange, "PDNO": ticker,
        "ORD_QTY": str(qty), "OVRS_ORD_UNPR": str(round(price, 2)),
        "ORD_SVR_DVSN_CD": "0", "ORD_DVSN": "00",  # 해외는 지정가(00)만 지원하는 거래소가 많음 ⚠️확인필요
    }
    hashkey = get_hashkey(cfg, body)
    headers = {
        "content-type": "application/json; charset=utf-8", "authorization": f"Bearer {token}",
        "appkey": cfg["appkey"], "appsecret": cfg["appsecret"], "tr_id": tr_id,
        "custtype": "P", "hashkey": hashkey,
    }
    url = base_url + OVERSEAS_ORDER_PATH

    if IS_MOCK or CONFIRM_LIVE_TRADING:
        res = requests.post(url, headers=headers, data=json.dumps(body), timeout=10)
        if res.status_code != 200:
            print(f"  [해외주문 HTTP 오류] status_code={res.status_code}")
            print(f"  [서버 응답 원문] {res.text}")
            print(f"  [보낸 요청 body] {json.dumps(body, ensure_ascii=False)}")
            print(f"  [tr_id] {tr_id}")
            if "EGW00123" in res.text:
                print("  [알림] 토큰이 만료됐습니다. 캐시를 삭제하니 다음 주기에는 새 토큰으로 재시도됩니다.")
                if os.path.exists(TOKEN_CACHE_PATH):
                    os.remove(TOKEN_CACHE_PATH)
            return {"rt_cd": "1", "msg1": f"HTTP {res.status_code}", "success": False}
        result = res.json()
        if result.get("rt_cd") != "0":
            print(f"  [해외주문 거부됨] msg_cd={result.get('msg_cd')} msg1={result.get('msg1')}")
            if result.get("msg_cd") == "EGW00123":
                print("  [알림] 토큰이 만료됐습니다. 캐시를 삭제하니 다음 주기에는 새 토큰으로 재시도됩니다.")
                if os.path.exists(TOKEN_CACHE_PATH):
                    os.remove(TOKEN_CACHE_PATH)
            result["success"] = False
        else:
            print(f"  [해외주문결과] {result.get('msg1')}")
            result["success"] = True
        return result
    else:
        print(f"  [DRY-RUN] {side} {ticker} {qty}주 @ {price} ({exchange})")
        return {"rt_cd": "0", "msg1": "dry-run", "success": True}


# 거래소코드를 정확히 알 수 없는 종목을 위해, 실패시 순서대로 시도해볼 후보 목록
US_EXCHANGE_CANDIDATES = ["AMEX", "NASD", "NYSE"]


# 이 코드가 뜨면 거래소를 바꿔봐도 소용없음 -> 계좌/종목 자체가 모의투자에서 거래 불가한 것으로 판단
ACCOUNT_LEVEL_BLOCK_CODES = {"40910000"}


def place_us_order_smart(cfg, token, ticker, side, qty, price, known_exchange=None):
    """거래소 코드를 모르거나 틀렸을 수 있는 상황에 대응.
    known_exchange가 있으면 그것부터 시도(이전에 성공했던 값 재사용),
    실패하면 후보 목록을 순서대로 시도해서 성공하는 거래소를 찾아냄.
    반환값: (result, 성공한 exchange 코드 또는 None)
    result에 account_level_block=True가 있으면, 거래소를 더 바꿔봐도 소용없는
    계좌/종목 자체의 문제이므로 호출부에서 그날 재시도를 중단해야 함."""
    tried = []
    candidates = ([known_exchange] if known_exchange else []) + \
                 [e for e in US_EXCHANGE_CANDIDATES if e != known_exchange]

    for exchange in candidates:
        if exchange in tried:
            continue
        tried.append(exchange)
        result = place_us_order(cfg, token, ticker, exchange, side, qty, price=price)
        if result.get("success"):
            if exchange != known_exchange:
                print(f"  [{ticker}] 거래소코드 '{exchange}'로 성공 (이후 이 종목엔 계속 이 값을 사용함)")
            return result, exchange
        else:
            msg = result.get("msg1", "")
            msg_cd = result.get("msg_cd", "")
            if msg_cd in ACCOUNT_LEVEL_BLOCK_CODES:
                # 거래소를 더 바꿔봐도 동일 오류가 날 게 뻔하므로, 나머지 후보는 시도하지 않고 바로 중단
                print(f"  [{ticker}] 거래소 '{exchange}'로 시도했으나 계좌/종목 자체 문제로 거부됨"
                      f"({msg_cd}: {msg}). 다른 거래소를 시도해도 동일할 것으로 판단해 중단합니다.")
                result["account_level_block"] = True
                return result, None
            print(f"  [{ticker}] 거래소 '{exchange}'로 시도했으나 실패({msg}), 다음 후보로 재시도...")
            time_module.sleep(1.0)
    print(f"  [{ticker}] 모든 거래소 후보({tried})로 시도했으나 전부 실패했습니다.")
    return result, None


def fetch_us_session_data(ticker):
    """yfinance로 현재 진행 중인 미국 세션의 5분봉을 가져옴 (검증된 방식)"""
    if yf is None:
        raise RuntimeError("yfinance가 설치되어 있지 않습니다: pip install yfinance")
    try:
        df = yf.download(ticker, period="2d", interval="5m", progress=False, auto_adjust=False, timeout=15)
    except Exception as e:
        print(f"  [{ticker}] yfinance 다운로드 자체가 실패함: {e}")
        return pd.DataFrame()

    if df.empty:
        print(f"  [{ticker}] yfinance가 빈 데이터를 반환함 (일시적 오류이거나 휴장일 가능성)")
        return pd.DataFrame()

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0] for c in df.columns]
    df = df.reset_index().rename(columns={
        "Datetime": "datetime", "Open": "open", "High": "high", "Low": "low", "Close": "close", "Volume": "volume"
    })
    if df["datetime"].dt.tz is not None:
        df["datetime"] = df["datetime"].dt.tz_localize(None)
    # 현재 진행 중인 세션(가장 최근 세션)만 추출
    # ⚠️ 이 데이터는 미국 동부시간(ET) 그대로라 자정을 안 넘김 -> 단순 달력일로 그룹핑하면 충분함
    # (예전엔 한국시간 자정걸침 세션용 -12시간 보정을 여기도 잘못 적용해서, 정오 이전 ET 데이터가
    #  하루 전 세션으로 잘못 분류되는 버그가 있었음)
    df["session_date"] = df["datetime"].dt.date
    latest_session = df["session_date"].max()
    result = df[df["session_date"] == latest_session].drop(columns=["session_date"]).reset_index(drop=True)
    print(f"  [{ticker}] 데이터 {len(result)}행 수신 (세션: {latest_session})")
    return result


# ============================================================
# 전략 로직
# ============================================================
def calc_vwap(df):
    cum_vol = df["volume"].cumsum()
    cum_vp = (df["close"]*df["volume"]).cumsum()
    return cum_vp/cum_vol.replace(0, np.nan)


# RSI(상대강도지수) 과매수 필터: 박스+VWAP을 돌파했더라도, RSI가 이 값 이상이면
# "이미 너무 많이 오른 뒤의 추격매수"로 판단해서 진입을 보류함
RSI_PERIOD = 14
RSI_OVERBOUGHT_THRESHOLD = 75


def calc_rsi(df, period=RSI_PERIOD):
    """표준 RSI(Wilder's smoothing) 계산. 데이터가 (period+1)개 미만이면 전부 NaN 반환
    (=필터 판단 불가 상태이며, 이 경우 진입 로직에서는 RSI 조건을 건너뛰도록 처리함)."""
    delta = df["close"].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    # 손실이 전혀 없었던 극단적 경우(avg_loss=0) -> RSI 100으로 처리
    rsi = rsi.where(avg_loss != 0, 100)
    return rsi


def process_us_ticker(ticker, exchange, cfg, token, state):
    strategy = get_strategy_for(ticker)
    orb_bars_local = max(1, strategy["orb_minutes"] // 5)  # 미국은 봉개수 기반이라 분->봉개수 환산
    stop_pct_local = strategy["stop_pct"]
    tp_trigger_local = strategy["tp_trigger_pct"]

    pos = state["us_positions"].setdefault(ticker, {"in_position": False, "day_traded": False, "session": None})

    df = fetch_us_session_data(ticker)
    if df.empty or len(df) < 7:
        print(f"  {ticker}: 데이터 부족 (세션 초반이거나 휴장)")
        return

    session_id = str(df["datetime"].iloc[0].date())

    if pos.get("session") != session_id:
        # 새 세션이 시작됐는데, 직전 세션에 아직 청산 안 된 포지션이 남아있는 경우
        # -> 절대 그냥 잊어버리지 말고, 지금 즉시 비상 매도부터 시도함 (당일청산 원칙을 지키기 위한 안전장치)
        # 단, yfinance의 세션 경계 판단이 가끔 오탐(같은 세션인데 다르게 인식)할 수 있어서,
        # 진입한 지 최소 8시간(정규장 하나보다 확실히 긴 시간)은 지나야 "진짜 이월"로 간주함
        entry_time_str = pos.get("entry_time")
        hours_since_entry = None
        if entry_time_str:
            try:
                hours_since_entry = (datetime.now() - datetime.fromisoformat(entry_time_str)).total_seconds() / 3600
            except Exception:
                hours_since_entry = None

        should_reset_session = True
        if pos.get("in_position") and (hours_since_entry is None or hours_since_entry >= 8):
            elapsed_msg = f"(진입 후 {hours_since_entry:.1f}시간 경과)" if hours_since_entry is not None else ""
            print(f"  {ticker}: [경고] 직전 세션에 청산 안 된 포지션이 남아있습니다{elapsed_msg}! 비상 매도를 시도합니다.")
            emergency_price = float(df.iloc[0]["open"])  # 새 세션 시가로 즉시 매도 시도
            shares = pos["shares"]
            result, used_exchange = place_us_order_smart(cfg, token, ticker, "sell", shares,
                                                           price=emergency_price * 0.99,
                                                           known_exchange=pos.get("exchange"))
            if result.get("success"):
                entry_price = pos["entry_price"]
                pnl_usd = (emergency_price - entry_price) * shares
                trade_fx_rate = get_fx_rate_safe()  # 매매 시점의 실시간 환율로 계산
                pnl_krw = pnl_usd * trade_fx_rate
                state["daily_pnl_us"] = state.get("daily_pnl_us", 0) + pnl_krw
                log_order("SELL_US", ticker, emergency_price, shares, "emergency_carryover_close",
                          extra=f"pnl_krw={pnl_krw:.0f}")
                print(f"  {ticker}: 비상 매도 성공 (적용환율 {trade_fx_rate:.2f}원/달러). 이제 새 세션을 시작합니다.")
            else:
                print(f"  {ticker}: [심각] 비상 매도도 실패했습니다. 반드시 증권사 앱에서 실제 보유 현황을 "
                      f"직접 확인하고 필요시 수동으로 정리해주세요. 프로그램 상태는 강제로 초기화합니다.")
        elif pos.get("in_position") and hours_since_entry is not None and hours_since_entry < 8:
            # 세션 경계 오탐 가능성이 높음(진입한 지 얼마 안 됐는데 세션이 바뀐 걸로 인식됨)
            # -> 포지션을 그대로 유지하고, 세션 갱신도 하지 않음 (다음 주기에 다시 판단)
            print(f"  {ticker}: 세션 변경이 감지됐지만 진입 후 {hours_since_entry:.1f}시간밖에 안 지나서 "
                  f"오탐으로 보고 포지션을 유지합니다.")
            should_reset_session = False

        if should_reset_session:
            # 새 세션 시작 -> 초기화
            pos.update({"in_position": False, "day_traded": False, "session": session_id, "blocked_reason": None})

    orb_high_key = f"{ticker}_us"
    orb_high = state["orb_high"].get(orb_high_key)
    if orb_high is None:
        if len(df) < orb_bars_local:
            return
        orb_high = float(df.iloc[:orb_bars_local]["high"].max())
        state["orb_high"][orb_high_key] = orb_high

    df["vwap"] = calc_vwap(df)
    df["rsi"] = calc_rsi(df)
    latest = df.iloc[-1]
    latest_et_time = latest["datetime"].time()  # 미국 종목 데이터는 원본(미국 동부시간) 그대로 유지됨
    capital_usd = CAPITAL_PER_TICKER_KRW / state.get("fx_rate", 1350)

    if not pos["in_position"] and not pos["day_traded"]:
        if state["daily_pnl_us"] <= -CIRCUIT_BREAKER_KRW:
            print(f"  {ticker}: 서킷브레이커 발동, 신규 진입 안 함")
            return
        if latest_et_time >= US_FORCE_CLOSE_ET:
            print(f"  {ticker}: 장마감 임박(미국 동부시간 {latest_et_time}), 신규 진입 안 함")
            return
        # RSI가 아직 계산 안 됐으면(데이터 부족) 필터 없이 통과, 계산됐으면 과매수 여부 확인
        rsi_ok = pd.isna(latest["rsi"]) or latest["rsi"] < RSI_OVERBOUGHT_THRESHOLD
        if latest["close"] > orb_high and latest["close"] > latest["vwap"] and rsi_ok:
            # 실제 매수 시점의 환율로 다시 계산 (5분 대기 사이클마다가 아니라, 진입 순간에만 조회)
            trade_fx_rate = get_fx_rate_safe()
            capital_usd = CAPITAL_PER_TICKER_KRW / trade_fx_rate
            buy_price = float(latest["close"])
            shares = int(capital_usd // buy_price)
            if shares <= 0:
                print(f"  {ticker}: 진입조건 충족했으나 자금 부족")
                return
            print(f"  {ticker}: 진입 신호! ${buy_price:.2f} x {shares}주")
            result, used_exchange = place_us_order_smart(cfg, token, ticker, "buy", shares,
                                                           price=buy_price * 1.005,
                                                           known_exchange=exchange)
            if not result.get("success"):
                if result.get("account_level_block"):
                    print(f"  {ticker}: 계좌/종목 자체 문제로 거래가 불가능합니다. "
                          f"오늘(이번 세션)은 이 종목 재시도를 중단합니다. (KIS 앱에서 수동 확인 권장)")
                    pos.update({"day_traded": True, "blocked_reason": result.get("msg1", "")})
                else:
                    print(f"  {ticker}: 매수 주문이 거부되어 포지션을 열지 않습니다. 다음 주기에 재시도합니다.")
                return
            pos.update({"in_position": True, "day_traded": True, "entry_price": buy_price,
                        "shares": shares, "stop_price": buy_price * (1 - stop_pct_local),
                        "entry_time": datetime.now().isoformat(), "exchange": used_exchange,
                        "entry_fx_rate": trade_fx_rate})
            log_order("BUY_US", ticker, buy_price, shares, "orb_vwap_breakout")
            print(f"  {ticker}: 매수 체결 (현재가 ${latest['close']:.2f}, 박스상단 ${orb_high:.2f}, "
                  f"VWAP ${latest['vwap']:.2f}, RSI {latest['rsi']:.1f}, 손절가 ${pos['stop_price']:.2f}, "
                  f"적용환율 {trade_fx_rate:.2f}원/달러)")
        else:
            above_box = latest["close"] > orb_high
            above_vwap = latest["close"] > latest["vwap"]
            rsi_val = latest["rsi"]
            is_overbought = pd.notna(rsi_val) and rsi_val >= RSI_OVERBOUGHT_THRESHOLD
            reason = []
            if not above_box:
                reason.append("박스상단 미돌파")
            if not above_vwap:
                reason.append("VWAP 아래")
            if is_overbought:
                reason.append(f"RSI 과매수({rsi_val:.1f})")
            rsi_str = f"{rsi_val:.1f}" if pd.notna(rsi_val) else "계산불가(데이터부족)"
            print(f"  {ticker}: 대기 중 (현재가 ${latest['close']:.2f}, 박스상단 ${orb_high:.2f}, "
                  f"VWAP ${latest['vwap']:.2f}, RSI {rsi_str}) - {', '.join(reason) if reason else '조건 충족 대기'}")
        return

    if pos["in_position"]:
        entry_price = pos["entry_price"]; stop_price = pos["stop_price"]; shares = pos["shares"]
        exit_reason = None
        if latest["low"] <= stop_price:
            exit_reason = "stop_loss"
        else:
            high_ret = (latest["high"] - entry_price) / entry_price
            if high_ret >= tp_trigger_local:
                exit_reason = "take_profit"
            elif latest_et_time >= US_FORCE_CLOSE_ET:
                exit_reason = "time_close"

        if exit_reason:
            sell_price = float(latest["close"])
            print(f"  {ticker}: 청산 신호({exit_reason})! ${sell_price:.2f} x {shares}주")
            result, used_exchange = place_us_order_smart(cfg, token, ticker, "sell", shares,
                                                           price=sell_price * 0.995,
                                                           known_exchange=pos.get("exchange"))
            if not result.get("success"):
                print(f"  {ticker}: 매도 주문이 거부되었습니다 (계좌 권한 문제일 수 있음). "
                      f"포지션을 그대로 유지하고 다음 주기에 재시도합니다.")
                return
            pnl_usd = (sell_price - entry_price) * shares
            trade_fx_rate = get_fx_rate_safe()  # 매매 시점의 실시간 환율로 계산
            pnl_krw = pnl_usd * trade_fx_rate
            state["daily_pnl_us"] = state.get("daily_pnl_us", 0) + pnl_krw
            log_order("SELL_US", ticker, sell_price, shares, exit_reason, extra=f"pnl_krw={pnl_krw:.0f}")
            print(f"  {ticker}: 매도 체결 (현재가 ${latest['close']:.2f}, VWAP ${latest['vwap']:.2f}, "
                  f"손익 ${pnl_usd:+.2f} / {pnl_krw:+,.0f}원, 적용환율 {trade_fx_rate:.2f}원/달러)")
            pos.update({"in_position": False})
        else:
            print(f"  {ticker}: 보유 중 (진입가 ${entry_price:.2f}, 현재 ${latest['close']:.2f}, "
                  f"박스상단 ${orb_high:.2f}, VWAP ${latest['vwap']:.2f}, 손절가 ${stop_price:.2f})")
        return

    # 포지션 없음 + 오늘(이번 세션) 이미 매매 완료
    price_info = f"(현재가 ${latest['close']:.2f}, 박스상단 ${orb_high:.2f}, VWAP ${latest['vwap']:.2f})"
    if pos.get("blocked_reason"):
        print(f"  {ticker}: 오늘 거래 불가 종목으로 표시됨 {price_info} - 사유: {pos['blocked_reason']}")
    else:
        print(f"  {ticker}: 이번 세션 매매 이미 완료됨 {price_info}")


def get_fx_rate_safe():
    if yf is None:
        print("  [환율] yfinance 미설치로 기본값(1350원) 사용")
        return 1350
    try:
        # 일봉(전일 종가)보다 최근 분봉이 매매 시점 환율에 더 가까움
        df = yf.download("KRW=X", period="1d", interval="5m", progress=False, timeout=15)
        if df.empty:
            df = yf.download("KRW=X", period="5d", interval="1d", progress=False, timeout=15)
        if not df.empty:
            rate = float(df["Close"].iloc[-1])
            if rate > 500:
                return rate
            print(f"  [환율] 조회된 값({rate})이 비정상적으로 낮아 기본값(1350원) 사용")
        else:
            print("  [환율] 조회 결과가 비어있어 기본값(1350원) 사용")
    except Exception as e:
        print(f"  [환율] 조회 실패({e})로 기본값(1350원) 사용")
    return 1350


def process_ticker(ticker, cfg, token, state):
    strategy = get_strategy_for(ticker)
    orb_minutes_local = strategy["orb_minutes"]
    stop_pct_local = strategy["stop_pct"]
    tp_trigger_local = strategy["tp_trigger_pct"]

    now = datetime.now()
    pos = state["positions"].setdefault(ticker, {"in_position": False, "day_traded": False})

    df = fetch_today_data(cfg, token, ticker)
    if df.empty:
        print(f"  {ticker}: 데이터 없음")
        return

    # 오늘 데이터를 로컬에 저장해둠 -> 다음 주 --optimize 실행 시 이 종목이 다시 선정되면
    # 누적된 과거 데이터로 실제 그리드서치가 가능해짐 (매번 최신 데이터로 덮어씀)
    try:
        os.makedirs(OPT_DATA_DIR, exist_ok=True)
        today_str = now.strftime("%Y%m%d")
        data_save_path = os.path.join(OPT_DATA_DIR, f"{ticker}_{today_str}.csv")
        df.to_csv(data_save_path, index=False, encoding="utf-8-sig")
    except Exception as _e:
        print(f"  [알림] {ticker} 데이터 이력 저장 실패(매매엔 영향 없음): {_e}")

    # 전날 청산 안 되고 넘어온 포지션이면(load_state가 carried_over로 표시함) 비상 매도부터 시도
    if pos.get("carried_over") and pos.get("in_position"):
        print(f"  {ticker}: [경고] 전날 청산 안 된 포지션이 남아있습니다! 비상 매도를 시도합니다.")
        emergency_price = float(df.iloc[0]["open"])
        shares = pos["shares"]
        result = place_order(cfg, token, ticker, "sell", shares, price=0)
        if result.get("success"):
            entry_price = pos["entry_price"]
            pnl = (emergency_price - entry_price) * shares
            state["daily_pnl"] = state.get("daily_pnl", 0) + pnl
            log_order("SELL", ticker, emergency_price, shares, "emergency_carryover_close", extra=f"pnl={pnl:.0f}")
            print(f"  {ticker}: 비상 매도 성공.")
            pos.update({"in_position": False, "day_traded": False, "carried_over": False})
        else:
            print(f"  {ticker}: [심각] 비상 매도도 실패했습니다. 반드시 증권사 앱에서 실제 보유 현황을 "
                  f"직접 확인하고 필요시 수동으로 정리해주세요.")
            return

    df["vwap"] = calc_vwap(df)
    df["rsi"] = calc_rsi(df)
    open_dt = pd.Timestamp.combine(date.today(), MARKET_OPEN)
    range_end = open_dt + pd.Timedelta(minutes=orb_minutes_local)
    opening = df[df["datetime"] < range_end]

    if opening.empty or df["datetime"].max() < range_end:
        print(f"  {ticker}: 아직 박스 형성 중 (09:30 이후 확인 필요)")
        return

    orb_high = state["orb_high"].get(ticker)
    if orb_high is None:
        orb_high = float(opening["high"].max())
        state["orb_high"][ticker] = orb_high

    latest = df.iloc[-1]
    capital = CAPITAL_PER_TICKER_KRW

    # --- 포지션 없음: 진입 조건 체크 ---
    if not pos["in_position"] and not pos["day_traded"]:
        if state["daily_pnl"] <= -CIRCUIT_BREAKER_KRW:
            print(f"  {ticker}: 서킷브레이커 발동 (오늘 손실 {state['daily_pnl']:,.0f}원), 신규 진입 안 함")
            return
        if now.time() >= FORCE_CLOSE_TIME:
            return

        rsi_ok = pd.isna(latest["rsi"]) or latest["rsi"] < RSI_OVERBOUGHT_THRESHOLD
        if latest["close"] > orb_high and latest["close"] > latest["vwap"] and rsi_ok:
            buy_price = latest["close"]
            shares = int(capital // buy_price)
            if shares <= 0:
                print(f"  {ticker}: 진입조건 충족했으나 자금 부족")
                return
            print(f"  {ticker}: 진입 신호! {buy_price:,.0f}원 x {shares}주")
            result = place_order(cfg, token, ticker, "buy", shares, price=0)
            if not result.get("success"):
                if result.get("msg_cd") in ACCOUNT_LEVEL_BLOCK_CODES:
                    print(f"  {ticker}: 계좌/종목 자체 문제로 거래가 불가능합니다. "
                          f"오늘은 이 종목 재시도를 중단합니다. (KIS 앱에서 수동 확인 권장)")
                    pos.update({"day_traded": True, "blocked_reason": result.get("msg1", "")})
                else:
                    print(f"  {ticker}: 매수 주문이 거부되어 포지션을 열지 않습니다. 다음 주기에 재시도합니다.")
                return
            pos.update({"in_position": True, "day_traded": True, "entry_price": buy_price,
                        "shares": shares, "stop_price": buy_price*(1-stop_pct_local),
                        "entry_time": now.isoformat()})
            log_order("BUY", ticker, buy_price, shares, "orb_vwap_breakout")
            print(f"  {ticker}: 매수 체결 (현재가 {latest['close']:,.0f}, 박스상단 {orb_high:,.0f}, "
                  f"VWAP {latest['vwap']:,.0f}, 손절가 {pos['stop_price']:,.0f})")
        else:
            above_box = latest["close"] > orb_high
            above_vwap = latest["close"] > latest["vwap"]
            reason = []
            if not above_box:
                reason.append("박스상단 미돌파")
            if not above_vwap:
                reason.append("VWAP 아래")
            print(f"  {ticker}: 대기 중 (현재가 {latest['close']:,.0f}, 박스상단 {orb_high:,.0f}, "
                  f"VWAP {latest['vwap']:,.0f}) - {', '.join(reason)}")
        return

    # --- 포지션 보유 중: 청산 조건 체크 ---
    if pos["in_position"]:
        entry_price = pos["entry_price"]
        stop_price = pos["stop_price"]
        shares = pos["shares"]
        exit_reason = None

        if latest["low"] <= stop_price:
            exit_reason = "stop_loss"
        else:
            high_ret = (latest["high"]-entry_price)/entry_price
            if high_ret >= tp_trigger_local:
                exit_reason = "take_profit"
            elif now.time() >= FORCE_CLOSE_TIME:
                exit_reason = "time_close"

        if exit_reason:
            sell_price = latest["close"]
            print(f"  {ticker}: 청산 신호({exit_reason})! {sell_price:,.0f}원 x {shares}주")
            result = place_order(cfg, token, ticker, "sell", shares, price=0)
            if not result.get("success"):
                print(f"  {ticker}: 매도 주문이 거부되었습니다. 포지션을 그대로 유지하고 다음 주기에 재시도합니다.")
                return
            pnl = (sell_price - entry_price) * shares
            state["daily_pnl"] = state.get("daily_pnl", 0) + pnl
            log_order("SELL", ticker, sell_price, shares, exit_reason, extra=f"pnl={pnl:.0f}")
            print(f"  {ticker}: 매도 체결 (현재가 {latest['close']:,.0f}, VWAP {latest['vwap']:,.0f}, "
                  f"손익 {pnl:+,.0f}원)")
            pos.update({"in_position": False})
        else:
            print(f"  {ticker}: 보유 중 (진입가 {entry_price:,.0f}, 현재 {latest['close']:,.0f}, "
                  f"박스상단 {orb_high:,.0f}, VWAP {latest['vwap']:,.0f}, 손절가 {stop_price:,.0f})")
        return

    # 포지션 없음 + 오늘 이미 매매 완료(또는 계좌/종목 문제로 차단)
    price_info = f"(현재가 {latest['close']:,.0f}, 박스상단 {orb_high:,.0f}, VWAP {latest['vwap']:,.0f})"
    if pos.get("blocked_reason"):
        print(f"  {ticker}: 오늘 거래 불가 종목으로 표시됨 {price_info} - 사유: {pos['blocked_reason']}")
    else:
        print(f"  {ticker}: 오늘 매매 이미 완료됨 {price_info}")


# ============================================================
# 메인
# ============================================================
def main():
    print("=" * 60)
    print(f" 자동매매 봇 실행 [{'모의투자' if IS_MOCK else '실전투자'}] - {datetime.now()}")
    print("=" * 60)

    # 계좌번호 형식 검증 (설정 실수를 여기서 바로 잡아냄)
    if "-" not in ACCOUNT_NO or len(ACCOUNT_NO.split("-")) != 2:
        print(f"[설정 오류] ACCOUNT_NO 형식이 잘못됐습니다: '{ACCOUNT_NO}'")
        print("올바른 형식: '12345678-01' (계좌번호 8자리-상품코드 2자리)")
        print(f"{USER_CONFIG_PATH} 파일을 메모장으로 열어서 account_no 값을 본인 계좌번호로 수정해주세요.")
        return
    cano, prdt_cd = ACCOUNT_NO.split("-")
    if not (cano.isdigit() and len(cano) == 8 and prdt_cd.isdigit() and len(prdt_cd) == 2):
        print(f"[설정 오류] ACCOUNT_NO 형식이 잘못됐습니다: '{ACCOUNT_NO}'")
        print("올바른 형식: 앞 8자리 숫자 + '-' + 뒤 2자리 숫자 (예: '12345678-01')")
        print(f"{USER_CONFIG_PATH} 파일을 메모장으로 열어서 account_no 값을 수정해주세요.")
        return

    if not IS_MOCK and not CONFIRM_LIVE_TRADING:
        print("[안전장치] 실전 주문이 나가지 않는 상태입니다 (CONFIG 확인).\n")

    now = datetime.now()
    cfg = load_kis_config()
    token = get_token(cfg)
    state = load_state()

    if state.get("fx_rate") is None:
        state["fx_rate"] = get_fx_rate_safe()
    print(f"적용 환율: {state['fx_rate']:.1f}원/달러")

    # 시간대에 따라 국내장/미국장 자동 판별
    is_weekday = now.weekday() < 5
    kr_market_hours = time(9, 0) <= now.time() <= time(15, 30)
    # 미국장(한국시간 기준, 서머타임 포함 대략 22:00~06:00)
    us_market_hours = now.time() >= time(22, 0) or now.time() <= time(6, 0)

    if is_weekday and kr_market_hours:
        print(f"\n[국내장 시간대 - 국내 {len(TICKERS)}종목 처리]")
        for ticker in TICKERS:
            print(f"\n[{ticker}]")
            try:
                process_ticker(ticker, cfg, token, state)
            except Exception as e:
                print(f"  [오류] {e}")

    if us_market_hours:
        print(f"\n[미국장 시간대 - 미국 {len(US_TICKERS)}종목 처리]")
        for ticker, info in US_TICKERS.items():
            print(f"\n[{ticker}]")
            try:
                process_us_ticker(ticker, info["exchange"], cfg, token, state)
            except Exception as e:
                import traceback
                print(f"  [오류] {type(e).__name__}: {e}")
                traceback.print_exc()
            time_module.sleep(2.0)  # yfinance 연속 호출 부담 완화

    if not (is_weekday and kr_market_hours) and not us_market_hours:
        print("\n국내장/미국장 모두 시간이 아닙니다. 종료합니다.")

    save_state(state)
    print(f"\n오늘 국내 누적손익: {state['daily_pnl']:+,.0f}원")
    print(f"오늘 미국 누적손익(원화환산): {state['daily_pnl_us']:+,.0f}원")

    git_push_logs()


def git_push_logs():
    """C:\\TradingBot이 Git 저장소로 초기화되어 있으면, 로그/주문 폴더 + 코드 파일(.py)을
    자동으로 커밋+push함. Git이 설정 안 되어 있으면 조용히 건너뜀(에러 아님).
    ⚠️ .gitignore로 걸러지는 파일(kis_config.json 등 민감정보)은 애초에 git add 대상에서
    아예 제외하고, 안전하게 로그성 폴더 + 알려진 스크립트 파일만 명시적으로 add함.
    (사람이 새 코드를 이 폴더에 직접 덮어써주기만 하면, 그 다음 커밋+push는 자동으로 처리됨)"""
    git_dir = os.path.join(BASE_DIR, ".git")
    if not os.path.isdir(git_dir):
        return  # Git 저장소가 아니면 아무 것도 안 함 (기존 사용자에게 영향 없도록)

    import subprocess
    safe_paths = ["Log", "Order", "ScreeningLog", "ParamsLog"]
    existing_paths = [p for p in safe_paths if os.path.isdir(os.path.join(BASE_DIR, p))]

    # 코드 파일들도 존재하면 같이 add 대상에 포함 (없는 파일은 조용히 건너뜀)
    known_scripts = ["auto_trading_bot.py", "kakao_watchdog.py", "kakao_setup.py",
                      "kakao_test.py", "kis_order_test.py", "kis_us_order_test.py"]
    existing_scripts = [s for s in known_scripts if os.path.isfile(os.path.join(BASE_DIR, s))]
    existing_paths += existing_scripts

    if not existing_paths:
        return

    # ⚠️ Windows 콘솔 기본 인코딩(CP949)이 Git의 UTF-8 출력을 못 읽어서 나던
    # UnicodeDecodeError 방지를 위해 encoding/errors를 명시적으로 지정함
    run_kwargs = dict(cwd=BASE_DIR, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")

    try:
        subprocess.run(["git", "add"] + existing_paths, timeout=30, **run_kwargs)

        commit_msg = f"자동 커밋 (로그/코드) {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        commit_res = subprocess.run(["git", "commit", "-m", commit_msg], timeout=30, **run_kwargs)
        # 변경사항이 없으면 commit이 실패하는데(정상), 그 경우는 조용히 넘어감
        if commit_res.returncode != 0 and "nothing to commit" not in commit_res.stdout.lower():
            if commit_res.stdout.strip():
                print(f"  [Git] 커밋 결과: {commit_res.stdout.strip()[:200]}")

        push_res = subprocess.run(["git", "push"], timeout=60, **run_kwargs)
        if push_res.returncode == 0:
            print("  [Git] 로그를 원격 저장소에 push 완료")
        else:
            print(f"  [Git] push 실패(무시하고 계속 진행): {push_res.stderr.strip()[:200]}")
    except Exception as e:
        print(f"  [Git] 자동 push 중 오류(무시하고 계속 진행): {e}")




# ============================================================================
# ============================================================================
#   아래부터는 "스크리닝(--screen)" 및 "주간 재최적화(--optimize)" 기능.
#   기존에 kis_stock_screener.py / weekly_optimizer.py로 분리되어 있던 걸
#   이 파일 하나로 통합했습니다. 실행 시 인수로 모드를 선택합니다.
#     python auto_trading_bot.py            -> 평소처럼 5분마다 매매 체크 (기본)
#     python auto_trading_bot.py --screen    -> 국내10+미국10 종목 자동 선별
#     python auto_trading_bot.py --optimize  -> 선별된 종목별 파라미터 재최적화
#     python auto_trading_bot.py --weekly    -> 위 두 개를 순서대로 한번에 실행
#       (일요일 밤 스케줄러에 이 --weekly 하나만 등록하면 됩니다)
# ============================================================================
# ============================================================================

# ---------------- 스크리닝/최적화 전용 CONFIG ----------------
TR_ID_DAILY_CHART = "FHKST03010100"       # 국내주식 일별 시세 조회
DAILY_CHART_PATH = "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice"
# ⚠️ 거래대금 순위 조회 - 공식문서로 100% 재검증은 안 됐으나, 널리 알려진 값으로 시도합니다.
TR_ID_VOLUME_RANK = "FHPST01710000"
VOLUME_RANK_PATH = "/uapi/domestic-stock/v1/quotations/volume-rank"

SCREEN_LOOKBACK_DAYS = 100
SCREEN_IDEAL_VOL_MIN = 2.0
SCREEN_IDEAL_VOL_MAX = 8.0
SCREEN_MAX_PANIC_DAY_RATIO = 0.15
SCREEN_MIN_WON_VOLUME_KRW = 3_000_000_000
SCREEN_MIN_MARKET_CAP_USD = 500_000_000
SCREEN_MIN_PRICE_USD = 5.0
SCREEN_MAX_PRICE_USD = 200.0   # 데이트레이딩엔 너무 비싼 종목(호가단위/스프레드 불리)은 제외
SCREEN_MAX_PRICE_KRW = 300000  # 국내도 동일한 취지로 상한 적용
SCREEN_MIN_DOLLAR_VOLUME_USD = 5_000_000
SCREEN_US_TOP_N_CANDIDATES = 15
SCREEN_US_LOOKBACK_DAYS = 60
SCREENING_LOG_DIR = os.path.join(BASE_DIR, "ScreeningLog")

OPT_DEFAULT_PARAMS = {"orb_bars": 6, "stop_pct": 0.015, "tp_trigger_pct": 0.010, "tp_max_pct": 0.040}
OPT_US_LOOKBACK_CALENDAR_DAYS = 21
OPT_GRID_ORB_BARS = [3, 6, 9]
OPT_GRID_STOP_PCT = [0.010, 0.015, 0.020]
OPT_GRID_TP_TRIGGER = [0.005, 0.010, 0.015]
OPT_GRID_TP_MAX = [0.020, 0.030, 0.040]
OPT_DATA_DIR = os.path.join(BASE_DIR, "data")
PARAMS_LOG_DIR = os.path.join(BASE_DIR, "ParamsLog")


# ============================================================
# 스크리닝: 국내 일봉/주봉 조회
# ============================================================
def screen_fetch_daily_chart(cfg, token, ticker, days=SCREEN_LOOKBACK_DAYS, period_div_code="D", max_retries=5):
    base_url = get_base_url()
    url = base_url + DAILY_CHART_PATH
    headers = {
        "content-type": "application/json; charset=utf-8", "authorization": f"Bearer {token}",
        "appkey": cfg["appkey"], "appsecret": cfg["appsecret"], "tr_id": TR_ID_DAILY_CHART, "custtype": "P",
    }
    end_date = datetime.now().strftime("%Y%m%d")
    start_date = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")
    params = {
        "FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": ticker,
        "FID_INPUT_DATE_1": start_date, "FID_INPUT_DATE_2": end_date,
        "FID_PERIOD_DIV_CODE": period_div_code, "FID_ORG_ADJ_PRC": "0",
    }
    for attempt in range(1, max_retries + 1):
        res = requests.get(url, headers=headers, params=params, timeout=10)
        if res.status_code == 200:
            data = res.json()
            if data.get("rt_cd") == "1" and data.get("msg_cd") == "EGW00201":
                wait = 2 ** attempt
                print(f"    [대기] 초당 호출 제한, {wait}초 후 재시도 ({attempt}/{max_retries})")
                time_module.sleep(wait)
                continue
            break
        else:
            if "EGW00201" in res.text and attempt < max_retries:
                wait = 2 ** attempt
                print(f"    [대기] 초당 호출 제한, {wait}초 후 재시도 ({attempt}/{max_retries})")
                time_module.sleep(wait)
                continue
            return None, f"HTTP {res.status_code}: {res.text}"
    else:
        return None, "재시도 횟수 초과 (초당 호출 제한)"

    if data.get("rt_cd") != "0":
        return None, f"{data.get('msg_cd')}: {data.get('msg1')}"
    rows = data.get("output2", [])
    if not rows:
        return None, "데이터 없음"

    df = pd.DataFrame(rows)
    try:
        df["date"] = pd.to_datetime(df["stck_bsop_date"])
        df["open"] = df["stck_oprc"].astype(float)
        df["high"] = df["stck_hgpr"].astype(float)
        df["low"] = df["stck_lwpr"].astype(float)
        df["close"] = df["stck_clpr"].astype(float)
        df["volume"] = df["acml_vol"].astype(float)
    except KeyError as e:
        return None, f"예상과 다른 응답 필드 구조: {e}"
    df = df.sort_values("date").reset_index(drop=True)
    return df[["date", "open", "high", "low", "close", "volume"]], None


def screen_fetch_top_traded_stocks(cfg, token, top_n=15):
    base_url = get_base_url()
    url = base_url + VOLUME_RANK_PATH
    headers = {
        "content-type": "application/json; charset=utf-8", "authorization": f"Bearer {token}",
        "appkey": cfg["appkey"], "appsecret": cfg["appsecret"], "tr_id": TR_ID_VOLUME_RANK, "custtype": "P",
    }
    params = {
        "FID_COND_MRKT_DIV_CODE": "J", "FID_COND_SCR_DIV_CODE": "20171", "FID_INPUT_ISCD": "0000",
        "FID_DIV_CLS_CODE": "0", "FID_BLNG_CLS_CODE": "0", "FID_TRGT_CLS_CODE": "111111111",
        "FID_TRGT_EXLS_CLS_CODE": "0000000000", "FID_INPUT_PRICE_1": "", "FID_INPUT_PRICE_2": "",
        "FID_VOL_CNT": "", "FID_INPUT_DATE_1": "",
    }
    res = requests.get(url, headers=headers, params=params, timeout=10)
    if res.status_code != 200:
        print(f"[거래대금순위 조회 HTTP 오류] status_code={res.status_code}")
        print(f"[서버 응답 원문] {res.text}")
        return None, f"HTTP {res.status_code}"
    data = res.json()
    if data.get("rt_cd") != "0":
        print(f"[거래대금순위 조회 실패] msg_cd={data.get('msg_cd')} msg1={data.get('msg1')}")
        return None, data.get("msg1")
    rows = data.get("output", [])
    if not rows:
        return None, "결과 없음"
    tickers = []
    for row in rows[:top_n]:
        code = row.get("mksc_shrn_iscd") or row.get("stck_shrn_iscd") or row.get("code")
        name = row.get("hts_kor_isnm", "")
        if code:
            tickers.append((code, name))
    return tickers, None


def screen_score_kr(df, period_div_code="D"):
    if period_div_code == "W":
        vol_min, vol_max, panic_threshold, max_panic_ratio = 5.0, 18.0, 30.0, 0.20
    else:
        vol_min, vol_max, panic_threshold, max_panic_ratio = SCREEN_IDEAL_VOL_MIN, SCREEN_IDEAL_VOL_MAX, 15.0, SCREEN_MAX_PANIC_DAY_RATIO

    df = df.copy()
    # ⚠️ 단순 (고가-저가)/시가 대신, 전일 종가 대비 갭까지 반영하는 진짜 ATR(Average True Range) 방식으로 계산.
    # 갭상승/갭하락이 있는 날은 (고가-저가)만으론 그날의 진짜 변동폭을 과소평가하게 됨.
    df["prev_close"] = df["close"].shift(1)
    tr1 = df["high"] - df["low"]
    tr2 = (df["high"] - df["prev_close"]).abs()
    tr3 = (df["low"] - df["prev_close"]).abs()
    df["true_range"] = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    df["true_range"] = df["true_range"].fillna(tr1)  # 첫 행은 전일종가가 없어 고가-저가로 대체
    df["daily_range_pct"] = df["true_range"] / df["open"] * 100

    avg_range = df["daily_range_pct"].mean()
    panic_day_ratio = (df["daily_range_pct"] > panic_threshold).sum() / len(df)
    df["won_volume"] = df["close"] * df["volume"]
    avg_won_volume = df["won_volume"].mean()
    df["today_ret"] = (df["close"] - df["open"]) / df["open"]
    df["next_gap"] = (df["open"].shift(-1) - df["close"]) / df["close"]
    trend_corr = df["today_ret"].corr(df["next_gap"])
    if pd.isna(trend_corr):
        trend_corr = 0
    last_price = float(df["close"].iloc[-1])

    score = 0
    reasons = []
    if vol_min <= avg_range <= vol_max:
        vol_score = 35
        reasons.append(f"변동폭 적정({avg_range:.1f}%)")
    elif avg_range < vol_min:
        vol_score = 35 * (avg_range / vol_min)
        reasons.append(f"변동폭 다소 낮음({avg_range:.1f}%)")
    else:
        vol_score = max(0, 35 - (avg_range - vol_max) * 3)
        reasons.append(f"변동폭 다소 높음({avg_range:.1f}%)")
    score += vol_score

    panic_score = max(0, min(20, 20 * (1 - panic_day_ratio / max_panic_ratio))) if max_panic_ratio > 0 else 20
    score += panic_score
    if panic_day_ratio > max_panic_ratio:
        reasons.append(f"패닉성 급변동 비율 높음({panic_day_ratio*100:.0f}%)")

    liquidity_ratio = avg_won_volume / SCREEN_MIN_WON_VOLUME_KRW
    score += min(25, 25 * liquidity_ratio)
    reasons.append("유동성 충분" if liquidity_ratio >= 1 else "유동성 부족 가능성")

    trend_score = max(0, min(20, (trend_corr + 0.3) * 33))
    score += trend_score
    if trend_corr > 0.05:
        reasons.append(f"추세지속성 양호({trend_corr:.2f})")
    elif trend_corr < -0.05:
        reasons.append(f"평균회귀 성향({trend_corr:.2f})")

    # NR7 가산점: 최근 7거래일 중 "가장 최근 날"의 변동폭이 가장 좁으면(=박스권 수축)
    # 돌파가 임박했을 가능성이 높다는 경험칙(Crabel)에 따라 가산점을 줌
    is_nr7 = False
    if len(df) >= 7:
        last_7 = df["daily_range_pct"].tail(7)
        if last_7.iloc[-1] == last_7.min():
            is_nr7 = True
            score += 10
            reasons.append("NR7(최근7일중 최저변동성, 돌파 임박 가능성)")

    # RVOL(상대거래량) 가산점: 가장 최근 거래일의 거래량이 그 이전 평균 대비 2배 이상이면
    # "오늘 유독 관심이 몰린 종목"으로 판단해서 가산점을 줌 (데이트레이더들이 흔히 쓰는 기준)
    rvol = None
    is_high_rvol = False
    if len(df) >= 11:
        recent_volume = df["volume"].iloc[-1]
        baseline_volume = df["volume"].iloc[-11:-1].mean()  # 최근 1일 제외한 이전 10일 평균
        if baseline_volume > 0:
            rvol = recent_volume / baseline_volume
            if rvol >= 2.0:
                is_high_rvol = True
                score += 10
                reasons.append(f"RVOL 높음(평소 대비 {rvol:.1f}배 거래량)")

    score = min(round(score, 1), 100.0)

    return {"market": "KR", "score": score, "avg_daily_range_pct": round(avg_range, 2),
            "panic_day_ratio_pct": round(panic_day_ratio * 100, 1), "is_nr7": is_nr7,
            "is_high_rvol": is_high_rvol, "rvol": round(rvol, 2) if rvol is not None else None,
            "avg_won_volume": round(avg_won_volume, 0), "trend_corr": round(trend_corr, 3),
            "last_price": round(last_price, 0), "reasons": "; ".join(reasons)}


# ============================================================
# 스크리닝: 미국 종목 자동 발굴 (웹 스크래핑)
# ============================================================
def screen_fetch_most_active_us_stocks():
    url = "https://stockanalysis.com/markets/active/"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    try:
        res = requests.get(url, headers=headers, timeout=15)
        res.raise_for_status()
    except Exception as e:
        return None, f"페이지 접속 실패: {e}"
    try:
        tables = pd.read_html(io.StringIO(res.text))
    except Exception as e:
        return None, f"표 파싱 실패(사이트 구조가 바뀌었을 수 있음): {e}"
    if not tables:
        return None, "표를 찾지 못함"
    df = tables[0]
    col_map = {}
    for c in df.columns:
        cl = str(c).lower()
        if "symbol" in cl:
            col_map["symbol"] = c
        elif "market cap" in cl:
            col_map["market_cap"] = c
        elif "price" in cl and "change" not in cl:
            col_map["price"] = c
    if "symbol" not in col_map:
        return None, f"필요한 컬럼을 못 찾음 (실제 컬럼: {list(df.columns)})"
    df = df.rename(columns={v: k for k, v in col_map.items()})
    return df, None


def screen_parse_market_cap(val):
    if pd.isna(val):
        return 0
    s = str(val).strip().upper().replace("$", "").replace(",", "")
    mult = 1
    if s.endswith("T"):
        mult, s = 1_000_000_000_000, s[:-1]
    elif s.endswith("B"):
        mult, s = 1_000_000_000, s[:-1]
    elif s.endswith("M"):
        mult, s = 1_000_000, s[:-1]
    elif s.endswith("K"):
        mult, s = 1_000, s[:-1]
    try:
        return float(s) * mult
    except ValueError:
        return 0


def screen_filter_us_candidates(df):
    df = df.copy()
    df["market_cap_usd"] = df["market_cap"].apply(screen_parse_market_cap) if "market_cap" in df.columns else np.nan
    if "price" in df.columns:
        df["price_usd"] = pd.to_numeric(
            df["price"].astype(str).str.replace("$", "", regex=False).str.replace(",", "", regex=False),
            errors="coerce")
    else:
        df["price_usd"] = np.nan
    before = len(df)
    if "market_cap" in df.columns:
        df = df[df["market_cap_usd"] >= SCREEN_MIN_MARKET_CAP_USD]
    if "price" in df.columns:
        df = df[df["price_usd"] >= SCREEN_MIN_PRICE_USD]
    print(f"  [미국] 필터링: {before}개 -> {len(df)}개 (시가총액 {SCREEN_MIN_MARKET_CAP_USD/1e8:.0f}억달러 이상, "
          f"주가 ${SCREEN_MIN_PRICE_USD} 이상)")
    return df.head(SCREEN_US_TOP_N_CANDIDATES)


def screen_analyze_us_ticker(ticker: str):
    if yf is None:
        return {"ticker": ticker, "market": "US", "error": "yfinance 미설치"}
    try:
        df = yf.download(ticker, period=f"{SCREEN_US_LOOKBACK_DAYS}d", interval="1d",
                          progress=False, auto_adjust=False, timeout=15)
    except Exception as e:
        return {"ticker": ticker, "market": "US", "error": f"다운로드 실패: {e}"}
    if df.empty or len(df) < 10:
        return {"ticker": ticker, "market": "US", "error": "데이터 부족"}
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0] for c in df.columns]
    df = df.reset_index()

    required_cols = ["Open", "High", "Low", "Close", "Volume"]
    df = df.dropna(subset=required_cols)
    df = df[(df["Open"] > 0) & (df["Close"] > 0)]
    if len(df) < 10:
        return {"ticker": ticker, "market": "US", "error": f"유효 데이터 부족 (결측치 제거 후 {len(df)}행만 남음)"}

    # ⚠️ 국내와 동일하게, 단순 (고가-저가)가 아니라 전일 종가 대비 갭까지 반영하는 ATR 방식으로 계산
    df["prev_close"] = df["Close"].shift(1)
    tr1 = df["High"] - df["Low"]
    tr2 = (df["High"] - df["prev_close"]).abs()
    tr3 = (df["Low"] - df["prev_close"]).abs()
    df["true_range"] = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    df["true_range"] = df["true_range"].fillna(tr1)
    df["daily_range_pct"] = df["true_range"] / df["Open"] * 100

    avg_range = df["daily_range_pct"].mean()
    panic_day_ratio = (df["daily_range_pct"] > 15).sum() / len(df)
    df["dollar_volume"] = df["Close"] * df["Volume"]
    avg_dollar_volume = df["dollar_volume"].mean()
    df["today_ret"] = (df["Close"] - df["Open"]) / df["Open"]
    df["next_gap"] = (df["Open"].shift(-1) - df["Close"]) / df["Close"]
    trend_corr = df["today_ret"].corr(df["next_gap"])
    if pd.isna(trend_corr):
        trend_corr = 0
    last_price = float(df["Close"].iloc[-1])

    if pd.isna(avg_range) or pd.isna(avg_dollar_volume) or pd.isna(last_price):
        return {"ticker": ticker, "market": "US", "error": "데이터에 결측치가 남아 점수 계산 불가"}

    score = 0
    reasons = []
    if SCREEN_IDEAL_VOL_MIN <= avg_range <= SCREEN_IDEAL_VOL_MAX:
        vol_score = 35
        reasons.append(f"변동폭 적정({avg_range:.1f}%)")
    elif avg_range < SCREEN_IDEAL_VOL_MIN:
        vol_score = 35 * (avg_range / SCREEN_IDEAL_VOL_MIN)
        reasons.append(f"변동폭 다소 낮음({avg_range:.1f}%)")
    else:
        vol_score = max(0, 35 - (avg_range - SCREEN_IDEAL_VOL_MAX) * 3)
        reasons.append(f"변동폭 다소 높음({avg_range:.1f}%)")
    score += vol_score

    panic_score = max(0, min(20, 20 * (1 - panic_day_ratio / SCREEN_MAX_PANIC_DAY_RATIO))) if SCREEN_MAX_PANIC_DAY_RATIO > 0 else 20
    score += panic_score
    if panic_day_ratio > SCREEN_MAX_PANIC_DAY_RATIO:
        reasons.append(f"패닉성 급변동일 비율 높음({panic_day_ratio*100:.0f}%)")

    liquidity_ratio = avg_dollar_volume / SCREEN_MIN_DOLLAR_VOLUME_USD
    score += min(25, 25 * liquidity_ratio)
    reasons.append("유동성 충분" if liquidity_ratio >= 1 else "유동성 부족 가능성")

    trend_score = max(0, min(20, (trend_corr + 0.3) * 33))
    score += trend_score
    if trend_corr > 0.05:
        reasons.append(f"추세지속성 양호({trend_corr:.2f})")
    elif trend_corr < -0.05:
        reasons.append(f"평균회귀 성향({trend_corr:.2f})")

    # NR7 가산점 (국내와 동일 로직)
    is_nr7 = False
    if len(df) >= 7:
        last_7 = df["daily_range_pct"].tail(7)
        if last_7.iloc[-1] == last_7.min():
            is_nr7 = True
            score += 10
            reasons.append("NR7(최근7일중 최저변동성, 돌파 임박 가능성)")

    # RVOL(상대거래량) 가산점 (국내와 동일 로직)
    rvol = None
    is_high_rvol = False
    if len(df) >= 11:
        recent_volume = df["Volume"].iloc[-1]
        baseline_volume = df["Volume"].iloc[-11:-1].mean()
        if baseline_volume > 0:
            rvol = recent_volume / baseline_volume
            if rvol >= 2.0:
                is_high_rvol = True
                score += 10
                reasons.append(f"RVOL 높음(평소 대비 {rvol:.1f}배 거래량)")

    score = min(round(score, 1), 100.0)

    # 종목명 조회 (실패해도 스크리닝 자체는 계속 진행되도록 예외 처리)
    company_name = ticker
    try:
        info = yf.Ticker(ticker).info
        company_name = info.get("shortName") or info.get("longName") or ticker
    except Exception:
        pass  # 조회 실패시 그냥 티커명을 이름으로 사용

    return {"ticker": ticker, "market": "US", "score": score, "name": company_name,
            "avg_daily_range_pct": round(avg_range, 2), "panic_day_ratio_pct": round(panic_day_ratio * 100, 1),
            "is_nr7": is_nr7, "is_high_rvol": is_high_rvol, "rvol": round(rvol, 2) if rvol is not None else None,
            "avg_won_volume": round(avg_dollar_volume, 0), "trend_corr": round(trend_corr, 3),
            "last_price": round(last_price, 2), "reasons": "; ".join(reasons)}


def screen_write_watchlist(df_result, top_kr=5, top_us=5):
    # 종목당 투입금액(CAPITAL_PER_TICKER_KRW)으로 1주도 못 사는 종목은 애초에 후보에서 제외.
    # -> 순위표에서 그냥 빠지고, 다음 순위 종목이 자연스럽게 그 자리를 채우게 됨
    # (매매 당일 "자금 부족"으로 기회를 날리는 것보다, 선정 단계에서 미리 걸러내는 게 안전함)
    fx_rate = get_fx_rate_safe()  # 미국 종목 가격을 원화로 환산해서 비교하기 위함

    def _is_affordable(row):
        if row["market"] == "KR":
            return row["last_price"] < CAPITAL_PER_TICKER_KRW
        else:
            return row["last_price"] * fx_rate < CAPITAL_PER_TICKER_KRW

    def _is_within_price_ceiling(row):
        # 너무 비싼 종목은 호가단위/스프레드 측면에서 데이트레이딩에 불리하다는
        # 일반적인 경험칙에 따라 별도 상한도 적용 (투입금액 기준과는 별개의 필터)
        if row["market"] == "KR":
            return row["last_price"] <= SCREEN_MAX_PRICE_KRW
        else:
            return row["last_price"] <= SCREEN_MAX_PRICE_USD

    affordable_mask = df_result.apply(_is_affordable, axis=1)
    excluded = df_result[~affordable_mask]
    if len(excluded) > 0:
        print(f"[알림] 종목당 투입금액({CAPITAL_PER_TICKER_KRW:,.0f}원)으로 1주도 살 수 없는 "
              f"{len(excluded)}개 종목을 후보에서 제외합니다:")
        for _, r in excluded.iterrows():
            price_str = f"{r['last_price']:,.0f}원" if r["market"] == "KR" else f"${r['last_price']:,.2f}"
            print(f"  ({r['market']}) {r['ticker']}: {price_str}")
    df_result = df_result[affordable_mask]

    price_ceiling_mask = df_result.apply(_is_within_price_ceiling, axis=1)
    excluded_ceiling = df_result[~price_ceiling_mask]
    if len(excluded_ceiling) > 0:
        print(f"[알림] 가격 상한(국내 {SCREEN_MAX_PRICE_KRW:,.0f}원 / 미국 ${SCREEN_MAX_PRICE_USD:.0f}) 초과로 "
              f"{len(excluded_ceiling)}개 종목을 후보에서 제외합니다:")
        for _, r in excluded_ceiling.iterrows():
            price_str = f"{r['last_price']:,.0f}원" if r["market"] == "KR" else f"${r['last_price']:,.2f}"
            print(f"  ({r['market']}) {r['ticker']}: {price_str}")
    df_result = df_result[price_ceiling_mask]

    kr_rows = df_result[df_result["market"] == "KR"].head(top_kr)
    us_rows = df_result[df_result["market"] == "US"].head(top_us)
    watchlist = {
        "generated_at": datetime.now().isoformat(),
        "kr_tickers": {row["ticker"]: {"account_prdt_cd": "01", "name": row.get("name", ""), "score": row["score"]}
                       for _, row in kr_rows.iterrows()},
        "us_tickers": {row["ticker"]: {"exchange": "AMEX", "score": row["score"], "name": row.get("name", row["ticker"])}
                       for _, row in us_rows.iterrows()},
    }

    def _is_valid_key(k):
        return isinstance(k, str) and k.strip() and k.strip().lower() != "nan"

    watchlist["kr_tickers"] = {k: v for k, v in watchlist["kr_tickers"].items() if _is_valid_key(k)}
    watchlist["us_tickers"] = {k: v for k, v in watchlist["us_tickers"].items() if _is_valid_key(k)}

    for attempt in range(3):
        try:
            with open(WATCHLIST_PATH, "w", encoding="utf-8") as f:
                json.dump(watchlist, f, ensure_ascii=False, indent=2)
            break
        except PermissionError as e:
            print(f"  [경고] watchlist.json 쓰기 실패(다른 프로그램이 열어둔 상태일 수 있음), 재시도 {attempt+1}/3: {e}")
            time_module.sleep(1.5)
    else:
        print("  [심각] watchlist.json 최종 저장 실패. 파일이 다른 프로그램에서 열려있지 않은지 확인 후 다시 실행해주세요.")
        print("  (이번 실행 결과가 반영되지 않고, 기존 watchlist.json이 그대로 유지됩니다)")
        return

    print(f"\n[연동용 파일 저장] {WATCHLIST_PATH}")
    print(f"  국내 {len(watchlist['kr_tickers'])}종목: {', '.join(watchlist['kr_tickers'].keys())}")
    print(f"  미국 {len(watchlist['us_tickers'])}종목: {', '.join(watchlist['us_tickers'].keys())}")


def run_screening_mode():
    print("=" * 70)
    print(" [--screen] 국내+미국 통합 ORB+VWAP 적합 종목 자동 추천")
    print("=" * 70)

    all_results = []
    cfg = load_kis_config()
    token = get_token(cfg)
    print("토큰 준비 완료")

    print("\n[국내 종목 분석]")
    kr_top_n = 15
    kr_candidates, err = screen_fetch_top_traded_stocks(cfg, token, top_n=kr_top_n)
    if kr_candidates is None:
        print(f"국내 자동 조회 실패({err}). 국내 종목은 건너뜁니다.")
    else:
        print(f"국내 후보: {', '.join(f'{n}({c})' for c, n in kr_candidates)}\n")
        for t, n in kr_candidates:
            print(f"  [국내] {n}({t}) 조회 중...")
            df, err = screen_fetch_daily_chart(cfg, token, t)
            time_module.sleep(2.0)
            if err:
                print(f"    실패: {err}")
                all_results.append({"ticker": t, "name": n, "market": "KR", "error": err})
                continue
            r = screen_score_kr(df, period_div_code="D")
            r["ticker"] = t
            r["name"] = n
            all_results.append(r)

    print("\n[미국 종목 분석]")
    if yf is None:
        print("yfinance가 없어 미국 종목 분석을 건너뜁니다.")
    else:
        raw_df, err = screen_fetch_most_active_us_stocks()
        if raw_df is None:
            print(f"미국 자동 조회 실패({err}). 미국 종목은 건너뜁니다.")
        else:
            filtered = screen_filter_us_candidates(raw_df)
            us_tickers = filtered["symbol"].tolist()
            print(f"미국 후보: {', '.join(us_tickers)}\n")
            for t in us_tickers:
                print(f"  [미국] {t} 조회 중...")
                r = screen_analyze_us_ticker(t)
                if "error" in r:
                    print(f"    실패: {r['error']}")
                all_results.append(r)

    valid = [r for r in all_results if "error" not in r]
    errors = [r for r in all_results if "error" in r]
    if not valid:
        print("\n분석 가능한 종목이 없습니다.")
        for e in errors:
            print(f"  {e.get('ticker')}: {e.get('error')}")
        return

    df_result = pd.DataFrame(valid)
    before_clean = len(df_result)
    df_result["ticker"] = df_result["ticker"].astype(str).str.strip()
    df_result = df_result[
        df_result["ticker"].notna() & (df_result["ticker"] != "") &
        (df_result["ticker"].str.lower() != "nan") & df_result["score"].notna()
    ]
    if len(df_result) < before_clean:
        print(f"\n[정리] 종목코드/점수가 불완전한 {before_clean - len(df_result)}건을 제외했습니다.")
    df_result = df_result.sort_values("score", ascending=False).reset_index(drop=True)

    print("\n" + "=" * 70)
    print(" 국내+미국 통합 스크리닝 결과 (점수 높은 순)")
    print("=" * 70)
    for _, r in df_result.iterrows():
        name_val = r.get("name")
        name_disp = f" {name_val}" if pd.notna(name_val) and str(name_val).strip() else ""
        price_str = f"{r['last_price']:,.0f}원" if r["market"] == "KR" else f"${r['last_price']:,.2f}"
        vol_str = f"{r['avg_won_volume']:,.0f}원" if r["market"] == "KR" else f"${r['avg_won_volume']:,.0f}"
        print(f"\n[{r['score']:>5.1f}점] ({r['market']}) {r['ticker']}{name_disp}")
        print(f"  현재가: {price_str} | 변동폭: {r['avg_daily_range_pct']}% | "
              f"패닉비율: {r['panic_day_ratio_pct']}% | 거래대금: {vol_str}")
        print(f"  코멘트: {r['reasons']}")

    if errors:
        print("\n[분석 실패 종목]")
        for e in errors:
            print(f"  ({e.get('market','?')}) {e.get('ticker')}: {e.get('error')}")

    def _safe_to_csv(df, path, label):
        """CSV 저장 실패(다른 프로그램이 파일을 열어둔 경우 등)가 전체 스크리닝을
        중단시키지 않도록 재시도 후에도 실패하면 경고만 남기고 넘어감."""
        for attempt in range(3):
            try:
                df.to_csv(path, index=False, encoding="utf-8-sig")
                print(f"{label}: {path}")
                return
            except PermissionError as e:
                print(f"  [경고] {os.path.basename(path)} 쓰기 실패(다른 프로그램이 열어둔 상태일 수 있음), "
                      f"재시도 {attempt+1}/3: {e}")
                time_module.sleep(1.5)
            except Exception as e:
                print(f"  [경고] {os.path.basename(path)} 쓰기 중 오류(건너뜀): {e}")
                return
        print(f"  [경고] {os.path.basename(path)} 최종 저장 실패. 파일이 다른 프로그램에서 열려있지 않은지 확인해주세요.")

    out_path = os.path.join(BASE_DIR, "combined_screening_result.csv")
    _safe_to_csv(df_result, out_path, "결과 저장")

    os.makedirs(SCREENING_LOG_DIR, exist_ok=True)
    week_log_path = os.path.join(SCREENING_LOG_DIR, f"screening_{datetime.now().strftime('%Y_%m_%d')}.csv")
    _safe_to_csv(df_result, week_log_path, "주간 이력 로그")

    # watchlist.json 저장은 가장 중요한 산출물이므로, 위 CSV 저장이 실패해도 반드시 실행됨
    screen_write_watchlist(df_result)


# ============================================================
# 주간 재최적화 (--optimize)
# ============================================================
def opt_simulate_day(day_df, orb_bars, stop_pct, tp_trigger_pct, tp_max_pct):
    df = day_df.sort_values("datetime").reset_index(drop=True)
    if len(df) < orb_bars + 1:
        return None
    df["vwap"] = calc_vwap(df)
    df["rsi"] = calc_rsi(df)  # ⚠️ 실전 진입 로직과 동일하게, RSI 과매수 필터를 백테스트에도 반영
    orb_high = df.iloc[:orb_bars]["high"].max()
    rest = df.iloc[orb_bars:].reset_index(drop=True)
    entry_idx = None
    for i in range(len(rest)):
        row = rest.iloc[i]
        rsi_ok = pd.isna(row["rsi"]) or row["rsi"] < RSI_OVERBOUGHT_THRESHOLD
        if row["close"] > orb_high and row["close"] > row["vwap"] and rsi_ok:
            entry_idx = i
            break
    if entry_idx is None:
        return None
    entry_row = rest.iloc[entry_idx]
    buy_price = entry_row["close"]
    stop_price = buy_price * (1 - stop_pct)
    after = rest.iloc[entry_idx + 1:]
    ret = None
    for _, row in after.iterrows():
        if row["low"] <= stop_price:
            ret = -stop_pct
            break
        high_ret = (row["high"] - buy_price) / buy_price
        if high_ret >= tp_trigger_pct:
            ret = min(high_ret, tp_max_pct)
            break
    if ret is None:
        last = df.iloc[-1]
        close_ret = (last["close"] - buy_price) / buy_price
        ret = -stop_pct if close_ret <= -stop_pct else close_ret
    return ret * 100


MIN_TRADES_FOR_STABILITY = 5  # 이 건수 이상일 때만 "안정성" 기준을 신뢰함 (너무 적으면 통계적으로 무의미)


def opt_run_grid_search(day_dataframes):
    """총수익률 합계가 아니라 "꾸준함"(평균수익률 ÷ 수익률 변동폭, 샤프비율과 비슷한 개념)이
    가장 높은 조합을 선택함. 즉 거래마다 들쭉날쭉 크게 남기는 조합보다,
    거래마다 비슷하게 조금씩이라도 꾸준히 남기는 조합을 우선시함."""
    if not day_dataframes:
        return None, 0, 0

    best_combo, best_stability, best_trades, best_return = None, -float("inf"), 0, 0
    fallback_combo, fallback_return, fallback_trades = None, -float("inf"), 0  # 안정성 기준 후보가 전혀 없을 때 대비

    for orb_bars, stop_pct, tp_trigger, tp_max in itertools.product(
            OPT_GRID_ORB_BARS, OPT_GRID_STOP_PCT, OPT_GRID_TP_TRIGGER, OPT_GRID_TP_MAX):
        if tp_trigger > tp_max:
            continue
        returns = []
        for ticker, date_str, df in day_dataframes:
            r = opt_simulate_day(df, orb_bars, stop_pct, tp_trigger, tp_max)
            if r is not None:
                returns.append(r)
        cnt = len(returns)
        if cnt == 0:
            continue

        total_ret = sum(returns)
        combo = {"orb_bars": orb_bars, "stop_pct": stop_pct,
                 "tp_trigger_pct": tp_trigger, "tp_max_pct": tp_max}

        # 총수익률 기준 폴백(안정성 기준 후보가 하나도 없을 경우를 대비)
        if total_ret > fallback_return:
            fallback_return, fallback_combo, fallback_trades = total_ret, combo, cnt

        if cnt < MIN_TRADES_FOR_STABILITY:
            continue  # 표본이 너무 적으면 안정성 계산 자체가 신뢰 못할 값이라 건너뜀

        avg_ret = total_ret / cnt
        std_ret = float(np.std(returns, ddof=1)) if cnt >= 2 else 0.0
        if std_ret > 0:
            stability = avg_ret / std_ret
        else:
            # 변동성이 0(모든 거래가 완전히 동일한 수익률) -> 평균이 양수면 최고로 안정적, 음수면 최악
            stability = float("inf") if avg_ret > 0 else -float("inf")

        if stability > best_stability:
            best_stability = stability
            best_combo, best_trades, best_return = combo, cnt, total_ret

    if best_combo is not None:
        return best_combo, best_trades, best_return
    if fallback_combo is not None:
        # 표본이 5건 이상인 조합이 하나도 없었던 경우 -> 총수익률 기준으로라도 값을 반환
        return fallback_combo, fallback_trades, fallback_return
    return None, 0, 0


def opt_fetch_us_data(ticker):
    if yf is None:
        return []
    try:
        df = yf.download(ticker, period=f"{OPT_US_LOOKBACK_CALENDAR_DAYS}d", interval="5m",
                          progress=False, auto_adjust=False, timeout=15)
    except Exception as e:
        print(f"  [{ticker}] 다운로드 실패: {e}")
        return []
    if df.empty:
        return []
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0] for c in df.columns]
    df = df.reset_index().rename(columns={
        "Datetime": "datetime", "Open": "open", "High": "high", "Low": "low", "Close": "close", "Volume": "volume"
    })
    if df["datetime"].dt.tz is not None:
        df["datetime"] = df["datetime"].dt.tz_localize(None)
    # ⚠️ ET(미국 동부시간) 그대로라 자정을 안 넘김 -> 단순 달력일 그룹핑이면 충분함
    # (예전엔 -12시간 보정을 잘못 적용해서, 하루치 세션이 정오를 기준으로 두 조각으로
    #  쪼개져 그리드서치에 왜곡된 '하루'가 들어가는 버그가 있었음)
    df["session_date"] = df["datetime"].dt.date
    result = []
    for sess, sess_df in df.groupby("session_date"):
        result.append((ticker, str(sess), sess_df.drop(columns=["session_date"]).reset_index(drop=True)))
    return result


def opt_fetch_kr_data_yfinance(ticker):
    """국내 종목도 yfinance(.KS/.KQ)로 최대 60일치 5분봉을 즉시 받아옴.
    KIS API와 달리 '당일'이라는 제약이 없어서, 신규로 뽑힌 종목도 바로 과거 데이터를 확보할 수 있음."""
    if yf is None:
        return []
    for suffix in [".KS", ".KQ"]:
        symbol = f"{ticker}{suffix}"
        try:
            df = yf.download(symbol, period=f"{OPT_US_LOOKBACK_CALENDAR_DAYS}d", interval="5m",
                              progress=False, auto_adjust=False, timeout=15)
        except Exception:
            continue
        if df.empty:
            continue
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [c[0] for c in df.columns]
        df = df.reset_index().rename(columns={
            "Datetime": "datetime", "Open": "open", "High": "high", "Low": "low", "Close": "close", "Volume": "volume"
        })
        if df["datetime"].dt.tz is not None:
            df["datetime"] = df["datetime"].dt.tz_localize(None)
        # 국내 종목은 정규장이 09:00~15:30로 자정을 안 넘기니, US처럼 세션 보정 없이 달력일 그대로 그룹핑
        df["date_only"] = df["datetime"].dt.date
        result = []
        for d, day_df in df.groupby("date_only"):
            result.append((ticker, str(d), day_df.drop(columns=["date_only"]).reset_index(drop=True)))
        if result:
            return result
    return []


def opt_load_kr_local_data(ticker):
    pattern = os.path.join(OPT_DATA_DIR, f"{ticker}_*.csv")
    files = sorted(glob.glob(pattern))
    result = []
    for f in files:
        date_str = os.path.basename(f).replace(f"{ticker}_", "").replace(".csv", "")
        try:
            df = pd.read_csv(f, parse_dates=["datetime"])
            result.append((ticker, date_str, df))
        except Exception:
            continue
    return result


def opt_load_kr_combined_data(ticker):
    """yfinance(최대 60일 과거) + 로컬 KIS 누적분(당일, 더 정확함)을 합침.
    같은 날짜가 겹치면 로컬(KIS 정식 데이터)을 우선함."""
    yf_days = opt_fetch_kr_data_yfinance(ticker)
    local_days = opt_load_kr_local_data(ticker)

    combined = {}
    for t, date_str, df in yf_days:
        # yfinance 날짜(YYYY-MM-DD)와 로컬 파일 날짜(YYYYMMDD) 형식을 통일
        key = date_str.replace("-", "")
        combined[key] = (t, date_str, df)
    for t, date_str, df in local_days:
        key = date_str.replace("-", "")
        combined[key] = (t, date_str, df)  # 로컬이 있으면 덮어써서 우선시킴

    result = list(combined.values())
    print(f"    (yfinance {len(yf_days)}일 + 로컬 {len(local_days)}일 -> 중복제거 후 {len(result)}일)")
    return result


def opt_optimize_one_ticker(ticker, day_data, min_days_required=1):
    """min_days_required를 1로 낮춰서, 데이터가 조금만 있어도(심지어 1거래일이라도)
    바로 그 데이터로 최적화를 시도함. 대신 표본이 적을 땐 note에 신뢰도를 명시해서
    "적어도 기본값보다는 낫지만, 아직 확신하긴 이르다"는 걸 알 수 있게 함."""
    if len(day_data) < min_days_required:
        params = dict(OPT_DEFAULT_PARAMS)
        params["note"] = f"데이터 전무({len(day_data)}거래일), 기본값 유지"
        return params

    best_combo, best_trades, best_return = opt_run_grid_search(day_data)
    if best_combo is None:
        params = dict(OPT_DEFAULT_PARAMS)
        params["note"] = f"그리드서치 결과 없음({len(day_data)}거래일 데이터로도 신호 없음), 기본값 유지"
        return params

    if len(day_data) < 5:
        confidence = f"⚠️ 신뢰도 낮음(표본 {len(day_data)}거래일뿐, 데이터 축적중)"
    elif len(day_data) < 10:
        confidence = f"신뢰도 보통(표본 {len(day_data)}거래일)"
    else:
        confidence = f"신뢰도 양호(표본 {len(day_data)}거래일)"

    params = dict(best_combo)
    basis = "안정성(꾸준함) 기준" if best_trades >= MIN_TRADES_FOR_STABILITY else \
            f"거래{best_trades}건뿐이라 안정성 계산 불가, 총수익률 기준으로 대체 선택"
    params["note"] = f"주간 재최적화 [{basis}] (거래{best_trades}건, 총수익률{best_return:.2f}%) - {confidence}"
    params["sample_days"] = len(day_data)
    return params


def run_optimization_mode():
    print("=" * 70)
    print(" [--optimize] 주간 ORB+VWAP 파라미터 재최적화 (종목별 개별)")
    print("=" * 70)

    if not os.path.exists(WATCHLIST_PATH):
        print(f"{WATCHLIST_PATH} 가 없습니다. 먼저 --screen을 실행해주세요.")
        return

    with open(WATCHLIST_PATH, "r", encoding="utf-8") as f:
        watchlist = json.load(f)
    kr_tickers = list(watchlist.get("kr_tickers", {}).keys())
    us_tickers = list(watchlist.get("us_tickers", {}).keys())
    print(f"이번 주 국내 종목({len(kr_tickers)}개): {kr_tickers}")
    print(f"이번 주 미국 종목({len(us_tickers)}개): {us_tickers}\n")

    per_ticker_params = {}

    print("[미국 종목 개별 최적화]")
    if yf is None:
        print("yfinance가 없어 미국 종목은 전부 기본값으로 채웁니다.")
        for t in us_tickers:
            p = dict(OPT_DEFAULT_PARAMS)
            p["note"] = "yfinance 미설치로 최적화 불가, 기본값 유지"
            per_ticker_params[t] = p
    else:
        for t in us_tickers:
            days = opt_fetch_us_data(t)
            print(f"  {t}: {len(days)}거래일 확보 -> 그리드서치 중...")
            params = opt_optimize_one_ticker(t, days)
            per_ticker_params[t] = params
            print(f"    -> {params}\n")

    print("[국내 종목 개별 최적화]")
    for t in kr_tickers:
        print(f"  {t}: 데이터 확보 중 (yfinance 과거이력 + 로컬 KIS 누적분)...")
        days = opt_load_kr_combined_data(t)
        print(f"  {t}: 총 {len(days)}거래일 확보 -> 그리드서치 중...")
        params = opt_optimize_one_ticker(t, days)
        per_ticker_params[t] = params
        print(f"    -> {params}\n")

    output = {"generated_at": datetime.now().isoformat(), "default": dict(OPT_DEFAULT_PARAMS), "per_ticker": {}}
    for ticker, p in per_ticker_params.items():
        entry = dict(p)
        entry["orb_minutes"] = entry["orb_bars"] * 5
        output["per_ticker"][ticker] = entry

    with open(STRATEGY_PARAMS_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    os.makedirs(PARAMS_LOG_DIR, exist_ok=True)
    week_log_path = os.path.join(PARAMS_LOG_DIR, f"params_{datetime.now().strftime('%Y_%m_%d')}.json")
    with open(week_log_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n[저장 완료] {STRATEGY_PARAMS_PATH}")
    print(f"[주간 이력 저장] {week_log_path}")
    print("\n종목별 최종 파라미터:")
    for ticker, p in output["per_ticker"].items():
        print(f"  {ticker}: 초기박스={p['orb_minutes']}분, 손절={p['stop_pct']*100:.1f}%, "
              f"익절트리거={p['tp_trigger_pct']*100:.1f}%, 상한={p['tp_max_pct']*100:.1f}%  ({p['note']})")

    print("\n⚠️ 이 파라미터들은 종목별로 지난 2~3주 데이터에 사후 최적화된 값입니다.")
    print("   매주 재조정되므로 과최적화 위험이 있습니다. 결과를 계속 지켜보세요.")



def _dispatch_mode():
    """명령행 인수로 실행 모드를 선택.
    --screen: 종목 자동 선별만, --optimize: 파라미터 재최적화만,
    --weekly: 위 둘을 순서대로, 인수 없음: 평소 매매 체크(main).
    단, 인수가 없더라도 일요일 22:00~22:09 사이라면(그리고 오늘 아직 안 돌았다면)
    자동으로 --weekly와 동일하게 동작함 -> 스케줄러에 트리거를 하나만 두면 됨."""
    args = sys.argv[1:]
    if "--weekly" in args:
        print(">>> --weekly 모드(수동 지정): 스크리닝 먼저 실행 후 이어서 최적화를 실행합니다.\n")
        run_screening_mode()
        print("\n" + "=" * 70)
        print(" 스크리닝 종료, 이어서 재최적화를 시작합니다")
        print("=" * 70 + "\n")
        run_optimization_mode()
    elif "--screen" in args:
        run_screening_mode()
    elif "--optimize" in args:
        run_optimization_mode()
    elif _auto_weekly_now:
        print(f">>> 일요일 {_dt.now().strftime('%H:%M')} 자동 감지: --weekly와 동일하게 "
              f"스크리닝+재최적화를 자동 수행합니다 (오늘 첫 실행).\n")
        run_screening_mode()
        print("\n" + "=" * 70)
        print(" 스크리닝 종료, 이어서 재최적화를 시작합니다")
        print("=" * 70 + "\n")
        run_optimization_mode()
        _mark_weekly_ran_today()
        print("\n[표시 완료] 오늘(이번 주 일요일)은 이 자동 실행을 다시 반복하지 않습니다.")
    else:
        main()


if __name__ == "__main__":
    try:
        _dispatch_mode()
    except Exception:
        import traceback
        print("\n오류 발생:")
        traceback.print_exc()
    finally:
        print(f" 실행 종료: {datetime.now()}")
        print(f"{'='*60}\n")
        # 주의: 예전엔 여기서 sys.stdin.isatty() 체크 후 input()으로 대기했으나,
        # 작업 스케줄러 실행 환경에서 isatty()가 예상과 다르게 True로 나와서
        # 실제로 4분씩 멈춰 서있는 심각한 문제가 있었음(watchdog에 의해서만 겨우 종료됨).
        # 결과는 trading_log_YYYY_MM_DD.txt 파일로 항상 확인 가능하므로, 더 이상
        # 화면에서 Enter를 기다리지 않고 즉시 종료함.
        sys.exit(0)
