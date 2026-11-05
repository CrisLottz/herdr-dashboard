"""Fallback backend (macOS and others): psutil only.

GPU usage needs root on macOS (powermetrics), so it is left out. psutil has no
temperature API on macOS either. Everything else in the dashboard still works.
"""


class Backend:
    def gpu(self):
        return None

    def disk_busy(self):
        return None

    def temps(self):
        return {"cpu": None, "gpu": None, "disk": None}

    def temps_hint(self):
        return "no GPU/temps on this OS yet"
