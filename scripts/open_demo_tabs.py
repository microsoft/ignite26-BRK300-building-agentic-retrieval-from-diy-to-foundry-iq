"""Open one browser tab per demo, in the deck's slide order.

Each tab loads the portal's DIY agents page with the agent and question selected, and asks the question right away.
Start the portal and the agents (python -m scripts.run_diy_agents) first.

Usage: python -m scripts.open_demo_tabs [--base-url URL] [--no-run] [step-name ...]
"""

import argparse
import sys
import time
import webbrowser
from urllib.parse import urlencode

from app.diy_agents import DEMOS, DIY_STEPS_BY_NAME


def demo_urls(base_url: str, step_names: list[str], run: bool) -> list[str]:
    urls = []
    for demo in DEMOS:
        if step_names and demo.step not in step_names:
            continue
        params = {"agent": demo.step, "question": demo.question}
        if run:
            params["run"] = "1"
        urls.append(f"{base_url.rstrip('/')}/diy-agents?{urlencode(params)}")
    return urls


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("steps", nargs="*", help="Only open demos for these steps.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000", help="Portal URL.")
    parser.add_argument("--no-run", action="store_true", help="Load each question without asking it.")
    args = parser.parse_args()
    unknown = [name for name in args.steps if name not in DIY_STEPS_BY_NAME]
    if unknown:
        sys.exit(f"Unknown step(s): {', '.join(unknown)}. Choose from: {', '.join(DIY_STEPS_BY_NAME)}")

    for number, url in enumerate(demo_urls(args.base_url, args.steps, run=not args.no_run), 1):
        print(f"{number:2}. {url}")
        webbrowser.open_new_tab(url)
        # A short pause keeps the tabs in talk order.
        time.sleep(0.4)


if __name__ == "__main__":
    main()
