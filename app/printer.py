"""TCP-Client für das FlashForge-Adventurer-3-Protokoll auf Port 8899.

Kommandos und Antwortformate folgen den Community-Quellen:
Parallel-7/flashforge-api-docs, Slugger2k/FlashForgePrinterApi,
andycb/AdventurerClientJS und modrzew/hass-flashforge-adventurer-3.
"""

from __future__ import annotations

import re
import socket
import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Any

from . import config

CMD_HELLO = "~M601 S1"
CMD_INFO = "~M115"
CMD_TEMP = "~M105"
CMD_STATUS = "~M119"
CMD_POS = "~M114"
CMD_PROGRESS = "~M27"
CMD_FILES = "~M661"
CMD_CAL = "~M650"

TEMP_RE = re.compile(
    r"T0:\s*(-?\d+(?:\.\d+)?)\s*/\s*(-?\d+(?:\.\d+)?)\s+B:\s*(-?\d+(?:\.\d+)?)\s*/\s*(-?\d+(?:\.\d+)?)",
    re.IGNORECASE,
)
POS_RE = re.compile(
    r"X:(-?\d+(?:\.\d+)?)\s+Y:(-?\d+(?:\.\d+)?)\s+Z:(-?\d+(?:\.\d+)?)",
    re.IGNORECASE,
)
PROGRESS_RE = re.compile(r"byte\s+(\d+)\s*/\s*(\d+)", re.IGNORECASE)
FILE_RE = re.compile(r"/data/([^:\x00-\x1f]+?\.(?:gx|gcode|g))\b", re.IGNORECASE)
FIELD_RE = re.compile(r"^(Machine Type|Machine Name|Firmware|SN|Tool Count|Mac Address|Endstop|MachineStatus|MoveMode|Status|LED|CurrentFile|PrintFileName)\s*:\s*(.*)$", re.IGNORECASE)
RAW_CMD_RE = re.compile(r"^~?[GM]\d{1,3}(?:\s+\S.*)?$", re.IGNORECASE)
FLASHPRINT_BUSY = "FlashPrint-Modus: Port 8899 ist freigegeben."

PRINTING_STATUSES = {
    "BUILDING",
    "BUILDING_FROM_SD",
    "BUILDING_COMPLETED",
    "PRINTING",
}
PAUSED_STATUSES = {"PAUSE", "PAUSED"}


@dataclass
class Snapshot:
    online: bool = False
    control: bool | None = None
    machine_type: str = ""
    machine_name: str = ""
    firmware: str = ""
    serial: str = ""
    mac: str = ""
    tool_count: str = ""
    machine_status: str = ""
    move_mode: str = ""
    endstop: str = ""
    led: str = ""
    current_file: str = ""
    nozzle: float | None = None
    nozzle_target: float | None = None
    bed: float | None = None
    bed_target: float | None = None
    x: float | None = None
    y: float | None = None
    z: float | None = None
    progress_current: int | None = None
    progress_total: int | None = None
    printing: bool = False
    paused: bool = False
    fan_hold_off: bool = False
    control_mode: str = "dashboard"
    error: str = ""
    updated_at: float = 0.0
    extra: dict[str, str] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        data = asdict(self)
        data["progress_pct"] = self.progress_pct()
        return data

    def progress_pct(self) -> int | None:
        if self.progress_current is None or not self.progress_total:
            return None
        return int(round(100.0 * self.progress_current / self.progress_total))


def _decode(raw: bytes) -> str:
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("latin-1", errors="replace")


def parse_info(text: str, snap: Snapshot) -> None:
    for line in text.splitlines():
        match = FIELD_RE.match(line.strip())
        if not match:
            continue
        key = match.group(1).lower()
        value = match.group(2).strip()
        if key == "machine type":
            snap.machine_type = value
        elif key == "machine name":
            snap.machine_name = value
        elif key == "firmware":
            snap.firmware = value
        elif key == "sn":
            snap.serial = value
        elif key == "tool count":
            snap.tool_count = value
        elif key == "mac address":
            snap.mac = value.replace(" ", "")


