# ── URLs ─────────────────────────────────────────────────────────────────────

HOME_URL   = "https://verdictfinder.sci.gov.in/elk_frontend/"
SEARCH_URL = (
    "https://verdictfinder.sci.gov.in/elk_frontend/"
    "free_text_search.php?search=THE&text-filter=b&new_search=true"
)
BASE_URL   = "https://verdictfinder.sci.gov.in/elk_frontend/"

# ── Output paths ──────────────────────────────────────────────────────────────

OUTPUT_DIR = "../trainingdata/scraped/pdfs"
LOG_DIR    = "../trainingdata/scraped/logs"

# ── Human behaviour timing (seconds) ─────────────────────────────────────────

DELAY_MIN        = 2.0   # minimum wait between actions
DELAY_MAX        = 8.0   # maximum wait between actions
TYPE_DELAY_MIN   = 0.05  # min pause between keystrokes
TYPE_DELAY_MAX   = 0.22  # max pause between keystrokes
THINK_PAUSE_PROB = 0.05  # probability of a longer "thinking" pause while typing

# ── Browser fingerprint pool ──────────────────────────────────────────────────

VIEWPORTS = [
    {"width": 1920, "height": 1080},
    {"width": 1366, "height": 768},
    {"width": 1440, "height": 900},
    {"width": 1280, "height": 800},
    {"width": 1536, "height": 864},
]

USER_AGENTS = [
    # Chrome on Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    # Chrome on macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    # Firefox on Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0",
    # Edge on Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0",
]

# ── Persistent browser profile ────────────────────────────────────────────────

# Profile folder sits next to this file (scraper/browser_profile/).
# Cookies, cache, localStorage accumulate here across runs — looks more human
# over time because the site sees a returning visitor, not a fresh browser.
PROFILE_DIR = "browser_profile"

# ── Tor (optional) ────────────────────────────────────────────────────────────

TOR_PROXY = "socks5://127.0.0.1:9050"  # default Tor SOCKS5 port
