# Adventurer-3-Webapp

Live-Steuerung für den FlashForge Adventurer 3 (hier: Bresser REX) über das TCP-Protokoll auf Port 8899. Eine Seite, Live-Status, Temperatur, Verfahren, Pause/Stopp, Dateiliste und Kameraproxy, wenn Port 8080 ein MJPEG-Bild liefert.

Lokal, LAN-only. Kein Firmware-Flash.

## Start lokal

Standardbindung ist nur localhost:

```bash
python -m app
```

Dann im Browser: <http://127.0.0.1:8765>

Drucker-IP (Standard `192.168.20.191`):

```bash
PRINTER_HOST=192.168.20.191 python -m app
```

Für Zugriff aus dem LAN (nicht der Standard):

```bash
BIND=0.0.0.0 PORT=8765 python -m app
```

Umgebung:

| Variable | Standard | Bedeutung |
| --- | --- | --- |
| `PRINTER_HOST` | `192.168.20.191` | Druckeradresse |
| `PRINTER_PORT` | `8899` | FlashForge-TCP |
| `CAMERA_PORT` | `8080` | MJPEG, oft `/?action=stream` |
| `BIND` | `127.0.0.1` | HTTP-Bind. Für LAN `0.0.0.0` |
| `PORT` | `8765` | HTTP-Port |
| `POLL_INTERVAL` | `1` | Statusabfrage in Sekunden |
| `FAN_HOLD_INTERVAL` | `0.7` | Abstand der M107-Pulse bei „Lüfter ausgeschaltet lassen“ |
| `PRINTER_MOCK` | aus | UI und API ohne physischen Drucker |
| `MQTT_HOST` | leer | Broker für Home Assistant. Leer = MQTT aus |
| `MQTT_PORT` | `1883` | MQTT-Port |
| `MQTT_USERNAME` | leer | Broker-Benutzer |
| `MQTT_PASSWORD` | leer | Broker-Passwort, nicht ins Git |

## Home Assistant

Bei gesetztem `MQTT_HOST` veröffentlicht die App Discovery unter `homeassistant/+/adventurer3/#` und den Zustand unter `adventurer3/state` (JSON). Verfügbarkeit: `adventurer3/status` (`online`/`offline`). MQTT muss im Broker mit Benutzer/Passwort stehen; die App erscheint danach als Gerät „Adventurer 3“.
| `PRINTER_READONLY` | aus | Nur Status, keine Steuerkommandos |

Statusabfragen nutzen `~M115` / `~M105` / `~M119` / `~M114` / `~M27`. Sitzungsübernahme `~M601 S1` nur bei Steueraktionen, nicht beim Polling.

## Docker

```bash
docker compose up --build -d
```

Die Compose-Datei bindet `0.0.0.0:8765` im Container und veröffentlicht Port 8765 auf dem Host.

Auf Host `dock` (192.168.88.3) liegt der Stack unter `~/adventurer3-webapp` und ist erreichbar unter <http://192.168.88.3:8765>.

## Protokoll

- TCP `~M601 S1` Sitzung, danach `~M115` / `~M105` / `~M119` / `~M114` / `~M27`
- Dateiliste `~M661` (Nutzdaten kommen nach `ok`)
- Druckstart `~M23` plus `~M24`, Pause `~M25`, Weiter `~M24`, Stopp `~M26`
- Lüfter aus `~M107`; Option „Lüfter ausgeschaltet lassen“ wiederholt das alle 0,7 s (Default aus)
- Kamera oft `http://<drucker>:8080/?action=stream`

Quellen, nicht neu erfunden: [Parallel-7/flashforge-api-docs](https://github.com/Parallel-7/flashforge-api-docs), [Slugger2k/FlashForgePrinterApi](https://github.com/Slugger2k/FlashForgePrinterApi), [andycb/AdventurerClientJS](https://github.com/andycb/AdventurerClientJS), [modrzew/hass-flashforge-adventurer-3](https://github.com/modrzew/hass-flashforge-adventurer-3).

## Tests

```bash
python -m unittest discover -s tests -v
```
