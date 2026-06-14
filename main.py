"""
VerdictFinder Scraper — Supreme Court of India
================================================
Usage:
    python main.py            # scrape 1 case
    python main.py --count 3  # scrape first 3 cases
    python main.py --tor      # route through Tor (Tor Browser must be running)

Flow:
    1. Opens browser with persistent profile (cookies/history accumulate)
    2. Pauses at homepage for you to solve the CAPTCHA manually
    3. Navigates to search results
    4. For each case: hovers → clicks VIEW PDF → intercepts the PDF at network
       level (works whether the site shows a modal, new tab, or download)
    5. Saves PDF + JSON metadata to trainingdata/scraped/
"""

import asyncio
import json
import logging
import re
import sys
from datetime import datetime
from pathlib import Path

from playwright.async_api import async_playwright, BrowserContext, Page

from config import (
    HOME_URL, SEARCH_URL, BASE_URL,
    OUTPUT_DIR, LOG_DIR,
    VIEWPORTS, USER_AGENTS, TOR_PROXY, PROFILE_DIR,
)
from human import (
    random_delay,
    human_mouse_move,
    simulate_reading,
    human_scroll,
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
    return logging.getLogger("scraper")


log = _setup_logging()


# ── Stealth JS injected into every page ──────────────────────────────────────

_STEALTH_SCRIPT = """
    Object.defineProperty(navigator, 'webdriver',  { get: () => undefined });
    Object.defineProperty(navigator, 'plugins',    { get: () => [1, 2, 3, 4, 5] });
    Object.defineProperty(navigator, 'languages',  { get: () => ['en-IN', 'en-US', 'en'] });
    Object.defineProperty(navigator, 'platform',   { get: () => 'Win32' });
    window.chrome = { runtime: {}, loadTimes: function(){}, csi: function(){} };
    const _origPermQuery = window.navigator.permissions.query.bind(navigator.permissions);
    window.navigator.permissions.query = (params) =>
        params.name === 'notifications'
            ? Promise.resolve({ state: Notification.permission })
            : _origPermQuery(params);
"""


# ── Browser factory (persistent profile) ─────────────────────────────────────

async def create_browser(playwright, use_tor: bool = False):
    """
    Launch Chromium with a persistent profile in scraper/browser_profile/.
    Cookies, cache, and history accumulate across runs — looks like a
    returning human rather than a fresh bot each time.
    Fingerprint (viewport + UA) is picked once on first run and locked.
    """
    profile_path = Path(__file__).parent / PROFILE_DIR
    profile_path.mkdir(parents=True, exist_ok=True)

    fp_file = profile_path / "fingerprint.json"
    if fp_file.exists():
        fp         = json.loads(fp_file.read_text())
        viewport   = fp["viewport"]
        user_agent = fp["user_agent"]
        log.info("Loaded existing browser fingerprint.")
    else:
        viewport   = random.choice(VIEWPORTS)
        user_agent = random.choice(USER_AGENTS)
        fp_file.write_text(json.dumps({"viewport": viewport, "user_agent": user_agent}))
        log.info("New fingerprint created and saved.")

    log.info(f"Profile  : {profile_path}")
    log.info(f"Viewport : {viewport['width']}x{viewport['height']}")
    log.info(f"UserAgent: {user_agent[:72]}...")

    launch_kwargs = dict(
        headless=False,
        viewport=viewport,
        user_agent=user_agent,
        accept_downloads=True,
        locale="en-IN",
        timezone_id="Asia/Kolkata",
        extra_http_headers={
            "Accept-Language": "en-IN,en-US;q=0.9,en;q=0.8",
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;"
                "q=0.9,image/avif,image/webp,*/*;q=0.8"
            ),
            "DNT": "1",
        },
        args=[
            "--disable-blink-features=AutomationControlled",
            "--disable-infobars",
            "--disable-dev-shm-usage",
            "--no-sandbox",
            "--disable-gpu",
        ],
    )

    if use_tor:
        launch_kwargs["proxy"] = {"server": TOR_PROXY}
        log.info(f"Routing through Tor ({TOR_PROXY})")

    context = await playwright.chromium.launch_persistent_context(
        str(profile_path),
        **launch_kwargs,
    )
    await context.add_init_script(_STEALTH_SCRIPT)
    return context


# ── CAPTCHA pause ─────────────────────────────────────────────────────────────

async def wait_for_captcha(page: Page) -> None:
    log.info("━" * 62)
    log.info("  CAPTCHA — solve it in the browser, then press ENTER here.")
    log.info("━" * 62)
    await asyncio.get_event_loop().run_in_executor(
        None, input, "\n  >>> Press ENTER after solving CAPTCHA: "
    )
    log.info("Resuming...")
    await random_delay(1.5, 3.0)


