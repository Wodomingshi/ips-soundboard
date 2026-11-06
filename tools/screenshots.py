"""Screenshot every dashboard tab at desktop and phone size, and check nothing overflows a phone screen.

    streamlit run tools/offline_dashboard.py --server.port 8599 --server.headless true &
    python tools/screenshots.py --url http://localhost:8599 --out screenshots

Exits with code 1 if any tab is wider than the screen at phone size (sideways scrolling).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

TABS = ["Overview", "Rules", "Holdings", "Risk", "New money"]
SIZES = {
    "desktop": {"viewport": {"width": 1400, "height": 1000}},
    "phone": {"viewport": {"width": 390, "height": 844}, "is_mobile": True, "has_touch": True,
              "device_scale_factor": 2},  # an iPhone-sized screen
}
CONTENT = '[data-testid="stMain"]'


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", default="http://localhost:8599")
    parser.add_argument("--out", default="screenshots")
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    problems = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for size, options in SIZES.items():
            page = browser.new_page(**options)
            page.goto(args.url)
            page.get_by_role("tab", name="Overview").wait_for(timeout=90_000)
            page.wait_for_timeout(3_000)
            for tab in TABS:
                page.get_by_role("tab", name=tab, exact=True).click()
                page.wait_for_timeout(2_000)
                if size == "phone" and (overflow := _overflow(page)):
                    problems.append(f"{tab}: {overflow}px wider than the phone screen")
                _full_screenshot(page, out / f"{size}-{tab.lower().replace(' ', '-')}.png",
                                 options["viewport"]["width"])
            page.close()
        browser.close()

    print(f"Screenshots in {out}/")
    for problem in problems:
        print(f"  ! {problem}")
    sys.exit(1 if problems else 0)


def _overflow(page: Page) -> int:
    """How far the page content sticks out sideways (0 = fits)."""
    return page.evaluate(f"""() => {{
        const main = document.querySelector('{CONTENT}') || document.documentElement;
        const page = document.documentElement;
        return Math.max(main.scrollWidth - main.clientWidth, page.scrollWidth - window.innerWidth, 0);
    }}""")


def _full_screenshot(page: Page, path: Path, width: int) -> None:
    """Streamlit scrolls inside its own container, so grow the window to the content height first."""
    original = page.viewport_size
    height = page.evaluate(f"() => (document.querySelector('{CONTENT}') || document.body).scrollHeight")
    page.set_viewport_size({"width": width, "height": min(int(height) + 40, 8_000)})
    page.wait_for_timeout(800)
    page.screenshot(path=str(path))
    page.set_viewport_size(original)


if __name__ == "__main__":
    main()
