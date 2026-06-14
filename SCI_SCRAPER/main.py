"""
SCI_SCRAPER – Supreme Court of India Judgment Downloader
=========================================================
Scrapes judgment PDFs from https://www.sci.gov.in/judgments

Usage:
    python main.py                          # last 30 days (default)
    python main.py --days 60               # last 60 days
    python main.py --from 2026-05-01 --to 2026-06-14   # custom range

Flow:
    1. Opens https://www.sci.gov.in/judgments in Chrome
    2. Fills in the date-range search form automatically
    3. Waits for results; if a CAPTCHA appears, pauses for manual solve
    4. For each judgment row: extracts metadata + downloads the PDF
       using route interception (aborts navigation, fetches via context.request)
    5. Paginates through all result pages
    6. Saves PDFs to output/pdfs/ and JSON metadata to output/metadata/
"""

import asyncio
import json
import logging
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urljoin

from playwright.async_api import async_playwright, BrowserContext, Page

from config import (
    BASE_URL, JUDGMENTS_URL,
    CHROME_PATH, HEADLESS, TIMEOUT,
    OUTPUT_DIR, PDF_DIR, META_DIR, LOG_DIR,
    DATE_FORMAT, DEFAULT_DAYS,
    VIEWPORTS, USER_AGENTS,
    DELAY_BETWEEN_PDFS,
)
from human import (
    random_delay, human_scroll, human_mouse_move,
    human_type, simulate_reading, dismiss_popups,
)

import random


# ── Logging ───────────────────────────────────────────────────────────────────

def _setup_logging() -> logging.Logger:
    Path(LOG_DIR).mkdir(parents=True, exist_ok=True)
    stamp    = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = Path(LOG_DIR) / f"scrape_{stamp}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(log_file, encoding="utf-8"),
        ],
    )
    return logging.getLogger("sci_scraper")


log = _setup_logging()


# ── Stealth JS ────────────────────────────────────────────────────────────────

_STEALTH = """
    Object.defineProperty(navigator, 'webdriver',  { get: () => undefined });
    Object.defineProperty(navigator, 'plugins',    { get: () => [1,2,3,4,5] });
    Object.defineProperty(navigator, 'languages',  { get: () => ['en-IN','en-US','en'] });
    Object.defineProperty(navigator, 'platform',   { get: () => 'Win32' });
    window.chrome = { runtime:{}, loadTimes:function(){}, csi:function(){} };
"""


# ── Browser factory ───────────────────────────────────────────────────────────

async def create_browser(playwright):
    viewport   = random.choice(VIEWPORTS)
    user_agent = random.choice(USER_AGENTS)
    log.info(f"Viewport: {viewport['width']}x{viewport['height']}")

    browser = await playwright.chromium.launch(
        headless=HEADLESS,
        executable_path=CHROME_PATH,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--disable-infobars",
            "--disable-dev-shm-usage",
            "--no-sandbox",
            "--disable-pdf-viewer",       # force PDF downloads, not inline render
            "--disable-plugins-discovery",
        ],
    )
    context = await browser.new_context(
        viewport=viewport,
        user_agent=user_agent,
        accept_downloads=True,
        locale="en-IN",
        timezone_id="Asia/Kolkata",
        extra_http_headers={
            "Accept-Language": "en-IN,en-US;q=0.9,en;q=0.8",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "DNT": "1",
        },
    )
    await context.add_init_script(_STEALTH)
    return browser, context


# ── Helpers ───────────────────────────────────────────────────────────────────

async def page_is_alive(page: Page) -> bool:
    try:
        await page.evaluate("1")
        return True
    except Exception:
        return False


async def cancel_task(task: asyncio.Task) -> None:
    if task and not task.done():
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass


async def save_debug_html(page: Page, label: str = "debug") -> None:
    try:
        html  = await page.content()
        stamp = datetime.now().strftime("%H%M%S")
        path  = Path(LOG_DIR) / f"{label}_{stamp}.html"
        path.write_text(html, encoding="utf-8")
        log.info(f"Debug HTML -> {path}")
    except Exception as e:
        log.warning(f"Cannot save debug HTML ({label}): {e}")


