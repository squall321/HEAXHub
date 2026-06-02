"""Tee a long-running process's stdout/stderr to a file + Redis pubsub channel.

Used by ``service_manager`` so the browser can subscribe to
``logs:service:{instance_id}`` and see realtime output, while a copy is also
persisted to disk for after-the-fact inspection.
"""
from __future__ import annotations

import subprocess
import threading
from pathlib import Path

import redis

from app.config import get_settings
from app.core.logger import get_logger

logger = get_logger(__name__)


def _redis() -> redis.Redis:
    return redis.Redis.from_url(get_settings().redis_url)


def tee_process(
    proc: subprocess.Popen[bytes],
    *,
    instance_id: str,
    log_file: Path,
) -> threading.Thread:
    """Spawn a background thread that copies ``proc.stdout`` lines to log_file
    and publishes each line on the Redis channel ``logs:service:{instance_id}``.

    Returns the started thread (daemon=True) so the caller can ``join()`` if
    they want — usually they don't, since service instances run forever.
    """
    log_file.parent.mkdir(parents=True, exist_ok=True)
    channel = f"logs:service:{instance_id}"

    def _pump() -> None:
        rclient = _redis()
        try:
            with log_file.open("ab") as fp:
                assert proc.stdout is not None
                for raw in iter(proc.stdout.readline, b""):
                    fp.write(raw)
                    fp.flush()
                    try:
                        line = raw.decode("utf-8", errors="replace").rstrip("\n")
                    except Exception:
                        line = "<undecodable line>"
                    try:
                        rclient.publish(channel, line)
                    except Exception:
                        logger.exception("redis publish failed for %s", channel)
        except Exception:
            logger.exception("instance log pump crashed for instance=%s", instance_id)

    t = threading.Thread(target=_pump, daemon=True, name=f"svc-log-{instance_id}")
    t.start()
    return t
