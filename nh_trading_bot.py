# -*- coding: utf-8 -*-
"""
NH투자증권(나무Plug) API 연동 - 개발 중 (KIS 봇과 완전히 별도)
=====================================================================
⚠️ 이 파일은 auto_trading_bot.py(KIS)와 전혀 무관한 별도의 병행 개발 파일입니다.
   기존 KIS 매매봇은 이 파일과 상관없이 그대로 계속 작동합니다.

⚠️ 현재 상태: "시세 조회"까지만 구현됨. 주문(매수/매도)은 아직 미구현.
   -> nhplug-sdk 저장소의 snippets/ 폴더에서 주문 관련 예제를 찾아서
      정확한 URL/파라미터를 확인한 뒤 이어서 구현할 예정.

[사전 준비 - 아직 안 하셨다면]
1. https://www.nhplug.com/intro 에서 앱키/앱시크릿 발급 신청
2. pip install nhplug --break-system-packages
3. 아래 NH_CONFIG_PATH 파일을 만들어서 앱키/시크릿 저장

[nh_config.json 형식 예시]
{
  "app_key": "발급받은_APP_KEY",
  "app_secret": "발급받은_APP_SECRET",
  "is_mock": true
}
"""

import os
import json
import pandas as pd
import numpy as np

BASE_DIR = r"C:\TradingBot"
NH_CONFIG_PATH = os.path.join(BASE_DIR, "nh_config.json")

MOCK_BASE_URL = "https://moapi.nhplug.com:8443"
REAL_BASE_URL = "https://api.nhplug.com:8443"

try:
    from nhplug import call
    NHPLUG_AVAILABLE = True
except ImportError:
    NHPLUG_AVAILABLE = False
    print("[알림] nhplug 패키지가 설치되어 있지 않습니다.")
    print("       pip install nhplug --break-system-packages 로 설치해주세요.")


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


def main():
    print("=" * 60)
    print(" NH투자증권(나무Plug) 연동 테스트 - 개발 중")
    print("=" * 60)

    if not NHPLUG_AVAILABLE:
        return

    cfg = load_nh_config()
    if cfg is None:
        return

    if cfg.get("app_key", "").startswith("여기에"):
        print("[알림] nh_config.json에 아직 실제 앱키/시크릿을 안 넣으셨습니다.")
        return

    # nhplug SDK는 환경변수로 앱키/시크릿/모의투자 전환을 관리함
    os.environ["NHPLUG_APP_KEY"] = cfg["app_key"]
    os.environ["NHPLUG_APP_SECRET"] = cfg["app_secret"]
    if cfg.get("is_mock", True):
        os.environ["NHPLUG_BASE_URL"] = MOCK_BASE_URL
        print("[모드] 모의투자(moapi)로 접속합니다.")
    else:
        print("[모드] ⚠️ 실전(운영) 서버로 접속합니다.")

    print("\n삼성전자(005930) 현재가 조회 테스트...")
    result = get_current_price("005930")
    print(f"결과: {result}")

    print("\n매수 주문 형식 확인(dry_run, 실제 전송 안 함)...")
    account_no = cfg.get("account_no", "")
    if account_no and not account_no.startswith("여기에"):
        dry_result = order_cash_buy(account_no, "005930", 1, orr_pr=70000, dry_run=True)
        print(f"주문 payload 확인: {dry_result}")
        print("실제로 주문을 넣고 싶으면, order_cash_buy(...) 호출 시 dry_run=False로 바꿔서 별도 스크립트로 실행하세요.")
    else:
        print("계좌번호가 아직 설정 안 되어 매수 테스트는 건너뜁니다.")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback
        traceback.print_exc()
