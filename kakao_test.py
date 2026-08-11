# -*- coding: utf-8 -*-
"""
카카오톡 알림 즉시 테스트 스크립트
=====================================================================
실제 매매 신호를 기다리지 않고, 지금 바로 카카오톡이 정상적으로
오는지 확인할 수 있습니다. 이미 저장된 kakao_token.json을 그대로
재사용하므로, kakao_setup.py처럼 다시 로그인할 필요가 없습니다.

[사용 방법]
    python kakao_test.py
"""

import os
import json
import requests
from datetime import datetime, timedelta

BASE_DIR = r"C:\TradingBot"
KAKAO_TOKEN_PATH = os.path.join(BASE_DIR, "kakao_token.json")


def refresh_access_token(token_data):
    url = "https://kauth.kakao.com/oauth/token"
    data = {
        "grant_type": "refresh_token",
        "client_id": token_data["rest_api_key"],
        "refresh_token": token_data["refresh_token"],
    }
    res = requests.post(url, data=data, timeout=10)
    if res.status_code != 200:
        print(f"[실패] 토큰 갱신 실패: {res.status_code} {res.text}")
        return None
    result = res.json()
    token_data["access_token"] = result["access_token"]
    expires_in = result.get("expires_in", 21599)
    token_data["access_token_expire_at"] = (datetime.now() + timedelta(seconds=expires_in)).isoformat()
    if "refresh_token" in result:
        token_data["refresh_token"] = result["refresh_token"]
    with open(KAKAO_TOKEN_PATH, "w", encoding="utf-8") as f:
        json.dump(token_data, f, ensure_ascii=False, indent=2)
    print("[정보] 토큰을 새로 갱신했습니다.")
    return token_data


def main():
    print("=" * 60)
    print(" 카카오톡 알림 즉시 테스트")
    print("=" * 60)

    if not os.path.exists(KAKAO_TOKEN_PATH):
        print(f"[실패] {KAKAO_TOKEN_PATH} 파일이 없습니다.")
        print("kakao_setup.py를 먼저 실행해서 인증을 완료해주세요.")
        return

    with open(KAKAO_TOKEN_PATH, "r", encoding="utf-8") as f:
        token_data = json.load(f)

    expire_at = datetime.fromisoformat(token_data["access_token_expire_at"])
    if datetime.now() >= expire_at - timedelta(minutes=5):
        print("[정보] 토큰이 만료됐거나 곧 만료되어 갱신을 시도합니다...")
        token_data = refresh_access_token(token_data)
        if token_data is None:
            print("[실패] 토큰 갱신 실패로 테스트를 중단합니다.")
            return
    else:
        print(f"[정보] 기존 토큰 사용 (만료까지 {(expire_at - datetime.now()).seconds // 60}분 남음)")

    url = "https://kapi.kakao.com/v2/api/talk/memo/default/send"
    headers = {"Authorization": f"Bearer {token_data['access_token']}"}
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    template = {
        "object_type": "text",
        "text": f"[테스트 알림]\n이 메시지가 보이면 카카오톡 연동이 정상 작동 중입니다.\n\n발송 시각: {now_str}",
        "link": {"web_url": "https://developers.kakao.com", "mobile_web_url": "https://developers.kakao.com"},
    }

    print("\n테스트 메시지 전송 중...")
    res = requests.post(url, headers=headers, data={"template_object": json.dumps(template)}, timeout=10)

    if res.status_code == 200:
        print("\n[성공] 테스트 메시지를 보냈습니다! 카카오톡 '나와의 채팅방'을 확인해보세요.")
    else:
        print(f"\n[실패] 전송 실패: status_code={res.status_code}")
        print(f"서버 응답: {res.text}")
        print("\n확인해보실 것:")
        print("- kakao_token.json의 refresh_token이 만료(보통 60~90일)되지 않았는지")
        print("- 카카오 디벨로퍼스에서 '카카오톡 메시지 전송(talk_message)' 동의항목이 여전히 켜져있는지")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback
        traceback.print_exc()
