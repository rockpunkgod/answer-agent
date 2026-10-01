"""Short, bounded cross-process resource locks. No database is used as a GUI lock."""
from contextlib import contextmanager
import os
from pathlib import Path
import threading
import time

_guard = threading.Lock()
_locks: dict[str, threading.Lock] = {}


@contextmanager
def resource_lock(path: str | Path, timeout: float = 5):
    path = str(Path(path).resolve())
    with _guard:
        local = _locks.setdefault(path, threading.Lock())
    if not local.acquire(timeout=timeout):
        raise TimeoutError("Resource busy; bounded wait expired")
    stream = None
    locked = False
    try:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        stream = open(path, "a+b")
        if stream.seek(0, 2) == 0:
            stream.write(b"0")
            stream.flush()
        deadline = time.monotonic() + timeout
        while True:
            stream.seek(0)
            try:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked = True
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise TimeoutError("Resource busy; bounded wait expired")
                time.sleep(.02)
        yield
    finally:
        if stream is not None:
            if locked:
                stream.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
            stream.close()
        local.release()
