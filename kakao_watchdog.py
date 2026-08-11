# -*- coding: utf-8 -*-
"""
매매봇 워치독 + 카카오 알림
------------------------------------------------
하는 일:
1. 오늘자 trading_log 파일에서 가장 최근 "실행 시각:" 을 읽는다.
2. 지금이 국내장/미국장 활성 시간대인데, 마지막 실행으로부터
   GAP_THRESHOLD_MIN 분 이상 지났으면 -> 봇이 멈춘 것으로 판단, 카카오 알림 발송.
3. 로그 끝부분에서 [오류] 문자열이 최근에 새로 찍혔으면 그것도 알림.
4. 같은 문제로 중복 알림을 계속 보내지 않도록 alert_state.json 으로 상태 기억.

Windows 작업 스케줄러에 별도 작업으로 등록해서
10~15분 간격으로 이 스크립트를 실행시키면 됩니다.
(매매봇 본체 스케줄과는 별개의 트리거로 등록 - 매매봇이 안 도는 상황도 감지해야 하므로)

[2026-07-26 수정] 경로를 실제 auto_trading_bot.py 폴더 구조(C:\\TradingBot)에 맞게 수정:
- LOG_DIR: C:\\trading_bot\\logs (오타) -> C:\\TradingBot\\Log
- 로그 파일명 형식은 auto_trading_bot.py와 이미 일치함 (trading_log_YYYY_MM_DD.txt)
"""

import json
import re
import os
from datetime import datetime, timedelta

import requests

# ===== 환경에 맞게 수정 =====
BASE_DIR = r"C:\TradingBot"
LOG_DIR = os.path.join(BASE_DIR, "Log")    # trading_log 파일이 있는 실제 폴더로 수정
LOG_PREFIX = "trading_log_"                 # trading_log_2026_07_22.txt 형식 (auto_trading_bot.py와 일치)
TOKEN_FILE = os.path.join(BASE_DIR, "kakao_token.json")
STATE_FILE = os.path.join(BASE_DIR, "alert_state.json")

GAP_THRESHOLD_MIN = 15   # 이 이상 실행 기록이 없으면 이상 신호로 판단
RE_ALERT_COOLDOWN_MIN = 60  # 같은 종류 알림 재발송 최소 간격

# 국내장 / 미국장 활성 시간대 (KST 기준, 필요시 조정)
KR_MARKET = [("09:00", "15:30")]
US_MARKET = [("22:30", "23:59"), ("00:00", "05:00")]  # 자정 걸침
# ============================


def now_kst():
    return datetime.now()


def in_time_range(now, ranges):
    t = now.strftime("%H:%M")
    for start, end in ranges:
        if start <= end:
            if start <= t <= end:
                return True
        else:  # 자정 걸침
            if t >= start or t <= end:
                return True
    return False


def is_weekday(now):
    # ⚠️ 자정이 아닌 새벽 5시 기준 거래일로 판단함.
    # (안 맞추면 금요일 밤 미국장이 토요일 새벽까지 이어질 때 "주말"로 오판해서
    #  워치독이 스킵해버리는 문제가 생김)
    trading_day = now - timedelta(hours=5)
    return trading_day.weekday() < 5  # 0=월 ... 4=금


def find_today_log():
    # ⚠️ auto_trading_bot.py와 동일하게, 자정이 아닌 새벽 5시 기준 거래일을 사용함.
    # (안 맞추면 00:00~05:00 사이에 "오늘자 로그가 없다"고 잘못 판단해서 헛알림이 감)
    trading_day = (datetime.now() - timedelta(hours=5)).strftime("%Y_%m_%d")
    fname = f"{LOG_PREFIX}{trading_day}.txt"
    path = os.path.join(LOG_DIR, fname)
    return path if os.path.exists(path) else None


def get_last_run_time(log_path):
    """로그 파일에서 마지막 '실행 시각:' 라인을 찾아 datetime으로 반환"""
    pattern = re.compile(r"실행 시각:\s*([\d\-]+\s[\d:.]+)")
    last_ts = None
    # 큰 파일 대비 뒤에서부터 훑기
    with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
        lines = f.readlines()
    for line in reversed(lines):
        m = pattern.search(line)
        if m:
            ts_str = m.group(1).strip()
            try:
                last_ts = datetime.strptime(ts_str.split(".")[0], "%Y-%m-%d %H:%M:%S")
            except ValueError:
                continue
            break
    return last_ts


def recent_error_lines(log_path, lookback_lines=50):
    with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
        lines = f.readlines()
    tail = lines[-lookback_lines:]
    return [l.strip() for l in tail if "[오류]" in l]


def load_json(path, default):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return default


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def refresh_access_token(token_data):
    """access_token 만료 대비 refresh_token으로 갱신.
    ⚠️ kakao_setup.py가 저장하는 필드명(access_token_expire_at 등)과
    이 워치독이 기대하는 필드명이 다를 수 있어, rest_api_key/refresh_token
    키가 없으면 조용히 실패하도록 방어 처리함."""
    if "rest_api_key" not in token_data or "refresh_token" not in token_data:
        print("[워치독] 토큰 파일에 필요한 필드가 없습니다. kakao_setup.py로 다시 인증해주세요.")
        return token_data
    url = "https://kauth.kakao.com/oauth/token"
    data = {
        "grant_type": "refresh_token",
        "client_id": token_data["rest_api_key"],
        "refresh_token": token_data["refresh_token"],
    }
    resp = requests.post(url, data=data, timeout=10)
    result = resp.json()
    if "access_token" in result:
        token_data["access_token"] = result["access_token"]
        if "refresh_token" in result:
            token_data["refresh_token"] = result["refresh_token"]
        save_json(TOKEN_FILE, token_data)
    return token_data


