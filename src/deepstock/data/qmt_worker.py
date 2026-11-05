"""Private native worker; started only by the QMT supervisor over stdin."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import threading
import time

import pandas as pd

from deepstock.massive import load_env_value
from .qmt_runtime import PORT
from .store import digest, write_json


def main():
    if sys.stdin.readline().strip() != "start":
        return
    request_path = Path(sys.argv[1])
    run = request_path.parent
    request = json.loads(request_path.read_text("utf-8"))
    receipt = {"request_sha256": digest(request_path), "status": "failed", "operations": [],
               "native_shutdown_fixed": False, "strategy_input_approved": False}
    token = ""
    stage = "configuration"
    try:
        if request["schema"] != "qmt-readonly-v1":
            raise ValueError("Unknown worker request schema")
        token = load_env_value(str(Path(request["root"]) / ".env"), "QMT_VIP_TOKEN") or ""
        if not token:
            raise ValueError("Missing local token")
        stage = "native_import"
        sys.path.insert(0, request["sdk_root"])
        from xtquant import xtdatacenter as dc
        from xtquant import xtdata as xd
        xd.enable_hello = False

        def operation(name, fn):
            nonlocal stage
            stage = name
            started = time.perf_counter()
            result = fn()
            receipt["operations"].append({"name": name, "seconds": time.perf_counter() - started, "result": result})
            return result

        def connect():
            dc.set_token(token)
            dc.set_data_home_dir(str(run / "data_home"))
            dc.set_init_markets(["SH", "SZ"])
            dc.init(start_local_service=False)
            ip, port = dc.listen("127.0.0.1", PORT)
            xd.connect(ip, port)
            return {"ip": ip, "port": port, "native_shutdown_available": hasattr(dc.__dict__.get("__dc"), "shutdown")}
        operation("connect", connect)
        day = request["date"].replace("-", "")
        stocks = request["symbols"]

        def safe_default(value):
            if hasattr(value, "tolist"):
                return value.tolist()
            if hasattr(value, "isoformat"):
                return value.isoformat()
            return str(value)

        def download(codes, period, start, end):
            progress = []
            result = xd.download_history_data2(codes, period, start_time=start, end_time=end,
                                               callback=progress.append, incrementally=False)
            value = {"return_metadata": result, "progress": progress}
            write_json(run / (period + "-download.json"), json.loads(json.dumps(value, default=safe_default)))
            return {"requested_stocks": len(codes), "metadata_stocks": len(result) if isinstance(result, dict) else None,
                    "finished": progress[-1].get("finished") if progress else None}

        operation("minute_download", lambda: download(stocks, "1m", day + "093000", day + "093200"))
        def read_minutes():
            data = xd.get_market_data_ex([], stocks, "1m", day + "093000", day + "093200", -1, "none", False)
            frames = []
            for symbol, frame in data.items():
                saved = frame.copy()
                saved.insert(0, "symbol", symbol)
                frames.append(saved)
            combined = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=["symbol", "time", "open", "high", "low", "close", "volume", "amount"])
            combined.to_csv(run / "minute.csv.gz", index=False, compression={"method": "gzip", "mtime": 0})
            return {"returned_stocks": len(data), "nonempty_stocks": sum(not f.empty for f in data.values()), "rows": len(combined)}
        operation("minute_read", read_minutes)
        if request["tick_symbols"]:
            codes = request["tick_symbols"]
            # Include the actual auction baseline and observations after 09:31.
            operation("tick_download", lambda: download(codes, "tick", day + "092500", day + "093103"))
            def read_ticks():
                data = xd.get_market_data_ex([], codes, "tick", day + "092500", day + "093103", -1, "none", False)
                write_json(run / "tick.json", {code: json.loads(frame.to_json(orient="split", double_precision=15)) for code, frame in data.items()})
                return {"nonempty_stocks": sum(not f.empty for f in data.values()), "rows": sum(len(f) for f in data.values())}
            operation("tick_read", read_ticks)
        if request["watch_seconds"]:
            def watch():
                events = []
                codes = (request["tick_symbols"] or stocks)[:3]
                def receive(data):
                    events.append({"received_at_utc": datetime.now(timezone.utc).isoformat(), "quotes": data})
                seq = xd.subscribe_whole_quote(codes, receive)
                if seq <= 0:
                    raise ValueError("Subscription rejected")
                try:
                    time.sleep(request["watch_seconds"])
                    snapshot = xd.get_full_tick(codes)
                finally:
                    xd.unsubscribe_quote(seq)
                write_json(run / "quotes.json", json.loads(json.dumps({"events": events, "snapshot": snapshot}, default=safe_default)))
                stamps = [float(q["time"]) for q in snapshot.values() if q.get("time")]
                return {"events": len(events), "snapshot_stocks": len(snapshot), "observed_seconds": request["watch_seconds"],
                        "max_source_age_seconds": max((time.time() - ms / 1000 for ms in stamps), default=None)}
            operation("quote_watch", watch)
        # RPC client has a working disconnect; the native data center does not.
        operation("client_disconnect", lambda: xd.disconnect())
        receipt["status"] = "complete"
    except Exception as exc:
        receipt.update(error_type=type(exc).__name__, failed_stage=stage)
        # The raw provider message is private and never emitted by the CLI.
        detail = str(exc).replace(token, "[REDACTED]") if token else str(exc)
        (run / "worker-error-private.txt").write_text(detail, encoding="utf-8")
    finally:
        receipt["files"] = [{"path": p.name, "bytes": p.stat().st_size, "sha256": digest(p)}
                            for p in sorted(run.iterdir()) if p.is_file() and p.name not in {"receipt.json", "native-private.log", "request.json"}]
        receipt["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
        write_json(run / "receipt.json", receipt)
        # Parent validates all files, then uses TerminateProcess. Never enter
        # the SDK's known-broken interpreter/DLL teardown or call its shutdown.
        threading.Event().wait()


if __name__ == "__main__":
    main()
