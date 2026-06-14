"""
Human behaviour simulation — random delays, mouse movement, typing, scrolling.
All functions are async and designed for Playwright pages.
"""

import asyncio
import random
from playwright.async_api import Page

from config import (
    DELAY_MIN, DELAY_MAX,
    TYPE_DELAY_MIN, TYPE_DELAY_MAX, THINK_PAUSE_PROB,
)


async def random_delay(min_s: float = DELAY_MIN, max_s: float = DELAY_MAX) -> None:
    """
    Gaussian-distributed pause — more natural than uniform random.
    Most pauses cluster near the midpoint, with occasional short/long outliers.
    """
    mean  = (min_s + max_s) / 2
    std   = (max_s - min_s) / 4
    delay = random.gauss(mean, std)
    delay = max(min_s, min(max_s, delay))
    await asyncio.sleep(delay)


async def human_type(page: Page, selector: str, text: str) -> None:
    """
    Click a field and type text with per-keystroke random delays.
    Occasionally inserts a longer 'thinking' pause mid-sentence.
    """
    await page.click(selector)
    await asyncio.sleep(random.uniform(0.3, 0.8))

    for char in text:
        await page.keyboard.type(char)

        if random.random() < THINK_PAUSE_PROB:
            # Rare longer pause — simulates hesitation
            await asyncio.sleep(random.uniform(0.4, 1.0))
        else:
            await asyncio.sleep(random.uniform(TYPE_DELAY_MIN, TYPE_DELAY_MAX))


async def human_scroll(page: Page) -> None:
    """
    Scroll a random amount — mostly downward (reading), occasionally up.
    Uses smaller increments to look like trackpad / mouse wheel.
    """
    direction = random.choices([1, -1], weights=[0.75, 0.25])[0]
    amount    = random.randint(80, 380)

    # Break into 2-4 small wheel events for a smoother feel
    steps = random.randint(2, 4)
    per_step = amount // steps
    for _ in range(steps):
        await page.mouse.wheel(0, per_step * direction)
        await asyncio.sleep(random.uniform(0.05, 0.15))

    await asyncio.sleep(random.uniform(0.3, 0.9))


async def human_mouse_move(page: Page, target_x: float, target_y: float) -> None:
    """
    Move mouse from a random starting point to (target_x, target_y)
    along an ease-in-out curved path with slight Gaussian wobble.
    Mimics how a real mouse drifts rather than teleports.
    """
    # Random start anywhere on a typical screen
    start_x = random.uniform(50, 900)
    start_y = random.uniform(50, 600)

    steps = random.randint(10, 25)

    for i in range(steps + 1):
        t = i / steps

        # Cubic ease-in-out: feels more organic than linear
        t_eased = t * t * (3 - 2 * t)

        # Gaussian wobble decreases as we approach the target
        wobble_scale = 1 - t_eased
        wx = random.gauss(0, 4 * wobble_scale)
        wy = random.gauss(0, 4 * wobble_scale)

        x = start_x + (target_x - start_x) * t_eased + wx
        y = start_y + (target_y - start_y) * t_eased + wy

        await page.mouse.move(x, y)
        await asyncio.sleep(random.uniform(0.008, 0.035))

    # Settle exactly on target
    await page.mouse.move(target_x, target_y)
    await asyncio.sleep(random.uniform(0.05, 0.15))


async def human_hover_and_click(page: Page, element) -> None:
    """
    Move mouse to element, hover briefly, then click.
    Wraps the common pre-click ritual into one call.
    """
    box = await element.bounding_box()
    if box:
        cx = box["x"] + box["width"]  * random.uniform(0.3, 0.7)
        cy = box["y"] + box["height"] * random.uniform(0.3, 0.7)
        await human_mouse_move(page, cx, cy)

    await asyncio.sleep(random.uniform(0.2, 0.6))
    await element.hover()
    await asyncio.sleep(random.uniform(0.15, 0.45))
    await element.click()


async def simulate_reading(page: Page, duration: float = None) -> None:
    """
    Scroll up and down for `duration` seconds to simulate a human reading the page.
    Duration defaults to a random 3–8 seconds.
    """
    if duration is None:
        duration = random.uniform(3.0, 8.0)

    deadline = asyncio.get_event_loop().time() + duration
    while asyncio.get_event_loop().time() < deadline:
        await human_scroll(page)
        await asyncio.sleep(random.uniform(0.6, 1.8))
