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
from datetime import datetime, timedelta, time as dtime
import pandas as pd
import numpy as np

BASE_DIR = r"C:\TradingBot"
NH_CONFIG_PATH = os.path.join(BASE_DIR, "nh_config.json")
NH_STATE_PATH = os.path.join(BASE_DIR, "nh_position_state.json")
WATCHLIST_PATH = os.path.join(BASE_DIR, "watchlist.json")
# KIS 봇(auto_trading_bot.py)과 같은 Log/, Order/ 폴더를 그대로 씀 - 파일명에만 NH를 붙여서 구분
LOG_DIR = os.path.join(BASE_DIR, "Log")
ORDER_DIR = os.path.join(BASE_DIR, "Order")

MOCK_BASE_URL = "https://moapi.nhplug.com:8443"
REAL_BASE_URL = "https://api.nhplug.com:8443"

MARKET_OPEN = dtime(9, 0)
MARKET_CLOSE = dtime(15, 30)

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


def log_order_nh(action: str, ticker: str, price, shares: int, reason: str, extra: str = ""):
    """실제 체결된 주문을 Order/order_log_NH_YYYY_MM_DD.csv에 기록.
    -- auto_trading_bot.py의 log_order() 구조를 참고함(그대로 복사 아님, 카카오알림 등은 제외) --
    기록 실패가 매매 상태 처리를 막으면 안 되므로 절대 예외를 위로 던지지 않음"""
    today_str = datetime.now().strftime("%Y_%m_%d")
    order_log_path = os.path.join(ORDER_DIR, f"order_log_NH_{today_str}.csv")
    row = pd.DataFrame([{
        "timestamp": datetime.now().isoformat(), "action": action, "ticker": ticker,
        "price": price, "shares": shares, "reason": reason, "extra": extra,
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


def fetch_today_minute_bars(iem_cd: str, market_cd: str = "KRX", xtick: str = "1",
                             array_cnt: str = "120") -> pd.DataFrame:
    """오늘 분봉 데이터 조회 (POST /krstock/quote/v1/period, gubun=5 분봉)
    ⚠️ nhplug-sdk 저장소에는 이 엔드포인트의 예제 스니펫이 없어서 공식 명세(docs/krstock/openapi.json,
       scripts/fetch_docs.py로 www.nhplug.com에서 직접 내려받아 확인)만 보고 구현함.
       명세 자체에도 "Output_0는 Array로 선언됐지만 예시 응답은 Object"라는 경고가 있으므로
       실전에 쓰기 전에 반드시 실제 응답으로 한 번 검증해야 함.
    반환: datetime, open, high, low, close, volume 컬럼의 DataFrame (과거->현재 순 정렬)"""
    if not NHPLUG_AVAILABLE:
        print("[오류] nhplug 패키지 미설치로 조회 불가")
        return pd.DataFrame()
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
    except Exception as e:
        print(f"[오류] 분봉 조회 실패: {e}")
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

    open_dt = df["datetime"].iloc[0].normalize() + pd.Timedelta(hours=9)
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


def load_nh_state(tickers: list) -> dict:
    """포지션 상태 로드 + 날짜가 바뀌면 일일 카운터만 리셋(청산 안 된 포지션은 유지).
    -- auto_trading_bot.py의 load_state() 구조를 참고함(그대로 복사 아님) --"""
    if os.path.exists(NH_STATE_PATH):
        with open(NH_STATE_PATH, "r", encoding="utf-8") as f:
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

    now = datetime.now()
    is_weekday = now.weekday() < 5
    in_market_hours = MARKET_OPEN <= now.time() <= MARKET_CLOSE
    if not (is_weekday and in_market_hours):
        print(f"\n국내 정규장 시간이 아닙니다({MARKET_OPEN}~{MARKET_CLOSE}, 평일). 종료합니다.")
        return

    tickers = load_nh_watchlist()
    state = load_nh_state(tickers)

    print(f"\n[국내장 시간대 - {len(tickers)}종목 처리]")
    for ticker in tickers:
        if state["daily_pnl"] <= -circuit_breaker:
            print(f"  {ticker}: 서킷브레이커 발동(오늘 손실 {state['daily_pnl']:,.0f}원) - 신규 진입 중단")
            break
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
    print(f"\n오늘 NH 누적손익: {state['daily_pnl']:+,.0f}원")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback
        traceback.print_exc()
