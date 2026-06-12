from pathlib import Path
import os
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).parent.parent
DATA_DIR = Path(os.getenv("DATA_DIR", BASE_DIR / "data"))
DATA_DIR.mkdir(exist_ok=True)

APP_NAME = "投資組合儀表板"
APP_ICON = "📈"

# Startup validation: fail fast in production if the secret key was never set.
# DATABASE_URL being present is a reliable proxy for "this is a real deployment".
APP_SECRET_KEY = os.getenv("APP_SECRET_KEY", "")
_is_production = bool(os.getenv("DATABASE_URL", ""))
if _is_production and not APP_SECRET_KEY:
    raise RuntimeError(
        "APP_SECRET_KEY must be set in production. "
        "Generate one with: python -c \"import secrets; print(secrets.token_hex(32))\""
    )
if not APP_SECRET_KEY:
    APP_SECRET_KEY = "dev_secret_change_in_production"  # local dev only

# Taiwan stock symbols (yfinance needs .TW suffix)
TW_TICKERS = {
    "0050": "0050.TW",
    "006208": "006208.TW",
    "00713": "00713.TW",
}

# US stock symbols
US_TICKERS = {
    "QQQM": "QQQM",
    "VT": "VT",
}

PRICE_CACHE_TTL = 300       # 5 minutes
HISTORY_CACHE_TTL = 3600    # 1 hour

# Default US TWD cost basis (0 = not set; real value stored per-user in DB).
US_TWD_COST_BASIS = 0

# Pledge maintenance ratio thresholds (%)
PLEDGE_CRITICAL = 140.0
PLEDGE_WARNING  = 200.0
PLEDGE_SAFE     = 300.0

# Chart colours
COLOR_POSITIVE = "#00C896"
COLOR_NEGATIVE = "#FF4B5C"
COLOR_NEUTRAL  = "#4A90D9"
COLOR_WARNING  = "#FFB74D"
COLOR_CRITICAL = "#FF4B5C"
COLOR_PURPLE   = "#A855F7"
