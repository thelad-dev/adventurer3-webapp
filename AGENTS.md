# Project agent memory

This file is the project's committed home for project-intrinsic agent knowledge: build, test, release, architecture, and sharp-edge notes that should travel with the code.

- Add durable project-specific notes here as they are discovered through real work.

## Runtime

- Stdlib-only Python, Einstieg `python -m app`. Bind/Port/Drucker: `app/config.py`.
- Protokoll: FlashForge TCP 8899, Kommandos mit `~`. Parser und Client in `app/printer.py`.
- Status-Poll ohne `~M601`. Sitzung (`~M601 S1`) nur bei Steueraktionen.
- Dateiliste `~M661`: Dateinamen kommen nach `ok`, extra Drain in `list_files`.
- Kamera-Proxy: `http://<drucker>:8080/?action=stream` über `/api/camera`.
- `PRINTER_MOCK=1` für UI ohne physischen Drucker. Kein Firmware-Flash.
- „Lüfter ausgeschaltet lassen“: Server sendet `~M107` 1×/s, Default aus. Siehe `fan_hold_loop` in `app/server.py`.
- Default-Drucker `192.168.20.191`. Deploy-Port auf Host `dock`: 8765.

## Maintaining this file

Keep this file for knowledge useful to almost every future agent session in this project.
Do not repeat what the codebase already shows; point to the authoritative file or command instead.
Prefer rewriting or pruning existing entries over appending new ones.
When updating this file, preserve this bar for all agents and keep entries concise.
