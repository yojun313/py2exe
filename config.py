import os
from dotenv import load_dotenv

load_dotenv()

# Build Setting
APP_NAME = os.getenv("BUILD_APP_NAME", "MANAGER")
PROJECT_DIR = os.getenv("BUILD_PROJECT_DIR", "")
VERSION_PATTERN = os.getenv("BUILD_VERSION_PATTERN", r"MANAGER_([\w.\-]+)$")
DEFAULT_SPEC_NAME = os.getenv("BUILD_DEFAULT_SPEC_NAME", "build.spec")
DEFAULT_ISS_NAME = os.getenv("BUILD_DEFAULT_ISS_NAME", "setup.iss")
ISS_VERSION_PATTERN = os.getenv(
    "BUILD_ISS_VERSION_PATTERN", r'^\s*#define\s+MyAppVersion\s+"([\w.\-]+)"'
)


# Path
EXE_DIRECTORY = os.getenv("EXE_DIRECTORY")  # pyinstaller output
OUTPUT_DIRECTORY = os.getenv("OUTPUT_DIRECTORY")  # inno setup output
VENV_PYTHON = os.getenv("VENV_PYTHON")  # venv
INNO_SETUP_EXE = (
    os.getenv("INNO_SETUP_EXE")  # inno setup executable
)

if not os.path.exists(EXE_DIRECTORY):
    os.makedirs(EXE_DIRECTORY)
if not os.path.exists(OUTPUT_DIRECTORY):
    os.makedirs(OUTPUT_DIRECTORY)


# Cloudflare R2
ACCESS_KEY_ID = os.getenv("ACCESS_KEY_ID")
SECRET_ACCESS_KEY = os.getenv("SECRET_ACCESS_KEY")
ACCOUNT_ID = os.getenv("ACCOUNT_ID")
BUCKET_NAME = os.getenv("BUCKET_NAME")
R2_ENDPOINT = f"https://{ACCOUNT_ID}.r2.cloudflarestorage.com"
