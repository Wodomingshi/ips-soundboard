"""Extract a draft ips.yaml from an IPS document with Claude.

    python -m ips_soundboard.extract private/IPS.md
    python -m ips_soundboard.extract examples/sample_investor_eur/IPS.md --compare examples/sample_investor_eur/ips.yaml

Needs ANTHROPIC_API_KEY (environment, Codespaces secret, or a .env file). The draft is written
next to the document as ips.draft.yaml (git-ignored) unless -o is given.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

from ips_soundboard.extract.compare import compare
from ips_soundboard.extract.llm import ExtractionError, default_model, extract_ips
from ips_soundboard.extract.output import draft_yaml, report_lines
from ips_soundboard.llm import anthropic_client


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("document", help="IPS as Markdown, text or PDF")
    parser.add_argument("-o", "--output", help="draft YAML path (default: ips.draft.yaml next to the document)")
    parser.add_argument("--model", default=None, help=f"Claude model (default: {default_model()})")
    parser.add_argument("--compare", metavar="IPS_YAML", help="reference ips.yaml to compare the draft with")
    args = parser.parse_args()

    client = anthropic_client()
    source = Path(args.document)
    text = read_document(source)
    model = args.model or default_model()
    print(f"Extracting {source} with {model}...")

    try:
        result = extract_ips(text, client, model)
    except ExtractionError as error:
        sys.exit(f"Extraction failed: {error}")

    output = Path(args.output) if args.output else source.with_name("ips.draft.yaml")
    output.write_text(draft_yaml(result, source.name, model), encoding="utf-8")
    comparison = None
    if args.compare:
        comparison = compare(result.data, yaml.safe_load(Path(args.compare).read_text(encoding="utf-8")))
    print("\n".join(report_lines(result, comparison)))
    print(f"\nDraft written to {output}")


def read_document(path: Path) -> str:
    if path.suffix.lower() == ".pdf":
        try:
            from pypdf import PdfReader
        except ImportError:
            sys.exit('Reading PDFs needs pypdf: pip install -e ".[llm]"')
        return "\n".join(page.extract_text() or "" for page in PdfReader(path).pages)
    return path.read_text(encoding="utf-8")


if __name__ == "__main__":
    main()
