"""Windows backend: PDH performance counters, the registry and LibreHardwareMonitor.

- GPU usage, VRAM and disk busy come from PDH, the source Task Manager uses,
  so they work for AMD, Intel and NVIDIA without vendor SDKs.
- Total VRAM comes from the registry: WMI's AdapterRAM is a uint32 capped at 4 GB.
- Temperatures need a kernel driver on Windows. We read them from
  LibreHardwareMonitor's local web server instead (optional).
"""

import json
import os
import re
import threading
import time
import urllib.request
import winreg

import win32pdh

LHM_URL = os.environ.get("HDASH_LHM_URL", "http://localhost:8085/data.json")
DISPLAY_CLASS = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"


def gpu_info():
    """(name, total VRAM in bytes) of the adapter with the most VRAM.
    Walk every subkey because the GPU is not always under '0000'."""
    best_name, best_size = "GPU", 0
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, DISPLAY_CLASS) as root:
            for i in range(winreg.QueryInfoKey(root)[0]):
                try:
                    with winreg.OpenKey(root, winreg.EnumKey(root, i)) as k:
                        size = int(winreg.QueryValueEx(k, "HardwareInformation.qwMemorySize")[0])
                        if size > best_size:
                            best_size = size
                            best_name = winreg.QueryValueEx(k, "DriverDesc")[0]
                except OSError:
                    continue
    except OSError:
        pass
    return best_name, best_size or None


class LhmTemps:
    """Polls LHM's JSON on a background thread so a slow request never freezes
    the display. Values stay None when there is no reading."""

    # Preferred sensor per component, in order; falls back to the hottest one.
    PREFER = {"cpu": ("CPU Package", "Core (Tctl/Tdie)", "Core Max"),
              "gpu": ("GPU Core", "GPU Hot Spot"),
              "disk": ("Composite Temperature", "Temperature")}
    KIND = (("cpu", re.compile(r"^/(intel|amd)cpu/")),
            ("gpu", re.compile(r"^/gpu-")),
            ("disk", re.compile(r"^/(nvme|ssd|hdd)/")))

    def __init__(self):
        self.values = {"cpu": None, "gpu": None, "disk": None}
        self.online = False
        threading.Thread(target=self._loop, daemon=True).start()

    @staticmethod
    def _number(text):
        m = re.match(r"\s*(-?\d+(?:[.,]\d+)?)", str(text))
        return float(m.group(1).replace(",", ".")) if m else None

    def _collect(self, node, found):
        if node.get("Type") == "Temperature" and "Distance" not in node.get("Text", ""):
            value = self._number(node.get("Value"))
            if value and value > 0:  # 0 °C = sensor not read yet (e.g. SMART asleep)
                for kind, pattern in self.KIND:
                    if pattern.match(node.get("SensorId", "")):
                        found[kind].append((node.get("Text", ""), value))
        for child in node.get("Children", []):
            self._collect(child, found)

    def _pick(self, kind, sensors):
        for name in self.PREFER[kind]:
            for text, value in sensors:
                if text == name:
                    return value
        return max((v for _, v in sensors), default=None)

    def _loop(self):
        while True:
            try:
                with urllib.request.urlopen(LHM_URL, timeout=1) as resp:
                    data = json.load(resp)
                found = {"cpu": [], "gpu": [], "disk": []}
                self._collect(data, found)
                self.values = {k: self._pick(k, found[k]) for k in found}
                self.online = True
                time.sleep(1)
            except (OSError, ValueError):
                self.online = False
                self.values = {"cpu": None, "gpu": None, "disk": None}
                time.sleep(10)  # LHM is down: retry without hammering the port


class Backend:
    def __init__(self):
        self.query = win32pdh.OpenQuery()
        add = lambda path: win32pdh.AddEnglishCounter(self.query, path)
        # Wildcard counters: PDH picks up new processes on its own. One instance
        # per (process, engine); the 3D engine is what Task Manager calls "GPU".
        self.engine = add(r"\GPU Engine(*engtype_3D)\Utilization Percentage")
        self.vram = add(r"\GPU Adapter Memory(*)\Dedicated Usage")
        self.disk_idle = add(r"\PhysicalDisk(_Total)\% Idle Time")
        win32pdh.CollectQueryData(self.query)  # baseline for rate counters
        self.name, self.vram_total = gpu_info()
        self.lhm = LhmTemps()
        self._gpu = self._busy = None
        self._last_collect = 0.0

    def _sum(self, counter):
        try:
            return sum(win32pdh.GetFormattedCounterArray(counter, win32pdh.PDH_FMT_DOUBLE).values())
        except win32pdh.error:
            return 0.0  # no instances yet

    def _collect(self):
        # gpu() and disk_busy() are both called each sample; collect PDH only once.
        now = time.monotonic()
        if now - self._last_collect < 0.5:
            return
        self._last_collect = now
        win32pdh.CollectQueryData(self.query)
        self._gpu = (min(self._sum(self.engine), 100.0), self._sum(self.vram), self.vram_total, self.name)
        try:
            _, idle = win32pdh.GetFormattedCounterValue(self.disk_idle, win32pdh.PDH_FMT_DOUBLE)
            self._busy = max(0.0, min(100.0, 100.0 - idle))
        except win32pdh.error:
            self._busy = None

    def gpu(self):
        self._collect()
        return self._gpu

    def disk_busy(self):
        self._collect()
        return self._busy

    def temps(self):
        return dict(self.lhm.values)

    def temps_hint(self):
        return "" if self.lhm.online else "no temps: start LibreHardwareMonitor"
