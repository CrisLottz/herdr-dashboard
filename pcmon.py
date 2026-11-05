"""pcmon — a live resource monitor for the Windows terminal, built for Herdr.

Modes:
    pcmon.py                 full view (for a popup or a large pane)
    pcmon.py --strip         one-line strip (to keep under a column)
    pcmon.py --toggle-strip  open or close the strip under Herdr's focused pane
    pcmon.py --open-popup    open the full view as a Herdr popup
Quit: q, Esc or Ctrl+C.

Data sources:
- psutil: CPU (total and per thread), RAM, swap, disk (space + I/O), network.
- PDH (Windows performance counters, through pywin32): GPU usage, VRAM and
  disk busy time. Task Manager reads the same source, so it works with AMD,
  Intel and NVIDIA without vendor SDKs.
- LibreHardwareMonitor (optional): temperatures. Windows does not expose them
  without a kernel driver; LHM ships one and publishes everything at
  http://localhost:8085/data.json. Without LHM, pcmon still runs, minus temperatures.

Rendering: hand-written ANSI (no TUI library). Each frame moves the cursor home
and overwrites the previous one instead of clearing the screen, so it never flickers.
"""

import collections
import json
import msvcrt
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
import winreg

import psutil
import win32pdh

INTERVAL = 1.0          # seconds between samples
HISTORY = 120           # samples kept for the sparklines
SPARK = " ▁▂▃▄▅▆▇█"     # 9 levels: 0 = empty, 8 = full
SPINNER = "◐◓◑◒"
LHM_URL = os.environ.get("PCMON_LHM_URL", "http://localhost:8085/data.json")
STRIP_TITLE = "pcmon-strip"   # terminal title that identifies the strip pane

# --- ANSI colors (256-color palette) -----------------------------------------
RESET, BOLD, DIM = "\x1b[0m", "\x1b[1m", "\x1b[2m"


def fg(n):
    return f"\x1b[38;5;{n}m"


def level_color(pct):
    """Green < 60 %, yellow < 85 %, red >= 85 %."""
    return fg(42) if pct < 60 else fg(220) if pct < 85 else fg(196)


def temp_color(c):
    """Green < 70 °C, yellow < 85 °C, red >= 85 °C."""
    return fg(42) if c < 70 else fg(220) if c < 85 else fg(196)


# --- GPU and disk through PDH ------------------------------------------------
class PdhCounters:
    """Wildcard (*) PDH counters: PDH picks up new processes on its own, so the
    instances never need to be re-expanded on each frame."""

    def __init__(self):
        self.query = win32pdh.OpenQuery()
        add = lambda path: win32pdh.AddEnglishCounter(self.query, path)
        # One instance per (process, engine); keep the 3D engine, which is what
        # Task Manager shows as "GPU" by default.
        self.engine = add(r"\GPU Engine(*engtype_3D)\Utilization Percentage")
        self.vram = add(r"\GPU Adapter Memory(*)\Dedicated Usage")
        self.disk_idle = add(r"\PhysicalDisk(_Total)\% Idle Time")
        win32pdh.CollectQueryData(self.query)  # first sample: baseline for rates

    def _sum(self, counter):
        try:
            values = win32pdh.GetFormattedCounterArray(counter, win32pdh.PDH_FMT_DOUBLE)
            return sum(values.values())
        except win32pdh.error:
            return 0.0  # no instances yet

    def read(self):
        win32pdh.CollectQueryData(self.query)
        gpu = min(self._sum(self.engine), 100.0)
        vram = self._sum(self.vram)
        try:
            _, idle = win32pdh.GetFormattedCounterValue(self.disk_idle, win32pdh.PDH_FMT_DOUBLE)
            disk_busy = max(0.0, min(100.0, 100.0 - idle))
        except win32pdh.error:
            disk_busy = 0.0
        return gpu, vram, disk_busy


def gpu_info():
    """(name, total VRAM in bytes) of the adapter with the most VRAM.

    WMI's AdapterRAM is a uint32 capped at 4 GB; the registry stores the real
    size as a QWORD. Walk every subkey of the 'Display' class because the GPU
    is not always under '0000' (virtual adapters, leftover drivers)."""
    base = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"
    best_name, best_size = "GPU", 0
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base) as root:
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


