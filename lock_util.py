"""
Lock Utility Module for Microsoft Word COM Process Synchronization.
Provides a thread-safe and process-safe file-based mutex lock to prevent concurrent
access to the Microsoft Word COM automation server and mitigate WINWORD.EXE crashes.
"""

import os
import sys
import time
import subprocess
from pathlib import Path
from contextlib import contextmanager

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_LOCK_FILE = BASE_DIR / "word_com.lock"


class WordComLockTimeout(Exception):
    """Raised when the Word COM mutex cannot be acquired within the timeout window."""
    pass


def kill_hung_word_processes():
    """
    Forcefully terminates any orphan or hung WINWORD.EXE processes on Windows.
    This clears stuck COM locks, dialog popups, or unresponsive Word server states.
    """
    if sys.platform != "win32":
        return

    try:
        # Use taskkill to kill Word instances gracefully or forcibly
        result = subprocess.run(
            ["taskkill", "/f", "/im", "winword.exe"],
            capture_output=True,
            text=True
        )
        if result.returncode == 0:
            print("[lock_util] Cleaned up hung WINWORD.EXE process(es).")
    except Exception as exc:
        print(f"[lock_util] Note: taskkill winword check: {exc}")


@contextmanager
def word_com_lock(lock_path: Path = DEFAULT_LOCK_FILE, timeout: float = 60.0, retry_interval: float = 0.5):
    """
    Thread-safe and process-safe context manager for Word COM operations.
    
    Args:
        lock_path: Path to the lock file.
        timeout: Maximum seconds to wait for lock acquisition.
        retry_interval: Seconds between acquisition attempts.
    """
    start_time = time.time()
    lock_fd = None
    acquired = False

    while not acquired:
        try:
            # Atomic file creation across Windows and POSIX
            # O_CREAT | O_EXCL ensures only one process creates the file successfully
            lock_fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_RDWR)
            # Write current PID and timestamp to lock file for diagnostics and stale lock cleanup
            lock_info = f"PID:{os.getpid()}|TIME:{time.time()}\n"
            os.write(lock_fd, lock_info.encode("utf-8"))
            acquired = True
        except FileExistsError:
            # Check for stale lock (older than 2 minutes)
            try:
                mtime = os.path.getmtime(lock_path)
                if (time.time() - mtime) > 120.0:
                    print(f"[lock_util] Detected stale lock at {lock_path}. Reclaiming lock...")
                    kill_hung_word_processes()
                    try:
                        os.remove(lock_path)
                    except OSError:
                        pass
            except OSError:
                pass

            if (time.time() - start_time) > timeout:
                kill_hung_word_processes()
                raise WordComLockTimeout(
                    f"Timed out after {timeout}s waiting for Word COM lock at {lock_path}."
                )
            time.sleep(retry_interval)
        except Exception as exc:
            if (time.time() - start_time) > timeout:
                raise WordComLockTimeout(f"Failed to acquire Word COM lock: {exc}")
            time.sleep(retry_interval)

    try:
        yield
    finally:
        if acquired:
            try:
                if lock_fd is not None:
                    os.close(lock_fd)
            except OSError:
                pass
            try:
                if os.path.exists(lock_path):
                    os.remove(lock_path)
            except OSError:
                pass
