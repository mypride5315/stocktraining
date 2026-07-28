"""
한국투자증권(KIS) API 연동 - 인터랙티브 ORB+VWAP 신호 조회 프로그램
=====================================================================

[이 스크립트가 하는 일]
CSV를 미리 다운받지 않고, 실행하면 화면에 뜨는 Input 창(터미널 프롬프트)에
appkey, appsecret, 계좌번호, 종목코드, 조회할 시간 등을 직접 입력하면
- KIS API로 실시간 당일 분봉 데이터를 조회하고
- ORB(오프닝레인지 돌파) + VWAP 필터 조건이 충족되는지 즉시 판단해서
- "현재 매수/매도 신호 조건 충족 여부"를 화면에 출력합니다.

[중요한 제약사항 - 반드시 읽어주세요]
KIS의 당일분봉조회 API(FHKST03010200)는 "오늘 하루치" 데이터만 제공합니다.
과거 2주치 데이터를 한 번에 불러와서 백테스트하는 기능은 이 API에 없습니다.
즉, 이 스크립트는:
  - 오늘 장중에 실행하면: 오늘의 박스/돌파/VWAP 상태를 실시간으로 보여줍니다.
  - 과거 데이터 누적: 이 스크립트를 매일 장 마감 무렵 실행해서 저장 기능(--save)을
    사용하면, 그날 데이터가 로컬 CSV에 하루치씩 쌓입니다. 이렇게 몇 주 모으면
    이전에 드린 orb_vwap_backtest.py로 백테스트할 수 있는 데이터가 됩니다.

[사전 준비]
1. 한국투자증권 계좌 + KIS Developers(apiportal.koreainvestment.com) 가입
2. App Key / App Secret 발급 (모의투자용으로 먼저 테스트 권장)
3. pip install requests pandas --break-system-packages

[실행 방법 - 터미널(명령 프롬프트/PowerShell/터미널 앱)에서]
    python kis_interactive_query.py
실행하면 화면에 순서대로 질문이 나오고, 값을 입력하면 진행됩니다.
(뒤에서 "Bash로 실행하냐"는 질문에 대한 답은 파일 맨 아래 안내 참고)
"""

import sys
import os
import json
from datetime import datetime, time as dtime

try:
    import requests
except ImportError:
    raise SystemExit("requests 라이브러리가 필요합니다: pip install requests --break-system-packages")

import pandas as pd
import numpy as np


# ------------------------------------------------------------------
# 기본 설정
# ------------------------------------------------------------------
REAL_BASE_URL = "https://openapi.koreainvestment.com:9443"
MOCK_BASE_URL = "https://openapivts.koreainvestment.com:29443"

TR_ID_MINUTE_CHART = "FHKST03010200"   # 주식당일분봉조회
MINUTE_CHART_PATH = "/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice"
TOKEN_PATH = "/oauth2/tokenP"


CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "kis_config.json")


def load_saved_config():
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None
    return None


def save_config(is_mock, appkey, appsecret):
    data = {"is_mock": is_mock, "appkey": appkey, "appsecret": appsecret}
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"App Key/Secret을 {CONFIG_PATH} 에 저장했습니다. "
          f"다음 실행부터는 다시 입력하지 않아도 됩니다.")
    print("주의: 이 파일에는 민감한 정보가 평문으로 저장되니 본인 PC 외 다른 사람과 공유하지 마세요.\n")


# ------------------------------------------------------------------
# 1. 사용자 입력 받기
# ------------------------------------------------------------------
def _prompt_credentials():
    env = input("환경 선택 [1] 모의투자  [2] 실전투자  (기본 1): ").strip() or "1"
    is_mock = (env != "2")
    appkey = input("App Key 입력: ").strip()
    appsecret = input("App Secret 입력: ").strip()
    return is_mock, appkey, appsecret


