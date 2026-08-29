"""MQTT-Status und Home-Assistant-Discovery für den Adventurer 3."""

from __future__ import annotations

import json
import threading
from typing import Any

from . import config

DISCOVERY_PREFIX = "homeassistant"
NODE = "adventurer3"
STATE_TOPIC = f"{NODE}/state"
AVAIL_TOPIC = f"{NODE}/status"
DEVICE = {
    "identifiers": [NODE],
    "name": "Adventurer 3",
    "manufacturer": "Flashforge",
    "model": "Adventurer 3",
}


def discovery_payloads() -> list[tuple[str, dict[str, Any]]]:
    sensors = [
        ("nozzle", "Düse", "temperature", "°C", "{{ value_json.nozzle }}"),
        ("nozzle_target", "Düse Soll", "temperature", "°C", "{{ value_json.nozzle_target }}"),
        ("bed", "Bett", "temperature", "°C", "{{ value_json.bed }}"),
        ("bed_target", "Bett Soll", "temperature", "°C", "{{ value_json.bed_target }}"),
        ("x", "X", None, "mm", "{{ value_json.x }}"),
        ("y", "Y", None, "mm", "{{ value_json.y }}"),
        ("z", "Z", None, "mm", "{{ value_json.z }}"),
        ("progress", "Fortschritt", None, "%", "{{ value_json.progress_pct }}"),
        ("status", "Status", None, None, "{{ value_json.machine_status }}"),
        ("file", "Datei", None, None, "{{ value_json.current_file }}"),
        ("firmware", "Firmware", None, None, "{{ value_json.firmware }}"),
        ("move_mode", "Bewegungsmodus", None, None, "{{ value_json.move_mode }}"),
        ("control_mode", "Steuerung", None, None, "{{ value_json.control_mode }}"),
    ]
    binaries = [
        ("online", "Online", "connectivity", "{{ value_json.online }}"),
        ("printing", "Druckt", "running", "{{ value_json.printing }}"),
        ("paused", "Pausiert", "pause", "{{ value_json.paused }}"),
        ("led", "LED", "light", "{{ value_json.led_on }}"),
        ("fan_hold", "Lüfter gehalten", None, "{{ value_json.fan_hold_off }}"),
        ("camera", "Kamera Drucker", "connectivity", "{{ value_json.camera_ok }}"),
    ]
    out: list[tuple[str, dict[str, Any]]] = []
    for uid, name, device_class, unit, tmpl in sensors:
        payload: dict[str, Any] = {
            "name": name,
            "unique_id": f"{NODE}_{uid}",
            "state_topic": STATE_TOPIC,
            "value_template": tmpl,
            "availability_topic": AVAIL_TOPIC,
            "payload_available": "online",
            "payload_not_available": "offline",
            "device": DEVICE,
            "object_id": f"{NODE}_{uid}",
        }
        if device_class:
            payload["device_class"] = device_class
        if unit:
            payload["unit_of_measurement"] = unit
            payload["state_class"] = "measurement"
        out.append((f"{DISCOVERY_PREFIX}/sensor/{NODE}/{uid}/config", payload))
    for uid, name, device_class, tmpl in binaries:
        payload = {
            "name": name,
            "unique_id": f"{NODE}_{uid}",
            "state_topic": STATE_TOPIC,
            "value_template": tmpl,
            "payload_on": True,
            "payload_off": False,
            "availability_topic": AVAIL_TOPIC,
            "payload_available": "online",
            "payload_not_available": "offline",
            "device": DEVICE,
            "object_id": f"{NODE}_{uid}",
        }
        if device_class:
            payload["device_class"] = device_class
        out.append((f"{DISCOVERY_PREFIX}/binary_sensor/{NODE}/{uid}/config", payload))
    return out


def state_payload(snap: dict[str, Any], *, camera_ok: bool, control_mode: str) -> dict[str, Any]:
    led = str(snap.get("led") or "").lower()
    return {
        "online": bool(snap.get("online")),
        "control": snap.get("control"),
        "control_mode": control_mode,
        "machine_type": snap.get("machine_type") or "",
        "machine_name": snap.get("machine_name") or "",
        "firmware": snap.get("firmware") or "",
        "serial": snap.get("serial") or "",
        "machine_status": snap.get("machine_status") or "",
        "move_mode": snap.get("move_mode") or "",
        "led": snap.get("led") or "",
        "led_on": led in {"1", "on", "true", "open"},
        "current_file": snap.get("current_file") or "",
        "nozzle": snap.get("nozzle"),
        "nozzle_target": snap.get("nozzle_target"),
        "bed": snap.get("bed"),
        "bed_target": snap.get("bed_target"),
        "x": snap.get("x"),
        "y": snap.get("y"),
        "z": snap.get("z"),
        "progress_pct": snap.get("progress_pct"),
        "printing": bool(snap.get("printing")),
        "paused": bool(snap.get("paused")),
        "fan_hold_off": bool(snap.get("fan_hold_off")),
        "camera_ok": bool(camera_ok),
        "error": snap.get("error") or "",
        "updated_at": snap.get("updated_at") or 0,
    }


class MqttPublisher:
    def __init__(self) -> None:
        self._client = None
        self._lock = threading.Lock()
        self._ready = False

    def enabled(self) -> bool:
        return bool(config.MQTT_HOST)

    def start(self) -> None:
        if not self.enabled():
            print("MQTT aus (MQTT_HOST leer)", flush=True)
            return
        import paho.mqtt.client as mqtt

        client = mqtt.Client(client_id=f"{NODE}-webapp", protocol=mqtt.MQTTv311)
        if config.MQTT_USERNAME:
            client.username_pw_set(config.MQTT_USERNAME, config.MQTT_PASSWORD or None)
        client.will_set(AVAIL_TOPIC, "offline", qos=1, retain=True)

        def on_connect(_c, _u, _f, rc, *_a):
            if rc != 0:
                print(f"MQTT Connect fehlgeschlagen rc={rc}", flush=True)
                return
            for topic, payload in discovery_payloads():
                client.publish(topic, json.dumps(payload, ensure_ascii=False), qos=1, retain=True)
            client.publish(AVAIL_TOPIC, "online", qos=1, retain=True)
            with self._lock:
                self._ready = True
            print(f"MQTT verbunden {config.MQTT_HOST}:{config.MQTT_PORT}", flush=True)

        client.on_connect = on_connect
        client.connect_async(config.MQTT_HOST, config.MQTT_PORT, keepalive=30)
        client.loop_start()
        self._client = client

    def publish_state(self, snap: dict[str, Any], *, camera_ok: bool, control_mode: str) -> None:
        client = self._client
        if client is None:
            return
        with self._lock:
            if not self._ready:
                return
        body = json.dumps(
            state_payload(snap, camera_ok=camera_ok, control_mode=control_mode),
            ensure_ascii=False,
        )
        client.publish(STATE_TOPIC, body, qos=0, retain=True)

    def stop(self) -> None:
        client = self._client
        if client is None:
            return
        try:
            client.publish(AVAIL_TOPIC, "offline", qos=1, retain=True)
            client.loop_stop()
            client.disconnect()
        except Exception:
            pass
        self._client = None
