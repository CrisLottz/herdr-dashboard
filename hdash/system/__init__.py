"""System sampling: portable parts through psutil, the rest through an OS backend.

psutil covers CPU, RAM, swap, disk space/IO and network on every OS. GPU usage,
VRAM, temperatures and disk busy time have no portable API, so each OS gets a
backend module with the same four methods:

    gpu()        -> (usage %, vram used bytes, vram total bytes or None, name) or None
    temps()      -> {"cpu": °C or None, "gpu": ..., "disk": ...}
    temps_hint() -> short text shown when temperatures are unavailable, or ""
    disk_busy()  -> % of time the busiest disk was working, or None
"""

import collections
import sys
import time

import psutil

HISTORY = 120  # samples kept for the sparklines


def load_backend():
    if sys.platform == "win32":
        from . import windows
        return windows.Backend()
    if sys.platform.startswith("linux"):
        from . import linux
        return linux.Backend()
    from . import generic
    return generic.Backend()


class Sampler:
    """Gathers every source and computes rates (bytes/s) between two samples."""

    def __init__(self):
        self.backend = load_backend()
        self.hist = {k: collections.deque(maxlen=HISTORY) for k in ("cpu", "ram", "gpu", "disk")}
        psutil.cpu_percent(percpu=True)  # first call: baseline, returns 0
        self.io, self.net, self.t = psutil.disk_io_counters(), psutil.net_io_counters(), time.monotonic()
        self.last = None

    def sample(self):
        now = time.monotonic()
        dt = max(now - self.t, 1e-6)
        s = {"cores": psutil.cpu_percent(percpu=True)}
        s["cpu"] = sum(s["cores"]) / len(s["cores"])
        s["freq"] = psutil.cpu_freq()
        s["mem"], s["swap"] = psutil.virtual_memory(), psutil.swap_memory()
        gpu = self.backend.gpu()
        s["gpu_ok"] = gpu is not None
        s["gpu"], s["vram"], s["vram_total"], s["gpu_name"] = gpu or (0.0, 0, None, "no GPU data")
        s["vram_pct"] = s["vram"] / s["vram_total"] * 100 if s["vram_total"] else 0.0
        busy = self.backend.disk_busy()
        s["disk_ok"] = busy is not None
        s["disk"] = busy or 0.0
        s["temps"] = self.backend.temps()
        s["temps_hint"] = self.backend.temps_hint()
        io, net = psutil.disk_io_counters(), psutil.net_io_counters()
        if io and self.io:
            s["rd"], s["wr"] = (io.read_bytes - self.io.read_bytes) / dt, (io.write_bytes - self.io.write_bytes) / dt
        else:
            s["rd"] = s["wr"] = 0.0
        s["rx"], s["tx"] = (net.bytes_recv - self.net.bytes_recv) / dt, (net.bytes_sent - self.net.bytes_sent) / dt
        self.io, self.net, self.t = io, net, now
        for k, v in (("cpu", s["cpu"]), ("ram", s["mem"].percent), ("gpu", s["gpu"]), ("disk", s["disk"])):
            self.hist[k].append(v)
        self.last = s
        return s
