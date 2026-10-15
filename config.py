"""Deployment settings. Credentials and application IDs stay outside Git."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_EMPLOYEE_ID = os.environ.get("SHASTEEL_EMPLOYEE_ID", "").strip()
EMPLOYEE_ID = DEFAULT_EMPLOYEE_ID
EMPLOYEE_NAME = os.environ.get("SHASTEEL_EMPLOYEE_NAME", "")
PASSWORD = os.environ.get("SHASTEEL_PASSWORD", "")
LOGIN_URL = os.environ.get("SHASTEEL_LOGIN_URL", "")
MY_APPS_URL = os.environ.get("SHASTEEL_MY_APPS_URL", "")
SSO_REDIRECT_URL = os.environ.get("SHASTEEL_SSO_REDIRECT_URL", "")
APP_ID = os.environ.get("SHASTEEL_APP_ID", "")
APP_ID_STR = os.environ.get("SHASTEEL_APP_ID_STR", "")
H5_BASE = os.environ.get("SHASTEEL_H5_BASE", "").rstrip("/")
DEEPSEEK_TOKEN_FILE = os.path.expanduser(os.environ.get("DEEPSEEK_TOKEN_FILE", ""))
DEEPSEEK_BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_MODEL_FAST = os.environ.get("DEEPSEEK_MODEL_FAST", "")
DEEPSEEK_MODEL_PRO = os.environ.get("DEEPSEEK_MODEL_PRO", "") or DEEPSEEK_MODEL_FAST
HEADLESS = os.environ.get("DAILY_PRACTICE_HEADLESS", "1") != "0"
BROWSER_TIMEOUT = 30000
BROWSER_LONG_TIMEOUT = 60000
VIEWPORT_WIDTH = 390
VIEWPORT_HEIGHT = 844
DEVICE_SCALE_FACTOR = 2
STATE_DIR = Path(os.environ.get("DAILY_PRACTICE_STATE_DIR", str(BASE_DIR)))
SCREENSHOT_DIR = STATE_DIR / "screenshots"
LOG_DIR = STATE_DIR / "logs"
BROWSER_STATE_FILE = STATE_DIR / "browser_state.json"
TOKEN_CACHE_FILE = STATE_DIR / "token_cache.json"
MAX_RETRIES = 3
RETRY_BACKOFF_BASE = 2
OCR_LANG = "chi_sim+eng"
OCR_PSM = 6
PREPROCESS_THRESHOLD = 140
