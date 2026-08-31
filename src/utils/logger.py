"""
src/utils/logger.py
-------------------
Structured JSON logger.

Every log line is a JSON object with a consistent schema:
  {"ts": "...", "level": "INFO", "logger": "...", "msg": "...", ...extra}

This makes logs trivially parseable by any log aggregator (Datadog,
Splunk, CloudWatch Logs Insights) without additional parsing rules.

In "text" mode (local dev), output is human-readable instead.
"""

from __future__ import annotations

import json
import logging
import sys
import time
from typing import Any


class JsonFormatter(logging.Formatter):
    """Format log records as single-line JSON."""

    def format(self, record: logging.LogRecord) -> str:
        log: dict[str, Any] = {
            "ts":     self.formatTime(record, datefmt="%Y-%m-%dT%H:%M:%S"),
            "level":  record.levelname,
            "logger": record.name,
            "msg":    record.getMessage(),
        }
        # Attach any extra fields passed via logger.info("msg", extra={...})
        for key, val in record.__dict__.items():
            if key not in {
                "name", "msg", "args", "created", "filename", "funcName",
                "levelname", "levelno", "lineno", "message", "module",
                "msecs", "pathname", "process", "processName", "relativeCreated",
                "stack_info", "thread", "threadName", "exc_info", "exc_text",
            }:
                log[key] = val

        if record.exc_info:
            log["exc"] = self.formatException(record.exc_info)

        return json.dumps(log)


class TextFormatter(logging.Formatter):
    """Human-readable formatter for local development."""

    LEVEL_COLORS = {
        "DEBUG":    "\033[36m",   # cyan
        "INFO":     "\033[32m",   # green
        "WARNING":  "\033[33m",   # yellow
        "ERROR":    "\033[31m",   # red
        "CRITICAL": "\033[35m",   # magenta
    }
    RESET = "\033[0m"

    def format(self, record: logging.LogRecord) -> str:
        color = self.LEVEL_COLORS.get(record.levelname, "")
        reset = self.RESET
        ts    = self.formatTime(record, datefmt="%H:%M:%S")
        msg   = record.getMessage()
        base  = f"{color}{ts} [{record.levelname:<8}] {record.name}: {msg}{reset}"
        if record.exc_info:
            base += "\n" + self.formatException(record.exc_info)
        return base


def get_logger(name: str, level: str = "INFO", fmt: str = "json") -> logging.Logger:
    """
    Return a configured logger.

    Parameters
    ----------
    name  : module name, e.g. __name__
    level : "DEBUG" | "INFO" | "WARNING" | "ERROR"
    fmt   : "json" (production) | "text" (dev)
    """
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger   # already configured — avoid duplicate handlers

    logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter() if fmt == "json" else TextFormatter())
    logger.addHandler(handler)
    logger.propagate = False
    return logger
