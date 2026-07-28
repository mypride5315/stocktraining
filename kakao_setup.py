"""
카카오톡 "나에게 보내기" 최초 인증 스크립트 (딱 1번만 실행하면 됩니다)
=====================================================================

[사전 준비 - developers.kakao.com에서]
1. 애플리케이션 추가 후 REST API 키 복사
2. 제품 설정 > 카카오 로그인 > 활성화 ON
3. Redirect URI에 https://example.com 등록 (실제 서버 필요 없음)
4. 카카오 로그인 > 동의항목 > "카카오톡 메시지 전송(talk_message)" 활성화

[사용 방법]
    python kakao_setup.py
REST API 키를 입력하면 인증 URL이 뜨고, 그 URL로 브라우저에서 로그인하면
주소창에 "code=..." 가 포함된 페이지로 리다이렉트됩니다. 그 URL 전체를
복사해서 붙여넣으면 자동으로 토큰을 저장합니다.

이후 auto_trading_bot.py가 이 토큰 파일(kakao_token.json)을 자동으로
읽어서 체결 알림을 보냅니다. 토큰이 만료되면 auto_trading_bot.py가
refresh_token으로 자동 갱신하니, 이 스크립트는 정말 최초 1번만 실행하면 됩니다.
"""

import os
import json
import requests
from datetime import datetime, timedelta
from urllib.parse import urlparse, parse_qs

BASE_DIR = r"C:\TradingBot"
KAKAO_TOKEN_PATH = os.path.join(BASE_DIR, "kakao_token.json")
REDIRECT_URI = "https://example.com"


def main():
    print("=" * 60)
    print(" 카카오톡 나에게 보내기 - 최초 인증")
    print("=" * 60)

    rest_api_key = input("카카오 REST API 키 입력: ").strip()
    if not rest_api_key:
        print("입력이 없어 종료합니다.")
        return

    auth_url = (
        f"https://kauth.kakao.com/oauth/authorize"
        f"?response_type=code&client_id={rest_api_key}&redirect_uri={REDIRECT_URI}"
        f"&scope=talk_message"
    )
    print("\n아래 URL을 브라우저에 붙여넣어 로그인/동의해주세요:")
    print(auth_url)
    print("\n로그인 후 리다이렉트된 주소창의 URL 전체를 복사해서 붙여넣어주세요.")
    print("(예: https://example.com/?code=AbCdEfG12345...)")

    redirected_url = input("\n리다이렉트된 URL 붙여넣기: ").strip()
    parsed = urlparse(redirected_url)
    code = parse_qs(parsed.query).get("code", [None])[0]

    if not code:
        # 혹시 URL 전체가 아니라 code 값만 붙여넣은 경우도 허용
        code = redirected_url.strip()

    if not code:
        print("인가코드(code)를 찾지 못했습니다. 다시 시도해주세요.")
        return

    token_url = "https://kauth.kakao.com/oauth/token"
    data = {
        "grant_type": "authorization_code",
        "client_id": rest_api_key,
        "redirect_uri": REDIRECT_URI,
        "code": code,
    }
    res = requests.post(token_url, data=data, timeout=10)
    if res.status_code != 200:
        print(f"[오류] 토큰 발급 실패: {res.status_code}")
        print(res.text)
        return

    result = res.json()
    expires_in = result.get("expires_in", 21599)
    refresh_expires_in = result.get("refresh_token_expires_in", 5183999)

    token_data = {
        "rest_api_key": rest_api_key,
        "access_token": result["access_token"],
        "refresh_token": result["refresh_token"],
        "access_token_expire_at": (datetime.now() + timedelta(seconds=expires_in)).isoformat(),
        "refresh_token_expire_at": (datetime.now() + timedelta(seconds=refresh_expires_in)).isoformat(),
    }

    os.makedirs(BASE_DIR, exist_ok=True)
    with open(KAKAO_TOKEN_PATH, "w", encoding="utf-8") as f:
        json.dump(token_data, f, ensure_ascii=False, indent=2)

    print(f"\n[저장 완료] {KAKAO_TOKEN_PATH}")

    # 테스트 메시지 전송
    test_url = "https://kapi.kakao.com/v2/api/talk/memo/default/send"
    headers = {"Authorization": f"Bearer {token_data['access_token']}"}
    template = {
        "object_type": "text",
        "text": "카카오톡 알림 연동이 완료됐습니다! 앞으로 체결 내역을 여기로 보내드릴게요.",
        "link": {"web_url": "https://developers.kakao.com", "mobile_web_url": "https://developers.kakao.com"},
    }
    test_res = requests.post(test_url, headers=headers, data={"template_object": json.dumps(template)}, timeout=10)
    if test_res.status_code == 200:
        print("테스트 메시지를 카카오톡으로 보냈습니다. 확인해보세요!")
    else:
        print(f"[경고] 테스트 메시지 전송 실패: {test_res.status_code} {test_res.text}")
        print("토큰은 저장됐으니, auto_trading_bot.py 실행 시 다시 시도됩니다.")


if __name__ == "__main__":
    main()
