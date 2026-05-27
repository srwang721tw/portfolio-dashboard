from pathlib import Path
import os
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).parent.parent
DATA_DIR = Path(os.getenv("DATA_DIR", BASE_DIR / "data"))
DATA_DIR.mkdir(exist_ok=True)

APP_NAME = "投資組合儀表板"
APP_ICON = "📈"
APP_SECRET_KEY = os.getenv("APP_SECRET_KEY", "dev_secret_change_in_production")

USERS_FILE = DATA_DIR / "users.json"
PLEDGE_FILE = DATA_DIR / "pledge_config.json"
HISTORY_FILE = DATA_DIR / "portfolio_history.json"
US_COST_CONFIG_FILE = DATA_DIR / "us_cost_config.json"
TW_CSV_FILE = DATA_DIR / "tw_stocks.csv"
US_CSV_FILE = DATA_DIR / "us_stocks.csv"
SAMPLE_TW_CSV = BASE_DIR / "data" / "sample_tw_stocks.csv"
SAMPLE_US_CSV = BASE_DIR / "data" / "sample_us_stocks.csv"

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

PRICE_CACHE_TTL = 300       # 5 minutes (matches auto-refresh interval)
HISTORY_CACHE_TTL = 3600    # 1 hour

# Actual TWD amount invested in US stocks (bank transfer total).
# Update this whenever you add more US funds.
US_TWD_COST_BASIS = 2_188_129

# Pledge thresholds
PLEDGE_CRITICAL = 140.0     # Margin call
PLEDGE_WARNING = 200.0      # Warning
PLEDGE_SAFE = 300.0         # Safe zone

# Colors
COLOR_POSITIVE = "#00C896"
COLOR_NEGATIVE = "#FF4B5C"
COLOR_NEUTRAL = "#4A90D9"
COLOR_WARNING = "#FFB74D"
COLOR_CRITICAL = "#FF4B5C"
COLOR_PURPLE = "#A855F7"

PLOTLY_LAYOUT = dict(
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(0,0,0,0)",
    font=dict(color="#E6EDF3", family="sans-serif"),
    xaxis=dict(gridcolor="#30363D", zerolinecolor="#30363D"),
    yaxis=dict(gridcolor="#30363D", zerolinecolor="#30363D"),
    margin=dict(l=10, r=10, t=40, b=10),
    legend=dict(bgcolor="rgba(0,0,0,0)"),
)
