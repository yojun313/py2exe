import boto3
import os
import re
from dotenv import load_dotenv
from config import (
    R2_ENDPOINT,
    ACCESS_KEY_ID,
    SECRET_ACCESS_KEY,
    ACCOUNT_ID,
    BUCKET_NAME,
)
import sys

load_dotenv()


def parse_version(version_str):
    return tuple(map(int, version_str.split(".")))


def find_latest_version_file(app_config):
    output_directory = app_config["OUTPUT_DIRECTORY"]
    version_pattern_str = app_config["VERSION_PATTERN"]
    
    if not os.path.exists(output_directory):
        return None

    version_pattern = re.compile(version_pattern_str)
    latest_file = None
    latest_version = (0, 0, 0)

    for filename in os.listdir(output_directory):
        match = version_pattern.match(filename)
        if match:
            version = match.group(1)
            try:
                parsed = parse_version(version)
                if parsed > latest_version:
                    latest_version = parsed
                    latest_file = filename
            except:
                continue

    return latest_file


def upload_file(local_path):
    filename = os.path.basename(local_path)

    if not os.path.exists(local_path):
        print(f"[❌] 파일을 찾을 수 없습니다: {local_path}")
        return

    session = boto3.session.Session()
    client = session.client(
        "s3",
        region_name="auto",
        endpoint_url=R2_ENDPOINT,
        aws_access_key_id=ACCESS_KEY_ID,
        aws_secret_access_key=SECRET_ACCESS_KEY,
    )

    try:
        print(f"[⏫] 업로드 중: {filename} → R2 버킷 '{BUCKET_NAME}'")
        client.upload_file(local_path, BUCKET_NAME, filename)
        print(
            f"[✅] 업로드 완료: https://{ACCOUNT_ID}.r2.cloudflarestorage.com/{BUCKET_NAME}/{filename}"
        )
    except Exception as e:
        print(f"[❌] 업로드 실패: {e}")


if __name__ == "__main__":
    from config import APPS
    
    if not APPS:
        print("[❌] config.json에 등록된 앱이 없습니다.")
        sys.exit(1)
        
    print("=== 등록된 앱 목록 ===")
    for idx, app in enumerate(APPS):
        print(f"[{idx}] {app['APP_NAME']}")
        
    try:
        app_idx = int(input("작업할 앱의 번호를 선택하세요: ").strip())
        selected_app = APPS[app_idx]
    except (ValueError, IndexError):
        print("[❌] 올바른 번호를 선택해주세요.")
        sys.exit(1)

    while True:
        version_input = input(f"[{selected_app['APP_NAME']}] 업로드할 버전을 입력하세요 (최신 파일은 'n'): ").strip()

        if version_input.lower() == "n":
            latest_file = find_latest_version_file(selected_app)
            if latest_file:
                full_path = os.path.join(selected_app["OUTPUT_DIRECTORY"], latest_file)
                print(f"[🔍] 최신 버전 파일 감지: {full_path}")
                upload_file(full_path)
            else:
                print("[❌] 업로드 가능한 버전 파일을 찾을 수 없습니다.")
        else:
            filename = f"{selected_app['APP_NAME']}_{version_input}.exe"
            full_path = os.path.join(selected_app["OUTPUT_DIRECTORY"], filename)
            upload_file(full_path)