import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import config  # noqa: E402
from app.printer import (  # noqa: E402
    MockPrinterClient,
    Snapshot,
    parse_files,
    parse_info,
    parse_position,
    parse_progress,
    parse_status,
    parse_temps,
    sanitize_print_name,
    validate_raw,
)

M115 = """CMD M115 Received.
Machine Type: FlashForge Adventurer III
Machine Name: Bresser REX_flashforged
Firmware: v1.3.7
SN: SNFFAD265210
X: 150 Y: 150 Z: 150
Tool Count: 1
Mac Address:88:A9:A7:91:B1:0E

ok
"""

M105 = """CMD M105 Received.
T0:52/240 B:46/100
ok
"""

M119 = """CMD M119 Received.
Endstop: X-max:1 Y-max:0 Z-max:0
MachineStatus: BUILDING_FROM_SD
MoveMode: MOVING
Status: S:1 L:0 J:0 F:0
LED: 0
CurrentFile: NvidiaV100Fan_eABS+HS@245+10%.gx
ok
"""

M114 = """CMD M114 Received.
X:79.9875 Y:-1000 Z:77.211 A:0 B:0
ok
"""

M27 = """CMD M27 Received.
SD printing byte 0/100
ok
"""

M661 = (
    "CMD M661 Received.\nok\n"
    "::££\x00\x00\x00*/data/deadpool-bust-v2-supportless-by-e.gx"
    "::££\x00\x00\x00%/data/Nintendo_Switch_joy-con_Grip.gx"
    "::££\x00\x00\x00*/data/NvidiaV100Fan_eABS+HS@245+10%.gx"
    "::££\x00\x00\x00/data/YoshiRemeshed.g::"
)


class ParseTests(unittest.TestCase):
    def test_info(self):
        snap = Snapshot()
        parse_info(M115, snap)
        self.assertEqual(snap.machine_type, "FlashForge Adventurer III")
        self.assertEqual(snap.machine_name, "Bresser REX_flashforged")
        self.assertEqual(snap.firmware, "v1.3.7")
        self.assertEqual(snap.serial, "SNFFAD265210")
        self.assertEqual(snap.mac, "88:A9:A7:91:B1:0E")

    def test_temps(self):
        snap = Snapshot()
        parse_temps(M105, snap)
        self.assertEqual(snap.nozzle, 52)
        self.assertEqual(snap.nozzle_target, 240)
        self.assertEqual(snap.bed, 46)
        self.assertEqual(snap.bed_target, 100)

    def test_status_printing(self):
        snap = Snapshot()
        parse_status(M119, snap)
        self.assertTrue(snap.printing)
        self.assertFalse(snap.paused)
        self.assertEqual(snap.machine_status, "BUILDING_FROM_SD")
        self.assertEqual(snap.current_file, "NvidiaV100Fan_eABS+HS@245+10%.gx")
        self.assertEqual(snap.led, "0")

    def test_status_pause(self):
        snap = Snapshot()
        parse_status("MachineStatus: PAUSED\nCurrentFile: test.gx\nok\n", snap)
        self.assertTrue(snap.paused)
        self.assertFalse(snap.printing)

    def test_position(self):
        snap = Snapshot()
        parse_position(M114, snap)
        self.assertAlmostEqual(snap.x, 79.9875)
        self.assertAlmostEqual(snap.y, -1000)
        self.assertAlmostEqual(snap.z, 77.211)

    def test_progress(self):
        snap = Snapshot()
        parse_progress(M27, snap)
        self.assertEqual(snap.progress_current, 0)
        self.assertEqual(snap.progress_total, 100)
        self.assertEqual(snap.progress_pct(), 0)

    def test_files(self):
        names = parse_files(M661)
        self.assertIn("deadpool-bust-v2-supportless-by-e.gx", names)
        self.assertIn("NvidiaV100Fan_eABS+HS@245+10%.gx", names)
        self.assertIn("YoshiRemeshed.g", names)

    def test_raw_allows_g_and_m(self):
        self.assertEqual(validate_raw("M119"), "~M119")
        self.assertEqual(validate_raw("~G1 X10 F3000"), "~G1 X10 F3000")

    def test_raw_rejects_other(self):
        with self.assertRaises(ValueError):
            validate_raw("rm -rf /")
        with self.assertRaises(ValueError):
            validate_raw("M28 123 0:/user/x.gx")

    def test_sanitize_name(self):
        self.assertEqual(sanitize_print_name("/data/demo.gx"), "demo.gx")
        with self.assertRaises(ValueError):
            sanitize_print_name("../etc/passwd.gx")

    def test_mock_controls_without_network(self):
        client = MockPrinterClient()
        self.assertIn("demo-cube.gx", client.list_files())
        client.pause()
        self.assertTrue(client.snapshot.paused)
        client.start_print("demo-cube.gx")
        self.assertEqual(client.snapshot.current_file, "demo-cube.gx")

    def test_readonly_blocks_mock_writes(self):
        old = config.PRINTER_READONLY
        config.PRINTER_READONLY = True
        try:
            client = MockPrinterClient()
            with self.assertRaises(ValueError):
                client.stop()
        finally:
            config.PRINTER_READONLY = old


if __name__ == "__main__":
    unittest.main()
