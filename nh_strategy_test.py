# -*- coding: utf-8 -*-
"""
NH ORB+VWAP 전략 로직 검증 (진단용, 네트워크 호출 없음)
=====================================================================
nh_trading_bot.py의 calc_vwap / check_orb_vwap_signal / check_exit_signal /
process_ticker_nh를 합성(가짜) 데이터로만 검증합니다.

⚠️ 실제 API를 호출하지 않습니다. process_ticker_nh 테스트에서는
   fetch_today_minute_bars와 order_cash_buy/order_cash_sell을
   전부 mock으로 교체해서 실제 주문이 절대 나가지 않게 했습니다.

[사용 방법]
    python nh_strategy_test.py
"""
import pandas as pd
from unittest.mock import patch

import nh_trading_bot as nh


def bars(rows):
    """[(HH:MM, open, high, low, close, volume), ...] -> DataFrame"""
    df = pd.DataFrame(rows, columns=["time", "open", "high", "low", "close", "volume"])
    df["datetime"] = df["time"].apply(lambda t: pd.Timestamp(f"2026-08-17 {t}:00"))
    return df[["datetime", "open", "high", "low", "close", "volume"]]


def test_calc_vwap():
    df = bars([
        ("09:00", 100, 100, 100, 100, 10),
        ("09:05", 102, 102, 102, 102, 20),
        ("09:10", 98, 98, 98, 98, 10),
    ])
    vwap = nh.calc_vwap(df)
    # (100*10 + 102*20 + 98*10) / 40 = 4020/40 = 100.5
    assert abs(vwap.iloc[-1] - 100.5) < 1e-9, f"VWAP 계산 오류: {vwap.iloc[-1]}"
    print("test_calc_vwap OK")


def test_orb_vwap_box_forming():
    # 09:00, 09:05 두 봉뿐 -> orb_minutes=30 박스가 아직 안 끝남
    df = bars([
        ("09:00", 100, 101, 99, 100, 10),
        ("09:05", 100, 101, 99, 100, 10),
    ])
    sig = nh.check_orb_vwap_signal(df, orb_minutes=30)
    assert sig["box_forming"] is True
    print("test_orb_vwap_box_forming OK")


def test_orb_vwap_entry_signal_true():
    # 박스상단, VWAP을 모두 명확히 돌파하는 케이스
    rows = [(f"09:{5*i:02d}", 100, 101, 99, 100, 10) for i in range(6)]  # 09:00~09:25
    rows.append(("09:30", 105, 106, 104, 105, 10))
    rows.append(("09:35", 108, 109, 107, 108, 10))  # 최신봉: 박스상단(101)도, VWAP도 확실히 상회
    df = bars(rows)
    sig = nh.check_orb_vwap_signal(df, orb_minutes=30)
    assert sig["box_forming"] is False
    assert sig["close"] > sig["orb_high"], "박스상단 돌파 조건이 성립해야 함"
    assert sig["close"] > sig["vwap"], "VWAP 상회 조건이 성립해야 함"
    assert sig["entry_signal"] is True
    print("test_orb_vwap_entry_signal_true OK")


def test_orb_vwap_entry_signal_false_below_box():
    # VWAP은 넘지만, 오프닝레인지 중 스파이크(저거래량 고가)로 박스상단이 더 높은 케이스
    rows = [
        ("09:00", 100, 101, 100, 100, 10),
        ("09:05", 100, 101, 100, 100, 10),
        ("09:10", 100, 101, 100, 100, 10),
        ("09:15", 100, 101, 100, 100, 10),
        ("09:20", 100, 115, 100, 101, 1),   # 저거래량 스파이크: 박스상단만 밀어올림(115)
        ("09:25", 100, 101, 100, 100, 10),
        ("09:30", 102, 104, 102, 103, 50),  # 오프닝레인지 종료 후
        ("09:35", 104, 106, 104, 105, 50),  # 최신봉: VWAP은 넘지만 박스상단(115)엔 못미침
    ]
    df = bars(rows)
    sig = nh.check_orb_vwap_signal(df, orb_minutes=30)
    assert sig["box_forming"] is False
    assert sig["orb_high"] == 115
    assert sig["close"] > sig["vwap"], "이 케이스는 VWAP은 넘어야 함"
    assert sig["close"] < sig["orb_high"], "이 케이스는 박스상단은 못 넘어야 함"
    assert sig["entry_signal"] is False
    print("test_orb_vwap_entry_signal_false_below_box OK")


