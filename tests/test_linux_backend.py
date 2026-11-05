"""Linux backend against a fake /sys tree, so it runs on any OS (and in CI)."""

import os
import tempfile
import unittest

from hdash.system import linux


def write(root, rel, text):
    path = os.path.join(root, *rel.split("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)


class FakeSysfs(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        r = self.root = self.tmp.name
        # AMD RX 6600: busy 37 %, 2 GiB of 8 GiB VRAM; a connector dir that must be ignored.
        write(r, "class/drm/card1/device/gpu_busy_percent", "37\n")
        write(r, "class/drm/card1/device/mem_info_vram_used", str(2 * 2**30))
        write(r, "class/drm/card1/device/mem_info_vram_total", str(8 * 2**30))
        write(r, "class/drm/card1/device/vendor", "0x1002\n")
        write(r, "class/drm/card1-DP-1/status", "connected\n")
        # Intel iGPU without a busy counter: not an "AMD card".
        write(r, "class/drm/card0/device/vendor", "0x8086\n")
        # hwmon: Intel CPU, amdgpu, NVMe, plus a sensor that reads 0 (unread).
        write(r, "class/hwmon/hwmon0/name", "coretemp\n")
        write(r, "class/hwmon/hwmon0/temp1_input", "63000\n")
        write(r, "class/hwmon/hwmon0/temp1_label", "Package id 0\n")
        write(r, "class/hwmon/hwmon0/temp2_input", "71000\n")
        write(r, "class/hwmon/hwmon0/temp2_label", "Core 0\n")
        write(r, "class/hwmon/hwmon1/name", "amdgpu\n")
        write(r, "class/hwmon/hwmon1/temp1_input", "50000\n")
        write(r, "class/hwmon/hwmon1/temp1_label", "edge\n")
        write(r, "class/hwmon/hwmon1/temp2_input", "58000\n")
        write(r, "class/hwmon/hwmon1/temp2_label", "junction\n")
        write(r, "class/hwmon/hwmon2/name", "nvme\n")
        write(r, "class/hwmon/hwmon2/temp1_input", "41850\n")
        write(r, "class/hwmon/hwmon2/temp1_label", "Composite\n")
        write(r, "class/hwmon/hwmon3/name", "acpitz\n")
        write(r, "class/hwmon/hwmon3/temp1_input", "0\n")

    def tearDown(self):
        self.tmp.cleanup()

    def test_amd_cards_skip_connectors_and_cards_without_counter(self):
        cards = linux.amd_cards(self.root)
        self.assertEqual([os.path.basename(os.path.dirname(c)) for c in cards], ["card1"])

    def test_hwmon_prefers_labels(self):
        temps = linux.pick_temps(linux.read_hwmon(self.root))
        self.assertEqual(temps, {"cpu": 63.0, "gpu": 50.0, "disk": 41.85})

    def test_hwmon_falls_back_to_hottest(self):
        sensors = {"k10temp": [("Tccd1", 55.0), ("Tccd2", 61.5)]}
        self.assertEqual(linux.pick_temps(sensors)["cpu"], 61.5)

    def test_zero_readings_are_ignored(self):
        self.assertNotIn("acpitz", linux.read_hwmon(self.root))

    def test_backend_gpu(self):
        old = linux.SYSFS
        linux.SYSFS = self.root
        try:
            backend = linux.Backend()
            util, used, total, name = backend.gpu()
        finally:
            linux.SYSFS = old
        self.assertEqual((util, used, total), (37.0, 2 * 2**30, 8 * 2**30))
        self.assertTrue(name)  # product_name, lspci or "AMD GPU"


if __name__ == "__main__":
    unittest.main()
