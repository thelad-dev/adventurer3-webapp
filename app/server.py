"""Lokaler HTTP-Dienst: Live-Status, Kamera-Proxy und Steuer-API."""

from __future__ import annotations

import argparse
import json
import socket
import sys
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from . import config
from .printer import (
    MockPrinterClient,
    PrinterClient,
    Snapshot,
    build_client,
    pulse_fan_hold_off,
    validate_raw,
)

STATIC_DIR = Path(__file__).resolve().parent / "static"
MIME = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
}

printer: PrinterClient | MockPrinterClient | None = None
stop_event = threading.Event()
camera_ok = False


def snapshot() -> Snapshot:
    assert printer is not None
    return printer.snapshot


def json_bytes(payload: Any, status: int = 200) -> tuple[int, bytes, str]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return status, body, "application/json; charset=utf-8"


def camera_probe(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=2) as sock:
            sock.settimeout(2)
            sock.sendall(
                b"GET /?action=stream HTTP/1.0\r\n"
                + f"Host: {host}:{port}\r\n".encode()
                + b"Connection: close\r\n\r\n"
            )
            data = sock.recv(64)
            return bool(data)
    except OSError:
        return False


class QuietHTTPServer(ThreadingHTTPServer):
    def handle_error(self, request, client_address) -> None:
        err = sys.exc_info()[1]
        if isinstance(err, (ConnectionResetError, BrokenPipeError, TimeoutError)):
            return
        super().handle_error(request, client_address)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    timeout = 30

    def handle(self) -> None:
        try:
            super().handle()
        except (ConnectionResetError, BrokenPipeError, TimeoutError):
            return

    def log_message(self, fmt: str, *args: Any) -> None:
        if args and str(args[0]).startswith(("GET /api/events", "GET /api/camera")):
            return
        super().log_message(fmt, *args)

    def _send(self, status: int, body: bytes, content_type: str, extra: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if extra:
            for key, value in extra.items():
                self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, payload: Any, status: int = 200) -> None:
        code, body, ctype = json_bytes(payload, status)
        self._send(code, body, ctype)

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length > 1_000_000:
            raise ValueError("Anfrage ist zu groß.")
        raw = self.rfile.read(length) if length else b"{}"
        if not raw:
            return {}
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("JSON-Objekt erwartet.")
        return data

    def do_HEAD(self) -> None:  # noqa: N802
        self.do_GET()

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path in {"/", "/index.html"}:
            self._static("index.html")
            return
        if path.startswith("/static/"):
            self._static(path.removeprefix("/static/"))
            return
        if path == "/api/health":
            self._json(
                {
                    "ok": True,
                    "printer": config.PRINTER_HOST,
                    "camera": camera_ok,
                    "mock": config.PRINTER_MOCK,
                    "readonly": config.PRINTER_READONLY,
                }
            )
            return
        if path == "/api/status":
            self._json(snapshot().to_json())
            return
        if path == "/api/files":
            try:
                assert printer is not None
                self._json({"files": printer.list_files()})
            except (OSError, ValueError) as exc:
                self._json({"error": str(exc)}, 502)
            return
        if path == "/api/events":
            self._sse()
            return
        if path == "/api/camera":
            self._camera()
            return
        self._send(404, b"Not found\n", "text/plain; charset=utf-8")

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        assert printer is not None
        try:
            body = self._read_json() if self.headers.get("Content-Length") else {}
        except (ValueError, json.JSONDecodeError) as exc:
            self._json({"error": str(exc)}, 400)
            return
        try:
            reply = self._dispatch_post(path, body)
        except ValueError as exc:
            self._json({"error": str(exc)}, 400)
            return
        except OSError as exc:
            self._json({"error": str(exc)}, 502)
            return
        if reply is None:
            self._json({"error": "Unbekannte Aktion"}, 404)
            return
        self._json({"ok": True, "reply": reply, "status": snapshot().to_json()})

    def _dispatch_post(self, path: str, body: dict[str, Any]) -> str | None:
        assert printer is not None
        if path == "/api/pause":
            return printer.pause()
        if path == "/api/resume":
            return printer.resume()
        if path == "/api/stop":
            return printer.stop()
        if path == "/api/home":
            return printer.home()
        if path == "/api/led":
            return printer.set_led(bool(body.get("on")))
        if path == "/api/fan":
            return printer.set_fan(bool(body.get("on")))
        if path == "/api/fan-hold":
            return printer.set_fan_hold_off(bool(body.get("on")))
        if path == "/api/motors":
            return printer.set_motors(bool(body.get("on")))
        if path == "/api/temps":
            nozzle = body.get("nozzle")
            bed = body.get("bed")
            return printer.set_temps(
                float(nozzle) if nozzle is not None and nozzle != "" else None,
                float(bed) if bed is not None and bed != "" else None,
            )
        if path == "/api/move":
            return printer.move_relative(str(body.get("axis", "")), float(body.get("mm", 0)))
        if path == "/api/print":
            return printer.start_print(str(body.get("filename", "")))
        if path == "/api/command":
            return printer.raw(validate_raw(str(body.get("command", ""))))
        return None

    def _static(self, name: str) -> None:
        candidate = (STATIC_DIR / name).resolve()
        if not str(candidate).startswith(str(STATIC_DIR.resolve())) or not candidate.is_file():
            self._send(404, b"Not found\n", "text/plain; charset=utf-8")
            return
        data = candidate.read_bytes()
        ctype = MIME.get(candidate.suffix, "application/octet-stream")
        extra = {}
        if candidate.suffix == ".html":
            extra["Content-Security-Policy"] = "default-src 'self'; img-src 'self' blob:; connect-src 'self'"
        self._send(200, data, ctype, extra)

    def _sse(self) -> None:
        self.timeout = None
        try:
            self.connection.settimeout(None)
        except OSError:
            pass
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        last = None
        try:
            while not stop_event.is_set():
                current = json.dumps(snapshot().to_json(), ensure_ascii=False)
                if current != last:
                    self.wfile.write(f"data: {current}\n\n".encode("utf-8"))
                    self.wfile.flush()
                    last = current
                time.sleep(0.4)
        except (BrokenPipeError, ConnectionResetError, TimeoutError, OSError):
            return

    def _camera(self) -> None:
        host = config.PRINTER_HOST
        port = config.CAMERA_PORT
        upstream = None
        headers_sent = False
        self.timeout = None
        try:
            self.connection.settimeout(None)
        except OSError:
            pass
        try:
            upstream = socket.create_connection((host, port), timeout=4)
            upstream.settimeout(8)
            request = (
                b"GET /?action=stream HTTP/1.0\r\n"
                + f"Host: {host}:{port}\r\n".encode()
                + b"Accept: multipart/x-mixed-replace,image/jpeg,*/*\r\n"
                + b"Connection: close\r\n\r\n"
            )
            upstream.sendall(request)
            first = upstream.recv(4096)
            if not first:
                raise OSError("Kamera liefert keine Daten")
            self.send_response(HTTPStatus.OK)
            if first.startswith(b"HTTP/"):
                header, _, rest = first.partition(b"\r\n\r\n")
                content_type = b"multipart/x-mixed-replace; boundary=--jpgboundary"
                for line in header.split(b"\r\n")[1:]:
                    if line.lower().startswith(b"content-type:"):
                        content_type = line.split(b":", 1)[1].strip()
                self.send_header("Content-Type", content_type.decode("latin-1", errors="replace"))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                headers_sent = True
                if rest:
                    self.wfile.write(rest)
            else:
                self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                headers_sent = True
                self.wfile.write(
                    b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + first + b"\r\n"
                )
            self.wfile.flush()
            upstream.settimeout(30)
            while not stop_event.is_set():
                chunk = upstream.recv(16384)
                if not chunk:
                    break
                self.wfile.write(chunk)
                self.wfile.flush()
        except (OSError, TimeoutError, BrokenPipeError, ConnectionResetError):
            if not headers_sent:
                try:
                    self._send(502, b"camera unavailable\n", "text/plain; charset=utf-8")
                except OSError:
                    pass
        finally:
            if upstream is not None:
                try:
                    upstream.close()
                except OSError:
                    pass


