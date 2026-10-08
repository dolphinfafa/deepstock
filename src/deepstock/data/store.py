from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

RULE_VERSION = "clean-v1.13"
ROOT = Path(__file__).resolve().parents[3]
_local = threading.local()


class DataQualityError(ValueError):
    pass


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    temporary.replace(path)


def root_for(path: Path) -> Path:
    for parent in [path.resolve(), *path.resolve().parents]:
        if (parent / "artifacts/datasets").is_dir():
            return parent
        if (parent / "pyproject.toml").is_file() and (parent / "src/deepstock").is_dir():
            return parent
    return ROOT


class DataStore:
    def __init__(self, project: Path = ROOT, node: str | None = None):
        self.project = Path(project).resolve()
        self.root = self.project / "artifacts/datasets"
        self.node = node or ("quant-computer" if os.name == "nt" else "deepstock-server")

    def manifests(self):
        return sorted(self.root.glob("*/manifest.json"))

    def get(self, version: str) -> dict:
        if len(version) != 64 or any(c not in "0123456789abcdef" for c in version):
            raise DataQualityError("Invalid dataset version")
        return json.loads((self.root / version / "manifest.json").read_text(encoding="utf-8"))

    def _alias(self, source: Path) -> Path:
        key = hashlib.sha256(str(source.resolve()).encode()).hexdigest()
        return self.root / "aliases" / f"{key}.json"

    def resolve(self, source: Path | str, allow_quarantine: bool = False) -> dict:
        if isinstance(source, str) and len(source) == 64 and all(c in "0123456789abcdef" for c in source):
            result = self.get(source)
        else:
            path = Path(source).resolve()
            alias = self._alias(path)
            if not alias.exists():
                raise DataQualityError(f"Input not registered: {path.name}; run scripts/clean_existing_data.py first")
            result = self.get(json.loads(alias.read_text(encoding="utf-8"))["version"])
            if not path.exists() or digest(path) != result["source_sha256"]:
                raise DataQualityError(f"Source changed after cleaning: {path.name}; register a new version")
        q = result["quality"]
        accepted_quarantine = allow_quarantine and result.get("kind") == "auction_snapshot" and q.get("invalid_rows", 0) > 0 and not q.get("conflict_rows", 0) and not q.get("invalid_dates", 0)
        if result["status"] != "ready" and not accepted_quarantine:
            raise DataQualityError(f"Dataset blocked: {result['source_name']}: {result['quality']['issues']}")
        return result

    def import_file(self, source: Path, metadata: dict | None = None) -> dict:
        source = source.resolve()
        metadata = metadata or {}
        source_hash = digest(source)
        # Different provenance/contracts remain different versions, even with equal bytes.
        contract = {"source": str(source), "sha256": source_hash, "rules": RULE_VERSION, "metadata": metadata, "node": self.node}
        version = hashlib.sha256(json.dumps(contract, sort_keys=True, default=str).encode()).hexdigest()
        folder = self.root / version
        if (folder / "manifest.json").exists():
            result = self.get(version)
            self.verified_path(result, "raw")
            if result.get("clean_file"):
                self.verified_path(result, "clean")
            write_json(self._alias(source), {"version": version})
            return result
        folder.mkdir(parents=True, exist_ok=True)
        raw = folder / ("raw" + "".join(source.suffixes))
        if source.suffix == ".sqlite3":
            with sqlite3.connect(f"file:{source}?mode=ro", uri=True) as origin, sqlite3.connect(raw) as target:
                origin.backup(target)
            source_hash = digest(raw)
        else:
            shutil.copyfile(source, raw)
            if digest(raw) != source_hash or digest(source) != source_hash:
                raise DataQualityError("Source changed during snapshot capture; no version published")
        result = {"id": version, "node": self.node, "market": metadata.get("market", "US"),
                  "provider": metadata.get("provider", "unknown"), "source_name": source.name,
                  "source_path": str(source), "source_sha256": digest(source), "raw_sha256": digest(raw),
                  "origin": metadata.get("origin", "legacy_import"), "imported_at_utc": datetime.now(timezone.utc).isoformat(),
                  "rules": RULE_VERSION, "contract": metadata, "raw_file": raw.name,
                  "preview_allowed": not metadata.get("restricted", False), "status": "blocked"}
        try:
            if source.suffix == ".json":
                value = json.loads(raw.read_text(encoding="utf-8"))
                if not source.name.startswith("membership-"):
                    raise DataQualityError("JSON is source evidence, not a supported tabular input")
                rows = []
                for symbol, intervals in value.items():
                    for interval in intervals:
                        start, end = pd.Timestamp(interval["start"]), pd.Timestamp(interval["end"])
                        if start > end:
                            raise DataQualityError("Invalid membership interval")
                        rows.append({"symbol": symbol, "start": start.date().isoformat(), "end": end.date().isoformat()})
                clean = folder / "clean.json"
                write_json(clean, value)
                result.update(kind="membership", rows=len(rows), status="ready", quality={"issues": [], "modified_rows": 0})
            elif source.suffix == ".sqlite3":
                raise DataQualityError("Database archived as evidence; market snapshots must be extracted and cleaned separately")
            else:
                frame = pd.read_csv(raw, dtype={"stock_code": "string", "ts_code": "string", "symbol": "string"})
                cleaned, rejected, quality, kind = clean_frame(frame, metadata)
                clean = folder / "clean.csv.gz"
                cleaned.to_csv(clean, index=False, compression={"method": "gzip", "mtime": 0})
                if not rejected.empty:
                    rejected.to_csv(folder / "quarantine.csv.gz", index=False, compression={"method": "gzip", "mtime": 0})
                result.update(kind=kind, rows=len(cleaned), raw_rows=len(frame), quality=quality,
                              status="blocked" if quality["blocking"] else "ready")
                date_col = next((c for c in ["date", "trade_date", "snapshot_date", "timestamp"] if c in cleaned), None)
                if date_col and not cleaned.empty:
                    result.update(data_start=str(cleaned[date_col].min())[:10], data_end=str(cleaned[date_col].max())[:10])
            result.update(clean_file=clean.name, clean_sha256=digest(clean))
        except (ValueError, KeyError, TypeError, pd.errors.EmptyDataError) as error:
            result.update(kind="unsupported", rows=0, quality={"issues": [str(error)], "blocking": True})
            if metadata.get("origin") == "provider_response":
                result.update(kind="provider_response", status="evidence")
        write_json(folder / "manifest.json", result)
        write_json(self._alias(source), {"version": version})
        return result

    def resolve_pinned(self, source: Path, version: str, allow_quarantine: bool = False) -> dict:
        """Aliases are discovery hints, not authority over a manifest-pinned input."""
        if not isinstance(version, str) or len(version) != 64 or any(c not in "0123456789abcdef" for c in version):
            raise DataQualityError("Invalid pinned dataset version")
        result = self.resolve(version, allow_quarantine=allow_quarantine)
        source = Path(source).resolve()
        if not source.exists() or digest(source) != result["source_sha256"]:
            raise DataQualityError("Source changed from pinned dataset version")
        self.verified_path(result, "raw")
        return result

    def verified_path(self, manifest: dict, layer: str = "clean") -> Path:
        key = "raw_file" if layer == "raw" else "clean_file"
        name = manifest.get(key)
        if not name or Path(name).name != name:
            raise DataQualityError("Invalid or unavailable dataset file")
        path = self.root / manifest["id"] / name
        if digest(path) != manifest[layer + "_sha256"]:
            raise DataQualityError("Dataset checksum mismatch")
        return path