# ── Debug helper ──────────────────────────────────────────────────────────────

async def save_debug_html(page: Page, label: str = "debug") -> Path:
    html  = await page.content()
    stamp = datetime.now().strftime("%H%M%S")
    path  = Path(LOG_DIR) / f"{label}_{stamp}.html"
    path.write_text(html, encoding="utf-8")
    log.info(f"Debug HTML saved → {path}")
    return path


# ── Result-page parsing ───────────────────────────────────────────────────────

async def find_pdf_buttons(page: Page) -> list:
    """
    Locate the case buttons in the search results table.

    From DevTools inspection: each result row contains a
      <button class="show-modal-btn" data-diaryno="..." data-orderdate="...">
    That is the element to click — it opens the Enscript Output modal with the PDF.

    Selector confirmed from browser DevTools:
      table.table-bordered.nowrap.dataTable tbody tr td button.show-modal-btn
    """
    # Primary — exact selector confirmed from DevTools
    primary = "button.show-modal-btn"
    try:
        elements = await page.query_selector_all(primary)
        if elements:
            log.info(f"Found {len(elements)} case button(s) via primary selector: {primary!r}")
            return elements
    except Exception as exc:
        log.warning(f"Primary selector failed: {exc}")

    # Fallback 1 — any button with data-diaryno attribute
    fallback1 = "button[data-diaryno]"
    try:
        elements = await page.query_selector_all(fallback1)
        if elements:
            log.info(f"Found {len(elements)} button(s) via fallback: {fallback1!r}")
            return elements
    except Exception:
        pass

    # Fallback 2 — scan all buttons inside the results table
    log.warning("Confirmed selectors found nothing — scanning table buttons...")
    candidates = []
    for btn in await page.query_selector_all("table button, table a"):
        text = (await btn.inner_text()).strip()
        if text:
            candidates.append(btn)

    log.info(f"Fallback table scan: {len(candidates)} candidate(s)")
    return candidates


async def extract_table_rows(page: Page) -> list[dict]:
    """
    Pull structured metadata from each result row.

    From DevTools: each row has a <button class="show-modal-btn">
    with data-diaryno and data-orderdate attributes.
    We extract those plus the full case name text.
    """
    rows = []
    try:
        await page.wait_for_selector("table.dataTable", timeout=12_000)
        for i, tr in enumerate(await page.query_selector_all("table tbody tr")):

            row_data = {"row_index": i}

            # Pull data attributes from the show-modal-btn inside this row
            btn = await tr.query_selector("button.show-modal-btn")
            if btn:
                row_data["case_name"]   = (await btn.inner_text()).strip()
                row_data["diary_no"]    = await btn.get_attribute("data-diaryno") or ""
                row_data["order_date"]  = await btn.get_attribute("data-orderdate") or ""

            # Also capture plain cell text as fallback columns
            for j, td in enumerate(await tr.query_selector_all("td")):
                row_data[f"col_{j}"] = (await td.inner_text()).strip()

            rows.append(row_data)

        log.info(f"Table: {len(rows)} row(s) extracted")
        if rows:
            log.info(f"  First case: {rows[0].get('case_name', '?')} | Diary: {rows[0].get('diary_no', '?')}")
    except Exception as exc:
        log.warning(f"Table extraction skipped: {exc}")
    return rows


# ── PDF capture — network interception (primary method) ───────────────────────