async def close_modal(page: Page) -> None:
    try:
        btn = await page.query_selector(
            ".modal .close, .modal [data-dismiss='modal'], "
            ".modal .btn-close, button[aria-label='Close']"
        )
        if btn:
            await btn.click(force=True)
        else:
            await page.keyboard.press("Escape")
        await asyncio.sleep(0.6)
    except Exception:
        pass


# ── Date helpers ──────────────────────────────────────────────────────────────

def date_range(days: int = DEFAULT_DAYS,
               from_date: str = None,
               to_date: str = None) -> tuple:
    """
    Return (from_str, to_str) in DD-MM-YYYY format.
    If from_date/to_date are given as YYYY-MM-DD strings, use them directly.
    """
    today = datetime.today()
    if from_date:
        dt_from = datetime.strptime(from_date, "%Y-%m-%d")
    else:
        dt_from = today - timedelta(days=days)
    if to_date:
        dt_to = datetime.strptime(to_date, "%Y-%m-%d")
    else:
        dt_to = today

    return dt_from.strftime(DATE_FORMAT), dt_to.strftime(DATE_FORMAT)


# ── Search form ───────────────────────────────────────────────────────────────

async def fill_search_form(page: Page, from_str: str, to_str: str) -> bool:
    """
    Fill the date-range search form on the SCI judgments page.
    Returns True if the form was submitted successfully.
    """
    log.info(f"Filling search form: {from_str} → {to_str}")

    # Wait for the form to appear
    try:
        await page.wait_for_selector(
            "#judgment_date_from, input[name='judgment_date_from'], "
            "input[name*='from'], input[id*='from'], #fromDate",
            timeout=TIMEOUT,
        )
    except Exception:
        log.warning("Date-from field not found – trying to proceed anyway")

    # Confirmed selectors from DevTools inspection of www.sci.gov.in/judgments
    from_selectors = [
        "#judgment_date_from",
        "input[name='judgment_date_from']",
        "#fromDate", "#from_date", "input[name='fromDate']",
        "input[name='from_date']", "input[placeholder*='From']",
        "input[id*='from']",
    ]
    to_selectors = [
        "#judgment_date_to",
        "input[name='judgment_date_to']",
        "#toDate", "#to_date", "input[name='toDate']",
        "input[name='to_date']", "input[placeholder*='To']",
        "input[id*='to']",
    ]

    filled_from = False
    for sel in from_selectors:
        try:
            el = await page.query_selector(sel)
            if el:
                await el.triple_click()
                await el.type(from_str, delay=80)
                filled_from = True
                log.info(f"  From date filled via {sel!r}")
                break
        except Exception:
            pass

    filled_to = False
    for sel in to_selectors:
        try:
            el = await page.query_selector(sel)
            if el:
                await el.triple_click()
                await el.type(to_str, delay=80)
                filled_to = True
                log.info(f"  To date filled via {sel!r}")
                break
        except Exception:
            pass

    if not filled_from or not filled_to:
        log.warning(
            f"Could not fill date fields automatically "
            f"(from={filled_from}, to={filled_to}). "
            "Please fill them manually in the browser."
        )

    await random_delay(0.5, 1.5)

    # Submit the form
    submit_selectors = [
        "#searchJudgment",                  # confirmed ID from SCI page
        "button[type='submit']", "input[type='submit']",
        "button:has-text('Search')", "button:has-text('Submit')",
        ".search-btn", "#searchBtn",
        "button.btn-primary", "button.btn-search",
    ]
    for sel in submit_selectors:
        try:
            btn = await page.query_selector(sel)
            if btn:
                await btn.click()
                log.info(f"  Form submitted via {sel!r}")
                await asyncio.sleep(3)
                return True
        except Exception:
            pass

    # Fallback: press Enter
    await page.keyboard.press("Enter")
    await asyncio.sleep(3)
    return True


# ── Result parsing ────────────────────────────────────────────────────────────