def clean_dividends(frame: pd.DataFrame):
    """Keep disclosures, collapse only identical economic events, never amounts.

    Tushare may revise base_unit/net_ex_date without changing cash entitlement.
    Auxiliary revisions remain in immutable raw evidence and explicit audit.
    """
    frame = frame.copy()
    frame["_raw_row"] = np.arange(len(frame))
    frame["_quality_flags"] = ""
    identifier = "ts_code" if "ts_code" in frame else "symbol"
    frame[identifier] = frame[identifier].astype("string").str.strip().str.upper()
    bad = frame[identifier].isna() | frame[identifier].eq("")
    implemented = frame.div_proc.eq("实施") if "div_proc" in frame else pd.Series(True, index=frame.index)
    invalid_dates = 0
    for col in [c for c in frame if c.endswith("date")]:
        # Nullable provider integer dates arrive as CSV floats (20260119.0).
        values = frame[col].astype("string").str.replace(r"\.0$", "", regex=True)
        present = values.notna() & values.ne("")
        dates = pd.to_datetime(values, errors="coerce", format="mixed")
        invalid = present & dates.isna()
        if col in {"ann_date", "record_date", "ex_date", "pay_date"}:
            invalid |= implemented & dates.isna()
        bad |= invalid
        invalid_dates += int(invalid.sum())
        frame[col] = dates.dt.strftime("%Y-%m-%d")
    cash_col = "div_cash" if "div_cash" in frame else "cash_per_share"
    frame[cash_col] = pd.to_numeric(frame[cash_col], errors="coerce")
    bad |= implemented & (~np.isfinite(frame[cash_col]) | frame[cash_col].lt(0))
    bad |= implemented & (frame.pay_date.lt(frame.ex_date) | frame.record_date.ge(frame.ex_date) | frame.ann_date.gt(frame.ex_date))
    keys = [identifier, "ex_date"] + (["div_proc"] if "div_proc" in frame else [])
    economics = ["ann_date", "record_date", "pay_date", cash_col]
    conflict = pd.Series(False, index=frame.index)
    for _, group in frame.loc[implemented & ~bad].groupby(keys, dropna=False):
        if len(group[economics].drop_duplicates()) > 1:
            conflict.loc[group.index] = True
    bad |= conflict
    # Non-implemented disclosures retain their exact content; no guessed events.
    identical = frame.duplicated([c for c in frame if not c.startswith("_")])
    equivalent = implemented & ~bad & frame.duplicated(keys + economics)
    removed = identical | equivalent
    frame.loc[~bad, "_quality_flags"] = "normalized_dividend_dates;"
    frame.loc[bad, "_quality_flags"] = "invalid_or_conflicting;"
    quality = {"blocking": bool(bad.any() or frame.empty), "issues": [], "invalid_rows": int(bad.sum()),
               "invalid_dates": invalid_dates, "conflict_rows": int(conflict.sum()), "duplicate_rows": int(identical.sum()),
               "equivalent_disclosures": int((equivalent & ~identical).sum()), "modified_rows": int((~bad).sum()),
               "imputed_rows": 0, "policy": "Economic event keys; auxiliary revisions retained in raw, no amount/date repairs"}
    if bad.any():
        quality["issues"].append(f"{int(bad.sum())} invalid/conflicting dividend rows quarantined")
    if removed.any():
        quality["issues"].append(f"{int(removed.sum())} equivalent dividend disclosures collapsed; auxiliary revisions retained in raw")
    return frame.loc[~bad & ~removed].sort_values(keys).reset_index(drop=True), frame.loc[bad].copy(), quality, "dividends"