async def capture_pdf(
    page: Page,
    context: BrowserContext,
    element,
) -> tuple[bytes | None, str]:
    """
    Hover over element, then click it and capture the PDF.

    WHY NETWORK INTERCEPTION:
    This site opens a modal/dialog with an embedded PDF viewer when you
    click VIEW PDF. Our earlier approach (waiting for a new tab or download)
    misses this pattern entirely.

    Network interception works regardless of HOW the PDF is displayed —
    modal, new tab, inline viewer, or download — because we catch the actual
    HTTP response carrying the PDF bytes before the browser renders it.

    Fallback chain:
      1. Network interception (catches modal/iframe/any pattern)
      2. Modal DOM scan (extract iframe src from dialog element)
      3. New browser tab
      4. Browser download event
    """
    pdf_result: dict = {"bytes": None, "filename": "case.pdf"}
    pdf_ready  = asyncio.Event()

    # ── Hover to look human ───────────────────────────────────────────────────
    box = await element.bounding_box()
    if box:
        cx = box["x"] + box["width"]  * random.uniform(0.3, 0.7)
        cy = box["y"] + box["height"] * random.uniform(0.3, 0.7)
        await human_mouse_move(page, cx, cy)
    await element.hover()
    await random_delay(0.3, 0.8)

    # ── Attempt 1: intercept PDF at network level ─────────────────────────────
    # Registers a response listener on the page BEFORE clicking, so we catch
    # the PDF response the moment the browser fetches it.

    async def _on_response(response):
        if pdf_ready.is_set():
            return
        content_type = response.headers.get("content-type", "")
        url          = response.url
        is_pdf = (
            "application/pdf" in content_type
            or url.lower().split("?")[0].endswith(".pdf")
        )
        if not is_pdf:
            return
        try:
            body = await response.body()
            if body and len(body) > 1_024:   # >1 KB — real PDF, not an error page
                fname = url.rstrip("/").split("?")[0].split("/")[-1]
                if not fname.lower().endswith(".pdf"):
                    fname = f"judgment_{datetime.now().strftime('%H%M%S')}.pdf"
                pdf_result["bytes"]    = body
                pdf_result["filename"] = fname
                pdf_ready.set()
                log.info(f"PDF intercepted from network: {url}")
                log.info(f"  Content-Type : {content_type}")
                log.info(f"  Size         : {len(body):,} bytes")
        except Exception as exc:
            log.debug(f"Response body read error: {exc}")

    page.on("response", _on_response)

    try:
        await element.click()
        await asyncio.wait_for(pdf_ready.wait(), timeout=15.0)
        if pdf_result["bytes"]:
            return pdf_result["bytes"], pdf_result["filename"]
    except asyncio.TimeoutError:
        log.info("Network interception timed out — trying modal scan...")
    finally:
        page.remove_listener("response", _on_response)

    # ── Attempt 2: modal/dialog DOM scan ─────────────────────────────────────
    # If the PDF is inside a <dialog> or modal <div>, look for its iframe src.
    try:
        await asyncio.sleep(2.5)   # let the modal fully render

        modal_selectors = [
            "dialog[open]", ".modal.show .modal-body",
            "#pdfModal", "#viewModal", "#docModal",
            "[role='dialog']", ".modal-content",
            ".popup", ".overlay", ".lightbox",
        ]
        embed_attrs = [
            ("iframe",  "src"),
            ("object",  "data"),
            ("embed",   "src"),
        ]

        pdf_src = None

        for modal_sel in modal_selectors:
            modal = await page.query_selector(modal_sel)
            if not modal:
                continue
            for tag, attr in embed_attrs:
                el = await modal.query_selector(f"{tag}[{attr}]")
                if el:
                    src = (await el.get_attribute(attr) or "").strip()
                    if src and src not in ("about:blank", "") and "pdf" in src.lower():
                        pdf_src = src
                        log.info(f"PDF src found in modal ({modal_sel}→{tag}): {src[:80]}")
                        break
            if pdf_src:
                break

        # Also scan page-level iframes that appeared after the click
        if not pdf_src:
            for iframe in await page.query_selector_all("iframe[src]"):
                src = (await iframe.get_attribute("src") or "").strip()
                if src and "pdf" in src.lower() and src != "about:blank":
                    pdf_src = src
                    log.info(f"PDF iframe on page: {src[:80]}")
                    break

        if pdf_src:
            if pdf_src.startswith("//"):
                pdf_src = "https:" + pdf_src
            elif pdf_src.startswith("/"):
                pdf_src = BASE_URL.rstrip("/") + pdf_src

            resp = await page.request.get(pdf_src)
            if resp.ok:
                body = await resp.body()
                fname = pdf_src.split("?")[0].rstrip("/").split("/")[-1]
                if not fname.lower().endswith(".pdf"):
                    fname += ".pdf"
                log.info(f"PDF fetched from modal src: {len(body):,} bytes")
                return body, fname

    except Exception as exc:
        log.info(f"Modal scan failed: {exc} — trying new-tab...")

    # ── Attempt 3: new browser tab ────────────────────────────────────────────
    try:
        async with context.expect_page(timeout=8_000) as new_page_info:
            await element.click()
        new_page = await new_page_info.value
        await new_page.wait_for_load_state("networkidle", timeout=20_000)
        pdf_url  = new_page.url
        log.info(f"PDF in new tab: {pdf_url}")
        response = await new_page.goto(pdf_url)
        body     = await response.body() if response else None
        fname    = pdf_url.split("?")[0].rstrip("/").split("/")[-1]
        if not fname.lower().endswith(".pdf"):
            fname += ".pdf"
        await new_page.close()
        return body, fname
    except Exception as exc:
        log.info(f"New-tab failed: {exc} — trying download event...")

    # ── Attempt 4: browser download event ────────────────────────────────────
    try:
        async with page.expect_download(timeout=12_000) as dl_info:
            await element.click()
        dl       = await dl_info.value
        tmp      = await dl.path()
        fname    = dl.suggested_filename or "case.pdf"
        body     = Path(tmp).read_bytes()
        log.info(f"PDF via download: {fname} ({len(body):,} bytes)")
        return body, fname
    except Exception as exc:
        log.error(f"All 4 capture methods failed. Last error: {exc}")
        return None, "case.pdf"


