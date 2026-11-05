"""Linux backend: sysfs for GPU and temperatures, nvidia-smi for NVIDIA cards.

Everything is read from plain files, no root needed:
- AMD (amdgpu): /sys/class/drm/cardN/device/gpu_busy_percent, mem_info_vram_used
  and mem_info_vram_total.
- NVIDIA: `nvidia-smi --query-gpu=...` on a background thread (proprietary
  driver only; nouveau exposes no usage counter).
- Intel: no usage counter without root (intel_gpu_top needs CAP_PERFMON), so
  Intel-only machines show "no GPU data".
- Temperatures: /sys/class/hwmon/hwmonN/{name, tempM_input, tempM_label}.
- Disk busy: psutil's per-disk busy_time (milliseconds spent doing I/O).

HDASH_SYSFS overrides the "/sys" root, which lets the tests run on a fake tree.
"""

import glob
import os
import shutil
import subprocess
import threading
import time

import psutil

SYSFS = os.environ.get("HDASH_SYSFS", "/sys")
VENDORS = {"0x1002": "AMD", "0x10de": "NVIDIA", "0x8086": "Intel"}

# hwmon driver names -> component, and the preferred sensor labels in order.
HWMON = {
    "cpu": {"coretemp": ("Package id 0",), "k10temp": ("Tctl", "Tdie"), "zenpower": ("Tdie", "Tctl"),
            "cpu_thermal": ()},
    "gpu": {"amdgpu": ("edge", "junction"), "nouveau": (), "radeon": ()},
    "disk": {"nvme": ("Composite",), "drivetemp": ()},
}


def _read(path, default=None):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return default


def _read_int(path):
    value = _read(path)
    try:
        return int(value) if value is not None else None
    except ValueError:
        return None


def read_hwmon(root=None):
    """{driver name: [(label, °C), ...]} for every hwmon device."""
    root = root or SYSFS
    sensors = {}
    for dev in sorted(glob.glob(os.path.join(root, "class", "hwmon", "hwmon*"))):
        name = _read(os.path.join(dev, "name"), "")
        for inp in sorted(glob.glob(os.path.join(dev, "temp*_input"))):
            milli = _read_int(inp)
            if milli is None or milli <= 0:
                continue
            label = _read(inp.replace("_input", "_label"), os.path.basename(inp).split("_")[0])
            sensors.setdefault(name, []).append((label, milli / 1000))
    return sensors


def pick_temps(sensors):
    """Choose one value per component: the preferred label, else the hottest sensor."""
    out = {}
    for kind, drivers in HWMON.items():
        value = None
        for driver, labels in drivers.items():
            readings = sensors.get(driver, [])
            if not readings:
                continue
            value = next((v for name in labels for (l, v) in readings if l == name), None)
            if value is None:
                value = max(v for _, v in readings)
            break
        out[kind] = value
    return out


def amd_cards(root=None):
    """Device dirs of GPUs that expose amdgpu's busy counter."""
    root = root or SYSFS
    cards = []
    for card in sorted(glob.glob(os.path.join(root, "class", "drm", "card[0-9]*"))):
        if "-" in os.path.basename(card):  # connectors such as card0-DP-1
            continue
        dev = os.path.join(card, "device")
        if os.path.exists(os.path.join(dev, "gpu_busy_percent")):
            cards.append(dev)
    return cards


def card_name(dev):
    name = _read(os.path.join(dev, "product_name"))
    if name:
        return name
    slot = os.path.basename(os.path.realpath(dev))
    if shutil.which("lspci"):
        try:
            out = subprocess.run(["lspci", "-mm", "-s", slot], capture_output=True, text=True, timeout=2).stdout
            parts = out.split('"')
            if len(parts) >= 6:
                return parts[5]  # -mm format: slot "class" "vendor" "device" ...
        except (OSError, subprocess.SubprocessError):
            pass
    return f"{VENDORS.get(_read(os.path.join(dev, 'vendor'), ''), 'GPU')} GPU"


class NvidiaSmi:
    """Runs nvidia-smi every 2 s on a thread; one call costs ~50 ms."""

    QUERY = "utilization.gpu,memory.used,memory.total,name,temperature.gpu"

    def __init__(self):
        self.value = None
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while True:
            try:
                out = subprocess.run(["nvidia-smi", f"--query-gpu={self.QUERY}", "--format=csv,noheader,nounits"],
                                     capture_output=True, text=True, timeout=5).stdout.splitlines()
                util, used, total, name, tmp = (x.strip() for x in out[0].split(","))
                self.value = (float(util), float(used) * 2**20, float(total) * 2**20, name, float(tmp))
            except (OSError, subprocess.SubprocessError, ValueError, IndexError):
                self.value = None
            time.sleep(2)


class Backend:
    def __init__(self):
        self.cards = amd_cards()
        self.names = {dev: card_name(dev) for dev in self.cards}
        self.nvidia = NvidiaSmi() if not self.cards and shutil.which("nvidia-smi") else None
        self.prev_busy, self.prev_t = self._busy_times(), time.monotonic()

    def gpu(self):
        if self.cards:
            # Several AMD GPUs (iGPU + dGPU): show the one with the most VRAM.
            best = max(self.cards, key=lambda d: _read_int(os.path.join(d, "mem_info_vram_total")) or 0)
            return (float(_read_int(os.path.join(best, "gpu_busy_percent")) or 0),
                    _read_int(os.path.join(best, "mem_info_vram_used")) or 0,
                    _read_int(os.path.join(best, "mem_info_vram_total")),
                    self.names[best])
        if self.nvidia and self.nvidia.value:
            util, used, total, name, _ = self.nvidia.value
            return util, used, total, name
        return None

    def temps(self):
        t = pick_temps(read_hwmon())
        if t["gpu"] is None and self.nvidia and self.nvidia.value:
            t["gpu"] = self.nvidia.value[4]
        return t

    def temps_hint(self):
        return "" if any(read_hwmon()) else "no hwmon sensors (load lm_sensors modules)"

    @staticmethod
    def _busy_times():
        """{disk: busy ms} for whole disks only (a partition's time is already in its disk)."""
        try:
            counters = psutil.disk_io_counters(perdisk=True) or {}
        except (OSError, RuntimeError):
            return {}
        return {name: c.busy_time for name, c in counters.items()
                if hasattr(c, "busy_time") and os.path.exists(os.path.join(SYSFS, "block", name))
                and not name.startswith(("loop", "ram", "zram", "sr"))}

    def disk_busy(self):
        now, cur = time.monotonic(), self._busy_times()
        dt_ms = max((now - self.prev_t) * 1000, 1)
        busy = [min(100.0, (cur[d] - self.prev_busy[d]) / dt_ms * 100) for d in cur if d in self.prev_busy]
        self.prev_busy, self.prev_t = cur, now
        return max(busy, default=None)
