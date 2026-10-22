"""Publish only bounded licensed-node metadata, never market rows."""
import json
import argparse
from contextlib import closing
from datetime import datetime
import hashlib
import sqlite3
import httpx
from deepstock.data.store import DataStore
from deepstock.data.publication import canonical_json, publication_batches, publication_manifest
from deepstock.web.config import settings


def publish(manifests, state_path):
    """Only HTTP-acknowledged, identical wire projections may be skipped."""
    endpoint = settings.public_base_url.rstrip('/') + '/api/agent/research/data-versions'
    state_path.parent.mkdir(parents=True, exist_ok=True)
    published = skipped = 0
    with closing(sqlite3.connect(state_path, timeout=60)) as db:
        db.execute('CREATE TABLE IF NOT EXISTS acknowledgements '
                   '(endpoint TEXT, version TEXT, wire_sha256 TEXT NOT NULL, PRIMARY KEY(endpoint, version))')
        db.commit()
        def pending():
            nonlocal skipped
            for value in manifests:
                wire_hash = hashlib.sha256(canonical_json(value)).hexdigest()
                row = db.execute('SELECT wire_sha256 FROM acknowledgements WHERE endpoint=? AND version=?',
                                 (endpoint, value['id'])).fetchone()
                if row:
                    if row[0] != wire_hash:
                        raise ValueError('Previously acknowledged immutable metadata changed')
                    skipped += 1
                else:
                    yield value

        for batch in publication_batches(pending()):
            try:
                response = httpx.post(endpoint, headers={'Authorization': f'Bearer {settings.node_token}'},
                                      json=batch, timeout=60)
                response.raise_for_status()
            except httpx.HTTPStatusError as error:
                raise RuntimeError(f'Metadata publication failed: HTTP {error.response.status_code}; '
                                   f'batch_items={len(batch)}; no acknowledgement saved for this batch') from None
            except httpx.RequestError as error:
                raise RuntimeError(f'Metadata publication failed: {type(error).__name__}; '
                                   'retry metadata only; no acknowledgement saved for this batch') from None
            if response.json() != {'published': len(batch)}:
                raise RuntimeError('Metadata publication acknowledgement did not match the batch')
            db.executemany('INSERT OR IGNORE INTO acknowledgements VALUES (?,?,?)',
                           [(endpoint, value['id'], hashlib.sha256(canonical_json(value)).hexdigest()) for value in batch])
            db.commit()
            published += len(batch)
    return {'published': published, 'already_acknowledged': skipped}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--since", help="Publish only versions imported since this timezone-aware ISO UTC time")
    args = parser.parse_args()
    since = datetime.fromisoformat(args.since) if args.since else None
    if since is not None and since.tzinfo is None:
        raise ValueError("Metadata publishing start time must include a timezone")
    manifests = []
    for path in DataStore().manifests():
        value = json.loads(path.read_text(encoding="utf-8"))
        if since is not None and datetime.fromisoformat(value["imported_at_utc"]) < since:
            continue
        manifests.append(publication_manifest(value))
    if not settings.node_token:
        raise RuntimeError("DEEPSTOCK_NODE_TOKEN missing")
    print(json.dumps(publish(manifests, settings.project_root / 'artifacts/data-publication/acknowledged.sqlite3')))


if __name__ == "__main__":
    main()
