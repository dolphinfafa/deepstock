"""Explicit partial auction views, never silent changes to a daily price panel."""
from deepstock.data.store import read_clean_csv


def read_auction_csv(path, **kwargs):
    return read_clean_csv(path, allow_quarantine=True, **kwargs)