async def extract_judgment_rows(page: Page) -> list:
    """
    Extract judgment metadata and PDF links from the results table.
    Returns a list of dicts with keys: title, case_no, date, bench, pdf_url, pdf_href
    """
    rows = []
    try:
        # Wait for results table (confirmed ID: judgmentTable)
        await page.wait_for_selector(
            "#judgmentTable tbody tr, table#judgmentTable tbody tr, "
            "table tbody tr, .judgment-list li",
            timeout=TIMEOUT,
        )
        await asyncio.sleep(1)

        # SCI table has columns: S.No. | Case No. | Parties | Date | Subject | Download
        # The Download column (last) contains the PDF link
        pdf_link_selectors = [
            "#judgmentTable a[href]",       # confirmed table ID
            "table#judgmentTable td a",
            "a[href*='.pdf']",
            "a[href*='/pdf/']",
            "a[href*='judgment']",
            "a[href*='download']",
            "td:last-child a[href]",
            "td a[href]",
        ]

        seen_hrefs = set()

        for sel in pdf_link_selectors:
            links = await page.query_selector_all(sel)
            for link in links:
                try:
                    href = await link.get_attribute("href") or ""
                    text = (await link.inner_text()).strip()

                    if not href or href in seen_hrefs:
                        continue
                    seen_hrefs.add(href)

                    # Resolve relative URLs
                    full_url = urljoin(BASE_URL, href) if not href.startswith("http") else href

                    # Try to get row metadata from parent <tr>
                    row_data = {"pdf_href": href, "pdf_url": full_url, "link_text": text}

                    # Walk up to find the parent <tr>
                    tr = await link.evaluate_handle(
                        "el => el.closest('tr')"
                    )
                    if tr:
                        tds = await tr.query_selector_all("td")
                        for i, td in enumerate(tds):
                            row_data[f"col_{i}"] = (await td.inner_text()).strip()

                    rows.append(row_data)
                except Exception:
                    pass

            if rows:
                break  # found results with this selector

        log.info(f"Extracted {len(rows)} judgment link(s) from page")

    except Exception as exc:
        log.warning(f"Row extraction failed: {exc}")

    return rows


# ── PDF capture ───────────────────────────────────────────────────────────────

async def download_pdf(context: BrowserContext, page: Page,
                       pdf_url: str, referer: str = "") -> bytes | None:
    """
    Download a PDF using multiple strategies:
      1. Direct HTTP fetch via context.request.get() (fastest)
      2. Route interception (aborts navigation, fetches via context)
      3. Playwright download event
    Returns raw PDF bytes or None.
    """
    if not pdf_url:
        return None

    headers = {"Referer": referer or JUDGMENTS_URL}

    # ── Strategy 1: direct HTTP fetch ────────────────────────────────────────
    for attempt in range(3):
        try:
            resp = await context.request.get(pdf_url, headers=headers, timeout=TIMEOUT)
            if resp.ok:
                body = await resp.body()
                ct   = resp.headers.get("content-type", "")
                if body and (b"%PDF" in body[:10] or "pdf" in ct.lower()):
                    log.info(f"[Fetch] PDF: {len(body):,} B from {pdf_url[:80]}")
                    return body
                else:
                    log.debug(f"[Fetch] Not a PDF (ct={ct})")
                    break
            else:
                log.debug(f"[Fetch] HTTP {resp.status} attempt {attempt+1}")
        except Exception as e:
            log.debug(f"[Fetch] Error attempt {attempt+1}: {e}")
        await asyncio.sleep(1.5 * (attempt + 1))

    # ── Strategy 2: navigate page + route interception ───────────────────────
    if not await page_is_alive(page):
        return None

    click_done = [False]
    nav_url    = {"url": None}
    nav_event  = asyncio.Event()

    async def _intercept(route):
        if not click_done[0]:
            await route.continue_()
            return
        try:
            is_main = route.request.frame == page.main_frame
        except Exception:
            is_main = False
        if route.request.resource_type == "document" and is_main:
            url = route.request.url
            if url and url not in ("about:blank", ""):
                nav_url["url"] = url
                nav_event.set()
                await route.abort()
                return
        await route.continue_()

    await page.route("**/*", _intercept)
    download_task = asyncio.create_task(
        page.wait_for_event("download", timeout=15_000)
    )

    click_done[0] = True
    try:
        await page.goto(pdf_url, wait_until="commit", timeout=TIMEOUT)
    except Exception:
        pass

    nav_wait = asyncio.create_task(asyncio.wait_for(nav_event.wait(), 12.0))
    done, pending = await asyncio.wait(
        {download_task, nav_wait},
        timeout=15.0,
        return_when=asyncio.FIRST_COMPLETED,
    )
    for t in pending:
        await cancel_task(t)

    try:
        await page.unroute("**/*", _intercept)
    except Exception:
        pass

    # Handle download
    if download_task in done and not download_task.cancelled():
        try:
            dl   = download_task.result()
            tmp  = await dl.path()
            body = Path(tmp).read_bytes()
            log.info(f"[Download] PDF: {len(body):,} B")
            return body
        except Exception as e:
            log.debug(f"[Download] Error: {e}")

    # Handle intercepted navigation
    if nav_url["url"]:
        try:
            resp = await context.request.get(nav_url["url"], headers=headers, timeout=TIMEOUT)
            if resp.ok:
                body = await resp.body()
                if body and len(body) > 1_024:
                    log.info(f"[Intercept] PDF: {len(body):,} B")
                    return body
        except Exception as e:
            log.debug(f"[Intercept] Error: {e}")

    log.warning(f"All download strategies failed for {pdf_url[:80]}")
    return None


