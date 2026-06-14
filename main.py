"""
VerdictFinder Scraper – Supreme Court of India
================================================
Usage:
    python main.py            # scrape 1 case
    python main.py --count 3  # scrape first 3 cases
    python main.py --all      # scrape every PDF across all pages
    python main.py --tor      # route through Tor

Flow:
    1. Opens browser with system Chrome
    2. Pauses at homepage for you to solve the CAPTCHA + submit a search
    3. Once the results table is visible, press ENTER
    4. For each case: clicks the show-modal-btn, captures the PDF via whichever
       mechanism fires first:
         A. Route interception  – aborts main-frame PDF navigation, fetches directly
         B. Network response    – PDF served as HTTP response
         C. Browser download    – PDF download event
         D. Modal iframe scan   – PDF embedded in Bootstrap modal
         E. New page / window   – PDF opened in new tab
    5. Paginates automatically until all results are exhausted
    6. Saves PDF + JSON metadata to trainingdata/scraped/
"""

import asyncio
import json
import logging
import re
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin

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


# -- Logging ------------------------------------------------------------------

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


# -- Stealth JS ---------------------------------------------------------------

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


# -- Browser factory ----------------------------------------------------------

async def create_browser(playwright, use_tor: bool = False):
    fp_dir  = Path(__file__).parent / PROFILE_DIR
    fp_dir.mkdir(parents=True, exist_ok=True)
    fp_file = fp_dir / "fingerprint.json"

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

    log.info(f"Viewport : {viewport['width']}x{viewport['height']}")
    log.info(f"UserAgent: {user_agent[:72]}...")

    chrome_path = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
    proxy = {"server": TOR_PROXY} if use_tor else None
    if use_tor:
        log.info(f"Routing through Tor ({TOR_PROXY})")

    browser = await playwright.chromium.launch(
        headless=False,
        executable_path=chrome_path,
        proxy=proxy,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--disable-infobars",
            "--disable-dev-shm-usage",
            "--no-sandbox",
            "--disable-pdf-viewer",
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
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;"
                "q=0.9,image/avif,image/webp,*/*;q=0.8"
            ),
            "DNT": "1",
        },
    )
    await context.add_init_script(_STEALTH_SCRIPT)
    return browser, context


# -- Helpers ------------------------------------------------------------------

async def page_is_alive(page: Page) -> bool:
    try:
        await page.evaluate("1 + 1")
        return True
    except Exception:
        return False


async def close_modal(page: Page) -> None:
    try:
        close_btn = await page.query_selector(
            ".modal .close, .modal [data-dismiss='modal'], "
            ".modal .btn-close, .modal button[aria-label='Close']"
        )
        if close_btn:
            await close_btn.click(force=True)
        else:
            await page.keyboard.press("Escape")
        await asyncio.sleep(0.8)
    except Exception:
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


async def cancel_task(task: asyncio.Task) -> None:
    """Cancel a task and suppress CancelledError."""
    if task and not task.done():
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass


# -- Result-page parsing ------------------------------------------------------

async def find_pdf_buttons(page: Page) -> list:
    for selector in ("button.show-modal-btn", "button[data-diaryno]"):
        try:
            els = await page.query_selector_all(selector)
            if els:
                log.info(f"Found {len(els)} button(s) via {selector!r}")
                return els
        except Exception:
            pass

    log.warning("Scanning table buttons as last resort...")
    candidates = []
    for btn in await page.query_selector_all("table button, table a"):
        if (await btn.inner_text()).strip():
            candidates.append(btn)
    log.info(f"Fallback scan: {len(candidates)} candidate(s)")
    return candidates


async def extract_table_rows(page: Page) -> list:
    rows = []
    try:
        await page.wait_for_selector("table.dataTable", timeout=12_000)
        for i, tr in enumerate(await page.query_selector_all("table tbody tr")):
            row = {"row_index": i}
            btn = await tr.query_selector("button.show-modal-btn")
            if btn:
                row["case_name"]  = (await btn.inner_text()).strip()
                row["diary_no"]   = await btn.get_attribute("data-diaryno") or ""
                row["order_date"] = await btn.get_attribute("data-orderdate") or ""
            for j, td in enumerate(await tr.query_selector_all("td")):
                row[f"col_{j}"] = (await td.inner_text()).strip()
            rows.append(row)
        log.info(f"Table: {len(rows)} row(s)")
        if rows:
            log.info(f"  First: {rows[0].get('case_name','?')} | Diary: {rows[0].get('diary_no','?')}")
    except Exception as exc:
        log.warning(f"Table extraction skipped: {exc}")
    return rows


