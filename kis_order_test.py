# -*- coding: utf-8 -*-
"""
KIS 국내주식 주문 API 직접 테스트 (진단용)
=====================================================================
매매봇을 통하지 않고, 딱 1주만 직접 주문을 넣어서 KIS 서버의
"원본 응답 전체"를 그대로 보여주는 진단 스크립트입니다.
반드시 국내 장 시간(09:00~15:30)에 실행해야 의미 있는 결과가 나옵니다.

⚠️ 실제로 모의투자 주문이 나갑니다 (금액은 소액이지만, 실제 주문입니다).

[사용 방법]
    python kis_order_test.py
"""

import os
import json
import requests
from datetime import datetime

BASE_DIR = r"C:\TradingBot"
KIS_CONFIG_PATH = os.path.join(BASE_DIR, "kis_config.json")
TOKEN_CACHE_PATH = os.path.join(BASE_DIR, "kis_token_cache_TEST.json")  # 본체 봇 캐시와 별도 파일 사용(형식 충돌 방지)

BASE_URL = "https://openapivts.koreainvestment.com:29443"  # 모의투자 서버


def load_kis_config():
    with open(KIS_CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def get_token(cfg):
    # 캐시 확인 (형식이 다르거나 손상되어 있어도 안전하게 새로 발급받도록 처리)
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

    for attempt in range(2):
        res = requests.post(url, headers={"content-type": "application/json"}, data=json.dumps(body), timeout=10)
        print(f"[토큰 발급 응답] status_code={res.status_code}")
        print(f"[토큰 발급 원본 응답] {res.text}")

        if res.status_code == 403 and "EGW00133" in res.text and attempt == 0:
            print("[정보] 1분당 1회 제한에 걸렸습니다. 65초 대기 후 자동 재시도합니다...")
            import time as _time_module
            _time_module.sleep(65)
            continue
        break

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


def main():
    print("=" * 70)
    print(" KIS 국내주식 주문 API 진단 테스트")
    print("=" * 70)

    ticker = input("테스트할 종목코드 입력 (기본 229200): ").strip() or "229200"
    qty = input("테스트할 수량 입력 (기본 1): ").strip() or "1"

    cfg = load_kis_config()
    print(f"\n[설정 확인] appkey 앞 8자리: {cfg['appkey'][:8]}...")
    print(f"[설정 확인] is_mock: {cfg.get('is_mock')}")

    token = get_token(cfg)
    print(f"\n[토큰 확인] 앞 20자리: {token[:20]}...")

    cano, prdt_cd = None, None
    # user_config.json에서 계좌번호 읽기
    user_cfg_path = os.path.join(BASE_DIR, "user_config.json")
    with open(user_cfg_path, "r", encoding="utf-8") as f:
        user_cfg = json.load(f)
    account_no = user_cfg["account_no"]
    cano, prdt_cd = account_no.split("-")
    print(f"[계좌 확인] CANO={cano}, ACNT_PRDT_CD={prdt_cd}")

    body = {
        "CANO": cano,
        "ACNT_PRDT_CD": prdt_cd,
        "PDNO": ticker,
        "ORD_DVSN": "01",  # 시장가
        "ORD_QTY": qty,
        "ORD_UNPR": "0",
    }

    hashkey = get_hashkey(cfg, body)

    url = BASE_URL + "/uapi/domestic-stock/v1/trading/order-cash"
    headers = {
        "content-type": "application/json; charset=utf-8",
        "authorization": f"Bearer {token}",
        "appkey": cfg["appkey"],
        "appsecret": cfg["appsecret"],
        "tr_id": "VTTC0802U",  # 모의투자 매수
        "custtype": "P",
    }
    if hashkey:
        headers["hashkey"] = hashkey

    print(f"\n[주문 요청 본문] {json.dumps(body, ensure_ascii=False)}")
    print(f"[주문 요청 헤더] tr_id={headers['tr_id']}, appkey 앞8자리={headers['appkey'][:8]}...")

    print("\n주문 전송 중...")
    res = requests.post(url, headers=headers, data=json.dumps(body), timeout=10)

    print("\n" + "=" * 70)
    print(" 서버 응답 (원본 전체)")
    print("=" * 70)
    print(f"HTTP 상태 코드: {res.status_code}")
    print(f"응답 헤더 tr_id: {res.headers.get('tr_id', '없음')}")
    print(f"\n응답 본문 전체:")
    try:
        result = res.json()
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except Exception:
        print(res.text)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback
        traceback.print_exc()
