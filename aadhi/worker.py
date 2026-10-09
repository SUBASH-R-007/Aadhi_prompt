"""Standalone job worker: ``python -m aadhi.worker [--concurrency N] [--kinds a,b] [--drain]``.

* SIGINT / SIGTERM (and SIGBREAK / Ctrl+Break on Windows) stop gracefully: no new claims, running
  jobs get ``--shutdown-timeout`` seconds, then are released to another worker. A second signal
  exits immediately (leases are recovered by the next start or by the reaper).
* ``--drain`` processes everything currently claimable and exits (cron / one-off maintenance).
"""

from __future__ import annotations

import argparse
import inspect
import logging
import os
import signal
import sys
import threading
from collections.abc import Callable, Sequence
from typing import Any

from .config import ALL_JOB_KINDS, Settings, get_settings
from .jobs.worker import Worker

log = logging.getLogger("aadhi.worker")


def build_parser() -> argparse.ArgumentParser:
    """Command-line interface of the worker."""
    p = argparse.ArgumentParser(prog="python -m aadhi.worker", description="Aadhi EduEngine job worker")
    p.add_argument("--concurrency", type=int, default=None, help="worker threads (default WORKER_CONCURRENCY)")
    p.add_argument(
        "--kinds",
        default=None,
        help=f"comma-separated job kinds (default WORKER_KINDS); any of {','.join(ALL_JOB_KINDS)}",
    )
    p.add_argument(
        "--shutdown-timeout",
        type=float,
        default=60.0,
        help="seconds running jobs may take to finish on shutdown (default 60)",
    )
    p.add_argument("--drain", action="store_true", help="process claimable jobs, then exit")
    p.add_argument("--drain-timeout", type=float, default=None, help="give up draining after N seconds")
    p.add_argument("--no-schedule-cleanup", action="store_true", help="do not enqueue the daily cleanup job")
    return p


def parse_kinds(value: str | None, settings: Settings) -> list[str]:
    """Validate ``--kinds`` (raises ValueError on unknown kinds)."""
    if not value:
        return list(settings.worker_kinds)
    kinds = [k.strip() for k in value.split(",") if k.strip()]
    unknown = [k for k in kinds if k not in ALL_JOB_KINDS]
    if unknown:
        raise ValueError(f"unknown job kinds: {', '.join(unknown)}")
    if not kinds:
        raise ValueError("--kinds is empty")
    return kinds


def configure_logging(settings: Settings) -> None:
    """Use ``aadhi.logging_setup`` when available, else a plain stderr configuration."""
    try:
        from . import logging_setup  # type: ignore[attr-defined,unused-ignore]
    except ImportError:
        logging_setup = None
    fn: Callable[..., Any] | None = None
    if logging_setup is not None:
        fn = getattr(logging_setup, "configure_logging", None) or getattr(logging_setup, "setup_logging", None)
    if fn is not None:
        try:
            if inspect.signature(fn).parameters:
                fn(settings)
            else:
                fn()
            return
        except Exception as exc:  # noqa: BLE001 - pragma: no cover - fall back to basic logging
            print(f"logging setup failed ({exc!r}); using basic logging", file=sys.stderr)
    logging.basicConfig(
        level=getattr(logging, str(settings.log_level).upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


class ShutdownSignals:
    """Installs SIGINT/SIGTERM/SIGBREAK handlers that set ``stop``; a second signal force-exits."""

    def __init__(self, stop: threading.Event, *, force_exit: Callable[[int], Any] = os._exit) -> None:
        self.stop = stop
        self.count = 0
        self._force_exit = force_exit
        self._previous: dict[int, Any] = {}

    def handler(self, signum: int, _frame: Any) -> None:
        self.count += 1
        if self.count == 1:
            log.info("signal %s received: shutting down gracefully (send again to force)", signum)
            self.stop.set()
        else:
            log.warning("second signal: exiting immediately")
            self._force_exit(130)

    def install(self) -> None:
        names = ["SIGINT", "SIGTERM", "SIGBREAK"]
        for name in names:
            sig = getattr(signal, name, None)
            if sig is None:
                continue
            try:
                self._previous[sig] = signal.signal(sig, self.handler)
            except (ValueError, OSError):  # not in the main thread / unsupported
                continue

    def restore(self) -> None:
        for sig, prev in self._previous.items():
            try:
                signal.signal(sig, prev)
            except (ValueError, OSError, TypeError):
                pass
        self._previous.clear()


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point; returns the process exit code."""
    args = build_parser().parse_args(argv)
    settings = get_settings()
    configure_logging(settings)
    try:
        settings.validate_for_runtime()
        kinds = parse_kinds(args.kinds, settings)
    except (RuntimeError, ValueError) as exc:
        log.error("%s", settings.redact(str(exc)))
        return 2
    concurrency = args.concurrency if args.concurrency is not None else settings.worker_concurrency
    if concurrency < 1:
        log.error("--concurrency must be >= 1")
        return 2
    worker = Worker(
        settings,
        kinds=kinds,
        concurrency=concurrency,
        name="worker",
        schedule_cleanup=not args.no_schedule_cleanup and not args.drain,
        host_housekeeping=not args.drain,
    )
    if args.drain:
        drained = worker.run_until_idle(timeout=args.drain_timeout)
        return 0 if drained else 1

    stop = threading.Event()
    signals = ShutdownSignals(stop)
    signals.install()
    try:
        worker.start()
        while not stop.wait(0.5):  # short waits keep signal delivery responsive on Windows
            pass
    finally:
        clean = worker.stop(timeout=args.shutdown_timeout)
        signals.restore()
    return 0 if clean else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
