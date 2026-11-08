"""Run the dashboard on the fictional demo investor with synthetic market data (no internet).

    streamlit run tools/offline_dashboard.py

Used for layout checks and screenshots: the private/ option is hidden, so real data never appears.
Reading an IPS document works too: a stand-in for Claude replays a correct reading of the sample IPS.
"""

import os
import runpy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tests")]
os.environ["IPS_SOUNDBOARD_DEMO_ONLY"] = "1"

from types import SimpleNamespace  # noqa: E402

from test_dashboard import sample_market  # noqa: E402
from test_extract import GOLDEN  # noqa: E402

from ips_soundboard.dashboard.claude import set_client  # noqa: E402
from ips_soundboard.dashboard.providers import set_provider  # noqa: E402


# Given the holdings' sleeve names, Claude reuses them; the stand-in does the same.
HOLDINGS_IDS = ["eu_equity", "world_ex_eu_equity", "em_equity", "eur_govt_bonds", "gold", "cash"]
READING = GOLDEN.model_copy(update={"sleeves": [s.model_copy(update={"id": i})
                                                for s, i in zip(GOLDEN.sleeves, HOLDINGS_IDS)]})


class SampleReading:
    """Answers every request with the correct reading of the sample IPS."""

    def __init__(self) -> None:
        self.messages = self

    def create(self, **request):
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=READING.model_dump_json())],
                               stop_reason="end_turn")


set_provider(sample_market())
set_client(SampleReading())
runpy.run_path(str(ROOT / "app" / "dashboard.py"), run_name="__main__")
