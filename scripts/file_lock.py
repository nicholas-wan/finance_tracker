"""One cross-process lock for every finance-data writer."""

import os
import threading
import time
from pathlib import Path


class FinanceWriteLock:
    """Serialize threads and processes that publish or edit finance data."""

    def __init__(self, path, timeout=30):
        self.path = Path(path)
        self.timeout = timeout
        self.thread_lock = threading.Lock()
        self.handle = None

    def __enter__(self):
        self.thread_lock.acquire()
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.handle = self.path.open("a+b")
            self.handle.seek(0, os.SEEK_END)
            if self.handle.tell() == 0:
                self.handle.write(b"0")
                self.handle.flush()
            deadline = time.monotonic() + self.timeout
            while True:
                try:
                    self._lock()
                    return self
                except OSError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError(
                            "Another finance import or dashboard save is still running."
                        )
                    time.sleep(0.05)
        except Exception:
            if self.handle is not None:
                self.handle.close()
                self.handle = None
            self.thread_lock.release()
            raise

    def _lock(self):
        self.handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            self.handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        finally:
            self.handle.close()
            self.handle = None
            self.thread_lock.release()
