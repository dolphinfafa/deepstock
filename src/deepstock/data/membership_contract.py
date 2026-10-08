"""One versioned provider guarantee, never blanket permission to fill prices."""
from __future__ import annotations

import hashlib
import json

from .store import ROOT, DataQualityError

CONTRACT_PATH = "config/norgate_effective_membership_v1.json"
REPLY_PATH = "docs/research/norgate-membership-provider-reply-20261008.txt"
REPLY_SHA256 = "a25ba40513d74d5877f28e3ba3ad258d7048cbf12eedfd316c34405f8cbb0926"


def load_effective_membership_contract(root=ROOT):
    """Pin the reviewed contract and LF-normalized user-forwarded text.

    Git may use CRLF on Windows. This is a canonical text hash, not an assertion
    of original email bytes or independently authenticated email headers.
    """
    path = root / CONTRACT_PATH
    contract = json.loads(path.read_text(encoding="utf-8"))
    expected = {
        "contract_id": "norgate-effective-membership-v1",
        "index_name": "S&P 500", "padding": "ALLMARKETDAYS",
        "package_version": "1.0.77",
        "semantics": "Historical effective constituent status including non-trading dates; provider evaluated, not client forward-filled",
        "announcement_dates_available": False,
        "executable_price_padding_allowed": False,
        "major_exchange_listing_acquired": False,
        "reply_source": "User-forwarded support reply text; original MIME and headers not supplied",
        "reply_at_utc": "2026-10-07T23:00:00+00:00",
        "reply_path": REPLY_PATH, "reply_text_sha256_lf": REPLY_SHA256,
    }
    if contract != expected:
        raise DataQualityError("Effective membership contract differs from the reviewed v1")
    text = (root / REPLY_PATH).read_bytes().decode("utf-8").replace("\r\n", "\n")
    if hashlib.sha256(text.encode("utf-8")).hexdigest() != REPLY_SHA256:
        raise DataQualityError("User-forwarded provider reply text changed")
    return contract, text


def verify_effective_evidence(store, version, contract):
    """A new capture must bind the actual reply, not an arbitrary dataset ID."""
    source = store.get(version)
    value = json.loads(store.verified_path(source, "raw").read_text(encoding="utf-8"))
    if value.get("contract") != contract or not isinstance(value.get("text"), str):
        raise DataQualityError("Provider reply evidence contract mismatch")
    canonical = value["text"].replace("\r\n", "\n")
    if hashlib.sha256(canonical.encode("utf-8")).hexdigest() != REPLY_SHA256:
        raise DataQualityError("Provider reply evidence text mismatch")
    return source
