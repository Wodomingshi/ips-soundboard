"""Turn your holdings, IPS config and IPS document into the text for Streamlit's secrets box.

    python tools/make_secrets.py private

Writes private/streamlit-secrets.toml (git-ignored, like everything in private/). Copy its whole
content into the "Secrets" field of your Streamlit Community Cloud app (see docs/deploy.md).
"""

from __future__ import annotations

import argparse
import json
import sys
import tomllib
from pathlib import Path

FILES = {"holdings_csv": "holdings.csv", "ips_yaml": "ips.yaml", "ips_md": "IPS.md"}  # IPS.md is optional


def secrets_toml(folder: Path) -> str:
    values = {}
    for key, name in FILES.items():
        path = folder / name
        if path.exists():
            values[key] = path.read_text(encoding="utf-8")
        elif key != "ips_md":
            raise FileNotFoundError(f"{path} is missing")
    text = "# IPS Soundboard: your portfolio for the private Streamlit deployment. Keep it private.\n\n"
    text += "\n\n".join(f"{key} = {toml_string(value)}" for key, value in values.items()) + "\n"
    if tomllib.loads(text) != values:  # never hand over something that would not read back identically
        raise ValueError("the secrets text does not read back identically")
    return text


def toml_string(value: str) -> str:
    """A readable multi-line literal string; a JSON-escaped string if the text contains '''."""
    if "'''" in value:
        return json.dumps(value)  # a JSON string is also a valid TOML basic string
    return "'''\n" + value + ("" if value.endswith("\n") else "\n") + "'''"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("folder", nargs="?", default="private")
    folder = Path(parser.parse_args().folder)
    try:
        text = secrets_toml(folder)
    except (FileNotFoundError, ValueError) as error:
        sys.exit(f"Could not build the secrets: {error}")
    out = folder / "streamlit-secrets.toml"
    out.write_text(text, encoding="utf-8")
    print(f"Written {out}. Copy its whole content into your Streamlit app's Secrets field.")


if __name__ == "__main__":
    main()
