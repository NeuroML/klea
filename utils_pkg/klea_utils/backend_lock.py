#!/usr/bin/env python3
"""
Backend lockfile helpers shared by Klea backends.

See ``devdocs/system/api-sse-sequence.md`` and ``devdocs/system/c4-container.md``;
ADR-0023 (one backend per storage root) and ADR-0031.

File: klea_utils/backend_lock.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging
import os

logger = logging.getLogger(__name__)


def _windows_pid_alive(pid: int) -> bool:
    """Return whether *pid* names a running Windows process.

    Uses ``OpenProcess`` + ``WaitForSingleObject`` rather than ``os.kill``:
    on Windows ``os.kill`` with a non-control signal terminates the target
    process, so it is not a safe liveness probe.
    """
    import ctypes
    from ctypes import wintypes

    synchronize = 0x00100000
    wait_timeout = 0x102
    error_access_denied = 5
    kernel32 = getattr(ctypes, "WinDLL")(  # noqa: B009
        "kernel32", use_last_error=True
    )
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    handle = kernel32.OpenProcess(synchronize, False, pid)
    if not handle:
        # Access denied means the process exists but belongs to another user.
        return getattr(ctypes, "get_last_error")() == error_access_denied  # noqa: B009
    try:
        return kernel32.WaitForSingleObject(handle, 0) == wait_timeout
    finally:
        kernel32.CloseHandle(handle)


def _windows_start_time(pid: int) -> str | None:
    """Return a Windows process's creation time as a stable string."""
    import ctypes
    from ctypes import wintypes

    class FileTime(ctypes.Structure):
        _fields_ = [
            ("dwLowDateTime", wintypes.DWORD),
            ("dwHighDateTime", wintypes.DWORD),
        ]

    process_query_limited_information = 0x1000
    kernel32 = getattr(ctypes, "WinDLL")(  # noqa: B009
        "kernel32", use_last_error=True
    )
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetProcessTimes.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(FileTime),
        ctypes.POINTER(FileTime),
        ctypes.POINTER(FileTime),
        ctypes.POINTER(FileTime),
    ]
    kernel32.GetProcessTimes.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
    if not handle:
        return None
    created = FileTime()
    exited = FileTime()
    kernel = FileTime()
    user = FileTime()
    try:
        if not kernel32.GetProcessTimes(
            handle,
            ctypes.byref(created),
            ctypes.byref(exited),
            ctypes.byref(kernel),
            ctypes.byref(user),
        ):
            return None
    finally:
        kernel32.CloseHandle(handle)
    return str((created.dwHighDateTime << 32) | created.dwLowDateTime)


def _posix_start_time(pid: int) -> str | None:
    """Return a POSIX process's start time as a stable string.

    Linux exposes it as field 22 of ``/proc/<pid>/stat``.  macOS has no
    procfs, so fall back to ``ps -o lstart=`` (one-second resolution).
    Both the lock writer and the stale-lock reader use this helper, so the
    recorded and observed strings compare consistently, and a reused PID
    still has a different start time.
    """
    import subprocess
    from pathlib import Path

    try:
        stat = Path(f"/proc/{pid}/stat").read_text(errors="replace")
    except OSError:
        stat = None
    if stat is not None:
        # ``comm`` (field 2) may contain spaces and parentheses, so only
        # split after its closing parenthesis.
        rparen = stat.rfind(")")
        if rparen >= 0:
            fields = stat[rparen + 1 :].split()
            # ``fields[0]`` is field 3 (state); start time is field 22.
            if len(fields) > 19:
                return fields[19]

    try:
        result = subprocess.run(
            ["ps", "-o", "lstart=", "-p", str(pid)],
            capture_output=True,
            text=True,
            check=False,
            env={**os.environ, "LC_ALL": "C", "TZ": "UTC"},
        )
    except OSError as exc:
        logger.debug("Could not read start time for pid %s: %s", pid, exc)
        return None
    start = " ".join(result.stdout.split())
    return start or None