def clean_frame(frame: pd.DataFrame, metadata: dict) -> tuple[pd.DataFrame, pd.DataFrame, dict, str]:
    if metadata.get("endpoint") == "easy_tdx_bars":
        from .easy_tdx import clean_tdx_frame
        return clean_tdx_frame(frame, metadata)
    if metadata.get("endpoint") == "fund_div" or metadata.get("kind") == "dividends":
        return clean_dividends(frame)
    if metadata.get("endpoint") == "dividend" or metadata.get("kind") == "stock_dividends":
        return clean_stock_dividends(frame)
    if frame.empty and metadata.get("endpoint") == "suspend_d" and metadata.get("empty_response_verified"):
        frame = frame.copy()
        frame["_raw_row"] = pd.Series(dtype="int64")
        frame["_quality_flags"] = pd.Series(dtype="string")
        return frame, frame.iloc[:0], {"blocking": False, "issues": [], "imputed_rows": 0,
                                      "policy": "Captured empty optional suspension response, not a price input"}, "suspensions"
    frame = frame.copy()
    original = frame.copy()
    frame["_raw_row"] = np.arange(len(frame))
    frame["_quality_flags"] = ""
    issues = []
    bad = pd.Series(False, index=frame.index)
    keys = []
    for col in ["symbol", "ts_code", "stock_code", "con_code"]:
        if col in frame:
            normalized = frame[col].astype("string").str.strip().str.upper()
            if col == "stock_code":
                normalized = normalized.str.zfill(6)
            changed = normalized.ne(frame[col]).fillna(False)
            frame.loc[changed, "_quality_flags"] += "normalized_identifier;"
            frame[col] = normalized
            bad |= normalized.isna() | normalized.eq("")
            keys.append(col)
            break
    date_col = next((c for c in ["date", "trade_date", "snapshot_date", "timestamp", "trade_time", "time", "cal_date", "ann_date", "collected_date"] if c in frame), None)
    if metadata.get("endpoint") == "stock_basic" and "ts_code" in frame and "name" in frame:
        if frame.ts_code.isna().any() or frame.ts_code.duplicated().any():
            raise DataQualityError("Invalid provider security reference")
        return frame, frame.iloc[:0], {"issues": [], "blocking": False, "modified_rows": 0}, "security_reference"
    if "security_id" in frame and "name" in frame and "stock_code" in frame:
        if frame.stock_code.duplicated().any() or frame.security_id.isna().any():
            raise DataQualityError("Invalid security master")
        return frame, frame.iloc[:0], {"issues": [], "blocking": False, "modified_rows": 0}, "security_master"
    if not date_col:
        raise DataQualityError("Unsupported schema: no event/session date")
    dates = pd.to_datetime(frame[date_col].astype(str), errors="coerce", format="mixed")
    invalid_dates = int(dates.isna().sum())
    bad |= dates.isna()
    if date_col in {"timestamp", "trade_time", "time"}:
        if dates.dt.tz is None:
            if not metadata.get("timezone"):
                raise DataQualityError("Minute timestamps require a declared timezone")
            dates = dates.dt.tz_localize(metadata["timezone"], ambiguous="raise", nonexistent="raise")
        frame[date_col] = dates.astype(str)
    else:
        frame[date_col] = dates.dt.strftime("%Y-%m-%d")
    changed = original[date_col].astype(str).ne(frame[date_col]).fillna(False)
    frame.loc[changed, "_quality_flags"] += "normalized_date;"
    keys.append(date_col)
    if metadata.get("endpoint") == "suspend_d":
        keys.extend(c for c in ["suspend_type", "suspend_timing"] if c in frame)
    if "title" in frame and "url" in frame:
        keys.extend(["title", "url"])
    numeric = [c for c in frame if c in {"adjusted_open", "adjusted_high", "adjusted_low", "adjusted_close", "open", "high", "low", "close", "price", "pre_close", "volume", "vol", "amount", "turnover", "entry_0931_open", "entry_0931_vwap", "entry_0931_volume", "entry_0931_close", "weight", "turnover_rate", "volume_ratio", "float_share", "adj_factor", "div_cash", "cash_per_share", "up_limit", "down_limit"}]
    for col in numeric:
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
        invalid = ~np.isfinite(frame[col])
        if col in {"volume", "vol", "amount", "turnover", "entry_0931_volume", "weight", "float_share", "div_cash", "cash_per_share"}:
            invalid |= frame[col] < 0
        elif col not in {"turnover_rate", "volume_ratio"}:
            invalid |= frame[col] <= 0
        if "price" in frame and col in {"turnover_rate", "volume_ratio", "float_share", "pre_close", "amount"}:
            # Auxiliary features are nullable, unlike executable prices. The frozen
            # model handles them inside its training-only feature imputation.
            frame.loc[invalid, "_quality_flags"] += f"missing_optional_{col};"
        else:
            bad |= invalid
    if {"open", "high", "low", "close"}.issubset(frame):
        bad |= (frame.low > frame.high) | (frame.high < frame[["open", "close"]].max(axis=1)) | (frame.low > frame[["open", "close"]].min(axis=1))
    if {"adjusted_high", "adjusted_low", "adjusted_close"}.issubset(frame):
        bad |= (frame.adjusted_low > frame.adjusted_high) | (frame.adjusted_close > frame.adjusted_high) | (frame.adjusted_close < frame.adjusted_low)
        if "adjusted_open" in frame:
            bad |= (frame.adjusted_open > frame.adjusted_high) | (frame.adjusted_open < frame.adjusted_low)
    if not numeric and metadata.get("kind") != "calendar" and not any(c in frame for c in ["is_open", "name", "title", "suspend_type"]):
        raise DataQualityError("Unsupported schema: no recognised market values")
    values = [c for c in frame if not c.startswith("_")]
    duplicates = frame.duplicated(values)
    conflicts = frame.loc[~duplicates].duplicated(keys, keep=False)
    conflict_index = conflicts.index[conflicts]
    bad.loc[conflict_index] = True
    frame.loc[bad, "_quality_flags"] += "invalid_or_conflicting;"
    rejected = frame.loc[bad].copy()
    cleaned = frame.loc[~bad & ~duplicates].sort_values(keys).reset_index(drop=True)
    if bad.any():
        issues.append(f"{int(bad.sum())} invalid/conflicting rows quarantined")
    if duplicates.any():
        issues.append(f"{int(duplicates.sum())} identical duplicates removed")
    missing = 0
    unexpected = 0
    calendar_state = "not_applicable"
    if date_col in {"date", "trade_date"} and metadata.get("market") == "US" and not cleaned.empty:
        import exchange_calendars as xcals
        calendar = xcals.get_calendar("XNYS", start=str((pd.Timestamp(cleaned[date_col].min()) - pd.Timedelta(days=1)).date()), end=str((pd.Timestamp(cleaned[date_col].max()) + pd.Timedelta(days=1)).date()))
        sessions = pd.DatetimeIndex(calendar.sessions).tz_localize(None)
        observed = pd.DatetimeIndex(pd.to_datetime(cleaned[date_col].unique()))
        unexpected = len(observed.difference(sessions))
        if keys[0] != date_col:
            for _, group in cleaned.groupby(keys[0]):
                group_dates = pd.DatetimeIndex(pd.to_datetime(group[date_col]))
                expected = sessions[(sessions >= group_dates.min()) & (sessions <= group_dates.max())]
                missing += len(expected.difference(group_dates))
        else:
            missing = len(sessions.difference(observed))
        calendar_state = "XNYS"
        if missing:
            issues.append(f"{missing} missing sessions within observed symbol spans; not filled")
        if unexpected:
            issues.append(f"{unexpected} unexpected sessions")
    elif metadata.get("market") == "CN":
        calendar_state = "requires_strategy_calendar_validation"
    quality = {"issues": issues, "blocking": bool(bad.any() or unexpected or cleaned.empty), "invalid_rows": int(bad.sum()), "conflict_rows": len(conflict_index), "invalid_dates": invalid_dates,
               "duplicate_rows": int(duplicates.sum()), "modified_rows": int(cleaned._quality_flags.ne("").sum()),
               "missing_sessions": missing, "unexpected_sessions": unexpected, "calendar": calendar_state,
               "imputed_rows": 0, "policy": "Evidence-only repair; unknown price/activity gaps remain unfilled"}
    kind = "daily_prices" if "adjusted_close" in frame else "daily_ohlc" if "open" in frame else "universe" if "snapshot_date" in frame else "auction_entries" if "entry_0931_vwap" in frame else "auction_snapshot" if "price" in frame else "minute_bars"
    if "adj_factor" in frame and "close" not in frame:
        kind = "adjustment_factors"
    if "up_limit" in frame:
        kind = "daily_limits"
    if "div_cash" in frame or "cash_per_share" in frame:
        kind = "dividends"
    if "title" in frame:
        kind = "announcements"
    elif "is_open" in frame:
        kind = "calendar"
    elif "suspend_type" in frame:
        kind = "suspensions"
    return cleaned, rejected, quality, kind


