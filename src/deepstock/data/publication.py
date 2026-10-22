"""Bounded publication projections; native dataset manifests stay immutable."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import re

MAX_MANIFEST_CHARS = 100_000
MAX_BATCH_BYTES = 512 * 1024
MAX_BATCH_ITEMS = 500


def canonical_json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')


def valid_upstream(value):
    if isinstance(value, list):
        return all(isinstance(item, str) and re.fullmatch('[0-9a-f]{64}', item) for item in value)
    return (isinstance(value, dict) and set(value) == {'representation', 'count', 'sha256'}
            and value['representation'] == 'sha256_v1'
            and type(value['count']) is int and value['count'] > 0
            and isinstance(value['sha256'], str) and bool(re.fullmatch('[0-9a-f]{64}', value['sha256'])))


def publication_manifest(native):
    value = deepcopy(native)
    for key in ('source_path', 'raw_file', 'clean_file'):
        value.pop(key, None)
    value['preview_allowed'] = False
    if len(json.dumps(value, allow_nan=False)) > MAX_MANIFEST_CHARS:
        upstream = value.get('contract', {}).get('upstream_versions')
        if not isinstance(upstream, list) or not upstream or not valid_upstream(upstream):
            raise ValueError('Oversized metadata has no valid upstream list to summarize')
        value['contract']['upstream_versions'] = {
            'representation': 'sha256_v1', 'count': len(upstream),
            'sha256': hashlib.sha256(canonical_json(upstream)).hexdigest(),
        }
    if len(json.dumps(value, allow_nan=False)) > MAX_MANIFEST_CHARS:
        raise ValueError('Metadata still exceeds the per-manifest publication limit')
    return value


def publication_batches(manifests):
    batch, size = [], 2  # JSON array brackets, plus commas below.
    for value in manifests:
        length = len(json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8'))
        if length + 2 > MAX_BATCH_BYTES:
            raise ValueError('Metadata exceeds the request publication limit')
        if batch and (len(batch) >= MAX_BATCH_ITEMS or size + 1 + length > MAX_BATCH_BYTES):
            yield batch
            batch, size = [], 2
        size += length + bool(batch)
        batch.append(value)
    if batch:
        yield batch