def _acquire_backend_lock(
    lock_path,
    root,
    host: str,
    port: int,
) -> int:
    """Atomically create and exclusively lock the backend lockfile.

    The lockfile is the single source of truth for "which backend owns
    this storage root" (ADR-0023: one backend per storage root).  It is
    created with ``O_CREAT | O_EXCL`` and additionally protected by an
    exclusive OS file lock (``fcntl.flock`` on POSIX,
    ``msvcrt.locking`` on Windows), and records the owning PID, that
    process' start time (``/proc/<pid>/stat`` field 22 on POSIX,
    ``GetProcessTimes`` on Windows), the resolved storage root, the Klea
    version, the bind address and a timestamp.

    A lockfile whose owner is gone -- the OS lock is free and either the
    PID is dead or its start time no longer matches, i.e. the PID was
    recycled -- is *stale* and is reclaimed.  A lockfile that is still
    held makes this function raise :class:`SystemExit` naming the live
    owner.

    :param lock_path: Path of the lockfile to create
    :param root: Resolved storage root that the lock guards
    :param host: Host the backend binds to (recorded for the owner info)
    :param port: Port the backend binds to (recorded for the owner info)
    :returns: The open file descriptor that holds the lock; keep it open
        for the lifetime of the process so the OS lock is not released
    :raises SystemExit: If another live backend already owns *root*
    """
    import json
    import time
    from pathlib import Path

    try:
        from importlib.metadata import version as _pkg_version

        klea_version = _pkg_version("klea-utils")
    except Exception:  # noqa: BLE001 - may be running from a source tree
        klea_version = "unknown"

    lock_path = Path(lock_path)
    root = Path(root)

    def _start_time(pid: int) -> str | None:
        """Return the process start time, if it is readable.

        POSIX reads ``/proc/<pid>/stat`` field 22 or falls back to ``ps`` on
        macOS.  Windows uses ``GetProcessTimes`` (creation time), which is
        stable for the lifetime of a process and changes if the PID is reused.
        """
        if os.name == "nt":
            return _windows_start_time(pid)
        return _posix_start_time(pid)

    def _pid_alive(pid: int) -> bool:
        """Return True if *pid* names a running process."""
        if not isinstance(pid, int) or pid <= 0:
            return False
        if os.name == "nt":
            return _windows_pid_alive(pid)
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            # Owned by another user, but it exists.
            return True
        return True

    def _lock_fd(fd: int) -> None:
        """Take a non-blocking exclusive OS lock on *fd*."""
        if os.name == "nt":
            import msvcrt

            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            os.lseek(fd, 0, os.SEEK_SET)
            return
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _read_owner() -> dict:
        """Return the recorded owner info, or an empty dict."""
        try:
            with lock_path.open(encoding="utf-8") as handle:
                owner = json.load(handle)
        except (OSError, ValueError):
            return {}
        return owner if isinstance(owner, dict) else {}

    def _owner_summary(owner: dict) -> str:
        return (
            f"pid {owner.get('pid', '?')} "
            f"(started {owner.get('timestamp', '?')}) "
            f"on {owner.get('host', '?')}:{owner.get('port', '?')}, "
            f"root {owner.get('root', root)}, "
            f"Klea {owner.get('version', '?')}"
        )

    fd = None
    for _attempt in range(3):
        try:
            fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o644)
        except FileExistsError:
            fd = None
        except OSError as exc:
            raise SystemExit(
                f"Could not create the backend lockfile {lock_path}: {exc}"
            ) from exc

        if fd is not None:
            try:
                _lock_fd(fd)
            except OSError as exc:
                os.close(fd)
                raise SystemExit(
                    f"Could not lock the backend lockfile {lock_path}: {exc}"
                ) from exc
            break

        # The lockfile already exists: find out whether it is live or stale.
        try:
            probe = os.open(lock_path, os.O_RDWR)
        except FileNotFoundError:
            continue  # the owner removed it; try to create it again
        except OSError as exc:
            raise SystemExit(
                f"Could not open the backend lockfile {lock_path}: {exc}"
            ) from exc

        try:
            _lock_fd(probe)
        except OSError:
            # Another process holds the OS lock: a live backend.
            os.close(probe)
            raise SystemExit(
                "Another Klea backend already owns this storage root.\n"
                f"  lockfile: {lock_path}\n"
                f"  owner: {_owner_summary(_read_owner())}\n"
                "Stop that backend, or run this one with a different "
                "--data-dir / KLEA_AGENT_HOME."
            ) from None

        # The OS lock is free, so no process is using the lockfile.  Only
        # treat it as live if the recorded process is genuinely the same
        # process (PID *and* start time), which guards against PID reuse.
        owner = _read_owner()
        recorded_pid = owner.get("pid")
        # ``json.load`` yields ``Any``; only an ``int`` can be a PID, and 0
        # is never a live process, so a non-int record means "no owner".
        pid = recorded_pid if isinstance(recorded_pid, int) else 0
        if _pid_alive(pid) and owner.get("starttime") == _start_time(pid):
            os.close(probe)
            raise SystemExit(
                "Another Klea backend already owns this storage root "
                "(its lockfile is unlocked but its process is alive).\n"
                f"  lockfile: {lock_path}\n"
                f"  owner: {_owner_summary(owner)}\n"
                "Stop that backend, or run this one with a different "
                "--data-dir / KLEA_AGENT_HOME."
            )

        logger.warning(
            "Reclaiming stale backend lock %s (previous owner: %s)",
            lock_path,
            _owner_summary(owner),
        )
        if os.name == "nt":
            # Windows does not normally allow deleting a file while a handle
            # is open.  Drop the probe lock, then remove the stale marker; the
            # outer O_EXCL loop retries creation.
            os.close(probe)
            try:
                os.unlink(lock_path)
            except FileNotFoundError:
                pass
            except OSError as exc:
                logger.warning(
                    "Could not remove stale backend lock %s: %s", lock_path, exc
                )
            continue
        try:
            os.unlink(lock_path)
        except FileNotFoundError:
            pass
        os.close(probe)
    else:
        raise SystemExit(f"Could not acquire the backend lock {lock_path}")

    payload = {
        "pid": os.getpid(),
        "starttime": _start_time(os.getpid()),
        "root": str(root),
        "version": klea_version,
        "host": host,
        "port": port,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    try:
        os.write(fd, json.dumps(payload, indent=2).encode("utf-8") + b"\n")
        os.fsync(fd)
    except OSError as exc:
        raise SystemExit(
            f"Could not write the backend lockfile {lock_path}: {exc}"
        ) from exc

    logger.info(
        "Acquired backend lock %s for %s:%s (pid %s)",
        lock_path,
        host,
        port,
        payload["pid"],
    )
    return fd


def _release_backend_lock(fd, lock_path) -> None:
    """Release the backend lock and remove its lockfile.

    Closing *fd* drops the OS lock and unlinking the file removes the
    on-disk marker, so the next backend to start finds no owner at all.

    This is the *normal* shutdown path only (the lifespan shutdown plus the
    SIGINT/SIGTERM handlers).  Crash recovery does not depend on it: a
    backend that is SIGKILLed, segfaults or loses power never gets here,
    and the next start treats the leftover lockfile as *stale* -- the OS
    lock died with the process and either the recorded PID is dead or
    its start time no longer matches, so PID reuse cannot fool it -- and
    reclaims it (see :func:`_acquire_backend_lock`).

    :param fd: Open file descriptor from :func:`_acquire_backend_lock`
    :param lock_path: Path of the lockfile to unlink
    """
    from pathlib import Path

    if fd is not None:
        try:
            os.close(fd)
        except OSError as exc:
            logger.debug("Could not close the backend lock fd: %s", exc)
    try:
        Path(lock_path).unlink()
    except FileNotFoundError:
        return
    except OSError as exc:
        logger.warning("Could not remove the backend lockfile %s: %s", lock_path, exc)
        return
    logger.info("Released backend lock %s", lock_path)


def _install_lock_signal_handlers(release):
    """Install SIGINT/SIGTERM handlers that release the backend lock.

    The handlers chain to whatever was installed before them (uvicorn
    installs its own for graceful shutdown), so releasing the lock never
    swallows the shutdown signal.  Signal handlers may only be installed
    from the main thread; elsewhere this is skipped and the lifespan
    shutdown still releases the lock.

    :param release: Zero-argument callable that releases the lock
    :returns: Mapping of signal number to the handler it replaced
    """
    import signal
    import threading

    if threading.current_thread() is not threading.main_thread():
        logger.debug("Not in the main thread; skipping backend lock signal handlers")
        return {}

    installed: dict[int, object] = {}

    def _handler(signum, frame):
        # Restore the replaced handlers first so the release runs once and
        # a chained handler can safely shut the server down.
        _restore_lock_signal_handlers(installed)
        release()
        previous = installed.get(signum)
        if callable(previous):
            previous(signum, frame)
            return
        # Nothing to chain to: keep the default disposition of the signal.
        signal.signal(signum, signal.SIG_DFL)
        os.kill(os.getpid(), signum)

    for signum in (signal.SIGINT, signal.SIGTERM):
        try:
            previous = signal.getsignal(signum)
            signal.signal(signum, _handler)
        except (OSError, ValueError) as exc:  # pragma: no cover - defensive
            logger.debug("Could not install a handler for %s: %s", signum, exc)
            continue
        installed[signum] = previous

    logger.debug("Installed backend lock signal handlers: %s", sorted(installed))
    return installed


def _restore_lock_signal_handlers(installed) -> None:
    """Put back the signal handlers replaced by the lock handlers.

    :param installed: Mapping returned by
        :func:`_install_lock_signal_handlers`
    """
    import signal

    for signum, previous in (installed or {}).items():
        try:
            signal.signal(signum, previous if callable(previous) else signal.SIG_DFL)
        except (OSError, ValueError) as exc:  # pragma: no cover - defensive
            logger.debug("Could not restore the handler for %s: %s", signum, exc)
