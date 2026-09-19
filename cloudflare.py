"""Cloudflare R2 업로드.

라이브러리로 쓸 때: R2Uploader(settings.r2, log=...).upload(path, key)
실패하면 예외를 던진다 (조용히 넘어가지 않는다).

CLI 로 쓸 때:
    python cloudflare.py                         # 대화형
    python cloudflare.py --app MyApp --latest    # output 폴더의 최신 인스톨러 업로드
    python cloudflare.py --app MyApp --version 1.2.3
    python cloudflare.py --file D:/path/to/any.exe [--key custom/name.exe]
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import threading
from typing import Callable

from config import AppConfig, ConfigError, R2Config, load_settings

LogFunc = Callable[[str], None]


class R2Uploader:
    def __init__(self, config: R2Config, log: LogFunc | None = None):
        config.require()
        self.config = config
        self.log = log or print
        self._client = None

    @property
    def client(self):
        if self._client is None:
            import boto3
            from botocore.config import Config as BotoConfig

            session = boto3.session.Session()
            self._client = session.client(
                "s3",
                region_name="auto",
                endpoint_url=self.config.endpoint,
                aws_access_key_id=self.config.access_key_id,
                aws_secret_access_key=self.config.secret_access_key,
                config=BotoConfig(retries={"max_attempts": 5, "mode": "standard"}),
            )
        return self._client

    def public_url(self, key: str) -> str | None:
        if self.config.public_base_url:
            return f"{self.config.public_base_url}/{key}"
        return None

    def upload(self, local_path: str, key: str | None = None) -> str:
        """파일을 업로드하고 객체 키를 돌려준다. 실패 시 예외."""
        if not os.path.isfile(local_path):
            raise FileNotFoundError(f"업로드할 파일이 없습니다: {local_path}")

        key = key or os.path.basename(local_path)
        total = os.path.getsize(local_path)
        self.log(f"업로드 시작: {os.path.basename(local_path)} ({_human_size(total)}) → {self.config.bucket_name}/{key}")

        progress = _Progress(total, self.log)
        self.client.upload_file(local_path, self.config.bucket_name, key, Callback=progress)

        url = self.public_url(key)
        self.log(f"업로드 완료: {key}" + (f" ({url})" if url else ""))
        return key


class _Progress:
    """boto3 Callback: 20% 단위로만 로그를 남긴다."""

    def __init__(self, total: int, log: LogFunc, step: int = 20):
        self.total = max(total, 1)
        self.log = log
        self.step = step
        self.sent = 0
        self.next_mark = step
        self._lock = threading.Lock()

    def __call__(self, bytes_amount: int) -> None:
        with self._lock:
            self.sent += bytes_amount
            percent = int(self.sent * 100 / self.total)
            while percent >= self.next_mark and self.next_mark <= 100:
                self.log(f"  ... {self.next_mark}% ({_human_size(self.sent)})")
                self.next_mark += self.step


def _human_size(num: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if num < 1024 or unit == "GB":
            return f"{num:.1f} {unit}" if unit != "B" else f"{int(num)} B"
        num /= 1024
    return f"{num:.1f} GB"


def find_latest_installer(app: AppConfig) -> str | None:
    """OUTPUT_DIRECTORY 에서 `<App>_<version>.exe` 중 가장 높은 버전의 경로."""
    from builder import parse_version

    if not os.path.isdir(app.output_directory):
        return None
    pattern = re.compile(rf"^{re.escape(app.app_name)}_(.+?)\.exe$", re.IGNORECASE)
    best = None
    for name in os.listdir(app.output_directory):
        match = pattern.match(name)
        if not match or name.lower().endswith("_update.exe"):
            continue
        version = parse_version(match.group(1))
        if version is None:
            continue
        if best is None or version > best[0]:
            best = (version, name)
    return os.path.join(app.output_directory, best[1]) if best else None


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def _select_app_interactive(apps) -> AppConfig:
    print("=== 등록된 앱 목록 ===")
    for idx, app in enumerate(apps):
        print(f"[{idx}] {app.app_name}")
    while True:
        raw = input("작업할 앱 번호 (q: 종료): ").strip()
        if raw.lower() == "q":
            sys.exit(0)
        if raw.isdigit() and int(raw) < len(apps):
            return apps[int(raw)]
        print("올바른 번호를 입력하세요.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="빌드 산출물을 Cloudflare R2 에 업로드합니다.")
    parser.add_argument("--app", help="config.json 의 APP_NAME")
    parser.add_argument("--version", help="업로드할 인스톨러 버전 (예: 1.2.3)")
    parser.add_argument("--latest", action="store_true", help="output 폴더의 최신 인스톨러 업로드")
    parser.add_argument("--with-update", action="store_true", help="같은 버전의 _update.exe 도 함께 업로드")
    parser.add_argument("--file", help="임의의 파일 경로를 직접 업로드")
    parser.add_argument("--key", help="--file 사용 시 저장할 객체 키 (기본: 파일명)")
    parser.add_argument("--config", help="config.json 경로")
    args = parser.parse_args(argv)

    try:
        settings = load_settings(args.config)
        uploader = R2Uploader(settings.r2)
    except ConfigError as e:
        print(f"[설정 오류] {e}")
        return 2

    try:
        if args.file:
            uploader.upload(args.file, args.key)
            return 0

        if args.app:
            app = settings.get_app(args.app)
        else:
            app = _select_app_interactive(settings.apps)

        targets: list[str] = []
        if args.latest or args.version:
            if args.latest:
                latest = find_latest_installer(app)
                if not latest:
                    print(f"[오류] {app.output_directory} 에 업로드할 인스톨러가 없습니다.")
                    return 1
                targets.append(latest)
                version = re.match(rf"^{re.escape(app.app_name)}_(.+?)\.exe$", os.path.basename(latest)).group(1)
            else:
                version = args.version
                targets.append(app.installer_path(version))
            if args.with_update:
                targets.append(app.update_exe_path(version))
        else:
            # 대화형
            while True:
                raw = input(f"[{app.app_name}] 업로드할 버전 (n: 최신 인스톨러, q: 종료): ").strip()
                if raw.lower() == "q":
                    return 0
                if raw.lower() == "n":
                    latest = find_latest_installer(app)
                    if not latest:
                        print("[오류] 업로드 가능한 인스톨러를 찾을 수 없습니다.")
                        continue
                    targets = [latest]
                else:
                    targets = [app.installer_path(raw)]
                    update = app.update_exe_path(raw)
                    if os.path.isfile(update) and input("_update.exe 도 업로드할까요? [y/N] ").strip().lower() == "y":
                        targets.append(update)
                break

        for path in targets:
            uploader.upload(path, app.upload_key(os.path.basename(path)))
        return 0
    except (ConfigError, FileNotFoundError) as e:
        print(f"[오류] {e}")
        return 1
    except Exception as e:  # boto3/네트워크 오류
        print(f"[업로드 실패] {e}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