def poll_loop() -> None:
    assert printer is not None
    cycle = 0
    while not stop_event.is_set():
        printer.poll(full=cycle % 20 == 0)
        cycle += 1
        stop_event.wait(config.POLL_INTERVAL)


def fan_hold_loop() -> None:
    assert printer is not None
    while not stop_event.is_set():
        started = time.monotonic()
        holding = bool(printer.snapshot.fan_hold_off)
        if holding:
            try:
                pulse_fan_hold_off(True, lambda: printer.set_fan(False))
            except (OSError, ValueError):
                pass
        remaining = (config.FAN_HOLD_INTERVAL if holding else 0.2) - (time.monotonic() - started)
        stop_event.wait(max(0.05, remaining))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Adventurer-3-Webapp")
    parser.add_argument("--bind", default=config.BIND, help="Bind-Adresse, Standard 127.0.0.1")
    parser.add_argument("--port", type=int, default=config.PORT)
    parser.add_argument("--printer", default=config.PRINTER_HOST)
    parser.add_argument("--printer-port", type=int, default=config.PRINTER_PORT)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    global printer, camera_ok
    args = parse_args(argv)
    config.BIND = args.bind
    config.PORT = args.port
    config.PRINTER_HOST = args.printer
    config.PRINTER_PORT = args.printer_port
    printer = build_client(args.printer, args.printer_port)
    camera_ok = False if config.PRINTER_MOCK else camera_probe(args.printer, config.CAMERA_PORT)
    worker = threading.Thread(target=poll_loop, name="printer-poll", daemon=True)
    worker.start()
    holder = threading.Thread(target=fan_hold_loop, name="fan-hold", daemon=True)
    holder.start()
    httpd = QuietHTTPServer((args.bind, args.port), Handler)
    mode = "Mock" if config.PRINTER_MOCK else f"Drucker {args.printer}:{args.printer_port}"
    print(f"Adventurer-3-Webapp auf http://{args.bind}:{args.port} ({mode})", flush=True)
    try:
        httpd.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        print("Beende …", flush=True)
    finally:
        stop_event.set()
        httpd.server_close()
        printer.close()
