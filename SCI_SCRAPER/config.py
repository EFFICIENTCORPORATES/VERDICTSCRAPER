"""
Configuration for SCI_SCRAPER – Supreme Court of India (www.sci.gov.in)
"""

# ── Target URLs ───────────────────────────────────────────────────────────────
BASE_URL      = "https://www.sci.gov.in"
JUDGMENTS_URL = "https://www.sci.gov.in/judgments"

# ── Date range ────────────────────────────────────────────────────────────────
# The SCI form uses DD-MM-YYYY format.
# Default: last 30 days. Override via CLI --days N or --from/--to.
DEFAULT_DAYS   = 30
DATE_FORMAT    = "%d/%m/%Y"   # format the SCI search form expects  (DD/MM/YYYY)

# ── Output paths ─────────────────────────────────────────────────────────────
OUTPUT_DIR = "output"
PDF_DIR    = "output/pdfs"
META_DIR   = "output/metadata"
LOG_DIR    = "output/logs"

# ── Browser ───────────────────────────────────────────────────────────────────
CHROME_PATH = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
HEADLESS    = False   # set True to run without a visible window

# ── Timing ───────────────────────────────────────────────────────────────────
DELAY_MIN          = 2.0   # seconds between actions
DELAY_MAX          = 6.0
DELAY_BETWEEN_PDFS = 3.0   # seconds between PDF downloads
TIMEOUT            = 60_000  # ms for Playwright waits

# ── Browser fingerprint pool ──────────────────────────────────────────────────
VIEWPORTS = [
    {"width": 1920, "height": 1080},
    {"width": 1366, "height": 768},
    {"width": 1440, "height": 900},
    {"width": 1280, "height": 800},
]

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
]