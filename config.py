import os
import json
from dotenv import load_dotenv

load_dotenv()

CONFIG_JSON_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

def load_apps_config():
    if not os.path.exists(CONFIG_JSON_PATH):
        raise FileNotFoundError(f"config.json 파일을 찾을 수 없습니다: {CONFIG_JSON_PATH}")
    with open(CONFIG_JSON_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

config_data = load_apps_config()
APPS = config_data.get("apps", [])
COMMON = config_data.get("common", {})
INNO_SETUP_EXE = COMMON.get("INNO_SETUP_EXE", "")

ACCESS_KEY_ID = os.getenv("ACCESS_KEY_ID")
SECRET_ACCESS_KEY = os.getenv("SECRET_ACCESS_KEY")
ACCOUNT_ID = os.getenv("ACCOUNT_ID")
BUCKET_NAME = os.getenv("BUCKET_NAME")
R2_ENDPOINT = f"https://{ACCOUNT_ID}.r2.cloudflarestorage.com"