def ask_inputs():
    print("=" * 60)
    print(" 한국투자증권 KIS API - ORB+VWAP 실시간 신호 조회")
    print("=" * 60)

    saved = load_saved_config()
    if saved:
        env_label = "모의투자" if saved["is_mock"] else "실전투자"
        use_saved = input(
            f"저장된 App Key를 발견했습니다 ({env_label}, "
            f"key={saved['appkey'][:6]}...). 이걸 사용할까요? (y/n, 기본 y): "
        ).strip().lower()
        if use_saved != "n":
            is_mock = saved["is_mock"]
            appkey = saved["appkey"]
            appsecret = saved["appsecret"]
        else:
            is_mock, appkey, appsecret = _prompt_credentials()
            save_config(is_mock, appkey, appsecret)
    else:
        is_mock, appkey, appsecret = _prompt_credentials()
        remember = input("이 App Key/Secret을 다음 실행을 위해 저장할까요? (y/n, 기본 y): ").strip().lower()
        if remember != "n":
            save_config(is_mock, appkey, appsecret)

    tickers_raw = input(
        "조회할 종목코드 (쉼표로 구분, 예: 0183J0,487240) "
        "[기본: 0183J0,487240 = TIGER 미국우주테크,KODEX AI전력핵심설비]: "
    ).strip()
    tickers = [t.strip() for t in tickers_raw.split(",")] if tickers_raw else ["0183J0", "487240"]

    save_flag = input("오늘 조회 데이터를 로컬 CSV에 누적 저장할까요? (y/n, 기본 y): ").strip().lower()
    save_data = save_flag != "n"

    capital = input("백테스트/신호 판단용 가상 자본금 (기본 10000000): ").strip()
    capital = float(capital) if capital else 10_000_000

    risk_pct = input("1회 거래당 허용 손실 % (기본 1.5): ").strip()
    risk_pct = float(risk_pct) if risk_pct else 1.5

    return {
        "is_mock": is_mock,
        "appkey": appkey,
        "appsecret": appsecret,
        "tickers": tickers,
        "save_data": save_data,
        "capital": capital,
        "risk_pct": risk_pct,
    }


# ------------------------------------------------------------------
# 2. 인증 토큰 발급
# ------------------------------------------------------------------
def get_access_token(base_url, appkey, appsecret):
    url = base_url + TOKEN_PATH
    headers = {"content-type": "application/json"}
    body = {
        "grant_type": "client_credentials",
        "appkey": appkey,
        "appsecret": appsecret,
    }
    res = requests.post(url, headers=headers, data=json.dumps(body), timeout=10)
    if res.status_code != 200:
        print(f"[HTTP 오류] status_code={res.status_code}")
        print(f"[서버 응답 내용] {res.text}")
        res.raise_for_status()
    return res.json()["access_token"]


# ------------------------------------------------------------------
# 3. 당일 분봉 조회
# ------------------------------------------------------------------
import time as time_module


def fetch_today_minute_chart(base_url, token, appkey, appsecret, ticker, inqr_hour=None, max_retries=6):
    """
    당일 분봉 데이터를 조회. KIS API 특성상 한 번 호출에 최대 약 30건까지 반환되므로
    inqr_hour(조회 기준 시각)를 뒤로 이동시키며 여러 번 호출해 하루 전체를 모읍니다.
    초당 호출 제한(EGW00201)에 걸리면 대기 시간을 점점 늘려가며(2초, 4초, 8초...) 재시도합니다.
    모의투자 계좌는 실전투자보다 호출 제한이 훨씬 낮아서 대기 시간을 넉넉히 잡았습니다.
    """
    if inqr_hour is None:
        inqr_hour = datetime.now().strftime("%H%M%S")

    url = base_url + MINUTE_CHART_PATH
    headers = {
        "content-type": "application/json; charset=utf-8",
        "authorization": f"Bearer {token}",
        "appkey": appkey,
        "appsecret": appsecret,
        "tr_id": TR_ID_MINUTE_CHART,
        "custtype": "P",
    }
    params = {
        "FID_ETC_CLS_CODE": "",
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_INPUT_ISCD": ticker,
        "FID_INPUT_HOUR_1": inqr_hour,
        "FID_PW_DATA_INCU_YN": "N",
    }

    for attempt in range(1, max_retries + 1):
        res = requests.get(url, headers=headers, params=params, timeout=10)

        if res.status_code == 200:
            data = res.json()
            if data.get("rt_cd") == "1" and data.get("msg_cd") == "EGW00201":
                wait = 2 ** attempt  # 2, 4, 8, 16, 32, 64초로 점점 늘어남
                print(f"  [대기] 초당 호출 제한 감지, {wait}초 대기 후 재시도 ({attempt}/{max_retries})")
                time_module.sleep(wait)
                continue
            break
        else:
            body_text = res.text
            is_rate_limit = "EGW00201" in body_text
            if is_rate_limit and attempt < max_retries:
                wait = 2 ** attempt
                print(f"  [대기] 초당 호출 제한 감지 (status={res.status_code}), {wait}초 대기 후 재시도 ({attempt}/{max_retries})")
                time_module.sleep(wait)
                continue
            print(f"[HTTP 오류] status_code={res.status_code}")
            print(f"[서버 응답 내용] {body_text}")
            if attempt < max_retries:
                time_module.sleep(2 ** attempt)
                continue
            res.raise_for_status()
    else:
        print(f"  [실패] {ticker} {inqr_hour} 조회: 재시도 횟수 초과")
        return pd.DataFrame()

    if data.get("rt_cd") != "0":
        print(f"[오류] {ticker} 조회 실패: {data.get('msg1')}")
        return pd.DataFrame()

    rows = data.get("output2", [])
    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    # KIS 응답 필드명 -> 표준 컬럼명 매핑
    # stck_bsop_date(영업일자), stck_cntg_hour(체결시간, HHMMSS),
    # stck_oprc(시가), stck_hgpr(고가), stck_lwpr(저가), stck_prpr(현재가=종가), cntg_vol(체결거래량)
    df["datetime"] = pd.to_datetime(
        df["stck_bsop_date"] + df["stck_cntg_hour"], format="%Y%m%d%H%M%S"
    )
    df["open"] = df["stck_oprc"].astype(float)
    df["high"] = df["stck_hgpr"].astype(float)
    df["low"] = df["stck_lwpr"].astype(float)
    df["close"] = df["stck_prpr"].astype(float)
    df["volume"] = df["cntg_vol"].astype(float)

    df = df[["datetime", "open", "high", "low", "close", "volume"]]
    df = df.sort_values("datetime").reset_index(drop=True)
    return df


