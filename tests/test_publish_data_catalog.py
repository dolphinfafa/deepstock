from types import SimpleNamespace
import sys

import pytest

from deepstock.data.store import write_json
from scripts import publish_data_catalog as publisher


def test_recent_metadata_publication_filters_versions_and_never_sends_files(tmp_path, monkeypatch):
    paths = []
    for label, date in [("old", "2026-10-07T15:00:00+00:00"), ("new", "2026-10-07T16:00:00+00:00")]:
        path = tmp_path / (label + ".json")
        write_json(path, {"id": label, "imported_at_utc": date, "source_path": "private-local-path",
                          "raw_file": "raw.csv", "clean_file": "clean.csv", "preview_allowed": True,
                          "contract": {"padding": "NONE"}})
        paths.append(path)
    posted = []
    monkeypatch.setattr(publisher, "DataStore", lambda: SimpleNamespace(manifests=lambda: paths))
    monkeypatch.setattr(publisher, "settings", SimpleNamespace(node_token="synthetic-token", public_base_url="https://test.invalid"))
    monkeypatch.setattr(publisher.httpx, "post", lambda url, **kw: posted.append(kw["json"]) or SimpleNamespace(raise_for_status=lambda: None))
    monkeypatch.setattr(sys, "argv", ["publish_data_catalog", "--since", "2026-10-07T15:30:00+00:00"])
    publisher.main()
    assert len(posted) == 1 and len(posted[0]) == 1
    sent = posted[0][0]
    assert sent["id"] == "new" and sent["preview_allowed"] is False
    assert not {"source_path", "raw_file", "clean_file"}.intersection(sent)
    assert sent["contract"] == {"padding": "NONE"}


def test_metadata_publication_rejects_ambiguous_naive_cutoff(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["publish_data_catalog", "--since", "2026-10-07T15:30:00"])
    with pytest.raises(ValueError, match="include a timezone"):
        publisher.main()