# -- PDF capture --------------------------------------------------------------

async def capture_pdf(page: Page, context: BrowserContext, element) -> tuple:
    """
    Click the element ONCE and capture the PDF via whichever mechanism fires:
      A. Route interception  – intercepts main-frame PDF navigation, aborts it,
                               downloads the PDF via context.request.get()
      B. Network response    – PDF served as HTTP response
      C. Browser download    – download event
      D. Modal iframe scan   – PDF embedded in Bootstrap modal
      E. New page / window   – PDF opened in new tab
    Returns (pdf_bytes, filename).
    """
    # Hover
    box = await element.bounding_box()
    if box:
        cx = box["x"] + box["width"]  * random.uniform(0.3, 0.7)
        cy = box["y"] + box["height"] * random.uniform(0.3, 0.7)
        await human_mouse_move(page, cx, cy)
        await element.hover()
    await random_delay(0.2, 0.6)

    # ── A: Route interceptor ─────────────────────────────────────────────────
    # Intercepts any main-frame document navigation AFTER the click.
    # Aborts the navigation so the page doesn't close, captures the URL.
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
                log.info(f"[Route] Intercepted: {url[:100]}")
                nav_url["url"] = url
                nav_event.set()
                await route.abort()
                return
        await route.continue_()

    await page.route("**/*", _intercept)

    # ── B: Network response listener ─────────────────────────────────────────
    pdf_ready  = asyncio.Event()
    pdf_result = {"bytes": None, "filename": "case.pdf"}

    async def _on_response(response):
        if pdf_ready.is_set():
            return
        ct  = response.headers.get("content-type", "")
        url = response.url
        if "application/pdf" not in ct and not url.lower().split("?")[0].endswith(".pdf"):
            return
        try:
            body = await response.body()
            if body and len(body) > 1_024:
                fname = url.rstrip("/").split("?")[0].split("/")[-1]
                if not fname.lower().endswith(".pdf"):
                    fname = f"judgment_{datetime.now().strftime('%H%M%S')}.pdf"
                pdf_result["bytes"]    = body
                pdf_result["filename"] = fname
                pdf_ready.set()
                log.info(f"[Response] PDF: {url[:80]} ({len(body):,} B)")
        except Exception:
            pass

    page.on("response", _on_response)

    # ── C: Download listener ──────────────────────────────────────────────────
    download_task = asyncio.create_task(
        page.wait_for_event("download", timeout=20_000)
    )

    # ── Single click ──────────────────────────────────────────────────────────
    click_done[0] = True
    try:
        await element.click()
    except Exception as e:
        log.warning(f"Click failed: {e}")
        await cancel_task(download_task)
        page.remove_listener("response", _on_response)
        try:
            await page.unroute("**/*", _intercept)
        except Exception:
            pass
        return None, "case.pdf"

    # ── Wait for A, B, or C ───────────────────────────────────────────────────
    pdf_wait = asyncio.create_task(asyncio.wait_for(pdf_ready.wait(), 15.0))
    nav_wait = asyncio.create_task(asyncio.wait_for(nav_event.wait(), 15.0))

    done, pending = await asyncio.wait(
        {pdf_wait, download_task, nav_wait},
        timeout=20.0,
        return_when=asyncio.FIRST_COMPLETED,
    )
    for t in pending:
        await cancel_task(t)

    page.remove_listener("response", _on_response)
    try:
        await page.unroute("**/*", _intercept)
    except Exception:
        pass

    # Handle B: network response
    if pdf_result["bytes"]:
        log.info("[B] PDF via network response")
        return pdf_result["bytes"], pdf_result["filename"]

    # Handle C: download
    if download_task in done and not download_task.cancelled():
        try:
            dl    = download_task.result()
            tmp   = await dl.path()
            fname = dl.suggested_filename or "case.pdf"
            body  = Path(tmp).read_bytes()
            log.info(f"[C] PDF via download: {fname} ({len(body):,} B)")
            return body, fname
        except Exception as e:
            log.info(f"[C] Download error: {e}")

    # Handle A: intercepted navigation
    if nav_url["url"]:
        try:
            url = nav_url["url"]
            log.info(f"[A] Fetching intercepted URL: {url[:100]}")
            resp = await context.request.get(url)
            if resp.ok:
                body = await resp.body()
                if body and len(body) > 1_024:
                    fname = url.split("?")[0].rstrip("/").split("/")[-1]
                    if not fname.lower().endswith(".pdf"):
                        fname = f"judgment_{datetime.now().strftime('%H%M%S')}.pdf"
                    log.info(f"[A] PDF from intercepted nav: {len(body):,} B")
                    return body, fname
        except Exception as e:
            log.info(f"[A] Intercepted URL fetch failed: {e}")

    # ── Check page alive before DOM operations ────────────────────────────────
    if not await page_is_alive(page):
        log.warning("Page closed after click – skipping modal/tab scan")
        return None, "case.pdf"

    # ── D: Modal iframe scan ──────────────────────────────────────────────────
    try:
        await asyncio.sleep(2.5)

        modal_selectors = [
            ".modal.show", ".modal.show .modal-body",
            "dialog[open]",
            "#pdfModal", "#viewModal", "#docModal", "#myModal",
            "[role='dialog']", ".modal-content",
            ".popup", ".overlay", ".lightbox",
        ]
        embed_attrs = [("iframe", "src"), ("object", "data"), ("embed", "src")]
        pdf_src = None

        for modal_sel in modal_selectors:
            modal = await page.query_selector(modal_sel)
            if not modal:
                continue
            for tag, attr in embed_attrs:
                el = await modal.query_selector(f"{tag}[{attr}]")
                if el:
                    src = (await el.get_attribute(attr) or "").strip()
                    if src and src not in ("about:blank", ""):
                        pdf_src = src
                        log.info(f"[D] iframe in modal ({modal_sel}): {src[:100]}")
                        break
            if pdf_src:
                break

        if not pdf_src:
            for iframe in await page.query_selector_all("iframe[src]"):
                src = (await iframe.get_attribute("src") or "").strip()
                if src and src != "about:blank":
                    pdf_src = src
                    log.info(f"[D] iframe on page: {src[:100]}")
                    break

        if pdf_src:
            pdf_src = urljoin(page.url, pdf_src)
            log.info(f"[D] Fetching: {pdf_src[:100]}")
            resp = await page.request.get(pdf_src)
            if resp.ok:
                body = await resp.body()
                if body and len(body) > 1_024:
                    fname = pdf_src.split("?")[0].rstrip("/").split("/")[-1]
                    if not fname.lower().endswith(".pdf"):
                        fname = f"judgment_{datetime.now().strftime('%H%M%S')}.pdf"
                    log.info(f"[D] PDF from modal iframe: {len(body):,} B")
                    await close_modal(page)
                    return body, fname

    except Exception as exc:
        log.info(f"[D] Modal scan failed: {exc}")

    await close_modal(page)

    # ── E: New page / window ──────────────────────────────────────────────────
    try:
        async with context.expect_page(timeout=8_000) as new_page_info:
            await element.click()
        new_page = await new_page_info.value
        await new_page.wait_for_load_state("domcontentloaded", timeout=15_000)
        pdf_url  = new_page.url
        log.info(f"[E] PDF in new page: {pdf_url}")
        resp = await new_page.request.get(pdf_url)
        if resp.ok:
            body  = await resp.body()
            fname = pdf_url.split("?")[0].rstrip("/").split("/")[-1]
            if not fname.lower().endswith(".pdf"):
                fname = f"judgment_{datetime.now().strftime('%H%M%S')}.pdf"
            await new_page.close()
            return body, fname
    except Exception as exc:
        log.info(f"[E] New-page failed: {exc}")

    log.error("All capture methods failed")
    return None, "case.pdf"