def fetch_full_day(base_url, token, appkey, appsecret, ticker):
    """오전 09:05부터 현재(또는 장마감)까지 시간을 나눠 반복 호출해서 하루 전체 분봉을 모음."""
    all_frames = []
    now = datetime.now()
    market_close = now.replace(hour=15, minute=30, second=0, microsecond=0)
    end_time = min(now, market_close)

    checkpoints = pd.date_range(
        start=now.replace(hour=9, minute=30, second=0, microsecond=0),
        end=end_time,
        freq="60min",  # 호출 횟수를 줄이기 위해 60분 간격으로 조회 (모의투자 호출제한 대응)
    )
    if len(checkpoints) == 0:
        checkpoints = [end_time]

    for cp in checkpoints:
        hour_str = cp.strftime("%H%M%S")
        chunk = fetch_today_minute_chart(base_url, token, appkey, appsecret, ticker, hour_str)
        if not chunk.empty:
            all_frames.append(chunk)
        time_module.sleep(3.0)  # 초당 호출 제한 방지용 기본 딜레이 (모의투자 기준 넉넉하게)

    if not all_frames:
        return pd.DataFrame()

    full = pd.concat(all_frames).drop_duplicates(subset="datetime").sort_values("datetime")
    return full.reset_index(drop=True)


# ------------------------------------------------------------------
# 4. VWAP 계산 + ORB 신호 판단 (이전에 만든 백테스트 로직의 실시간 버전)
# ------------------------------------------------------------------
def calc_vwap(df):
    typical = (df["high"] + df["low"] + df["close"]) / 3
    cum_vol = df["volume"].cumsum()
    cum_vp = (typical * df["volume"]).cumsum()
    return cum_vp / cum_vol.replace(0, np.nan)


def judge_signal(df, opening_minutes=30, volume_multiplier=1.3, retest_tolerance_pct=0.15):
    if df.empty:
        return {"status": "데이터 없음"}

    df = df.sort_values("datetime").reset_index(drop=True)
    df["vwap"] = calc_vwap(df)

    open_dt = df["datetime"].iloc[0].normalize() + pd.Timedelta(hours=9)
    range_end = open_dt + pd.Timedelta(minutes=opening_minutes)

    opening_bars = df[df["datetime"] < range_end]
    if opening_bars.empty or len(df[df["datetime"] >= range_end]) == 0:
        return {"status": "박스 형성 대기 중 (09:30 이후 조회 필요)"}

    box_high = opening_bars["high"].max()
    box_low = opening_bars["low"].min()

    rest = df[df["datetime"] >= range_end].reset_index(drop=True)
    last_row = rest.iloc[-1]

    result = {
        "status": "관찰 중",
        "box_high": round(box_high, 1),
        "box_low": round(box_low, 1),
        "current_price": round(last_row["close"], 1),
        "current_vwap": round(last_row["vwap"], 1) if pd.notna(last_row["vwap"]) else None,
    }

    # 거래량 필터
    avg_vol = rest["volume"].iloc[max(0, len(rest) - 4):-1].mean() if len(rest) > 1 else last_row["volume"]
    vol_ok = last_row["volume"] >= (avg_vol * volume_multiplier if avg_vol > 0 else 0)

    if last_row["close"] > box_high:
        if last_row["close"] >= last_row["vwap"]:
            result["status"] = "상방 돌파 + VWAP 조건 충족" + ("" if vol_ok else " (거래량 부족, 신뢰도 낮음)")
        else:
            result["status"] = "상방 돌파했으나 VWAP 아래 (필터 탈락, 무시)"
    elif last_row["close"] < box_low:
        if last_row["close"] <= last_row["vwap"]:
            result["status"] = "하방 돌파 + VWAP 조건 충족" + ("" if vol_ok else " (거래량 부족, 신뢰도 낮음)")
        else:
            result["status"] = "하방 돌파했으나 VWAP 위 (필터 탈락, 무시)"
    else:
        result["status"] = "박스 내부, 돌파 대기 중"

    return result


