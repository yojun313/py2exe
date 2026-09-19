"""버전 관리 서버(version/run.py) 호출 클라이언트."""

from __future__ import annotations

import requests


class VersionApiError(Exception):
    pass


def register_version(
    api_url: str,
    app_name: str,
    version: str,
    base_url: str | None = None,
    timeout: float = 15.0,
) -> dict:
    """POST /api/version 으로 최신 버전을 등록하고 서버 응답(dict)을 돌려준다.

    신규 앱은 base_url 이 필수이며, 기존 앱은 base_url 을 주면 갱신, 없으면 저장된 값을 유지한다.
    """
    if not api_url:
        raise VersionApiError("버전 서버 주소(VERSION_API_URL)가 설정되지 않았습니다.")

    payload: dict = {"app_name": app_name, "version": version}
    if base_url:
        payload["base_url"] = base_url

    try:
        response = requests.post(f"{api_url.rstrip('/')}/api/version", json=payload, timeout=timeout)
    except requests.RequestException as e:
        raise VersionApiError(f"버전 서버에 연결할 수 없습니다 ({api_url}): {e}") from e

    if response.ok:
        return response.json()

    try:
        detail = response.json().get("detail")
    except ValueError:
        detail = response.text
    raise VersionApiError(f"버전 서버 응답 {response.status_code}: {detail}")


def fetch_versions(api_url: str, timeout: float = 10.0) -> dict:
    try:
        response = requests.get(f"{api_url.rstrip('/')}/api/versions", timeout=timeout)
        response.raise_for_status()
        return response.json()
    except requests.RequestException as e:
        raise VersionApiError(f"버전 서버 조회 실패 ({api_url}): {e}") from e
