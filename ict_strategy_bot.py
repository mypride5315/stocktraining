# -*- coding: utf-8 -*-
"""
ICT/SMC 스타일 단타 전략 - 병행 개발 중 (ORB+VWAP 봇과 완전히 별도)
=====================================================================
⚠️ 이 파일은 auto_trading_bot.py(ORB+VWAP)와 전혀 무관한 별도의 병행 개발 파일입니다.
   기존 ORB+VWAP 매매봇은 이 파일과 상관없이 그대로 계속 작동합니다.

⚠️ 현재 상태: "패턴 탐지 함수"까지만 구현됨. 아직 진입/청산 로직, 백테스트,
   실전 매매 연동은 미구현. 먼저 각 패턴 탐지 함수들이 정확한지 검증부터 진행.

[구현 상태]
✅ 1시간봉 추세 확인       - 명확한 기준으로 구현 가능해서 완료
✅ Fair Value Gap(FVG) 탐지 - 수학적으로 명확히 정의되어 완료
✅ 스윙 고점/저점 탐지      - 피보나치 계산의 기반, 완료
✅ 피보나치 되돌림/확장 레벨 계산 - 완료
⬜ 유동성 사냥(손절 유도 후 반등) 탐지 - 기준값 설계 필요 (아래 TODO 참고)
⬜ 수요존/공급존 탐지       - 기준값 설계 필요 (아래 TODO 참고)
⬜ 진입/청산 로직 결합
⬜ 백테스트/그리드서치 (ORB+VWAP과는 완전히 다른 파라미터 세트 필요)

[TODO - 다음에 설계 논의가 필요한 부분]
1. 유동성 사냥: "박스권 하단을 몇 % 이탈했다가, 몇 봉 이내에 회복해야
   유동성 사냥으로 인정할지" 기준값을 정해야 함
2. 수요존/공급존: "몇 봉 연속 몇 % 이상 움직여야 강한 매수세(존)로 인정할지"
   기준값을 정해야 함
"""

import pandas as pd
import numpy as np


# ============================================================
# 1. 1시간봉 추세 확인
# ============================================================
def check_1h_trend(df_1h, sma_period=20):
    """1시간봉 기준 추세 판단. 종가가 이동평균 위/아래인지 + 최근 고점·저점이
    계속 높아지고/낮아지고 있는지를 함께 봄.
    반환값: "up", "down", "neutral" 중 하나"""
    if len(df_1h) < sma_period + 5:
        return "neutral"  # 데이터 부족

    df = df_1h.copy()
    df["sma"] = df["close"].rolling(sma_period).mean()
    latest = df.iloc[-1]

    if pd.isna(latest["sma"]):
        return "neutral"

    above_sma = latest["close"] > latest["sma"]

    # 최근 5봉의 고점/저점이 각각 이전 5봉보다 높은지/낮은지(higher highs/higher lows 느낌)
    recent = df.tail(5)
    prior = df.tail(10).head(5)
    higher_highs = recent["high"].max() > prior["high"].max()
    higher_lows = recent["low"].min() > prior["low"].min()
    lower_highs = recent["high"].max() < prior["high"].max()
    lower_lows = recent["low"].min() < prior["low"].min()

    if above_sma and higher_highs and higher_lows:
        return "up"
    elif not above_sma and lower_highs and lower_lows:
        return "down"
    else:
        return "neutral"


# ============================================================
# 2. Fair Value Gap(FVG) 탐지
# ============================================================
def detect_fvg(df):
    """3개 캔들 단위로 Fair Value Gap을 탐지함.
    상승 FVG: (i+2)번째 캔들의 저가가 i번째 캔들의 고가보다 높음 -> 그 사이가 '빈틈'
    하락 FVG: (i+2)번째 캔들의 고가가 i번째 캔들의 저가보다 낮음 -> 그 사이가 '빈틈'
    반환값: [{"type": "bullish"/"bearish", "top": 상단가, "bottom": 하단가,
             "start_idx": 시작인덱스, "filled": bool}, ...] 리스트
    filled는 이후 캔들이 그 갭 구간을 다시 채웠는지(가격이 되돌아왔는지) 여부."""
    fvgs = []
    if len(df) < 3:
        return fvgs

    df = df.reset_index(drop=True)
    for i in range(len(df) - 2):
        c1, c3 = df.iloc[i], df.iloc[i + 2]

        # 상승 FVG: c3 저가가 c1 고가보다 높으면, 그 사이(c1고가 ~ c3저가)가 빈틈
        if c3["low"] > c1["high"]:
            gap = {"type": "bullish", "top": float(c3["low"]), "bottom": float(c1["high"]),
                   "start_idx": i, "filled": False}
            # 이후 캔들들이 이 구간을 다시 채웠는지(저가가 갭 하단까지 내려왔는지) 확인
            after = df.iloc[i + 3:]
            if len(after) > 0 and (after["low"] <= gap["bottom"]).any():
                gap["filled"] = True
            fvgs.append(gap)

        # 하락 FVG: c3 고가가 c1 저가보다 낮으면, 그 사이가 빈틈
        if c3["high"] < c1["low"]:
            gap = {"type": "bearish", "top": float(c1["low"]), "bottom": float(c3["high"]),
                   "start_idx": i, "filled": False}
            after = df.iloc[i + 3:]
            if len(after) > 0 and (after["high"] >= gap["top"]).any():
                gap["filled"] = True
            fvgs.append(gap)

    return fvgs


# ============================================================
# 3. 스윙 고점/저점 탐지 (피보나치 계산의 기반)
# ============================================================
def find_swing_points(df, lookback=5):
    """각 지점이 앞뒤 lookback개 봉보다 고가/저가가 더 높은/낮은지로 스윙 고점/저점 판단.
    반환값: (swing_high_idx_list, swing_low_idx_list)"""
    highs, lows = [], []
    n = len(df)
    for i in range(lookback, n - lookback):
        window_high = df["high"].iloc[i - lookback:i + lookback + 1]
        window_low = df["low"].iloc[i - lookback:i + lookback + 1]
        if df["high"].iloc[i] == window_high.max():
            highs.append(i)
        if df["low"].iloc[i] == window_low.min():
            lows.append(i)
    return highs, lows