def clean_stock_dividends(frame: pd.DataFrame):
    """Retain plan/progress disclosures, validate supplied event economics.

    Nullable plan dates/amounts stay missing; entrance checks implemented events
    in its own declared period. Never convert a missing cash entitlement to zero.
    """
    frame = frame.copy()
    frame["_raw_row"] = np.arange(len(frame))
    frame["_quality_flags"] = "normalized_statement_dates;"
    identifier = "ts_code" if "ts_code" in frame else "symbol"
    frame[identifier] = frame[identifier].astype("string").str.upper().str.strip()
    bad = frame[identifier].isna() | frame[identifier].eq("")
    implemented = frame.div_proc.eq("实施")
    invalid_dates = 0
    for col in [c for c in frame if c.endswith("date")]:
        values = frame[col].astype("string").str.replace(r"\.0$", "", regex=True)
        present = values.notna() & values.ne("")
        dates = pd.to_datetime(values, errors="coerce", format="mixed")
        invalid = present & dates.isna()
        if col == "ann_date":
            imp = pd.to_datetime(frame.get("imp_ann_date", pd.Series(index=frame.index, dtype="object")).astype("string").str.replace(r"\.0$", "", regex=True), errors="coerce", format="mixed")
            invalid |= implemented & dates.isna() & imp.isna()
        invalid_dates += int(invalid.sum())
        bad |= invalid
        frame[col] = dates.dt.strftime("%Y-%m-%d")
    for col in [c for c in ["stk_div", "stk_bo_rate", "stk_co_rate", "cash_div", "cash_div_tax"] if c in frame]:
        present = frame[col].notna()
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
        bad |= present & (~np.isfinite(frame[col]) | frame[col].lt(0))
    values = [c for c in frame if not c.startswith("_")]
    duplicates = frame.duplicated(values)
    conflicts = pd.Series(False, index=frame.index)
    superseded = pd.Series(False, index=frame.index)
    alternatives = pd.Series(False, index=frame.index)
    implemented = frame.div_proc.eq("实施")
    economics = [c for c in ["cash_div_tax", "stk_div", "record_date", "pay_date", "div_listdate"] if c in frame]
    # Multiple fiscal-period entitlements may share one ex-date. Only compare
    # revisions within the same supplied period; absent period evidence must
    # not be guessed from announcement dates or amounts.
    event_key = [identifier, "ex_date"] + (["end_date"] if "end_date" in frame else [])
    announcements = pd.to_datetime(frame.ann_date, errors="coerce")
    if "imp_ann_date" in frame:
        announcements = pd.to_datetime(frame.imp_ann_date, errors="coerce").fillna(announcements)
    for _, group in frame.loc[implemented & frame.ex_date.notna() & ~bad].groupby(event_key, dropna=False):
        # An explicitly later pre-ex announcement revises the same fiscal
        # entitlement. Keep the earlier disclosure in immutable raw evidence.
        known = group.loc[announcements.loc[group.index].le(pd.to_datetime(group.ex_date))]
        known_ann = announcements.loc[known.index]
        different_economics = len(group[economics].drop_duplicates()) > 1
        # Different proposal dates may be additional payouts or an aggregate
        # revision. Neither "latest" nor sum-all is safe: preserve alternatives
        # for the mandatory factor corroboration at the stock entrance.
        original_ann = pd.to_datetime(known.ann_date, errors="coerce")
        if different_economics and original_ann.nunique() > 1 and len(known) == len(group):
            alternatives.loc[group.index] = True
            frame.loc[group.index, "_quality_flags"] += "alternative_entitlements_require_factor_audit;"
            continue
        if "end_date" in group and group.end_date.notna().all() and not known.empty and known_ann.nunique() > 1 and not different_economics:
            latest = known_ann.max()
            superseded.loc[known.index[known_ann.lt(latest)]] = True
            group = group.loc[~superseded.loc[group.index]]
        if len(group[economics].drop_duplicates()) > 1:
            conflicts.loc[group.index] = True
    bad |= conflicts
    frame.loc[bad, "_quality_flags"] = "invalid_or_conflicting;"
    quality = {"blocking": bool(bad.any()), "issues": ["Invalid/conflicting stock dividend disclosures"] if bad.any() else [],
               "invalid_rows": int(bad.sum()), "conflict_rows": int(conflicts.sum()), "invalid_dates": invalid_dates,
               "duplicate_rows": int(duplicates.sum()), "modified_rows": len(frame), "imputed_rows": 0,
               "superseded_pre_ex_disclosures": int(superseded.sum()),
               "alternative_entitlement_rows": int(alternatives.sum()),
               "policy": "Keep nullable plans and missing implemented economics explicit; period-specific entrance audit required"}
    return frame.loc[~bad & ~duplicates & ~superseded].reset_index(drop=True), frame.loc[bad].copy(), quality, "stock_dividends"