def parse_temps(text: str, snap: Snapshot) -> None:
    match = TEMP_RE.search(text.replace("\r", " "))
    if not match:
        return
    snap.nozzle = float(match.group(1))
    snap.nozzle_target = float(match.group(2))
    snap.bed = float(match.group(3))
    snap.bed_target = float(match.group(4))


def parse_status(text: str, snap: Snapshot) -> None:
    for line in text.splitlines():
        match = FIELD_RE.match(line.strip())
        if not match:
            continue
        key = match.group(1).lower()
        value = match.group(2).strip()
        if key == "endstop":
            snap.endstop = value
        elif key == "machinestatus":
            snap.machine_status = value
        elif key == "movemode":
            snap.move_mode = value
        elif key == "led":
            snap.led = value
        elif key in {"currentfile", "printfilename"}:
            snap.current_file = value
        elif key == "status":
            snap.extra["flags"] = value
    status = snap.machine_status.upper()
    snap.printing = any(token in status for token in PRINTING_STATUSES)
    snap.paused = any(token in status for token in PAUSED_STATUSES)


def parse_position(text: str, snap: Snapshot) -> None:
    match = POS_RE.search(text.replace("\r", " "))
    if not match:
        return
    snap.x = float(match.group(1))
    snap.y = float(match.group(2))
    snap.z = float(match.group(3))


def parse_progress(text: str, snap: Snapshot) -> None:
    match = PROGRESS_RE.search(text)
    if not match:
        return
    snap.progress_current = int(match.group(1))
    snap.progress_total = int(match.group(2))


def pulse_fan_hold_off(enabled: bool, send_off) -> bool:
    """Genau ein Aus-Puls, und nur wenn der Halt aktiv ist."""
    if not enabled:
        return False
    send_off()
    return True


def parse_files(text: str) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for match in FILE_RE.finditer(text):
        name = match.group(1).strip()
        if name and name not in seen:
            seen.add(name)
            names.append(name)
    return names


def validate_raw(command: str) -> str:
    cleaned = command.strip()
    if not RAW_CMD_RE.fullmatch(cleaned):
        raise ValueError("Nur G- und M-Kommandos sind erlaubt.")
    if len(cleaned) > 180:
        raise ValueError("Kommando ist zu lang.")
    if not cleaned.startswith("~"):
        cleaned = "~" + cleaned
    code = cleaned[1:].split()[0].upper()
    if code in {"M28", "M29"}:
        raise ValueError("Datei-Upload über M28/M29 ist hier nicht freigeschaltet.")
    return cleaned