# ============================================================
# 4. 피보나치 되돌림/확장 레벨 계산
# ============================================================
def calc_fibonacci_levels(swing_high, swing_low, direction="retracement"):
    """스윙 고점·저점을 기준으로 피보나치 레벨 계산.
    direction='retracement'면 되돌림 레벨(23.6~78.6%), 'extension'이면 확장 레벨(127.2~161.8%)."""
    diff = swing_high - swing_low
    if direction == "retracement":
        ratios = [0.236, 0.382, 0.5, 0.618, 0.786]
        return {f"{int(r*1000)/10}%": round(swing_high - diff * r, 4) for r in ratios}
    else:
        ratios = [1.272, 1.414, 1.618]
        return {f"{int(r*1000)/10}%": round(swing_high + diff * (r - 1), 4) for r in ratios}


# ============================================================
# 5. 오더블록 탐지 (상승형: 강한 상승 돌파 직전의 마지막 음봉)
# ============================================================
ORDERBLOCK_IMPULSE_PCT = 0.015  # 이 캔들 이후 몇 봉 안에 이 정도(1.5%) 이상 올라야 "강한 돌파"로 인정
ORDERBLOCK_IMPULSE_BARS = 3     # 몇 봉 이내에 그 상승이 나와야 하는지


def find_bullish_orderblock(df):
    """가장 최근에 확정된 '상승 오더블록'을 찾음: 마지막 음봉(종가<시가) 캔들 이후,
    ORDERBLOCK_IMPULSE_BARS봉 이내에 ORDERBLOCK_IMPULSE_PCT 이상 상승이 나온 지점.
    반환값: {"idx": 인덱스, "high": 오더블록 고가, "low": 오더블록 저가} 또는 None(못 찾음)"""
    df = df.reset_index(drop=True)
    n = len(df)
    for i in range(n - ORDERBLOCK_IMPULSE_BARS - 1, -1, -1):  # 최근 것부터 역순 탐색
        row = df.iloc[i]
        if row["close"] >= row["open"]:
            continue  # 음봉이 아니면 오더블록 후보 아님
        future = df.iloc[i + 1:i + 1 + ORDERBLOCK_IMPULSE_BARS]
        if future.empty:
            continue
        impulse_pct = (future["high"].max() - row["close"]) / row["close"]
        if impulse_pct >= ORDERBLOCK_IMPULSE_PCT:
            return {"idx": i, "high": float(row["high"]), "low": float(row["low"])}
    return None


# ============================================================
# 6. MSB(Market Structure Break, 구조 돌파) 탐지
# ============================================================
def check_msb(df, lookback=5):
    """최근 종가가, 직전 lookback봉 안의 스윙 고점을 종가 기준으로 넘었는지 확인.
    (저희 ORB+VWAP의 '박스상단 종가 돌파'와 개념적으로 거의 동일함)
    반환값: (돌파여부(bool), 기준이 된 직전 스윙고점 가격)"""
    if len(df) < lookback + 1:
        return False, None
    recent_high = df["high"].iloc[-(lookback + 1):-1].max()  # 마지막 봉 제외한 직전 lookback봉의 고점
    latest_close = df["close"].iloc[-1]
    return latest_close > recent_high, float(recent_high)




# ============================================================================
# ============================================================================
#   아래부터는 실전(모의) 매매봇 부분. auto_trading_bot.py의 검증된 KIS 연동
#   패턴(토큰발급, 주문, 로그 저장)을 그대로 재사용하고, 진입 로직만
#   "오더블록+MSB(3단계 ICT 전략)"으로 교체했습니다.
#   ORB+VWAP 봇(auto_trading_bot.py)과는 완전히 독립적으로 동작합니다.
#   (아래 코드는 이 파일 안에서 함수로만 존재하고, 실제 실행은 파일 맨 끝의
#    run_ict_bot() 호출부에서 이루어집니다)
# ============================================================================
# ============================================================================

import os
import sys
import json
import time as time_module
import requests
from datetime import datetime, time as dtime, date, timedelta
from zoneinfo import ZoneInfo

ICT_BASE_DIR = r"C:\TradingBot"
ICT_KIS_CONFIG_PATH = os.path.join(ICT_BASE_DIR, "kis_config.json")
ICT_USER_CONFIG_PATH = os.path.join(ICT_BASE_DIR, "user_config.json")
ICT_WATCHLIST_PATH = os.path.join(ICT_BASE_DIR, "watchlist.json")  # ORB+VWAP 봇과 동일한 종목 리스트 재사용
ICT_TOKEN_CACHE_PATH = os.path.join(ICT_BASE_DIR, "kis_token_cache.json")  # ⚠️ auto_trading_bot.py와 동일 파일을 공유함
# 예전엔 kis_token_cache_ICT.json으로 따로 썼는데, 같은 kis_config.json(같은 앱키)을 쓰는 이상
# 캐시 파일을 분리하면 안 됨: KIS는 앱키당 유효 토큰이 1개뿐이라, 한쪽 봇이 재발급받으면
# 다른 쪽이 로컬에 들고 있던 토큰이 서버에서 조용히 무효화되어 "기간이 만료된 token" 오류가 남
# (2026-08-19 로그로 확인됨). 캐시 파일을 공유하면 어느 쪽이 갱신하든 서로 최신 토큰을 씀.