# --- temperatures through LibreHardwareMonitor -------------------------------
class Temps:
    """Polls LHM's JSON on a background thread so a slow request never freezes
    the display. `cpu`, `gpu` and `disk` stay None when there is no reading."""

    # Preferred sensor per component, in order; falls back to the hottest one.
    PREFER = {
        "cpu": ("CPU Package", "Core (Tctl/Tdie)", "Core Max"),
        "gpu": ("GPU Core", "GPU Hot Spot"),
        "disk": ("Composite Temperature", "Temperature"),
    }
    KIND = (("cpu", re.compile(r"^/(intel|amd)cpu/")),
            ("gpu", re.compile(r"^/gpu-")),
            ("disk", re.compile(r"^/(nvme|ssd|hdd)/")))

    def __init__(self):
        self.cpu = self.gpu = self.disk = None
        self.online = False
        threading.Thread(target=self._loop, daemon=True).start()

    @staticmethod
    def _number(text):
        m = re.match(r"\s*(-?\d+(?:[.,]\d+)?)", str(text))
        return float(m.group(1).replace(",", ".")) if m else None

    def _collect(self, node, found):
        sid = node.get("SensorId", "")
        if node.get("Type") == "Temperature" and "Distance" not in node.get("Text", ""):
            value = self._number(node.get("Value"))
            if value and value > 0:  # 0 °C = sensor not read yet (e.g. SMART asleep)
                for kind, pattern in self.KIND:
                    if pattern.match(sid):
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
                self.cpu, self.gpu, self.disk = (self._pick(k, found[k]) for k in ("cpu", "gpu", "disk"))
                self.online = True
                time.sleep(INTERVAL)
            except (OSError, ValueError):
                self.online = False
                self.cpu = self.gpu = self.disk = None
                time.sleep(10)  # LHM is down: retry without hammering the port


# --- drawing helpers ---------------------------------------------------------
def bar(pct, width):
    """Bar with 1/8-character resolution: full '█' blocks plus one partial."""
    pct = max(0.0, min(100.0, pct))
    eighths = round(pct / 100 * width * 8)
    full, part = divmod(eighths, 8)
    partial = " ▏▎▍▌▋▊▉"[part] if part else ""
    filled = "█" * full + partial
    return level_color(pct) + filled + DIM + "·" * (width - len(filled)) + RESET


def sparkline(values, width):
    """Last `width` samples (0–100) as vertical blocks."""
    tail = list(values)[-width:]
    tail = [0.0] * (width - len(tail)) + tail
    return "".join(level_color(v) + SPARK[round(v / 100 * 8)] for v in tail) + RESET


def human(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024:
            return f"{n:5.1f} {unit}"
        n /= 1024
    return f"{n:5.1f} PB"


def short(n):
    """Compact size for the strip: 934B, 41K, 13M, 2.5G."""
    for unit in ("B", "K", "M", "G", "T"):
        if abs(n) < 1000:
            return f"{n:.0f}{unit}" if unit in "BKM" else f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}P"


def temp(c):
    return f"{temp_color(c)}{c:.0f}°C{RESET}" if c is not None else ""


def row(label, pct, width, extra=""):
    """A row that always fits in `width` columns, so it never wraps.

    Fixed columns: ' ' + label(5) + ' ' + bar + ' ' + '100.0%'(6) + ' ' = 15.
    The bar takes what is left after the extra text (8 to 40 characters);
    if it still does not fit, the extra text is truncated."""
    fixed = 15
    bar_w = max(8, min(40, width - fixed - len(extra)))
    extra = extra[: max(0, width - fixed - bar_w)]
    return f" {BOLD}{label:<5}{RESET} {bar(pct, bar_w)} {level_color(pct)}{pct:5.1f}%{RESET} {DIM}{extra}{RESET}"


# --- sampling ----------------------------------------------------------------
class Sampler:
    """Gathers every source and computes rates (MB/s) between two samples."""

    def __init__(self):
        self.pdh = PdhCounters()
        self.temps = Temps()
        self.gpu_name, self.vram_total = gpu_info()
        self.hist = {k: collections.deque(maxlen=HISTORY) for k in ("cpu", "ram", "gpu", "disk")}
        psutil.cpu_percent(percpu=True)  # first call: baseline, returns 0
        self.io, self.net, self.t = psutil.disk_io_counters(), psutil.net_io_counters(), time.monotonic()

    def sample(self):
        now = time.monotonic()
        dt = max(now - self.t, 1e-6)
        s = {}
        s["cores"] = psutil.cpu_percent(percpu=True)
        s["cpu"] = sum(s["cores"]) / len(s["cores"])
        s["mem"], s["swap"] = psutil.virtual_memory(), psutil.swap_memory()
        s["gpu"], s["vram"], s["disk"] = self.pdh.read()
        s["vram_pct"] = s["vram"] / self.vram_total * 100 if self.vram_total else 0.0
        io, net = psutil.disk_io_counters(), psutil.net_io_counters()
        s["rd"], s["wr"] = (io.read_bytes - self.io.read_bytes) / dt, (io.write_bytes - self.io.write_bytes) / dt
        s["rx"], s["tx"] = (net.bytes_recv - self.net.bytes_recv) / dt, (net.bytes_sent - self.net.bytes_sent) / dt
        self.io, self.net, self.t = io, net, now
        for k, v in (("cpu", s["cpu"]), ("ram", s["mem"].percent), ("gpu", s["gpu"]), ("disk", s["disk"])):
            self.hist[k].append(v)
        return s