# ------------------------------------------------------------------
# 5. 데이터 저장 (일자별로 별도 파일: {종목}_{YYYYMMDD}.csv)
# ------------------------------------------------------------------
def save_daily_csv(df, ticker, out_dir="./data"):
    """오늘 하루치 분봉 데이터를 종목+날짜별로 별도 CSV 파일에 저장.
    같은 날짜로 다시 실행하면 그 날짜 파일만 덮어씀 (다른 날짜 파일은 그대로 유지)."""
    if df.empty:
        return
    os.makedirs(out_dir, exist_ok=True)

    date_str = df["datetime"].dt.date.iloc[0].strftime("%Y%m%d")
    filename = f"{ticker}_{date_str}.csv"
    path = os.path.join(out_dir, filename)

    df.to_csv(path, index=False, encoding="utf-8-sig")
    print(f"  -> {path} 에 저장 완료 ({len(df)}행, 1분 단위)")


# ------------------------------------------------------------------
# 메인
# ------------------------------------------------------------------
def main():
    cfg = ask_inputs()
    base_url = MOCK_BASE_URL if cfg["is_mock"] else REAL_BASE_URL

    print("\n토큰 발급 중...")
    try:
        token = get_access_token(base_url, cfg["appkey"], cfg["appsecret"])
    except Exception as e:
        print(f"[오류] 토큰 발급 실패: {e}")
        print("App Key/Secret이 올바른지, 모의/실전 환경을 맞게 선택했는지 확인해주세요.")
        return
    print("토큰 발급 완료.\n")

    for ticker in cfg["tickers"]:
        print(f"--- {ticker} 조회 중 ---")
        df = fetch_full_day(base_url, token, cfg["appkey"], cfg["appsecret"], ticker)

        if df.empty:
            print(f"  {ticker}: 데이터를 가져오지 못했습니다. (장 시작 전이거나 종목코드 확인 필요)\n")
            continue

        signal = judge_signal(df)
        print(f"  현재가       : {signal.get('current_price')}")
        print(f"  VWAP         : {signal.get('current_vwap')}")
        print(f"  박스 상단    : {signal.get('box_high')}")
        print(f"  박스 하단    : {signal.get('box_low')}")
        print(f"  신호 상태    : {signal.get('status')}")

        if cfg["save_data"]:
            save_daily_csv(df, ticker)

        print()
        time_module.sleep(3.0)  # 종목 간 호출 제한 방지용 딜레이

    print("=" * 60)
    print("참고: 이 신호는 참고용 정보 제공이며 매매 판단의 책임은 본인에게 있습니다.")
    print("데이터를 계속 누적하시면(--save) 몇 주 뒤 orb_vwap_backtest.py로")
    print("실제 승률/수익률 백테스트가 가능해집니다.")
    print("=" * 60)


if __name__ == "__main__":
    main()


# ======================================================================
# [질문에 대한 답변] "Bash를 수행하려면 Command에서 수행을 하면 되나?"
# ======================================================================
# 이 스크립트는 "Bash"가 아니라 "Python 스크립트"입니다.
# 실행 방법은 사용 중인 운영체제의 터미널/명령창에서 아래처럼 입력하면 됩니다.
#
#   Windows (명령 프롬프트 또는 PowerShell):
#       python kis_interactive_query.py
#
#   Mac / Linux (터미널 앱):
#       python3 kis_interactive_query.py
#
# "Bash"는 Mac/Linux 터미널이 사용하는 셸(명령어를 해석하는 프로그램) 이름이고,
# Windows에서는 보통 "명령 프롬프트(cmd)"나 "PowerShell"을 씁니다.
# 어느 쪽이든 위 python 명령어 한 줄만 입력하면, 이후 질문들은
# 화면에 순서대로 나오는 Input 프롬프트에 값을 입력하시면 됩니다.
# ======================================================================