def _remember(manifest: dict) -> None:
    if not hasattr(_local, "inputs"):
        _local.inputs = {}
        _local.exclusions = []
    _local.inputs[manifest["id"]] = {"version": manifest["id"], "raw_sha256": manifest["raw_sha256"], "clean_sha256": manifest["clean_sha256"], "source_name": manifest["source_name"], "node": manifest["node"]}


def read_clean_csv(source: Path | str, allow_quarantine: bool = False, *, version: str | None = None, **kwargs) -> pd.DataFrame:
    store = DataStore(root_for(Path(source)))
    manifest = store.resolve_pinned(Path(source), version, allow_quarantine) if version is not None else store.resolve(source, allow_quarantine=allow_quarantine)
    path = store.verified_path(manifest)
    frame = pd.read_csv(path, **kwargs)
    _remember(manifest)
    if manifest["status"] != "ready":
        _local.exclusions.append({"reason": "auction_cross_section_quarantine", "version": manifest["id"], "excluded_rows": manifest["quality"]["invalid_rows"], "policy": "Only valid observed auction rows; fixed model minimum cross-section still enforced"})
    frame.attrs["data_version"] = manifest["id"]
    return frame.drop(columns=[c for c in frame if c.startswith("_quality") or c == "_raw_row"])


def read_clean_json(source: Path, *, version: str | None = None) -> dict:
    store = DataStore(root_for(source))
    manifest = store.resolve_pinned(source, version) if version is not None else store.resolve(source)
    _remember(manifest)
    return json.loads(store.verified_path(manifest).read_text(encoding="utf-8"))