# --- views -------------------------------------------------------------------
def render_full(sp, s, width, tick):
    t = sp.temps
    spark_w = max(10, width - 9)
    freq = psutil.cpu_freq()
    cpu_extra = f"{len(s['cores'])} threads · {freq.current / 1000:.2f} GHz" if freq else f"{len(s['cores'])} threads"
    temp_hint = "" if t.online else f"  {DIM}(no temperatures: start LibreHardwareMonitor){RESET}"
    out = [
        f" {fg(45)}{BOLD}{SPINNER[tick % 4]} pcmon{RESET} {DIM}{time.strftime('%H:%M:%S')} · q/Esc to quit{RESET}{temp_hint}",
        "",
        row("CPU", s["cpu"], width, cpu_extra),
        f"       {sparkline(sp.hist['cpu'], spark_w)}",
        row("RAM", s["mem"].percent, width, f"{human(s['mem'].used)} / {human(s['mem'].total)}"),
        f"       {sparkline(sp.hist['ram'], spark_w)}",
        row("GPU", s["gpu"], width, sp.gpu_name),
        f"       {sparkline(sp.hist['gpu'], spark_w)}",
        row("VRAM", s["vram_pct"], width, f"{human(s['vram'])} / {human(sp.vram_total or 0)}"),
        row("DISK", s["disk"], width, f"R {human(s['rd'])}/s  W {human(s['wr'])}/s"),
        f"       {sparkline(sp.hist['disk'], spark_w)}",
        row("SWAP", s["swap"].percent, width, f"{human(s['swap'].used)} / {human(s['swap'].total)}"),
        "",
        f" {BOLD}NET{RESET}   {fg(42)}↓ {human(s['rx'])}/s{RESET}   {fg(214)}↑ {human(s['tx'])}/s{RESET}",
    ]
    if t.online:
        parts = [f"{name} {temp(v)}" for name, v in (("CPU", t.cpu), ("GPU", t.gpu), ("SSD", t.disk)) if v is not None]
        out.append(f" {BOLD}TEMP{RESET}  " + "   ".join(parts))
    out += ["", f" {BOLD}Threads{RESET}"]
    # Mini bars per thread, in as many columns as the pane width allows.
    per_line = max(1, (width - 2) // 16)
    cells = [f"{i:>2} {bar(p, 6)} {p:3.0f}%" for i, p in enumerate(s["cores"])]
    for i in range(0, len(cells), per_line):
        out.append(" " + "  ".join(cells[i:i + per_line]))
    out += ["", f" {BOLD}Drives{RESET}"]
    for part in psutil.disk_partitions(all=False):
        if "cdrom" in part.opts or not part.fstype:
            continue
        try:
            u = psutil.disk_usage(part.mountpoint)
        except OSError:
            continue
        out.append(row(part.mountpoint[:2], u.percent, width, f"{human(u.used)} / {human(u.total)}"))
    return out


def render_strip(sp, s, width, tick):
    """A single line. Each segment carries its visible length (without ANSI
    codes) to know how many fit; trailing ones are dropped in narrow panes."""
    t = sp.temps

    def seg(label, pct, extra_plain="", extra_ansi=""):
        plain = f"{label} {'x' * 5} {pct:3.0f}%" + (f" {extra_plain}" if extra_plain else "")
        ansi = (f"{BOLD}{label}{RESET} {bar(pct, 5)} {level_color(pct)}{pct:3.0f}%{RESET}"
                + (f" {extra_ansi}" if extra_ansi else ""))
        return plain, ansi

    def tseg(c):
        return (f"{c:.0f}°C", temp(c)) if c is not None else ("", "")

    segments = [
        seg("CPU", s["cpu"], *tseg(t.cpu)),
        seg("RAM", s["mem"].percent, short(s["mem"].used), f"{DIM}{short(s['mem'].used)}{RESET}"),
        seg("GPU", s["gpu"], *tseg(t.gpu)),
        seg("VRAM", s["vram_pct"]),
        seg("DSK", s["disk"]),
    ]
    net_plain = f"↓{short(s['rx'])} ↑{short(s['tx'])}"
    segments.append((net_plain, f"{fg(42)}↓{short(s['rx'])}{RESET} {fg(214)}↑{short(s['tx'])}{RESET}"))

    sep_plain, sep_ansi = " │ ", f" {DIM}│{RESET} "
    line_plain, line_ansi = f" {SPINNER[tick % 4]} ", f" {fg(45)}{SPINNER[tick % 4]}{RESET} "
    for i, (plain, ansi) in enumerate(segments):
        add_plain = (sep_plain if i else "") + plain
        if len(line_plain) + len(add_plain) > width:
            break
        line_plain += add_plain
        line_ansi += (sep_ansi if i else "") + ansi
    return [line_ansi]


# --- main loop ---------------------------------------------------------------
def wait_or_quit(seconds):
    """Sleep `seconds`, checking the keyboard every 50 ms. True means quit.
    In a Herdr popup the keys reach pcmon, so q/Esc close it."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        while msvcrt.kbhit():
            if msvcrt.getwch() in ("q", "Q", "\x1b"):
                return True
        time.sleep(0.05)
    return False


def run(view):
    os.system("")  # enable ANSI escape processing in the Windows console
    sp = Sampler()
    tick = 0
    title = STRIP_TITLE if view is render_strip else "pcmon"
    # \x1b]0;...\x07 = terminal title; \x1b[?25l = hide cursor.
    sys.stdout.write(f"\x1b]0;{title}\x07\x1b[?25l\x1b[2J")
    try:
        while not wait_or_quit(INTERVAL):
            s = sp.sample()
            width = shutil.get_terminal_size((80, 24)).columns
            tick += 1
            out = view(sp, s, width, tick)
            # \x1b[H = cursor home; \x1b[K = clear to end of line; \x1b[J = clear below.
            sys.stdout.write("\x1b[H" + "\n".join(line + "\x1b[K" for line in out) + "\x1b[J")
            sys.stdout.flush()
    except KeyboardInterrupt:
        pass
    finally:
        sys.stdout.write("\x1b[?25h" + RESET + "\n")  # restore the cursor


# --- Herdr integration -------------------------------------------------------
def herdr(*args):
    """Call the Herdr CLI and return the `result` field of its JSON output."""
    exe = os.environ.get("HERDR_BIN_PATH", "herdr")
    out = subprocess.run([exe, *args], capture_output=True, text=True, encoding="utf-8", check=True)
    return json.loads(out.stdout)["result"] if out.stdout.strip() else {}  # `pane run` prints nothing


def toggle_strip():
    """Open the strip under the focused pane, or close it if the tab already has one.

    The strip is recognized by its terminal title (STRIP_TITLE). The split
    `ratio` is the share the original pane keeps, computed so the strip gets
    `strip_rows` rows whatever the window height (Herdr clamps it to its minimum)."""
    strip_rows = 2
    target = os.environ.get("HERDR_PANE_ID") or herdr("pane", "current")["pane"]["pane_id"]
    panes = herdr("pane", "list")["panes"]
    tab = next(p["tab_id"] for p in panes if p["pane_id"] == target)
    for p in panes:
        if p["tab_id"] == tab and p.get("terminal_title_stripped") == STRIP_TITLE:
            herdr("pane", "close", p["pane_id"])
            return
    layout = herdr("pane", "layout", "--pane", target)["layout"]
    height = next(p["rect"]["height"] for p in layout["panes"] if p["pane_id"] == target)
    ratio = max(0.5, (height - strip_rows) / height)
    new = herdr("pane", "split", "--pane", target, "--direction", "down",
                "--ratio", f"{ratio:.4f}", "--no-focus")["pane"]["pane_id"]
    launcher = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pcmon.cmd")
    herdr("pane", "run", new, f"& '{launcher}' --strip")


def main():
    args = sys.argv[1:]
    if "--toggle-strip" in args:
        toggle_strip()
    elif "--open-popup" in args:
        # The popup is declared in herdr-plugin.toml ([[panes]] id = "monitor").
        herdr("plugin", "pane", "open", "--plugin", "pcmon", "--entrypoint", "monitor")
    else:
        run(render_strip if "--strip" in args else render_full)


if __name__ == "__main__":
    main()
