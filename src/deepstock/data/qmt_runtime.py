"""Bounded read-only QMT supervisor. The parent never imports native xtquant."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import socket
import subprocess
import sys
import time
import uuid
import zipfile

from .store import DataQualityError, digest, write_json

SDK_VERSION = "250807.1.2"
WHEEL_SHA256 = "91f19ff9a92971c5abe64fbd077e5212e0418f0820aa3427aef3444230f72921"
PORT = 58611


def verify_sdk(sdk: Path, wheel: Path) -> None:
    if digest(wheel) != WHEEL_SHA256:
        raise DataQualityError("QMT wheel checksum mismatch")
    expected = set()
    with zipfile.ZipFile(wheel) as archive:
        for info in archive.infolist():
            path = PurePosixPath(info.filename)
            if info.is_dir() or not path.parts or path.parts[0] != "xtquant":
                continue
            if path.is_absolute() or ".." in path.parts or "\\" in info.filename or ":" in info.filename:
                raise DataQualityError("Invalid SDK archive path")
            expected.add(path.as_posix())
            target = sdk.joinpath(*path.parts)
            if not target.is_file() or hashlib.sha256(target.read_bytes()).digest() != hashlib.sha256(archive.read(info)).digest():
                raise DataQualityError("Extracted QMT SDK differs from pinned wheel")
    for path in (sdk / "xtquant").rglob("*"):
        if path.suffix.lower() in {".py", ".pyd", ".dll"} and path.relative_to(sdk).as_posix() not in expected:
            raise DataQualityError("Unexpected executable file in isolated QMT SDK")


@contextmanager
def native_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise DataQualityError("Another QMT capture owns the token lock") from exc
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class WorkerJob:
    """Windows kernel kills the native worker when its parent handle closes."""
    def __enter__(self):
        self.handle = None
        if os.name != "nt":
            return self
        import ctypes as c
        from ctypes import wintypes as w

        class Limits(c.Structure):
            _fields_ = [("process_time", c.c_longlong), ("job_time", c.c_longlong),
                        ("flags", w.DWORD), ("min_ws", c.c_size_t), ("max_ws", c.c_size_t),
                        ("active", w.DWORD), ("affinity", c.c_size_t), ("priority", w.DWORD), ("scheduling", w.DWORD)]
        class IO(c.Structure):
            _fields_ = [(name, c.c_ulonglong) for name in ["read_ops", "write_ops", "other_ops", "read_bytes", "write_bytes", "other_bytes"]]
        class Extended(c.Structure):
            _fields_ = [("basic", Limits), ("io", IO), ("process_memory", c.c_size_t),
                        ("job_memory", c.c_size_t), ("peak_process", c.c_size_t), ("peak_job", c.c_size_t)]
        self.kernel = c.WinDLL("kernel32", use_last_error=True)
        self.kernel.CreateJobObjectW.argtypes = [c.c_void_p, w.LPCWSTR]
        self.kernel.CreateJobObjectW.restype = w.HANDLE
        self.kernel.SetInformationJobObject.argtypes = [w.HANDLE, c.c_int, c.c_void_p, w.DWORD]
        self.kernel.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
        self.kernel.CloseHandle.argtypes = [w.HANDLE]
        self.handle = self.kernel.CreateJobObjectW(None, None)
        if not self.handle:
            raise c.WinError(c.get_last_error())
        limits = Extended()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.kernel.SetInformationJobObject(self.handle, 9, c.byref(limits), c.sizeof(limits)):
            error = c.get_last_error()
            self.__exit__(None, None, None)
            raise c.WinError(error)
        return self

    def assign(self, process):
        if self.handle is not None and not self.kernel.AssignProcessToJobObject(self.handle, int(process._handle)):
            import ctypes
            raise ctypes.WinError(ctypes.get_last_error())

    def __exit__(self, *exc):
        if self.handle is not None:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


def verify_receipt(run: Path, request_hash: str) -> dict:
    receipt = json.loads((run / "receipt.json").read_text("utf-8"))
    if receipt.get("request_sha256") != request_hash or receipt.get("status") != "complete":
        raise DataQualityError("QMT capture did not complete the requested operations")
    files = receipt.get("files")
    if not isinstance(files, list) or not files:
        raise DataQualityError("QMT worker supplied no evidence files")
    names = set()
    for item in files:
        name = item["path"]
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts or "\\" in name or ":" in name or name in names:
            raise DataQualityError("Invalid QMT receipt file path")
        names.add(name)
        local = run.joinpath(*path.parts)
        if not local.is_file() or local.stat().st_size != item["bytes"] or digest(local) != item["sha256"]:
            raise DataQualityError("QMT captured evidence checksum mismatch")
    request = json.loads((run / "request.json").read_text("utf-8"))
    if request.get("schema") == "qmt-readonly-v1":
        required = {"minute.csv.gz", "1m-download.json"}
        if request.get("tick_symbols"):
            required |= {"tick.json", "tick-download.json"}
        if request.get("watch_seconds"):
            required.add("quotes.json")
        if not required.issubset(names):
            raise DataQualityError("QMT receipt omits requested capture outputs")
    return receipt


def supervise(command: list[str], root: Path, run: Path, *, timeout: float) -> dict:
    """No automatic retries; native failure and missing/partial receipts fail closed."""
    expected = digest(run / "request.json")
    started = time.monotonic()
    outcome = {"status": "failed", "cleanup": "parent_supervised_termination", "native_shutdown_fixed": False}
    with WorkerJob() as job, (run / "native-private.log").open("wb") as log:
        process = subprocess.Popen(command, cwd=root, stdin=subprocess.PIPE, stdout=log, stderr=log,
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        try:
            job.assign(process)
            process.stdin.write(b"start\n")
            process.stdin.flush()
            while time.monotonic() - started < timeout:
                code = process.poll()
                if code is not None:
                    raise DataQualityError(f"Native worker exited before supervised stop: {code}")
                if (run / "receipt.json").exists():
                    receipt = verify_receipt(run, expected)
                    if process.poll() is not None:
                        raise DataQualityError("Native worker exited while receipt was being verified")
                    outcome.update(status="complete", receipt=receipt)
                    break
                time.sleep(.05)
            else:
                raise TimeoutError("QMT native worker deadline exceeded")
        except Exception as exc:
            outcome.update(error_type=type(exc).__name__, error=str(exc))
        finally:
            # On Windows this is TerminateProcess, bypassing broken DLL teardown.
            if process.poll() is None:
                process.terminate()
            elif outcome["status"] == "complete":
                outcome.update(status="failed", error_type="DataQualityError", error="Native worker exited before termination")
            try:
                outcome["worker_returncode"] = process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                outcome["worker_returncode"] = process.wait(timeout=10)
            process.stdin.close()
            if outcome["status"] == "complete" and outcome["worker_returncode"] != (1 if os.name == "nt" else -15):
                outcome.update(status="failed", error_type="DataQualityError", error="Unexpected native termination status")
    outcome["elapsed_seconds"] = time.monotonic() - started
    write_json(run / "supervisor.json", outcome)
    return outcome


def capture(root: Path, symbols: list[str], trade_date: date, *, tick_symbols=(), watch_seconds=0, timeout=180) -> tuple[Path, dict]:
    import re
    if os.name != "nt":
        raise DataQualityError("Native QMT capture runs only on the quantitative Windows node")
    if not 1 <= len(symbols) <= 300 or len(set(symbols)) != len(symbols) or any(not re.fullmatch(r"\d{6}\.(SH|SZ)", s) for s in symbols):
        raise DataQualityError("Require 1–300 distinct SH/SZ symbols")
    if len(tick_symbols) > 30 or len(set(tick_symbols)) != len(tick_symbols) or not set(tick_symbols).issubset(symbols):
        raise DataQualityError("Tick sample must be a distinct subset of at most 30 symbols")
    if not 0 <= watch_seconds <= 60 or not 15 <= timeout <= 300:
        raise DataQualityError("Invalid bounded watch/deadline")
    runtime = root / "artifacts/qmt-vip"
    sdk = runtime / ("sdk-" + SDK_VERSION)
    wheel = runtime / ("xtquant-" + SDK_VERSION + "-py3-none-any.whl")
    with native_lock(runtime / "native.lock"):
        verify_sdk(sdk, wheel)
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=True).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=root, capture_output=True, text=True, check=True).stdout.strip()
        if dirty:
            raise DataQualityError("QMT capture requires committed clean tracked code")
        with socket.socket() as check:
            check.bind(("127.0.0.1", PORT))
        run = runtime / ("capture-" + uuid.uuid4().hex)
        run.mkdir()
        request = {"schema": "qmt-readonly-v1", "sdk_version": SDK_VERSION, "sdk_root": str(sdk.resolve()),
                   "root": str(root.resolve()), "symbols": symbols, "tick_symbols": list(tick_symbols),
                   "date": trade_date.isoformat(), "watch_seconds": watch_seconds, "nonce": uuid.uuid4().hex,
                   "code_commit": head}
        write_json(run / "request.json", request)
        result = supervise([sys.executable, "-m", "deepstock.data.qmt_worker", str((run / "request.json").resolve())], root, run, timeout=timeout)
        with socket.socket() as check:
            try:
                check.bind(("127.0.0.1", PORT))
                result["local_port_released"] = True
            except OSError:
                result.update(status="failed", local_port_released=False)
        write_json(run / "supervisor.json", result)
        return run, result