# ── Save to disk ──────────────────────────────────────────────────────────────

def save_pdf(content: bytes, index: int, row_data: dict) -> Path:
    stamp    = datetime.now().strftime("%Y%m%d_%H%M%S")
    # Try to build a meaningful filename from metadata
    case_no  = row_data.get("col_1", row_data.get("col_0", "")).strip()
    safe     = re.sub(r"[^\w\-]", "_", case_no)[:60] if case_no else "judgment"
    filename = f"{index:04d}_{stamp}_{safe}.pdf"
    path     = Path(PDF_DIR) / filename
    path.write_bytes(content)
    log.info(f"Saved PDF: {path.name}  ({len(content):,} B)")
    return path


def save_metadata(row_data: dict, pdf_path: Path) -> None:
    meta = {
        **row_data,
        "pdf_file":   str(pdf_path),
        "scraped_at": datetime.now().isoformat(),
    }
    meta_path = Path(META_DIR) / (pdf_path.stem + ".json")
    meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False))


# ── Pagination ────────────────────────────────────────────────────────────────

async def go_to_next_page(page: Page) -> bool:
    """Click the 'Next' pagination button. Returns True if navigated."""
    next_selectors = [
        "a.paginate_button.next:not(.disabled)",
        "li.paginate_button.next:not(.disabled) a",
        "a[id$='_next']:not(.disabled)",
        ".dataTables_paginate .next:not(.disabled)",
        "a[aria-label='Next']:not([aria-disabled='true'])",
        "a:has-text('Next')",
        "li.next a",
        ".pagination .next a",
    ]
    for sel in next_selectors:
        try:
            btn = await page.query_selector(sel)
            if btn:
                await btn.click()
                await asyncio.sleep(3)
                log.info("Navigated to next page")
                return True
        except Exception:
            pass
    return False


# ── Main scrape loop ──────────────────────────────────────────────────────────