# ── Save to disk ──────────────────────────────────────────────────────────────

def save_pdf(content: bytes, filename: str, index: int) -> Path:
    safe  = re.sub(r"[^\w\-.]", "_", filename)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    name  = f"{index:04d}_{stamp}_{safe}"
    path  = Path(OUTPUT_DIR) / name
    path.write_bytes(content)
    log.info(f"Saved: {path}  ({len(content):,} bytes)")
    return path


def save_metadata(row_data: dict, pdf_path: Path) -> None:
    meta = {**row_data, "pdf_file": str(pdf_path), "scraped_at": datetime.now().isoformat()}
    meta_path = Path(LOG_DIR) / (pdf_path.stem + ".json")
    meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False))
    log.info(f"Metadata: {meta_path}")


# ── Main ──────────────────────────────────────────────────────────────────────

async def run(count: int = 1, use_tor: bool = False) -> None:
    Path(OUTPUT_DIR).mkdir(parents=True, exist_ok=True)
    Path(LOG_DIR).mkdir(parents=True, exist_ok=True)

    async with async_playwright() as p:
        context = await create_browser(p, use_tor=use_tor)
        page    = await context.new_page()

        try:
            # ── 1. Homepage → CAPTCHA ─────────────────────────────────────
            log.info(f"Opening: {HOME_URL}")
            await page.goto(HOME_URL, wait_until="domcontentloaded", timeout=30_000)
            await random_delay(2.0, 4.0)
            await wait_for_captcha(page)

            # ── 2. Search results ─────────────────────────────────────────
            log.info(f"Loading search results: {SEARCH_URL}")
            await page.goto(SEARCH_URL, wait_until="networkidle", timeout=30_000)
            await random_delay(2.5, 5.5)
            await simulate_reading(page, duration=random.uniform(3.0, 6.0))

            # ── 3. Find PDF buttons ───────────────────────────────────────
            pdf_buttons = await find_pdf_buttons(page)
            if not pdf_buttons:
                log.error("No PDF buttons found — saving debug HTML.")
                await save_debug_html(page, "no_pdf_buttons")
                return

            table_rows   = await extract_table_rows(page)
            target_count = min(count, len(pdf_buttons))
            log.info(f"Scraping {target_count} case(s)...")

            # ── 4. Per-case loop ──────────────────────────────────────────
            for i in range(target_count):
                log.info(f"\n{'─'*55}")
                log.info(f"Case {i+1} / {target_count}")

                await random_delay(1.5, 4.0)
                await human_scroll(page)
                await random_delay(0.5, 1.5)

                btn = pdf_buttons[i]

                # capture_pdf handles hover + click internally
                pdf_bytes, filename = await capture_pdf(page, context, btn)

                if pdf_bytes and len(pdf_bytes) > 512:
                    pdf_path = save_pdf(pdf_bytes, filename, i + 1)
                    save_metadata(table_rows[i] if i < len(table_rows) else {}, pdf_path)
                else:
                    log.error(f"Case {i+1}: no PDF captured.")
                    await save_debug_html(page, f"case_{i+1:04d}_failed")

                if i < target_count - 1:
                    gap = random.uniform(5.0, 12.0)
                    log.info(f"Waiting {gap:.1f}s before next case...")
                    await asyncio.sleep(gap)

            log.info("\n" + "━" * 55)
            log.info(f"  Done — {target_count} case(s) scraped.")
            log.info(f"  PDFs → {Path(OUTPUT_DIR).resolve()}")
            log.info("━" * 55)

        except Exception as exc:
            log.error(f"Fatal: {exc}", exc_info=True)
            try:
                await save_debug_html(page, "fatal_error")
            except Exception:
                pass

        finally:
            await random_delay(2.0, 4.0)
            await context.close()
            log.info("Browser closed. Profile saved.")


# ── CLI ───────────────────────────────────────────────────────────────────────

def _parse_args() -> tuple[int, bool]:
    args    = sys.argv[1:]
    use_tor = "--tor" in args
    count   = 1
    if "--count" in args:
        idx = args.index("--count")
        try:
            count = int(args[idx + 1])
        except (IndexError, ValueError):
            log.warning("--count requires a number. Using 1.")
    return count, use_tor


if __name__ == "__main__":
    n, tor = _parse_args()
    log.info(f"Starting | cases={n} | tor={tor}")
    asyncio.run(run(count=n, use_tor=tor))