# ICT 전략 전용 로그/주문/상태 폴더 - 기존 ORB+VWAP 봇과 절대 안 섞이도록 완전히 분리
ICT_LOG_DIR = os.path.join(ICT_BASE_DIR, "Log_ICT")
ICT_ORDER_DIR = os.path.join(ICT_BASE_DIR, "Order_ICT")
ICT_STATE_PATH = os.path.join(ICT_BASE_DIR, "position_state_ICT.json")
# 카카오톡 알림: auto_trading_bot.py가 kakao_setup.py로 이미 인증해둔 토큰 파일을 그대로 재사용
ICT_KAKAO_TOKEN_PATH = os.path.join(ICT_BASE_DIR, "kakao_token.json")

os.makedirs(ICT_LOG_DIR, exist_ok=True)
os.makedirs(ICT_ORDER_DIR, exist_ok=True)


def ict_trading_day_str(dt_obj=None):
    """자정이 아닌 새벽 5시를 하루의 경계로 삼음 (auto_trading_bot.py와 동일한 이유)"""
    d = dt_obj or datetime.now()
    return (d - timedelta(hours=5)).strftime("%Y_%m_%d")


ICT_LOG_PATH = os.path.join(ICT_LOG_DIR, f"ict_trading_log_{ict_trading_day_str()}.txt")


class _ICTTeeOutput:
    def __init__(self, path):
        self.terminal = sys.stdout
        self.log = open(path, "a", encoding="utf-8")

    def write(self, msg):
        self.terminal.write(msg)
        self.log.write(msg)

    def flush(self):
        self.terminal.flush()
        self.log.flush()


ICT_BASE_URL_MOCK = "https://openapivts.koreainvestment.com:29443"
ICT_BASE_URL_REAL = "https://openapi.koreainvestment.com:9443"

ICT_MARKET_OPEN = dtime(9, 0)
ICT_FORCE_CLOSE_TIME = dtime(15, 20)

# 해외(미국) 정규장: 뉴욕 시간 09:30~16:00 기준. zoneinfo로 뉴욕 현지시각을 직접 구해서
# 서머타임(EDT/EST) 전환을 하드코딩 없이 자동으로 반영함.
ICT_US_MARKET_OPEN_ET = dtime(9, 30)
ICT_US_MARKET_CLOSE_ET = dtime(16, 0)
ICT_US_FORCE_CLOSE_BEFORE_MIN = 10  # 장마감 10분 전부터는 신규 진입 안 함(국내장 15:20 컷과 동일한 취지)

# 해외 시세조회(EXCD)와 해외 주문(OVRS_EXCG_CD)은 코드 체계가 다름 -> 매핑 필요
ICT_US_CHART_TO_ORDER_EXCD = {"NAS": "NASD", "NYS": "NYSE", "AMS": "AMEX"}

# ⚠️ watchlist.json(auto_trading_bot.py가 생성, 이 파일과 무관하니 그쪽은 건드리지 않음)의
# us_tickers.<ticker>.exchange 필드가 실제 상장 거래소와 무관하게 항상 "AMEX"로 고정되어
# 저장되고 있음(2026-08-27 확인). 이걸 그대로 믿고 EXCD로 변환하면 NASDAQ/NYSE 종목까지
# 전부 AMS(AMEX)로 잘못 조회됨. 실제 상장 거래소를 아는 종목은 여기서 직접 오버라이드함 -
# watchlist.json 값보다 이 표가 항상 우선함. 새 종목이 추가되면 실제 거래소를 확인해서
# 여기 추가해주세요(모르면 경고를 남기고 watchlist.json 값으로 폴백함).
ICT_US_TICKER_EXCHANGE_OVERRIDE = {
    "NU": "NYS",     # Nu Holdings - NYSE
    "SNAP": "NYS",   # Snap Inc. - NYSE
    "INTC": "NAS",   # Intel Corporation - NASDAQ
    "PATH": "NYS",   # UiPath, Inc. - NYSE
    "AAL": "NAS",    # American Airlines Group - NASDAQ
}


def ict_get_us_market_status():
    """현재 시각 기준 미국 정규장이 열려있는지 뉴욕 현지시각으로 직접 판단.
    반환값: (열림여부(bool), 강제청산시각_지남여부(bool), 뉴욕현지시각(datetime))"""
    now_et = datetime.now(ZoneInfo("America/New_York"))
    is_weekday_et = now_et.weekday() < 5
    is_open = is_weekday_et and (ICT_US_MARKET_OPEN_ET <= now_et.time() <= ICT_US_MARKET_CLOSE_ET)
    force_close_dt = (datetime.combine(now_et.date(), ICT_US_MARKET_CLOSE_ET)
                       - timedelta(minutes=ICT_US_FORCE_CLOSE_BEFORE_MIN))
    force_close_time = force_close_dt.time()
    past_force_close = now_et.time() >= force_close_time
    return is_open, past_force_close, now_et

# 전략 파라미터 (오더블록+MSB, 손익비 규칙 그대로)
ICT_MSB_LOOKBACK = 5          # MSB 판단시 직전 몇 봉의 고점을 기준으로 볼지
ICT_RISK_REWARD_RATIO = 2.0   # 손절폭 대비 익절폭 배수 (영상에서 "최소 2배" 언급)
ICT_CAPITAL_PER_TICKER_KRW = 500_000  # ORB+VWAP 봇과 동일 조건으로 비교하기 위해 같은 금액 사용
ICT_CIRCUIT_BREAKER_KRW = 30_000

# 해외 종목은 현재가가 달러라서 국내와 같은 원화 자금을 그대로 나누면 안 됨(환율 미반영 시 수량이 크게 틀어짐).
# 실시간 환율 연동은 이번 범위에서 제외하고, 대신 해외 전용 달러 자금 한도를 별도로 둠.
# 필요하면 이 값을 직접 조정해서 쓰세요.
ICT_CAPITAL_PER_TICKER_USD = 400