class PrinterClient:
    def __init__(self, host: str, port: int = 8899) -> None:
        self.host = host
        self.port = port
        self._lock = threading.Lock()
        self._sock: socket.socket | None = None
        self._flashprint = threading.Event()
        self.snapshot = Snapshot()
        self.last_raw: dict[str, str] = {}

    def close(self) -> None:
        with self._lock:
            self._close_unlocked()

    def is_yielded(self) -> bool:
        return self._flashprint.is_set()

    def yield_to_flashprint(self) -> None:
        self._flashprint.set()
        sock = self._sock
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        with self._lock:
            self._close_unlocked()
            self._mark_flashprint_unlocked()

    def reclaim(self) -> None:
        self._flashprint.clear()
        with self._lock:
            self.snapshot.control_mode = config.CONTROL_DASHBOARD
            self.snapshot.error = ""
            self.snapshot.updated_at = time.time()

    def _mark_flashprint_unlocked(self) -> None:
        self.snapshot.online = False
        self.snapshot.control = False
        self.snapshot.control_mode = config.CONTROL_FLASHPRINT
        self.snapshot.error = ""
        self.snapshot.updated_at = time.time()

    def _close_unlocked(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None

    def _connect_unlocked(self) -> None:
        if self._flashprint.is_set():
            raise OSError(FLASHPRINT_BUSY)
        self._close_unlocked()
        sock = socket.create_connection((self.host, self.port), timeout=8)
        if self._flashprint.is_set():
            try:
                sock.close()
            except OSError:
                pass
            raise OSError(FLASHPRINT_BUSY)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self._sock = sock
        self.snapshot.online = True
        self.snapshot.error = ""

    def _send_unlocked(self, command: str, drain_after_ok: float = 0.05) -> str:
        if self._sock is None:
            raise OSError("Keine Verbindung")
        line = command if command.endswith("\n") else command + "\r\n"
        self._sock.sendall(line.encode("ascii", errors="ignore"))
        chunks: list[bytes] = []
        self._sock.settimeout(8)
        deadline = time.monotonic() + 8
        saw_ok = False
        while time.monotonic() < deadline:
            try:
                data = self._sock.recv(8192)
            except socket.timeout:
                break
            if not data:
                raise OSError("Drucker hat die Verbindung getrennt")
            chunks.append(data)
            blob = b"".join(chunks).lower()
            if b"\nok" in blob or blob.endswith(b"ok\r\n") or blob.endswith(b"ok\n"):
                saw_ok = True
                self._sock.settimeout(max(drain_after_ok, 0.02))
                continue
            if saw_ok:
                self._sock.settimeout(drain_after_ok)
        return _decode(b"".join(chunks))

    def command(self, command: str, drain_after_ok: float = 0.05) -> str:
        with self._lock:
            return self._command_unlocked(command, drain_after_ok)

    def _command_unlocked(self, command: str, drain_after_ok: float = 0.05) -> str:
        last_error: OSError | None = None
        for attempt in range(2):
            if self._flashprint.is_set():
                self._mark_flashprint_unlocked()
                raise OSError(FLASHPRINT_BUSY)
            try:
                if self._sock is None:
                    self._connect_unlocked()
                return self._send_unlocked(command, drain_after_ok)
            except OSError as exc:
                last_error = exc
                self._close_unlocked()
                self.snapshot.online = False
                if self._flashprint.is_set():
                    self._mark_flashprint_unlocked()
                    raise OSError(FLASHPRINT_BUSY) from exc
                if attempt == 0:
                    continue
        self.snapshot.error = str(last_error) if last_error else "Unbekannter Fehler"
        raise OSError(self.snapshot.error)

    def _ensure_control_unlocked(self) -> None:
        if config.PRINTER_READONLY:
            raise ValueError("Schreibschutz: Steuerung ist abgeschaltet.")
        hello = self._send_unlocked(CMD_HELLO, drain_after_ok=0.05)
        self.last_raw["M601"] = hello
        failed = "control failed" in hello.lower() or "error:" in hello.lower()
        self.snapshot.control = not failed

    def control_command(self, command: str, drain_after_ok: float = 0.05) -> str:
        if config.PRINTER_READONLY:
            raise ValueError("Schreibschutz: Steuerung ist abgeschaltet.")
        with self._lock:
            last_error: OSError | None = None
            for attempt in range(2):
                if self._flashprint.is_set():
                    self._mark_flashprint_unlocked()
                    raise OSError(FLASHPRINT_BUSY)
                try:
                    if self._sock is None:
                        self._connect_unlocked()
                    self._ensure_control_unlocked()
                    return self._send_unlocked(command, drain_after_ok)
                except OSError as exc:
                    last_error = exc
                    self._close_unlocked()
                    self.snapshot.online = False
                    if self._flashprint.is_set():
                        self._mark_flashprint_unlocked()
                        raise OSError(FLASHPRINT_BUSY) from exc
                    if attempt == 0:
                        continue
            self.snapshot.error = str(last_error) if last_error else "Unbekannter Fehler"
            raise OSError(self.snapshot.error)

    def poll(self, full: bool = False) -> Snapshot:
        if self._flashprint.is_set():
            with self._lock:
                self._close_unlocked()
                self._mark_flashprint_unlocked()
            return self.snapshot
        try:
            if full or not self.snapshot.machine_type:
                info = self.command(CMD_INFO)
                self.last_raw["M115"] = info
                parse_info(info, self.snapshot)
            temps = self.command(CMD_TEMP)
            status = self.command(CMD_STATUS)
            pos = self.command(CMD_POS)
            progress = self.command(CMD_PROGRESS)
            self.last_raw["M105"] = temps
            self.last_raw["M119"] = status
            self.last_raw["M114"] = pos
            self.last_raw["M27"] = progress
            parse_temps(temps, self.snapshot)
            parse_status(status, self.snapshot)
            parse_position(pos, self.snapshot)
            parse_progress(progress, self.snapshot)
            self.snapshot.online = True
            self.snapshot.error = ""
        except OSError as exc:
            self.snapshot.online = False
            if self._flashprint.is_set():
                self.snapshot.control_mode = config.CONTROL_FLASHPRINT
                self.snapshot.error = ""
            else:
                self.snapshot.error = str(exc)
        self.snapshot.updated_at = time.time()
        return self.snapshot

    def list_files(self) -> list[str]:
        if self._flashprint.is_set():
            raise OSError(FLASHPRINT_BUSY)
        raw = self.command(CMD_FILES, drain_after_ok=1.2)
        self.last_raw["M661"] = raw
        return parse_files(raw)

    def pause(self) -> str:
        return self.control_command("~M25")

    def resume(self) -> str:
        return self.control_command("~M24")

    def stop(self) -> str:
        return self.control_command("~M26")

    def home(self) -> str:
        return self.control_command("~G28")

    def set_temps(self, nozzle: float | None = None, bed: float | None = None) -> str:
        replies: list[str] = []
        if nozzle is not None:
            replies.append(self.control_command(f"~M104 S{int(nozzle)}"))
        if bed is not None:
            replies.append(self.control_command(f"~M140 S{int(bed)}"))
        return "\n".join(replies)

    def move_relative(self, axis: str, mm: float, feed: int | None = None) -> str:
        axis = axis.upper()
        if axis not in {"X", "Y", "Z"}:
            raise ValueError("Achse muss X, Y oder Z sein.")
        if abs(mm) > 150:
            raise ValueError("Verfahrweg ist zu groß.")
        if feed is None:
            feed = 300 if axis == "Z" else 3000
        parts = [
            self.control_command("~G91"),
            self.control_command(f"~G1 {axis}{mm:g} F{int(feed)}"),
            self.control_command("~G90"),
        ]
        return "\n".join(parts)

    def set_led(self, on: bool) -> str:
        if on:
            return self.control_command("~M146 r255 g255 b255 F0")
        return self.control_command("~M146 r0 g0 b0 F0")

    def set_fan(self, on: bool) -> str:
        if on:
            self.snapshot.fan_hold_off = False
        return self.control_command("~M106" if on else "~M107")

    def set_fan_hold_off(self, on: bool) -> str:
        if not on:
            self.snapshot.fan_hold_off = False
            return "Lüfter-Dauer-Aus inaktiv"
        reply = self.set_fan(False)
        self.snapshot.fan_hold_off = True
        return reply

    def set_motors(self, on: bool) -> str:
        return self.control_command("~M17" if on else "~M18")

    def start_print(self, filename: str) -> str:
        name = sanitize_print_name(filename)
        select = self.control_command(f"~M23 0:/user/{name}")
        start = self.control_command("~M24")
        return select + "\n" + start

    def raw(self, command: str) -> str:
        cmd = validate_raw(command)
        code = cmd[1:].split()[0].upper()
        if code in READ_ONLY_CODES:
            return self.command(cmd)
        return self.control_command(cmd)


READ_ONLY_CODES = {"M105", "M115", "M119", "M114", "M27", "M661", "M650"}


def sanitize_print_name(filename: str) -> str:
    name = filename.strip().lstrip("/")
    if name.lower().startswith("data/"):
        name = name[5:]
    if "/" in name or "\\" in name or ".." in name:
        raise ValueError("Ungültiger Dateiname.")
    if not FILE_RE.search("/data/" + name):
        raise ValueError("Nur .g, .gx oder .gcode sind startbar.")
    return name


class MockPrinterClient:
    """Lokaler Stub, spricht den physischen Drucker nicht an."""

    def __init__(self) -> None:
        self.host = "mock"
        self.port = 0
        self._flashprint = threading.Event()
        self.last_raw: dict[str, str] = {}
        self.snapshot = Snapshot(
            online=True,
            control=True,
            machine_type="FlashForge Adventurer III",
            machine_name="Bresser REX (Mock)",
            firmware="v1.3.7",
            serial="MOCK",
            mac="00:00:00:00:00:00",
            machine_status="READY",
            move_mode="READY",
            led="0",
            current_file="",
            nozzle=25,
            nozzle_target=0,
            bed=24,
            bed_target=0,
            x=0,
            y=0,
            z=0,
            progress_current=0,
            progress_total=100,
        )

    def close(self) -> None:
        return

    def is_yielded(self) -> bool:
        return self._flashprint.is_set()

    def yield_to_flashprint(self) -> None:
        self._flashprint.set()
        self.snapshot.online = False
        self.snapshot.control = False
        self.snapshot.control_mode = config.CONTROL_FLASHPRINT
        self.snapshot.error = ""
        self.snapshot.updated_at = time.time()

    def reclaim(self) -> None:
        self._flashprint.clear()
        self.snapshot.online = True
        self.snapshot.control = True
        self.snapshot.control_mode = config.CONTROL_DASHBOARD
        self.snapshot.error = ""
        self.snapshot.updated_at = time.time()

    def poll(self, full: bool = False) -> Snapshot:
        if self._flashprint.is_set():
            self.snapshot.online = False
            self.snapshot.control_mode = config.CONTROL_FLASHPRINT
        self.snapshot.updated_at = time.time()
        return self.snapshot

    def list_files(self) -> list[str]:
        if self._flashprint.is_set():
            raise OSError(FLASHPRINT_BUSY)
        return ["demo-cube.gx", "NvidiaV100Fan_eABS+HS@245+10%.gx"]

    def _reply(self, code: str) -> str:
        if self._flashprint.is_set():
            raise OSError(FLASHPRINT_BUSY)
        if config.PRINTER_READONLY:
            raise ValueError("Schreibschutz: Steuerung ist abgeschaltet.")
        return f"CMD {code} Received.\nok"

    def pause(self) -> str:
        self.snapshot.paused = True
        self.snapshot.printing = False
        self.snapshot.machine_status = "PAUSED"
        return self._reply("M25")

    def resume(self) -> str:
        self.snapshot.paused = False
        self.snapshot.printing = True
        self.snapshot.machine_status = "BUILDING_FROM_SD"
        return self._reply("M24")

    def stop(self) -> str:
        self.snapshot.paused = False
        self.snapshot.printing = False
        self.snapshot.machine_status = "READY"
        self.snapshot.current_file = ""
        return self._reply("M26")

    def home(self) -> str:
        self.snapshot.x = 0
        self.snapshot.y = 0
        self.snapshot.z = 0
        return self._reply("G28")

    def set_temps(self, nozzle: float | None = None, bed: float | None = None) -> str:
        if nozzle is not None:
            self.snapshot.nozzle_target = float(nozzle)
        if bed is not None:
            self.snapshot.bed_target = float(bed)
        return self._reply("M104")

    def move_relative(self, axis: str, mm: float, feed: int | None = None) -> str:
        axis = axis.upper()
        if axis not in {"X", "Y", "Z"}:
            raise ValueError("Achse muss X, Y oder Z sein.")
        current = getattr(self.snapshot, axis.lower()) or 0
        setattr(self.snapshot, axis.lower(), current + float(mm))
        return self._reply("G1")

    def set_led(self, on: bool) -> str:
        self.snapshot.led = "1" if on else "0"
        return self._reply("M146")

    def set_fan(self, on: bool) -> str:
        if on:
            self.snapshot.fan_hold_off = False
        return self._reply("M106" if on else "M107")

    def set_fan_hold_off(self, on: bool) -> str:
        if not on:
            self.snapshot.fan_hold_off = False
            return "Lüfter-Dauer-Aus inaktiv"
        reply = self.set_fan(False)
        self.snapshot.fan_hold_off = True
        return reply

    def set_motors(self, on: bool) -> str:
        return self._reply("M17" if on else "M18")

    def start_print(self, filename: str) -> str:
        name = sanitize_print_name(filename)
        self.snapshot.current_file = name
        self.snapshot.printing = True
        self.snapshot.machine_status = "BUILDING_FROM_SD"
        return self._reply("M23")

    def raw(self, command: str) -> str:
        if self._flashprint.is_set():
            raise OSError(FLASHPRINT_BUSY)
        cmd = validate_raw(command)
        return f"CMD {cmd[1:].split()[0]} Received.\nok"


def build_client(host: str, port: int):
    if config.PRINTER_MOCK:
        return MockPrinterClient()
    return PrinterClient(host, port)