def test_orb_vwap_entry_signal_false_below_vwap():
    # 박스상단은 넘지만, 오프닝레인지 종료 후 유입된 대량거래로 VWAP이 더 높이 밀려올라간 케이스
    rows = [(f"09:{5*i:02d}", 100, 101, 100, 100, 10) for i in range(6)]  # 09:00~09:25, 박스상단=101
    rows.append(("09:30", 105, 112, 104, 110, 100000))  # 대량거래(박스 종료 후) -> VWAP을 급격히 끌어올림
    rows.append(("09:35", 102, 104, 102, 103, 10))       # 최신봉: 박스상단(101)은 넘지만 VWAP엔 못미침
    df = bars(rows)
    sig = nh.check_orb_vwap_signal(df, orb_minutes=30)
    assert sig["box_forming"] is False
    assert sig["orb_high"] == 101
    assert sig["close"] > sig["orb_high"], "이 케이스는 박스상단은 넘어야 함"
    assert sig["close"] < sig["vwap"], "이 케이스는 VWAP은 못 넘어야 함"
    assert sig["entry_signal"] is False
    print("test_orb_vwap_entry_signal_false_below_vwap OK")


def test_check_exit_signal():
    assert nh.check_exit_signal(latest_low=97, latest_high=101, entry_price=100,
                                 stop_pct=0.02, tp_trigger_pct=0.03) == "stop_loss"
    assert nh.check_exit_signal(latest_low=99, latest_high=103.5, entry_price=100,
                                 stop_pct=0.02, tp_trigger_pct=0.03) == "take_profit"
    assert nh.check_exit_signal(latest_low=99, latest_high=101, entry_price=100,
                                 stop_pct=0.02, tp_trigger_pct=0.03) is None
    print("test_check_exit_signal OK")


def _entry_breakout_df():
    rows = [(f"09:{5*i:02d}", 100, 101, 99, 100, 10) for i in range(6)]
    rows.append(("09:30", 108, 109, 107, 108, 10))
    return bars(rows)


def _holding_df(close_price):
    rows = [(f"09:{5*i:02d}", 100, 101, 99, 100, 10) for i in range(6)]
    rows.append(("09:30", 108, 109, 107, 108, 10))
    rows.append(("09:35", close_price, close_price + 1, close_price - 1, close_price, 10))
    return bars(rows)


