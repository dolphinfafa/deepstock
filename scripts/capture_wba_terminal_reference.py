"""Retain official public terms; do not turn contingent rights into cash."""
import argparse
from datetime import datetime, timezone
from html import unescape
import re
from pathlib import Path

import httpx

from deepstock.data.store import DataStore, digest, write_json

URL = "https://www.sycamorepartners.com/news-article/sycamore-partners-completes-acquisition-of-walgreens-boots-alliance"


def parse_terms(html):
    text = re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", html)))
    checks = ["August 28, 2025", "cash consideration of $11.45 per WBA share",
              "one non-transferable right", "up to an additional $3.00 in cash per WBA share",
              "net proceeds of the future monetization", "has ceased trading"]
    if not all(check in text for check in checks):
        raise ValueError("Official completion terms missing; no inferred proceeds")
    return text, {"security": "WBA-202508", "completion_date": "2025-08-28", "cash_consideration_usd_per_raw_share": 11.45,
                  "non_transferable_right_per_raw_share": 1, "contingent_cash_upper_bound_usd": 3.0,
                  "cash_settlement_date": None, "right_valuation": None, "right_payment_date": None,
                  "verified_full_proceeds": False,
                  "status": "official_terms_verified_execution_and_full_valuation_blocked",
                  "policy": "Upper bound is not cash or known fair value; no fictitious sale/zero-valued right"}


def capture(output):
    if output.exists():
        raise FileExistsError("Preserve prior official source captures")
    with httpx.Client(timeout=30, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0 Deepstock public research"}) as client:
        response = client.get(URL)
        response.raise_for_status()
        if str(response.url) != URL:
            raise ValueError("Official evidence redirected to another page")
    text, terms = parse_terms(response.text)
    output.mkdir(parents=True)
    raw = output / "official-completion.html"
    raw.write_bytes(response.content)
    (output / "readable.txt").write_text(text, encoding="utf-8")
    snapshot = DataStore().import_file(raw, {"market": "US", "provider": "Sycamore Partners official", "restricted": True,
                                           "origin": "provider_response", "endpoint": "official_wba_completion_terms"})
    value = {**terms, "source_url": URL, "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
             "raw_sha256": digest(raw), "raw_bytes": len(response.content), "data_version": snapshot["id"]}
    write_json(output / "terms.json", value)
    print(value)
    return value


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output-dir", type=Path, required=True)
    capture(p.parse_args().output_dir)
