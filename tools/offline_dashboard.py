"""Run the dashboard on the fictional demo investor with synthetic market data (no internet).

    streamlit run tools/offline_dashboard.py

Used for layout checks and screenshots: the private/ option is hidden, so real data never appears.
"""

import os
import runpy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tests")]
os.environ["IPS_SOUNDBOARD_DEMO_ONLY"] = "1"

from test_dashboard import sample_market  # noqa: E402

from ips_soundboard.dashboard.providers import set_provider  # noqa: E402

set_provider(sample_market())
runpy.run_path(str(ROOT / "app" / "dashboard.py"), run_name="__main__")