# -- Save to disk -------------------------------------------------------------

def save_pdf(content: bytes, filename: str, index: int) -> Path:
    safe  = re.sub(r"[^\w\-.]", "_", filename)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    name  = f"{index:04d}_{stamp}_{safe}"
    path  = Path(OUTPUT_DIR) / name
    path.write_bytes(content)
    log.info(f"Saved: {path}  ({len(content):,} bytes)")
    return path


def save_metadata(row_data: dict, pdf_path: Path) -> None:
    meta      = {**row_data, "pdf_file": str(pdf_path), "scraped_at": datetime.now().isoformat()}
    meta_path = Path(LOG_DIR) / (pdf_path.stem + ".json")
    meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False))
    log.info(f"Metadata: {meta_path}")


# -- Main ---------------------------------------------------------------------

async def run(count: int = 1, use_tor: bool = False) -> None:
    Path(OUTPUT_DIR).mkdir(parents=True, exist_ok=True)
    Path(LOG_DIR).mkdir(parents=True, exist_ok=True)

    async with async_playwright() as p:
        browser, context = await create_browser(p, use_tor=use_tor)
        page = await context.new_page()

        try:
            log.info(f"Opening: {HOME_URL}")
            await page.goto(HOME_URL, wait_until="commit", timeout=90_000)
            await asyncio.sleep(3)

            log.info("-" * 62)
            log.info("  ACTION REQUIRED in the browser:")
            log.info("  1. Solve the CAPTCHA")
            log.info("  2. Enter a search term and click Search")
            log.info("  3. Wait until the RESULTS TABLE is visible")
            log.info("  4. Press ENTER here to let the scraper take over")
            log.info("-" * 62)
            await asyncio.get_event_loop().run_in_executor(
                None, input,
                "\n  >>> Press ENTER once the results table is visible: "
            )
            log.info("Resuming...")
            await random_delay(1.5, 3.0)
            await simulate_reading(page, duration=random.uniform(2.0, 4.0))

            scraped_total = 0
            page_num      = 0

            while scraped_total < count:
                if not await page_is_alive(page):
                    log.error("Page is no longer alive – stopping")
                    break

                pdf_buttons = await find_pdf_buttons(page)
                if not pdf_buttons:
                    log.info("No buttons found – stopping")
                    break

                table_rows    = await extract_table_rows(page)
                cases_on_page = len(pdf_buttons)
                log.info(
                    f"Page {page_num + 1}: {cases_on_page} case(s) | "
                    f"scraped so far: {scraped_total}"
                )

                for row_idx in range(cases_on_page):
                    if scraped_total >= count:
                        break

                    if not await page_is_alive(page):
                        log.error("Page closed mid-loop – stopping")
                        break

                    current_buttons = await find_pdf_buttons(page)
                    if row_idx >= len(current_buttons):
                        log.warning(f"Button {row_idx} gone – skipping")
                        break

                    btn = current_buttons[row_idx]
                    log.info(f"\n{'-'*55}")
                    log.info(f"Case {scraped_total + 1} (page {page_num + 1}, row {row_idx + 1})")

                    await random_delay(1.5, 4.0)
                    await human_scroll(page)
                    await random_delay(0.5, 1.5)

                    pdf_bytes, filename = await capture_pdf(page, context, btn)

                    if pdf_bytes and len(pdf_bytes) > 512:
                        pdf_path = save_pdf(pdf_bytes, filename, scraped_total + 1)
                        save_metadata(
                            table_rows[row_idx] if row_idx < len(table_rows) else {},
                            pdf_path,
                        )
                        scraped_total += 1
                    else:
                        log.error(f"Case {scraped_total + 1}: no PDF captured.")
                        await save_debug_html(page, f"case_{scraped_total + 1:04d}_failed")

                    if scraped_total < count and row_idx < cases_on_page - 1:
                        gap = random.uniform(3.0, 8.0)
                        log.info(f"Waiting {gap:.1f}s...")
                        await asyncio.sleep(gap)

                if scraped_total >= count:
                    break

                if not await page_is_alive(page):
                    break

                next_btn = await page.query_selector(
                    "a.paginate_button.next:not(.disabled), "
                    "li.paginate_button.next:not(.disabled) a, "
                    "a[id$='_next']:not(.disabled), "
                    ".dataTables_paginate .next:not(.disabled), "
                    "a[aria-label='Next']:not([aria-disabled='true'])"
                )
                if next_btn:
                    log.info(f"Navigating to page {page_num + 2}...")
                    await next_btn.click()
                    await asyncio.sleep(3)
                    try:
                        await page.wait_for_selector("button.show-modal-btn", timeout=15_000)
                    except Exception:
                        log.warning("Timed out waiting for next-page buttons")
                    page_num += 1
                else:
                    log.info("No next-page button – all results scraped.")
                    break

            log.info("\n" + "-" * 55)
            log.info(
                f"  Done – {scraped_total} case(s) scraped "
                f"across {page_num + 1} page(s)."
            )
            log.info(f"  PDFs -> {Path(OUTPUT_DIR).resolve()}")
            log.info("-" * 55)

        except Exception as exc:
            log.error(f"Fatal: {exc}", exc_info=True)
            await save_debug_html(page, "fatal_error")

        finally:
            await random_delay(2.0, 4.0)
            try:
                await context.close()
                await browser.close()
            except Exception:
                pass
            log.info("Browser closed.")


# -- CLI ----------------------------------------------------------------------

def _parse_args() -> tuple:
    args    = sys.argv[1:]
    use_tor = "--tor" in args
    count   = 999_999 if "--all" in args else 1
    if "--count" in args:
        idx = args.index("--count")
        try:
            count = int(args[idx + 1])
        except (IndexError, ValueError):
            log.warning("--count requires a number. Using 1.")
    return count, use_tor


if __name__ == "__main__":
    n, tor = _parse_args()
    log.info(f"Starting | cases={'ALL' if n == 999_999 else n} | tor={tor}")
    asyncio.run(run(count=n, use_tor=tor))