def ict_load_kis_config():
    with open(ICT_KIS_CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def ict_load_user_config():
    with open(ICT_USER_CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def ict_load_watchlist_tickers():
    """ORB+VWAP 봇이 매주 선정한 국내 종목 리스트를 그대로 재사용 (같은 종목군으로
    두 전략을 비교하기 위함). watchlist.json이 없으면 빈 리스트 반환."""
    if not os.path.exists(ICT_WATCHLIST_PATH):
        print("[알림] watchlist.json이 없어 ICT 봇을 실행할 종목이 없습니다.")
        return []
    with open(ICT_WATCHLIST_PATH, "r", encoding="utf-8") as f:
        wl = json.load(f)
    return list(wl.get("kr_tickers", {}).keys())


def ict_load_us_watchlist_tickers():
    """watchlist.json의 'us_tickers' 항목을 읽음. 형식: {"AAPL": "NAS", "TSLA": "NAS", ...}
    값은 해외주식분봉조회용 거래소코드(NAS/NYS/AMS)여야 함.

    ⚠️ 실제로는 스크리닝 도구가 만든 watchlist.json이 값에 {"exchange": "AMEX", "score": ...}
    같은 객체를 넣는 경우가 있었음(2026-08-19 로그에서 확인됨, EXCD 자리에 dict가 그대로
    들어가 API가 거부함). 이걸 방어적으로 정규화함:
    1) 값이 dict면 "exchange" 키를 꺼냄
    2) 꺼낸 값이 주문용 4자리 코드(NASD/NYSE/AMEX)면 시세조회용 3자리 코드(NAS/NYS/AMS)로 변환

    ⚠️ 2026-08-27 추가 확인: watchlist.json의 exchange 필드 자체가 실제 상장 거래소와
    무관하게 항상 "AMEX"로 고정 저장되고 있어서(예: NASDAQ 종목인 INTC/AAL도 "AMEX"),
    위 정규화를 거쳐도 결국 전부 EXCD='AMS'로 잘못 조회되는 문제가 있었음. 그래서
    ICT_US_TICKER_EXCHANGE_OVERRIDE에 실제 거래소가 등록된 종목은 watchlist.json 값보다
    그 표를 최우선으로 씀 - 표에 없는 새 종목만 (부정확할 수 있는) watchlist.json 값으로 폴백함.

    아직 watchlist.json에 us_tickers가 없다면 빈 딕셔너리를 반환하니, 직접 추가해주세요.
    예: {"kr_tickers": {...}, "us_tickers": {"AAPL": "NAS", "SOXL": "AMS"}}"""
    if not os.path.exists(ICT_WATCHLIST_PATH):
        return {}
    with open(ICT_WATCHLIST_PATH, "r", encoding="utf-8") as f:
        wl = json.load(f)
    raw = wl.get("us_tickers", {})
    if not raw:
        print("[알림] watchlist.json에 'us_tickers'가 없어 해외장 종목이 없습니다. "
              "예: {\"us_tickers\": {\"AAPL\": \"NAS\"}} 형식으로 추가해주세요.")
        return {}

    order_to_chart = {
        "NASD": "NAS", "NYSE": "NYS", "AMEX": "AMS",
        "NASDAQ": "NAS",  # 전체 이름으로 넣는 경우 대응 (2026-08-19 로그에서 확인됨)
    }
    valid_chart_codes = {"NAS", "NYS", "AMS"}
    normalized = {}
    for ticker, value in raw.items():
        if ticker in ICT_US_TICKER_EXCHANGE_OVERRIDE:
            normalized[ticker] = ICT_US_TICKER_EXCHANGE_OVERRIDE[ticker]
            continue
        excd = value.get("exchange") if isinstance(value, dict) else value
        if not isinstance(excd, str):
            print(f"  [경고] {ticker}: watchlist.json의 거래소코드 값이 이상함({value!r}), 이 종목은 건너뜀")
            continue
        excd = excd.strip().upper()
        if excd in order_to_chart:
            excd = order_to_chart[excd]
        if excd not in valid_chart_codes:
            print(f"  [경고] {ticker}: 알 수 없는 거래소코드 '{excd}', 이 종목은 건너뜀 "
                  f"(NAS/NYS/AMS 중 하나여야 함)")
            continue
        normalized[ticker] = excd
    return normalized


def ict_get_base_url(is_mock):
    return ICT_BASE_URL_MOCK if is_mock else ICT_BASE_URL_REAL


def ict_get_token(cfg, is_mock):
    """auto_trading_bot.py의 get_token()과 완전히 동일한 캐시 파일 형식을 씀
    (access_token / expire_at(ISO문자열) / is_mock). 두 봇이 같은 kis_config.json(같은 앱키)을
    쓰는 이상, 캐시 파일도 같은 형식으로 공유해야 어느 쪽이 갱신해도 서로 최신 토큰을 씀."""
    if os.path.exists(ICT_TOKEN_CACHE_PATH):
        try:
            with open(ICT_TOKEN_CACHE_PATH, "r", encoding="utf-8") as f:
                cache = json.load(f)
            expire_at = datetime.fromisoformat(cache["expire_at"])
            if cache.get("is_mock") == is_mock and datetime.now() < expire_at:
                return cache["access_token"]
        except Exception:
            pass

    url = ict_get_base_url(is_mock) + "/oauth2/tokenP"
    body = {"grant_type": "client_credentials", "appkey": cfg["appkey"], "appsecret": cfg["appsecret"]}
    res = requests.post(url, headers={"content-type": "application/json"}, data=json.dumps(body), timeout=10)
    if res.status_code != 200:
        raise RuntimeError(f"토큰 발급 실패: {res.status_code} {res.text}")
    data = res.json()
    token = data["access_token"]
    expires_in = data.get("expires_in", 86400)
    expire_at = datetime.now() + timedelta(seconds=int(expires_in) - 300)
    with open(ICT_TOKEN_CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump({"access_token": token, "expire_at": expire_at.isoformat(), "is_mock": is_mock}, f)
    return token


def ict_get_hashkey(cfg, is_mock, body):
    url = ict_get_base_url(is_mock) + "/uapi/hashkey"
    headers = {"content-type": "application/json", "appkey": cfg["appkey"], "appsecret": cfg["appsecret"]}
    res = requests.post(url, headers=headers, data=json.dumps(body), timeout=10)
    if res.status_code == 200:
        return res.json().get("HASH")
    return None


def ict_fetch_today_data(cfg, token, is_mock, ticker):
    """당일 5분봉 조회 (ORB+VWAP 봇의 fetch_today_data와 동일한 방식)"""
    url = ict_get_base_url(is_mock) + "/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice"
    headers = {
        "content-type": "application/json; charset=utf-8", "authorization": f"Bearer {token}",
        "appkey": cfg["appkey"], "appsecret": cfg["appsecret"], "tr_id": "FHKST03010200", "custtype": "P",
    }
    params = {
        "FID_ETC_CLS_CODE": "", "FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": ticker,
        "FID_INPUT_HOUR_1": datetime.now().strftime("%H%M%S"), "FID_PW_DATA_INCU_YN": "Y",
    }
    try:
        res = requests.get(url, headers=headers, params=params, timeout=10)
        if res.status_code != 200:
            return pd.DataFrame()
        data = res.json()
        rows = data.get("output2", [])
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame(rows)
        df["datetime"] = pd.to_datetime(df["stck_bsop_date"] + df["stck_cntg_hour"], format="%Y%m%d%H%M%S")
        df["open"] = df["stck_oprc"].astype(float)
        df["high"] = df["stck_hgpr"].astype(float)
        df["low"] = df["stck_lwpr"].astype(float)
        df["close"] = df["stck_prpr"].astype(float)
        df["volume"] = df["cntg_vol"].astype(float)
        df = df.sort_values("datetime").reset_index(drop=True)
        return df[["datetime", "open", "high", "low", "close", "volume"]]
    except Exception as e:
        print(f"  [오류] 데이터 조회 실패: {e}")
        return pd.DataFrame()


def ict_fetch_today_data_overseas(cfg, token, is_mock, ticker, excd):
    """해외주식 당일 분봉 조회 (해외주식분봉조회[v1_해외주식-030], tr_id HHDFS76950200).
    excd: 해외주식분봉조회용 거래소코드(NAS/NYS/AMS 등, watchlist.json의 us_tickers 값)"""
    url = ict_get_base_url(is_mock) + "/uapi/overseas-price/v1/quotations/inquire-time-itemchartprice"
    headers = {
        "content-type": "application/json; charset=utf-8", "authorization": f"Bearer {token}",
        "appkey": cfg["appkey"], "appsecret": cfg["appsecret"], "tr_id": "HHDFS76950200", "custtype": "P",
    }
    params = {
        "AUTH": "", "EXCD": excd, "SYMB": ticker, "NMIN": "5",
        "PINC": "0",  # 0: 당일만 조회
        "NEXT": "", "NREC": "120", "FILL": "", "KEYB": "",
    }
    print(f"  [디버그] 요청 파라미터: {params}")  # 원인 파악용 임시 로그, 확인되면 제거 예정
    try:
        res = requests.get(url, headers=headers, params=params, timeout=10)
        if res.status_code != 200:
            print(f"  [오류] 해외 분봉 조회 HTTP {res.status_code}: {res.text[:300]}")
            return pd.DataFrame()
        data = res.json()
        # KIS는 API 자체 실패도 HTTP 200으로 응답하고 rt_cd/msg1에 사유를 담는 경우가 많음
        if data.get("rt_cd") not in (None, "0"):
            print(f"  [오류] 해외 분봉 조회 거부: rt_cd={data.get('rt_cd')} msg_cd={data.get('msg_cd')} "
                  f"msg1={data.get('msg1')}")
            return pd.DataFrame()
        rows = data.get("output2", [])
        if not rows:
            print(f"  [경고] 해외 분봉 조회는 성공했지만 output2가 비어있음 "
                  f"(EXCD={excd}, SYMB={ticker}, rt_cd={data.get('rt_cd')}, msg1={data.get('msg1')})")
            return pd.DataFrame()
        df = pd.DataFrame(rows)
        # kymd/khms: 한국기준일자/시간 (해외장이라도 한국시간 기준 컬럼을 그대로 씀 -> 국내봇과 시간 비교 일관성 유지)
        df["datetime"] = pd.to_datetime(df["kymd"] + df["khms"], format="%Y%m%d%H%M%S")
        df["open"] = df["open"].astype(float)
        df["high"] = df["high"].astype(float)
        df["low"] = df["low"].astype(float)
        df["close"] = df["last"].astype(float)
        df["volume"] = df["evol"].astype(float)
        df = df.sort_values("datetime").reset_index(drop=True)
        return df[["datetime", "open", "high", "low", "close", "volume"]]
    except Exception as e:
        print(f"  [오류] 해외 데이터 조회 실패: {e}")
        return pd.DataFrame()



def ict_place_order(cfg, token, is_mock, ticker, side, qty, price=0):
    """국내주식 주문 (auto_trading_bot.py의 place_order와 동일한 방식)"""
    url = ict_get_base_url(is_mock) + "/uapi/domestic-stock/v1/trading/order-cash"
    body = {
        "CANO": cfg["account_no"].split("-")[0], "ACNT_PRDT_CD": cfg["account_no"].split("-")[1],
        "PDNO": ticker, "ORD_DVSN": "01",
        "ORD_QTY": str(qty), "ORD_UNPR": str(int(price)) if price else "0",
    }
    tr_id = ("VTTC0802U" if side == "buy" else "VTTC0801U") if is_mock else \
            ("TTTC0802U" if side == "buy" else "TTTC0801U")
    headers = {
        "content-type": "application/json; charset=utf-8", "authorization": f"Bearer {token}",
        "appkey": cfg["appkey"], "appsecret": cfg["appsecret"], "tr_id": tr_id, "custtype": "P",
    }
    hashkey = ict_get_hashkey(cfg, is_mock, body)
    if hashkey:
        headers["hashkey"] = hashkey
    try:
        res = requests.post(url, headers=headers, data=json.dumps(body), timeout=10)
        if res.status_code != 200:
            return {"success": False, "msg1": f"HTTP {res.status_code}: {res.text}"}
        data = res.json()
        data["success"] = data.get("rt_cd") == "0"
        if not data["success"]:
            print(f"  [주문 거부됨] msg_cd={data.get('msg_cd')} msg1={data.get('msg1')}")
        return data
    except Exception as e:
        return {"success": False, "msg1": str(e)}


def ict_place_order_overseas(cfg, token, is_mock, ticker, ovrs_excg_cd, side, qty, price):
    """해외주식 주문 (해외주식 주문[v1_해외주식-001]). 지정가만 사용(ORD_DVSN='00').
    ovrs_excg_cd: 주문용 거래소코드(NASD/NYSE/AMEX 등, ICT_US_CHART_TO_ORDER_EXCD로 변환된 값)
    price: 반드시 0이 아닌 실제 가격을 넣어야 함(해외주식은 시장가 주문 체계가 국내와 달라
           이 봇에서는 안전하게 지정가만 사용, price=0이면 주문 거부됨)"""
    url = ict_get_base_url(is_mock) + "/uapi/overseas-stock/v1/trading/order"
    body = {
        "CANO": cfg["account_no"].split("-")[0], "ACNT_PRDT_CD": cfg["account_no"].split("-")[1],
        "OVRS_EXCG_CD": ovrs_excg_cd, "PDNO": ticker,
        "ORD_QTY": str(qty), "OVRS_ORD_UNPR": str(price),
        "CTAC_TLNO": "", "MGCO_APTM_ODNO": "", "SLL_TYPE": "00" if side == "sell" else "",
        "ORD_SVR_DVSN_CD": "0", "ORD_DVSN": "00",  # 00: 지정가
    }
    if ovrs_excg_cd not in ("NASD", "NYSE", "AMEX"):
        return {"success": False, "msg1": f"미지원 거래소코드: {ovrs_excg_cd} (현재 미국 거래소만 지원)"}
    tr_id = ("TTTT1002U" if side == "buy" else "TTTT1006U")
    if is_mock:
        tr_id = "V" + tr_id[1:]
    headers = {
        "content-type": "application/json; charset=utf-8", "authorization": f"Bearer {token}",
        "appkey": cfg["appkey"], "appsecret": cfg["appsecret"], "tr_id": tr_id, "custtype": "P",
    }
    hashkey = ict_get_hashkey(cfg, is_mock, body)
    if hashkey:
        headers["hashkey"] = hashkey
    try:
        res = requests.post(url, headers=headers, data=json.dumps(body), timeout=10)
        if res.status_code != 200:
            return {"success": False, "msg1": f"HTTP {res.status_code}: {res.text}"}
        data = res.json()
        data["success"] = data.get("rt_cd") == "0"
        if not data["success"]:
            print(f"  [주문 거부됨] msg_cd={data.get('msg_cd')} msg1={data.get('msg1')}")
        return data
    except Exception as e:
        return {"success": False, "msg1": str(e)}


def ict_load_state():
    if os.path.exists(ICT_STATE_PATH):
        with open(ICT_STATE_PATH, "r", encoding="utf-8") as f:
            state = json.load(f)
    else:
        state = {}
    today_str = (datetime.now() - timedelta(hours=5)).date().isoformat()
    if state.get("date") != today_str:
        state = {"date": today_str, "positions": {}, "daily_pnl": 0}
    state.setdefault("positions", {})
    state.setdefault("daily_pnl", 0)
    return state


def ict_save_state(state):
    with open(ICT_STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def ict_get_ticker_name(ticker, market):
    """카톡 메시지에 표시할 종목명을 watchlist.json에서 조회. 없으면 티커코드를 그대로 반환.
    (ict_load_watchlist_tickers/ict_load_us_watchlist_tickers는 코드만 반환해서 이름 정보가
     버려지므로, 표시용으로 별도 조회하는 가벼운 헬퍼 함수)"""
    if not os.path.exists(ICT_WATCHLIST_PATH):
        return ticker
    try:
        with open(ICT_WATCHLIST_PATH, "r", encoding="utf-8") as f:
            wl = json.load(f)
        key = "kr_tickers" if market == "domestic" else "us_tickers"
        entry = wl.get(key, {}).get(ticker, {})
        if isinstance(entry, dict):
            name = entry.get("name", "")
            return f"{ticker}({name})" if name and name != ticker else ticker
        return ticker
    except Exception:
        return ticker


# ============================================================
# 카카오톡 "나에게 보내기" 알림 (auto_trading_bot.py와 토큰 파일 공유, kakao_setup.py로 최초 인증)
# ============================================================
def _ict_kakao_refresh_access_token(token_data):
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
    if "refresh_token" in result:
        token_data["refresh_token"] = result["refresh_token"]
    with open(ICT_KAKAO_TOKEN_PATH, "w", encoding="utf-8") as f:
        json.dump(token_data, f, ensure_ascii=False, indent=2)
    return token_data


def ict_send_kakao_message(text):
    """ICT 봇 체결 알림을 카카오톡(나에게 보내기)으로 전송. 실패해도 예외를 던지지 않음
    (알림 실패가 실제 매매 로직을 막으면 안 되므로)."""
    if not os.path.exists(ICT_KAKAO_TOKEN_PATH):
        return  # 카카오 연동을 안 하신 경우, 조용히 건너뜀 (에러 아님)
    try:
        with open(ICT_KAKAO_TOKEN_PATH, "r", encoding="utf-8") as f:
            token_data = json.load(f)

        expire_at = datetime.fromisoformat(token_data["access_token_expire_at"])
        if datetime.now() >= expire_at - timedelta(minutes=5):
            refreshed = _ict_kakao_refresh_access_token(token_data)
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


def ict_log_order(action, ticker, price, shares, reason, extra=""):
    """체결 기록을 ICT 전용 Order_ICT 폴더에 저장 (ORB+VWAP 봇과 완전히 분리)"""
    today_str = ict_trading_day_str()
    order_log_path = os.path.join(ICT_ORDER_DIR, f"ict_order_log_{today_str}.csv")
    row = pd.DataFrame([{
        "timestamp": datetime.now().isoformat(), "action": action, "ticker": ticker,
        "price": price, "shares": shares, "reason": reason, "extra": extra, "strategy": "ICT_OrderBlock_MSB",
    }])
    try:
        header = not os.path.exists(order_log_path)
        row.to_csv(order_log_path, mode="a", index=False, header=header, encoding="utf-8-sig")
    except Exception as e:
        print(f"  [경고] 주문 로그 저장 실패(무시하고 계속 진행): {e}")


def ict_process_ticker(ticker, cfg, token, is_mock, state, market="domestic", excd=None):
    """오더블록+MSB 3단계 전략의 실제 매매 로직. 국내/해외 공용.
    1. 최근 데이터에서 상승 오더블록을 찾고
    2. MSB(직전 고점 종가돌파)가 뜨면 진입
    3. 손절=오더블록 저가, 익절=손절폭의 ICT_RISK_REWARD_RATIO배

    market: "domestic"(국내) 또는 "overseas"(해외, 현재 미국만 지원)
    excd: market="overseas"일 때 해외주식분봉조회용 거래소코드(NAS/NYS/AMS). watchlist.json의 us_tickers 값."""
    now = datetime.now()
    # 국내/해외 종목코드가 겹칠 가능성은 낮지만, state 키를 시장별로 분리해 혼선을 방지함
    state_key = ticker if market == "domestic" else f"US:{ticker}"
    pos = state["positions"].setdefault(state_key, {"in_position": False, "day_traded": False})

    if market == "domestic":
        df = ict_fetch_today_data(cfg, token, is_mock, ticker)
        force_close_time = ICT_FORCE_CLOSE_TIME
        force_closed_now = now.time() >= force_close_time
    else:
        df = ict_fetch_today_data_overseas(cfg, token, is_mock, ticker, excd)
        _, force_closed_now, _ = ict_get_us_market_status()

    if df.empty:
        print(f"  {ticker}: 데이터 없음")
        return

    latest = df.iloc[-1]

    # 보유 중일 때는 청산 체크(손절/익절/장마감)만 하면 되므로 최소 1행만 있어도 충분함.
    # 오더블록/MSB 탐지에 필요한 최소 봉수는 "신규 진입 판단" 시점에만 확인함.

    if pos["in_position"]:
        entry_price = pos["entry_price"]
        stop_price = pos["stop_price"]
        tp_price = pos["tp_price"]
        exit_reason = None
        if latest["low"] <= stop_price:
            exit_reason = "stop_loss"
        elif latest["high"] >= tp_price:
            exit_reason = "take_profit"
        elif force_closed_now:
            exit_reason = "time_close"

        if exit_reason:
            sell_price = float(latest["close"])
            shares = pos["shares"]
            if market == "domestic":
                result = ict_place_order(cfg, token, is_mock, ticker, "sell", shares, price=0)
            else:
                order_excd = ICT_US_CHART_TO_ORDER_EXCD.get(excd, excd)
                result = ict_place_order_overseas(cfg, token, is_mock, ticker, order_excd,
                                                   "sell", shares, price=sell_price)
            if not result.get("success"):
                print(f"  {ticker}: 매도 거부됨, 다음 주기 재시도")
                return
            pnl = (sell_price - entry_price) * shares
            state["daily_pnl"] += pnl
            ict_log_order("SELL", ticker, sell_price, shares, exit_reason, extra=f"pnl={pnl:.0f}|market={market}")
            print(f"  {ticker}: 매도 체결({exit_reason}) 현재가 {sell_price:,.2f}, 손익 {pnl:+,.2f}")
            market_label = "국내" if market == "domestic" else "해외"
            exit_label = {"stop_loss": "손절", "take_profit": "익절", "time_close": "장마감청산"}.get(
                exit_reason, exit_reason)
            ticker_display = ict_get_ticker_name(ticker, market)
            ict_send_kakao_message(
                f"[ICT봇 매도] {market_label} {ticker_display}\n"
                f"사유: {exit_label}\n"
                f"체결가: {sell_price:,.2f} x {shares}주\n"
                f"손익: {pnl:+,.2f}"
            )
            pos.update({"in_position": False})
        else:
            print(f"  {ticker}: 보유 중 (진입가 {entry_price:,.2f}, 현재 {latest['close']:,.2f}, "
                  f"손절가 {stop_price:,.2f}, 익절가 {tp_price:,.2f})")
        return

    if pos["day_traded"]:
        print(f"  {ticker}: 오늘 매매 이미 완료됨")
        return

    if state["daily_pnl"] <= -ICT_CIRCUIT_BREAKER_KRW:
        print(f"  {ticker}: 서킷브레이커 발동, 신규 진입 안 함")
        return
    if force_closed_now:
        return

    if len(df) < ICT_MSB_LOOKBACK + 2:
        print(f"  {ticker}: 신규 진입 판단엔 데이터 부족 (현재 {len(df)}봉)")
        return

    orderblock = find_bullish_orderblock(df.iloc[:-1])  # 마지막 봉(현재 진행중) 제외하고 탐색
    if orderblock is None:
        print(f"  {ticker}: 오더블록 미확인, 대기 중")
        return

    msb, ref_high = check_msb(df, lookback=ICT_MSB_LOOKBACK)
    if not msb:
        print(f"  {ticker}: 오더블록 확인됨(저가 {orderblock['low']:,.2f})이나 MSB 미발생, "
              f"현재가 {latest['close']:,.2f} vs 기준고점 {ref_high}")
        return

    buy_price = float(latest["close"])
    stop_price = orderblock["low"]
    if stop_price >= buy_price:
        print(f"  {ticker}: 오더블록 저가가 현재가보다 높아 손절 설정 불가, 진입 보류")
        return
    risk = buy_price - stop_price
    tp_price = buy_price + risk * ICT_RISK_REWARD_RATIO

    capital = ICT_CAPITAL_PER_TICKER_KRW if market == "domestic" else ICT_CAPITAL_PER_TICKER_USD
    shares = int(capital // buy_price)
    if shares <= 0:
        print(f"  {ticker}: 진입조건 충족했으나 자금 부족")
        return

    print(f"  {ticker}: 진입 신호! (오더블록 저가 {stop_price:,.2f}, MSB 돌파 {ref_high:,.2f}) "
          f"{buy_price:,.2f} x {shares}주")
    if market == "domestic":
        result = ict_place_order(cfg, token, is_mock, ticker, "buy", shares, price=0)
    else:
        order_excd = ICT_US_CHART_TO_ORDER_EXCD.get(excd, excd)
        result = ict_place_order_overseas(cfg, token, is_mock, ticker, order_excd,
                                           "buy", shares, price=buy_price)
    if not result.get("success"):
        print(f"  {ticker}: 매수 거부됨, 다음 주기 재시도")
        return
    pos.update({"in_position": True, "day_traded": True, "entry_price": buy_price,
                "shares": shares, "stop_price": stop_price, "tp_price": tp_price,
                "entry_time": now.isoformat()})
    ict_log_order("BUY", ticker, buy_price, shares, "orderblock_msb_breakout", extra=f"market={market}")
    # 손익비(1:N)만으로는 실제로 얼마를 벌고 잃는지 감이 안 와서, 예상 손실/수익 금액도 같이 계산함.
    max_loss_amount = (stop_price - buy_price) * shares
    max_gain_amount = (tp_price - buy_price) * shares
    unit = "원" if market == "domestic" else "달러"
    print(f"  {ticker}: 매수 체결 (손절가 {stop_price:,.2f}, 익절가 {tp_price:,.2f}, 손익비 1:{ICT_RISK_REWARD_RATIO}, "
          f"예상손익 {max_loss_amount:+,.0f}~{max_gain_amount:+,.0f}{unit})")
    market_label = "국내" if market == "domestic" else "해외"
    ticker_display = ict_get_ticker_name(ticker, market)
    ict_send_kakao_message(
        f"[ICT봇 매수] {market_label} {ticker_display}\n"
        f"체결가: {buy_price:,.2f} x {shares}주\n"
        f"손절가: {stop_price:,.2f} / 익절가: {tp_price:,.2f} (손익비 1:{ICT_RISK_REWARD_RATIO})\n"
        f"예상 손익: {max_loss_amount:+,.0f}{unit} ~ {max_gain_amount:+,.0f}{unit}"
    )


def run_ict_bot():
    sys.stdout = _ICTTeeOutput(ICT_LOG_PATH)
    print(f"\n{'='*60}")
    print(f" ICT(오더블록+MSB) 봇 실행 시각: {datetime.now()}")
    print(f"{'='*60}")

    import threading
    def _watchdog_exit():
        print("\n[경고] 실행 시간이 240초를 초과해 강제 종료합니다.")
        sys.stdout.flush()
        os._exit(1)
    watchdog = threading.Timer(240, _watchdog_exit)
    watchdog.daemon = True
    watchdog.start()

    try:
        cfg = ict_load_kis_config()
        user_cfg = ict_load_user_config()
        cfg["account_no"] = user_cfg["account_no"]
        is_mock = cfg.get("is_mock", True)
        print(f"모드: {'모의투자' if is_mock else '실전투자'} | 전략: 오더블록+MSB(ICT)")

        now = datetime.now()
        is_weekday_kst = now.weekday() < 5
        kr_market_hours = ICT_MARKET_OPEN <= now.time() <= dtime(15, 30)
        us_open, us_force_closed, us_now_et = ict_get_us_market_status()

        if not (is_weekday_kst and kr_market_hours) and not us_open:
            print(f"국내/해외 모두 장 시간이 아닙니다. 종료합니다. (참고: 현재 뉴욕시각 {us_now_et.strftime('%H:%M')})")
            return

        token = ict_get_token(cfg, is_mock)
        state = ict_load_state()

        if is_weekday_kst and kr_market_hours:
            tickers = ict_load_watchlist_tickers()
            if tickers:
                print(f"\n[국내 {len(tickers)}종목 처리 - ICT 오더블록+MSB 전략]")
                for ticker in tickers:
                    print(f"[{ticker}]")
                    try:
                        ict_process_ticker(ticker, cfg, token, is_mock, state, market="domestic")
                    except Exception as e:
                        import traceback
                        print(f"  [오류] {type(e).__name__}: {e}")
                        traceback.print_exc()
                    time_module.sleep(1.0)

        if us_open:
            us_tickers = ict_load_us_watchlist_tickers()
            if us_tickers:
                print(f"\n[해외(미국) {len(us_tickers)}종목 처리 - ICT 오더블록+MSB 전략, "
                      f"뉴욕시각 {us_now_et.strftime('%H:%M')}]")
                for ticker, excd in us_tickers.items():
                    print(f"[{ticker}]")
                    try:
                        ict_process_ticker(ticker, cfg, token, is_mock, state, market="overseas", excd=excd)
                    except Exception as e:
                        import traceback
                        print(f"  [오류] {type(e).__name__}: {e}")
                        traceback.print_exc()
                    time_module.sleep(1.0)

        ict_save_state(state)
        print(f"\n오늘 ICT 전략 누적손익: {state['daily_pnl']:+,.0f}원")
    except Exception:
        import traceback
        print("\n오류 발생:")
        traceback.print_exc()
    finally:
        print(f" 실행 종료: {datetime.now()}")
        sys.stdout.flush()


if __name__ == "__main__":
    run_ict_bot()
    sys.exit(0)
