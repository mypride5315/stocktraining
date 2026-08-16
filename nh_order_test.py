# -*- coding: utf-8 -*-
"""
NH 국내주식 주문 API 실제 체결 테스트 (진단용)
=====================================================================
nh_trading_bot.py를 통하지 않고, 딱 1주만 직접 매수 주문을 넣어서
NH 서버의 "원본 응답 전체"를 그대로 보여주는 진단 스크립트입니다.

⚠️ 실제로 모의투자 주문이 나갑니다 (dry_run=False). 실전(운영) 서버로는
   절대 실행하지 마세요 - nh_config.json의 is_mock이 true인지 꼭 확인하세요.

[사용 방법]
    python nh_order_test.py
"""
import json
import os

BASE_DIR = r"C:\TradingBot"
NH_CONFIG_PATH = os.path.join(BASE_DIR, "nh_config.json")
MOCK_BASE_URL = "https://moapi.nhplug.com:8443"


def main():
    cfg = json.load(open(NH_CONFIG_PATH, encoding="utf-8"))

    if not cfg.get("is_mock", True):
        print("[중단] nh_config.json의 is_mock이 false입니다. 실전 서버 오발주 방지를 위해 실행을 중단합니다.")
        return

    account_no = cfg.get("account_no", "")
    if not account_no or account_no.startswith("여기에"):
        print("[중단] nh_config.json에 account_no가 설정되지 않았습니다.")
        return

    os.environ["NHPLUG_APP_KEY"] = cfg["app_key"]
    os.environ["NHPLUG_APP_SECRET"] = cfg["app_secret"]
    os.environ["NHPLUG_BASE_URL"] = MOCK_BASE_URL

    from nh_trading_bot import get_balance, order_cash_buy, log_order_nh

    print("=" * 60)
    print(f" NH 실제 주문 체결 테스트 [모의투자] 계좌={account_no}")
    print("=" * 60)

    print("\n[1단계] 주문 전 잔고 확인...")
    before = get_balance(account_no)
    print(f"  rsp_cd={before.get('rsp_cd')} 예수금={before.get('Output_0', {}).get('dca')}")

    ticker = "005930"
    qty = 1
    price = 70000  # 지정가 7만원 (삼성전자 현재가 근처, 소액 테스트용)

    print(f"\n[2단계] 매수 주문 전송 (dry_run=False) - {ticker} {qty}주 @ {price:,}원...")
    result = order_cash_buy(account_no, ticker, qty, orr_pr=price, dry_run=False)
    print("  [서버 원본 응답]")
    print(" ", json.dumps(result, ensure_ascii=False, indent=2))

    orr_no = result.get("Output_0", {}).get("orr_no") if isinstance(result, dict) else None
    if orr_no:
        print(f"\n  -> 주문번호(orr_no)={orr_no} 발급됨. 실제로 주문이 접수된 것으로 보입니다.")
        log_order_nh("BUY", ticker, price, qty, "nh_order_test_diagnostic", extra=f"orr_no={orr_no}")
    else:
        print("\n  -> 주문번호(orr_no)가 없습니다. 응답 전체를 확인해서 rsp_cd/rsp_msg를 점검하세요.")

    print("\n[3단계] 주문 후 잔고 재확인 (체결까지는 시차가 있을 수 있음)...")
    after = get_balance(account_no)
    print(f"  rsp_cd={after.get('rsp_cd')} 예수금={after.get('Output_0', {}).get('dca')}")
    holdings = after.get("Output_1", [])
    print(f"  보유종목 수={len(holdings)}")
    for h in holdings:
        print("   -", h)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback
        traceback.print_exc()
