"""
Human-like browsing helpers for Playwright.
Adapted from VERDICTSCRAPER/human.py with improvements.
"""
import asyncio
import random
from playwright.async_api import Page

from config import DELAY_MIN, DELAY_MAX


async def random_delay(min_s: float = DELAY_MIN, max_s: float = DELAY_MAX) -> None:
    """Gaussian-distributed pause – more natural than uniform random."""
    mean  = (min_s + max_s) / 2
    std   = (max_s - min_s) / 4
    delay = max(min_s, min(max_s, random.gauss(mean, std)))
    await asyncio.sleep(delay)


async def human_scroll(page: Page, steps: int = 3) -> None:
    """Scroll down in small increments to simulate reading."""
    direction = random.choices([1, -1], weights=[0.8, 0.2])[0]
    for _ in range(steps):
        amount = random.randint(100, 400)
        await page.mouse.wheel(0, amount * direction)
        await asyncio.sleep(random.uniform(0.1, 0.4))
    await asyncio.sleep(random.uniform(0.3, 0.8))


async def human_mouse_move(page: Page, target_x: float, target_y: float) -> None:
    """Move mouse along a curved path to the target."""
    start_x = random.uniform(100, 800)
    start_y = random.uniform(100, 500)
    steps   = random.randint(8, 20)

    for i in range(steps + 1):
        t       = i / steps
        t_eased = t * t * (3 - 2 * t)
        wx = random.gauss(0, 3 * (1 - t_eased))
        wy = random.gauss(0, 3 * (1 - t_eased))
        x  = start_x + (target_x - start_x) * t_eased + wx
        y  = start_y + (target_y - start_y) * t_eased + wy
        await page.mouse.move(x, y)
        await asyncio.sleep(random.uniform(0.008, 0.03))

    await page.mouse.move(target_x, target_y)
    await asyncio.sleep(random.uniform(0.05, 0.15))


async def human_type(page: Page, selector: str, text: str) -> None:
    """Click a field and type text with per-keystroke random delays."""
    await page.click(selector)
    await asyncio.sleep(random.uniform(0.3, 0.7))
    for char in text:
        await page.keyboard.type(char)
        await asyncio.sleep(random.uniform(0.05, 0.18))


async def simulate_reading(page: Page, duration: float = None) -> None:
    """Scroll up and down for `duration` seconds to simulate reading."""
    if duration is None:
        duration = random.uniform(2.0, 5.0)
    deadline = asyncio.get_event_loop().time() + duration
    while asyncio.get_event_loop().time() < deadline:
        await human_scroll(page, steps=random.randint(1, 3))
        await asyncio.sleep(random.uniform(0.5, 1.5))


async def dismiss_popups(page: Page) -> None:
    """Try to dismiss common cookie/consent popups."""
    selectors = [
        "button[id*='accept']", "button[class*='accept']",
        "button[id*='consent']", "button[class*='consent']",
        "button[id*='agree']", "#onetrust-accept-btn-handler",
        ".cookie-accept", "[data-action='accept']",
    ]
    for sel in selectors:
        try:
            btn = await page.query_selector(sel)
            if btn:
                await btn.click()
                await asyncio.sleep(random.uniform(0.4, 0.9))
                break
        except Exception:
            pass