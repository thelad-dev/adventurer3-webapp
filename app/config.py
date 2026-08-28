"""Laufzeitkonfiguration aus der Umgebung."""

from __future__ import annotations

import os


def _env(name: str, default: str) -> str:
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return default
    return value.strip()


def _flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


PRINTER_HOST = _env("PRINTER_HOST", "192.168.20.191")
PRINTER_PORT = int(_env("PRINTER_PORT", "8899"))
CAMERA_PORT = int(_env("CAMERA_PORT", "8080"))
BIND = _env("BIND", "127.0.0.1")
PORT = int(_env("PORT", "8765"))
POLL_INTERVAL = float(_env("POLL_INTERVAL", "1"))
PRINTER_MOCK = _flag("PRINTER_MOCK")
PRINTER_READONLY = _flag("PRINTER_READONLY")
