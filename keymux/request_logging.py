"""Request logging with secrets kept out of the log.

One line per proxied request. Router keys, real upstream keys and full
``Authorization`` values are never logged, and no field of the log record can
carry them.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

LOGGER_NAME = "keymux"


@dataclass(frozen=True)
class RequestLogEntry:
    """The facts about one proxied request that are safe to log."""

    request_id: str
    route: str
    method: str
    path: str
    status: int
    streaming: bool
    egress: str
    latency_milliseconds: int
    error: str | None = None

    def render(self) -> str:
        line = (
            f"request_id={self.request_id} route={self.route} method={self.method} path={self.path} "
            f"status={self.status} streaming={'true' if self.streaming else 'false'} "
            f"egress={self.egress} latency={self.latency_milliseconds}ms"
        )
        if self.error:
            return f"{line} error={self.error}"
        return line


def configure_logging(level: str = "INFO") -> None:
    """Configure the KeyMux logger once, with a plain single-line format."""
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(level.upper())
    if logger.handlers:
        return
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    logger.propagate = False


def log_request(entry: RequestLogEntry) -> None:
    logging.getLogger(LOGGER_NAME).info(entry.render())


def log_event(message: str, *arguments: object) -> None:
    logging.getLogger(LOGGER_NAME).info(message, *arguments)