def input_evidence() -> dict:
    return {"data_versions": list(getattr(_local, "inputs", {}).values()), "input_exclusions": list(getattr(_local, "exclusions", []))}


def report_json(value: dict, *args, **kwargs) -> str:
    """All CLI summaries/manifests carry the resolved immutable input versions."""
    return json.dumps({**value, **input_evidence()} if isinstance(value, dict) else value, *args, **kwargs)


def complete_panel(frame: pd.DataFrame, subset=None) -> pd.DataFrame:
    """Allow explicitly logged common-inception trimming, never internal gaps."""
    required = frame.loc[:, subset] if subset is not None else frame
    valid = required.notna().all(axis=1)
    if not valid.any():
        raise DataQualityError("No complete input sessions")
    first = valid[valid].index[0]
    if not valid.loc[first:].all():
        raise DataQualityError("Missing required prices after common inception; no silent dropna")
    trimmed = frame.loc[first:]
    if len(trimmed) != len(frame):
        if not hasattr(_local, "exclusions"):
            _local.exclusions = []
        _local.exclusions.append({"reason": "common_inception", "excluded_sessions": len(frame) - len(trimmed), "start": str(first)})
    if isinstance(trimmed.index, pd.DatetimeIndex) and len(trimmed) > 1:
        import exchange_calendars as xcals
        calendar = xcals.get_calendar("XNYS", start=str(trimmed.index[0].date()), end=str(trimmed.index[-1].date()))
        sessions = pd.DatetimeIndex(calendar.sessions).tz_localize(None)
        if len(sessions.difference(trimmed.index)):
            raise DataQualityError("Missing entire required sessions; no implicit zero returns")
    return trimmed


def capture_frame(frame: pd.DataFrame, source: Path, metadata: dict) -> pd.DataFrame:
    """Provider response first, then a clean, version-pinned consumer view."""
    source.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(source, index=False)
    store = DataStore(root_for(source))
    store.import_file(source, {**metadata, "origin": "provider_response"})
    return read_clean_csv(source)
