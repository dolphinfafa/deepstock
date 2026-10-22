from types import SimpleNamespace
import sys

import pytest

from deepstock.data.store import write_json
from deepstock.data.publication import canonical_json, publication_manifest, publication_batches, MAX_BATCH_BYTES
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
    monkeypatch.setattr(publisher, "settings", SimpleNamespace(project_root=tmp_path, node_token="synthetic-token", public_base_url="https://test.invalid"))
    monkeypatch.setattr(publisher.httpx, "post", lambda url, **kw: posted.append(kw["json"]) or SimpleNamespace(raise_for_status=lambda: None, json=lambda: {'published': len(kw['json'])}))
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


def test_large_upstream_projection_keeps_full_native_manifest_and_order():
    import hashlib
    import json
    upstream = [hashlib.sha256(str(i).encode()).hexdigest() for i in range(2701)]
    native = {'id': 'a' * 64, 'source_path': 'private', 'contract': {'upstream_versions': upstream}}
    before = canonical_json(native)
    wire = publication_manifest(native)
    summary = wire['contract']['upstream_versions']
    assert summary == {'representation': 'sha256_v1', 'count': 2701,
                       'sha256': hashlib.sha256(canonical_json(upstream)).hexdigest()}
    assert canonical_json(native) == before and len(json.dumps(wire)) < 100000
    assert summary != publication_manifest({**native, 'contract': {'upstream_versions': upstream[::-1]}})['contract']['upstream_versions']
    assert publication_manifest(native) == wire
    with pytest.raises(ValueError, match='still exceeds'):
        publication_manifest({**native, 'quality': {'issues': ['x' * 100000]}})


def test_batches_bound_real_json_bytes_and_count():
    import json
    values = [{'id': str(i), 'text': '中' * 20000} for i in range(20)]
    batches = list(publication_batches(values))
    assert sum(map(len, batches)) == 20 and len(batches) > 1
    assert all(len(json.dumps(b, ensure_ascii=False, separators=(',', ':')).encode()) <= MAX_BATCH_BYTES for b in batches)
    assert list(map(len, publication_batches([{'id': i} for i in range(501)]))) == [500, 1]


def test_partial_publish_restart_retries_only_unacknowledged_and_rejects_changes(tmp_path, monkeypatch):
    import httpx
    values = [{'id': str(i)} for i in range(501)]
    posted = []
    monkeypatch.setattr(publisher, 'settings', SimpleNamespace(node_token='secret', public_base_url='https://test.invalid'))
    def post(url, **kw):
        posted.append(kw['json'])
        if len(posted) == 2:
            raise httpx.ReadTimeout('private credentials must not be logged')
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {'published': len(kw['json'])})
    monkeypatch.setattr(publisher.httpx, 'post', post)
    state = tmp_path / 'acks.sqlite3'
    with pytest.raises(RuntimeError, match='ReadTimeout') as error:
        publisher.publish(values, state)
    assert 'private' not in str(error.value)
    assert publisher.publish(values, state) == {'published': 1, 'already_acknowledged': 500}
    assert posted[-1] == values[500:]
    assert publisher.publish(values, state) == {'published': 0, 'already_acknowledged': 501}
    with pytest.raises(ValueError, match='immutable'):
        publisher.publish([{'id': '0', 'changed': True}], state)


def test_missing_ack_is_never_marked_success_and_http_errors_are_sanitized(tmp_path, monkeypatch):
    import httpx
    monkeypatch.setattr(publisher, 'settings', SimpleNamespace(node_token='secret', public_base_url='https://test.invalid'))
    state = tmp_path / 'acks.sqlite3'
    monkeypatch.setattr(publisher.httpx, 'post', lambda *a, **kw: httpx.Response(200, json={'published': 0}, request=httpx.Request('POST', a[0])))
    with pytest.raises(RuntimeError, match='acknowledgement'):
        publisher.publish([{'id': 'a'}], state)
    monkeypatch.setattr(publisher.httpx, 'post', lambda *a, **kw: httpx.Response(422, json={'detail': 'sensitive payload'}, request=httpx.Request('POST', a[0])))
    with pytest.raises(RuntimeError, match='HTTP 422') as error:
        publisher.publish([{'id': 'a'}], state)
    assert 'sensitive' not in str(error.value)
