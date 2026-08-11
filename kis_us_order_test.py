# -*- coding: utf-8 -*-
"""
KIS 해외주식(미국) 주문 API 직접 테스트 (진단용)
=====================================================================
매매봇을 통하지 않고, 딱 1주만 직접 주문을 넣어서 KIS 서버의
"원본 응답 전체"를 그대로 보여주는 진단 스크립트입니다.
반드시 미국 장 시간(한국시간 22:30~05:00)에 실행해야 의미 있는 결과가 나옵니다.

⚠️ 실제로 모의투자 주문이 나갑니다 (금액은 소액이지만, 실제 주문입니다).

[사용 방법]
    python kis_us_order_test.py
"""

import os
import json
import requests
from datetime import datetime

BASE_DIR = r"C:\TradingBot"
KIS_CONFIG_PATH = os.path.join(BASE_DIR, "kis_config.json")
TOKEN_CACHE_PATH = os.path.join(BASE_DIR, "kis_token_cache_TEST.json")  # 본체 봇 캐시와 별도 파일 사용

BASE_URL = "https://openapivts.koreainvestment.com:29443"  # 모의투자 서버

# 거래소 코드 후보 (모르면 순서대로 다 시도해봄)
EXCHANGE_CANDIDATES = ["NASD", "AMEX", "NYSE"]


def load_kis_config():
    with open(KIS_CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def get_token(cfg):
    if os.path.exists(TOKEN_CACHE_PATH):
        try:
            with open(TOKEN_CACHE_PATH, "r", encoding="utf-8") as f:
                cache = json.load(f)
            expire_at = cache.get("expire_at", 0)
            if isinstance(expire_at, (int, float)) and expire_at > datetime.now().timestamp():
                print("[정보] 캐시된 토큰 사용")
                return cache["token"]
        except Exception as e:
            print(f"[정보] 캐시 읽기 실패({e}), 새로 발급받습니다.")

    print("[정보] 새 토큰 발급 요청 중...")
    url = BASE_URL + "/oauth2/tokenP"
    body = {"grant_type": "client_credentials", "appkey": cfg["appkey"], "appsecret": cfg["appsecret"]}
    res = requests.post(url, headers={"content-type": "application/json"}, data=json.dumps(body), timeout=10)
    print(f"[토큰 발급 응답] status_code={res.status_code}")
    print(f"[토큰 발급 원본 응답] {res.text}")

    if res.status_code != 200:
        raise SystemExit("토큰 발급 실패, 여기서 원인을 확인하세요.")

    data = res.json()
    token = data["access_token"]
    expires_in = data.get("expires_in", 86400)
    with open(TOKEN_CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump({"token": token, "expire_at": datetime.now().timestamp() + expires_in - 300}, f)
    return token


def get_hashkey(cfg, body):
    url = BASE_URL + "/uapi/hashkey"
    headers = {"content-type": "application/json", "appkey": cfg["appkey"], "appsecret": cfg["appsecret"]}
    res = requests.post(url, headers=headers, data=json.dumps(body), timeout=10)
    print(f"[해시키 발급 응답] status_code={res.status_code}")
    print(f"[해시키 원본 응답] {res.text}")
    if res.status_code == 200:
        return res.json().get("HASH")
    return None


def try_order(cfg, token, cano, prdt_cd, ticker, qty, exchange):
    body = {
        "CANO": cano,
        "ACNT_PRDT_CD": prdt_cd,
        "OVRS_EXCG_CD": exchange,
        "PDNO": ticker,
        "ORD_QTY": str(qty),
        "OVRS_ORD_UNPR": "0",
        "ORD_SVR_DVSN_CD": "0",
        "ORD_DVSN": "00",  # 지정가(모의투자는 시장가 미지원인 경우가 많아 0원 지정가로 시도)
    }
    hashkey = get_hashkey(cfg, body)

    url = BASE_URL + "/uapi/overseas-stock/v1/trading/order"
    headers = {
        "content-type": "application/json; charset=utf-8",
        "authorization": f"Bearer {token}",
        "appkey": cfg["appkey"],
        "appsecret": cfg["appsecret"],
        "tr_id": "VTTT1002U",  # 모의투자 해외주식 매수
        "custtype": "P",
    }
    if hashkey:
        headers["hashkey"] = hashkey

    print(f"\n[{exchange}로 시도] 요청 본문: {json.dumps(body, ensure_ascii=False)}")
    res = requests.post(url, headers=headers, data=json.dumps(body), timeout=10)
    print(f"HTTP 상태 코드: {res.status_code}")
    try:
        result = res.json()
        print(f"응답: {json.dumps(result, ensure_ascii=False, indent=2)}")
        return result
    except Exception:
        print(f"응답(원문): {res.text}")
        return None


def main():
    print("=" * 70)
    print(" KIS 해외주식(미국) 주문 API 진단 테스트")
    print("=" * 70)

    ticker = input("테스트할 종목코드 입력 (기본 NVDA): ").strip().upper() or "NVDA"
    qty = input("테스트할 수량 입력 (기본 1): ").strip() or "1"

    cfg = load_kis_config()
    print(f"\n[설정 확인] appkey 앞 8자리: {cfg['appkey'][:8]}...")
    print(f"[설정 확인] is_mock: {cfg.get('is_mock')}")

    token = get_token(cfg)
    print(f"\n[토큰 확인] 앞 20자리: {token[:20]}...")

    user_cfg_path = os.path.join(BASE_DIR, "user_config.json")
    with open(user_cfg_path, "r", encoding="utf-8") as f:
        user_cfg = json.load(f)
    account_no = user_cfg["account_no"]
    cano, prdt_cd = account_no.split("-")
    print(f"[계좌 확인] CANO={cano}, ACNT_PRDT_CD={prdt_cd}")

    for exchange in EXCHANGE_CANDIDATES:
        result = try_order(cfg, token, cano, prdt_cd, ticker, qty, exchange)
        if result and result.get("rt_cd") == "0":
            print(f"\n[성공] {exchange}로 주문이 정상 처리됐습니다!")
            return
        print(f"[{exchange}] 실패, 다음 거래소 후보로 재시도...")

    print("\n모든 거래소 후보로 시도했으나 전부 실패했습니다. 위 응답 내용을 참고하세요.")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback
        traceback.print_exc()