def test_process_ticker_nh_full_state_machine():
    """실제 API를 절대 호출하지 않도록 fetch_today_minute_bars / order_cash_buy / order_cash_sell을
    전부 mock으로 교체한 뒤, 진입->보유->손절청산까지 상태기계 전체 흐름을 검증함."""
    state = {}
    buy_calls, sell_calls = [], []

    def fake_order_cash_buy(act_no, iem_cd, orr_qty, orr_pr=None, dry_run=True):
        buy_calls.append((act_no, iem_cd, orr_qty))
        return {"success": True, "Output_0": {"orr_no": 12345}}

    def fake_order_cash_sell(act_no, iem_cd, orr_qty, orr_pr=None, dry_run=True):
        sell_calls.append((act_no, iem_cd, orr_qty))
        return {"success": True, "Output_0": {"orr_no": 12346}}

    with patch.object(nh, "order_cash_buy", side_effect=fake_order_cash_buy), \
         patch.object(nh, "order_cash_sell", side_effect=fake_order_cash_sell):

        # 1) 박스 형성 중 -> no_data/box_forming
        with patch.object(nh, "fetch_today_minute_bars", return_value=pd.DataFrame()):
            r = nh.process_ticker_nh("005930", "50051000897", state, dry_run=False)
            assert r["status"] == "no_data"

        # 2) 진입 신호 -> 매수 주문 발생 + 포지션 오픈
        with patch.object(nh, "fetch_today_minute_bars", return_value=_entry_breakout_df()):
            r = nh.process_ticker_nh("005930", "50051000897", state, capital_krw=1_000_000, dry_run=False)
            assert r["status"] == "entry_signal", r
            assert len(buy_calls) == 1, "매수 주문이 정확히 1번 나가야 함"
            assert state["005930"]["in_position"] is True
            assert state["005930"]["day_traded"] is True

        # 3) 보유 중, 청산조건 미충족 -> holding
        with patch.object(nh, "fetch_today_minute_bars", return_value=_holding_df(close_price=109)):
            r = nh.process_ticker_nh("005930", "50051000897", state, dry_run=False)
            assert r["status"] == "holding", r
            assert len(sell_calls) == 0

        # 4) 손절가 도달 -> 매도 주문 발생 + 포지션 종료
        entry_price = state["005930"]["entry_price"]
        stop_trigger_price = entry_price * (1 - 0.02) - 1  # stop_pct 기본값(0.02) 아래로 확실히 하회
        with patch.object(nh, "fetch_today_minute_bars", return_value=_holding_df(close_price=stop_trigger_price)):
            r = nh.process_ticker_nh("005930", "50051000897", state, dry_run=False)
            assert r["status"] == "exit_signal:stop_loss", r
            assert len(sell_calls) == 1
            assert state["005930"]["in_position"] is False

        # 5) 당일 매매 이미 완료 -> 재진입 신호가 떠도 다시 매수하면 안 됨
        with patch.object(nh, "fetch_today_minute_bars", return_value=_entry_breakout_df()):
            r = nh.process_ticker_nh("005930", "50051000897", state, dry_run=False)
            assert r["status"] == "done_for_today", r
            assert len(buy_calls) == 1, "당일 매매 완료 후에는 추가 매수가 나가면 안 됨"

    print("test_process_ticker_nh_full_state_machine OK")


def test_process_ticker_nh_never_calls_real_network_functions():
    """process_ticker_nh 자체가 실수로라도 실제 call()을 거치지 않는지 재확인 (안전장치 검증)."""
    called = {"hit": False}

    def poison(*args, **kwargs):
        called["hit"] = True
        raise AssertionError("실제 nhplug.call()이 호출됨 - mock이 걸리지 않았음!")

    with patch.object(nh, "order_cash_buy", side_effect=poison), \
         patch.object(nh, "fetch_today_minute_bars", return_value=_entry_breakout_df()):
        nh.process_ticker_nh("005930", "50051000897", {}, dry_run=False)
    assert called["hit"] is True, "mock이 실제로 호출 경로에 걸려있는지 확인용"
    print("test_process_ticker_nh_never_calls_real_network_functions OK")


if __name__ == "__main__":
    tests = [
        test_calc_vwap,
        test_orb_vwap_box_forming,
        test_orb_vwap_entry_signal_true,
        test_orb_vwap_entry_signal_false_below_box,
        test_orb_vwap_entry_signal_false_below_vwap,
        test_check_exit_signal,
        test_process_ticker_nh_full_state_machine,
        test_process_ticker_nh_never_calls_real_network_functions,
    ]
    failed = 0
    for t in tests:
        try:
            t()
        except AssertionError as e:
            failed += 1
            print(f"[FAIL] {t.__name__}: {e}")
    print("=" * 60)
    if failed:
        print(f"{failed}개 실패")
        raise SystemExit(1)
    print(f"전체 {len(tests)}개 테스트 통과")