async def scrape(days: int = DEFAULT_DAYS,
                 from_date: str = None,
                 to_date: str = None) -> None:

    for d in (OUTPUT_DIR, PDF_DIR, META_DIR, LOG_DIR):
        Path(d).mkdir(parents=True, exist_ok=True)

    from_str, to_str = date_range(days, from_date, to_date)
    log.info(f"Date range: {from_str} → {to_str}")

    async with async_playwright() as p:
        browser, context = await create_browser(p)
        page = await context.new_page()

        try:
            # ── 1. Open judgments page ────────────────────────────────────────
            log.info(f"Opening: {JUDGMENTS_URL}")
            await page.goto(JUDGMENTS_URL, wait_until="commit", timeout=TIMEOUT)
            await asyncio.sleep(3)
            await dismiss_popups(page)

            # ── 2. CAPTCHA pause (before form fill) ───────────────────────────
            log.info("=" * 62)
            log.info("  STEP 1 – Solve CAPTCHA (if shown) in the browser.")
            log.info("  Once the judgments search form is visible,")
            log.info("  press ENTER here so the scraper can fill the dates.")
            log.info("=" * 62)
            await asyncio.get_event_loop().run_in_executor(
                None, input,
                "\n  >>> Press ENTER once the search form is visible: "
            )
            await asyncio.sleep(1)

            # ── 3. Fill date-range form ───────────────────────────────────────
            form_ok = await fill_search_form(page, from_str, to_str)

            # ── 4. Wait for results ───────────────────────────────────────────
            log.info("=" * 62)
            log.info("  STEP 2 – Verify the date fields are correct,")
            log.info("  wait for the results table to load,")
            log.info("  then press ENTER to start downloading.")
            log.info("=" * 62)
            await asyncio.get_event_loop().run_in_executor(
                None, input,
                "\n  >>> Press ENTER once results are visible: "
            )
            log.info("Resuming...")
            # Save debug HTML to inspect table structure
            await save_debug_html(page, "results_page")
            await random_delay(1.5, 3.0)
            await simulate_reading(page, duration=2.0)

            # ── 4. Paginated download loop ────────────────────────────────────
            total_downloaded = 0
            page_num         = 0

            while True:
                if not await page_is_alive(page):
                    log.error("Page closed – stopping")
                    break

                rows = await extract_judgment_rows(page)
                if not rows:
                    log.info("No judgment links found on this page – stopping")
                    await save_debug_html(page, f"no_results_page{page_num+1}")
                    break

                log.info(f"Page {page_num + 1}: {len(rows)} judgment(s) | total so far: {total_downloaded}")

                for i, row in enumerate(rows):
                    pdf_url = row.get("pdf_url", "")
                    if not pdf_url:
                        log.warning(f"  Row {i+1}: no PDF URL – skipping")
                        continue

                    log.info(f"  [{total_downloaded + 1}] {row.get('link_text', pdf_url)[:60]}")

                    pdf_bytes = await download_pdf(context, page, pdf_url,
                                                   referer=page.url)

                    if pdf_bytes and len(pdf_bytes) > 512:
                        pdf_path = save_pdf(pdf_bytes, total_downloaded + 1, row)
                        save_metadata(row, pdf_path)
                        total_downloaded += 1
                    else:
                        log.error(f"  [{total_downloaded + 1}] Download failed")

                    await asyncio.sleep(DELAY_BETWEEN_PDFS + random.uniform(0, 2))

                # ── Pagination ────────────────────────────────────────────────
                if not await page_is_alive(page):
                    break
                if not await go_to_next_page(page):
                    log.info("No next page – all results downloaded")
                    break
                page_num += 1

            log.info("=" * 55)
            log.info(f"  DONE – {total_downloaded} PDF(s) downloaded")
            log.info(f"  PDFs     -> {Path(PDF_DIR).resolve()}")
            log.info(f"  Metadata -> {Path(META_DIR).resolve()}")
            log.info("=" * 55)

        except Exception as exc:
            log.error(f"Fatal: {exc}", exc_info=True)
            await save_debug_html(page, "fatal_error")

        finally:
            await asyncio.sleep(2)
            try:
                await context.close()
                await browser.close()
            except Exception:
                pass
            log.info("Browser closed.")


# ── CLI ───────────────────────────────────────────────────────────────────────

def _parse_args():
    args      = sys.argv[1:]
    days      = DEFAULT_DAYS
    from_date = None
    to_date   = None

    if "--days" in args:
        idx = args.index("--days")
        try:
            days = int(args[idx + 1])
        except (IndexError, ValueError):
            pass

    if "--from" in args:
        idx = args.index("--from")
        try:
            from_date = args[idx + 1]
        except IndexError:
            pass

    if "--to" in args:
        idx = args.index("--to")
        try:
            to_date = args[idx + 1]
        except IndexError:
            pass

    return days, from_date, to_date


if __name__ == "__main__":
    d, f, t = _parse_args()
    log.info(f"SCI Scraper | days={d} | from={f} | to={t}")
    asyncio.run(scrape(days=d, from_date=f, to_date=t))