def send_kakao(message):
    token_data = load_json(TOKEN_FILE, None)
    if token_data is None:
        print("[워치독] kakao_token.json이 없습니다. kakao_setup.py를 먼저 실행하세요.")
        return

    url = "https://kapi.kakao.com/v2/api/talk/memo/default/send"
    template = {
        "object_type": "text",
        "text": message,
        "link": {"web_url": "https://developers.kakao.com", "mobile_web_url": "https://developers.kakao.com"},
    }
    headers = {"Authorization": f"Bearer {token_data['access_token']}"}
    resp = requests.post(url, headers=headers, data={"template_object": json.dumps(template)}, timeout=10)

    if resp.status_code == 401:
        # 토큰 만료 -> 갱신 후 재시도
        token_data = refresh_access_token(token_data)
        headers = {"Authorization": f"Bearer {token_data['access_token']}"}
        resp = requests.post(url, headers=headers, data={"template_object": json.dumps(template)}, timeout=10)

    if resp.status_code == 200:
        print("[워치독] 카카오 알림 발송 완료")
    else:
        print("[워치독] 카카오 알림 발송 실패:", resp.status_code, resp.text)


def should_alert_now(state, key, cooldown_min):
    last = state.get(key)
    if last is None:
        return True
    last_dt = datetime.strptime(last, "%Y-%m-%d %H:%M:%S")
    return (datetime.now() - last_dt) >= timedelta(minutes=cooldown_min)


def mark_alerted(state, key):
    state[key] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    save_json(STATE_FILE, state)


def main():
    now = datetime.now()
    print(f"[워치독] 실행 시각: {now}")
    state = load_json(STATE_FILE, {})

    if not is_weekday(now):
        print("[워치독] 주말입니다. 스킵.")
        return

    active = in_time_range(now, KR_MARKET) or in_time_range(now, US_MARKET)
    print(f"[워치독] 활성 시간대 여부(국내장/미국장): {active}")

    log_path = find_today_log()
    print(f"[워치독] 오늘자 로그 파일: {log_path if log_path else '없음'}")
    if log_path is None:
        if active and should_alert_now(state, "no_log_file", RE_ALERT_COOLDOWN_MIN):
            print("[워치독] -> 활성 시간대인데 로그 파일 자체가 없어 카톡 발송 시도")
            send_kakao(f"[매매봇 경고]\n오늘자 로그 파일 자체가 없습니다.\n봇이 실행되지 않았을 가능성이 높습니다.\n({now.strftime('%H:%M')} 기준)")
            mark_alerted(state, "no_log_file")
        else:
            print("[워치독] -> 비활성 시간대이거나 이미 최근에 알림을 보냈어서 스킵")
        return

    last_run = get_last_run_time(log_path)
    print(f"[워치독] 로그에서 확인된 마지막 실행 시각: {last_run}")

    if active and last_run is not None:
        gap_min = (now - last_run).total_seconds() / 60
        print(f"[워치독] 마지막 실행으로부터 경과: {gap_min:.1f}분 (기준: {GAP_THRESHOLD_MIN}분)")
        if gap_min >= GAP_THRESHOLD_MIN:
            if should_alert_now(state, "stale", RE_ALERT_COOLDOWN_MIN):
                print("[워치독] -> 기준 초과 + 최근 미발송 상태라 카톡 발송 시도")
                send_kakao(
                    f"[매매봇 경고]\n"
                    f"장 시간(활성 시간대)인데 봇 실행이 {int(gap_min)}분째 갱신되지 않았습니다.\n"
                    f"마지막 실행: {last_run.strftime('%H:%M:%S')}\n"
                    f"현재: {now.strftime('%H:%M:%S')}\n"
                    f"PC 절전/네트워크/스케줄러 상태를 확인해주세요."
                )
                mark_alerted(state, "stale")
            else:
                print("[워치독] -> 기준은 초과했지만 쿨다운(최근 발송 이력) 때문에 이번엔 재발송 안 함")
        else:
            print("[워치독] -> 정상 범위, 알림 불필요")
            # 정상 복구되면 다음 이상 발생시 다시 알림 가도록 상태 리셋
            if "stale" in state:
                del state["stale"]
                save_json(STATE_FILE, state)
    else:
        print("[워치독] -> 비활성 시간대라 실행시각 갭 체크는 건너뜀")

    # 최근 오류 라인 체크 (네트워크 오류 등)
    errors = recent_error_lines(log_path, lookback_lines=30)
    print(f"[워치독] 최근 30줄 내 [오류] 문자열 발견 개수: {len(errors)}")
    if errors and should_alert_now(state, "error", RE_ALERT_COOLDOWN_MIN):
        print("[워치독] -> 오류 발견 + 최근 미발송 상태라 카톡 발송 시도")
        sample = errors[-1][:150]
        send_kakao(f"[매매봇 경고]\n최근 로그에서 오류가 감지되었습니다.\n{sample}")
        mark_alerted(state, "error")


if __name__ == "__main__":
    main()
