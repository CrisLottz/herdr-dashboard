"""pcmon — monitor de recursos en la terminal para Windows, pensado para Herdr.

Modos:
    pcmon.py               vista completa (para un popup o un pane grande)
    pcmon.py --strip       tira de una línea (para dejarla abajo de una columna)
    pcmon.py --toggle-strip  abre o cierra la tira bajo el pane enfocado de Herdr
    pcmon.py --open-popup    abre la vista completa como popup de Herdr
Salir: q, Esc o Ctrl+C.

Fuentes de datos:
- psutil: CPU (total y por hilo), RAM, swap, disco (espacio + E/S), red.
- PDH (contadores de rendimiento de Windows, vía pywin32): uso de GPU, VRAM y
  ocupación del disco. Es la fuente del Administrador de tareas, así que sirve
  para AMD, Intel y NVIDIA sin SDKs del fabricante.
- LibreHardwareMonitor (opcional): temperaturas. Windows no las expone sin un
  driver de kernel; LHM lo trae y publica todo en http://localhost:8085/data.json.
  Si LHM no está corriendo, pcmon sigue funcionando sin temperaturas.

Render: ANSI a mano (sin librerías de TUI). Cada vuelta mueve el cursor al
inicio y reescribe encima en vez de limpiar la pantalla, por eso no parpadea.
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

INTERVAL = 1.0          # segundos entre muestras
HISTORY = 120           # muestras guardadas para los sparklines
SPARK = " ▁▂▃▄▅▆▇█"     # 9 niveles: 0 = vacío, 8 = lleno
SPINNER = "◐◓◑◒"
LHM_URL = os.environ.get("PCMON_LHM_URL", "http://localhost:8085/data.json")
STRIP_TITLE = "pcmon-strip"   # título de terminal con el que se reconoce la tira

# --- colores ANSI (256 colores) ----------------------------------------------
RESET, BOLD, DIM = "\x1b[0m", "\x1b[1m", "\x1b[2m"


def fg(n):
    return f"\x1b[38;5;{n}m"


def level_color(pct):
    """Verde < 60 %, amarillo < 85 %, rojo >= 85 %."""
    return fg(42) if pct < 60 else fg(220) if pct < 85 else fg(196)


def temp_color(c):
    """Verde < 70 °C, amarillo < 85 °C, rojo >= 85 °C."""
    return fg(42) if c < 70 else fg(220) if c < 85 else fg(196)


# --- GPU y disco vía PDH -----------------------------------------------------
class PdhCounters:
    """Contadores PDH con comodín (*): PDH incluye solos los procesos nuevos,
    así que no hay que re-expandir las instancias en cada vuelta."""

    def __init__(self):
        self.query = win32pdh.OpenQuery()
        add = lambda path: win32pdh.AddEnglishCounter(self.query, path)
        # Una instancia por (proceso, motor); filtramos el motor 3D, que es
        # lo que el Administrador de tareas muestra como "GPU" por defecto.
        self.engine = add(r"\GPU Engine(*engtype_3D)\Utilization Percentage")
        self.vram = add(r"\GPU Adapter Memory(*)\Dedicated Usage")
        self.disk_idle = add(r"\PhysicalDisk(_Total)\% Idle Time")
        win32pdh.CollectQueryData(self.query)  # primera muestra: base para las tasas

    def _sum(self, counter):
        try:
            values = win32pdh.GetFormattedCounterArray(counter, win32pdh.PDH_FMT_DOUBLE)
            return sum(values.values())
        except win32pdh.error:
            return 0.0  # sin instancias todavía

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
    """(nombre, VRAM total en bytes) del adaptador con más VRAM.

    WMI (AdapterRAM) es uint32 y se queda en 4 GB; el registro guarda el tamaño
    real en un QWORD. Recorremos las subclaves de la clase 'Display' porque la
    GPU no siempre está en '0000' (adaptadores virtuales, drivers viejos)."""
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


# --- temperaturas vía LibreHardwareMonitor -----------------------------------
class Temps:
    """Lee el JSON de LHM en un hilo aparte para que una petición lenta nunca
    congele el dibujo. `cpu`, `gpu` y `disk` quedan en None si no hay dato."""

    # Sensor preferido por componente, en orden; si ninguno existe se usa el máximo.
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
            if value and value > 0:  # 0 °C = sensor sin leer (p. ej. SMART dormido)
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
                time.sleep(10)  # LHM apagado: reintentar sin martillar el puerto


# --- helpers de dibujo -------------------------------------------------------
def bar(pct, width):
    """Barra con resolución de 1/8 de carácter: '█' llenos + un parcial."""
    pct = max(0.0, min(100.0, pct))
    eighths = round(pct / 100 * width * 8)
    full, part = divmod(eighths, 8)
    partial = " ▏▎▍▌▋▊▉"[part] if part else ""
    filled = "█" * full + partial
    return level_color(pct) + filled + DIM + "·" * (width - len(filled)) + RESET


def sparkline(values, width):
    """Últimas `width` muestras (0–100) como bloques verticales."""
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
    """Tamaño compacto para la tira: 934B, 41K, 13M, 2.5G."""
    for unit in ("B", "K", "M", "G", "T"):
        if abs(n) < 1000:
            return f"{n:.0f}{unit}" if unit in "BKM" else f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}P"


def temp(c):
    return f"{temp_color(c)}{c:.0f}°C{RESET}" if c is not None else ""


def row(label, pct, width, extra=""):
    """Una fila que siempre cabe en `width` columnas, para que no se parta en dos.

    Columnas fijas: ' ' + etiqueta(5) + ' ' + barra + ' ' + '100.0%'(6) + ' ' = 15.
    La barra toma lo que sobra después del texto extra (entre 8 y 40 caracteres);
    si aun así no cabe, se recorta el texto extra."""
    fixed = 15
    bar_w = max(8, min(40, width - fixed - len(extra)))
    extra = extra[: max(0, width - fixed - bar_w)]
    return f" {BOLD}{label:<5}{RESET} {bar(pct, bar_w)} {level_color(pct)}{pct:5.1f}%{RESET} {DIM}{extra}{RESET}"


# --- muestreo ----------------------------------------------------------------
class Sampler:
    """Junta todas las fuentes y calcula las tasas (MB/s) entre dos muestras."""

    def __init__(self):
        self.pdh = PdhCounters()
        self.temps = Temps()
        self.gpu_name, self.vram_total = gpu_info()
        self.hist = {k: collections.deque(maxlen=HISTORY) for k in ("cpu", "ram", "gpu", "disk")}
        psutil.cpu_percent(percpu=True)  # primera llamada: base, devuelve 0
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


# --- vistas ------------------------------------------------------------------
def render_full(sp, s, width, tick):
    t = sp.temps
    spark_w = max(10, width - 9)
    freq = psutil.cpu_freq()
    cpu_extra = f"{len(s['cores'])} hilos · {freq.current / 1000:.2f} GHz" if freq else f"{len(s['cores'])} hilos"
    temp_hint = "" if t.online else f"  {DIM}(sin temperaturas: abre LibreHardwareMonitor){RESET}"
    out = [
        f" {fg(45)}{BOLD}{SPINNER[tick % 4]} pcmon{RESET} {DIM}{time.strftime('%H:%M:%S')} · q/Esc para salir{RESET}{temp_hint}",
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
    out += ["", f" {BOLD}Hilos{RESET}"]
    # Mini-barras por hilo, en columnas que caben en el ancho del pane.
    per_line = max(1, (width - 2) // 16)
    cells = [f"{i:>2} {bar(p, 6)} {p:3.0f}%" for i, p in enumerate(s["cores"])]
    for i in range(0, len(cells), per_line):
        out.append(" " + "  ".join(cells[i:i + per_line]))
    out += ["", f" {BOLD}Discos{RESET}"]
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
    """Una sola línea. Cada segmento lleva su largo visible (sin códigos ANSI)
    para saber cuántos caben; los del final se descartan si el pane es angosto."""
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


# --- bucle principal ---------------------------------------------------------
def wait_or_quit(seconds):
    """Duerme `seconds` revisando el teclado cada 50 ms. True si hay que salir.
    En un popup de Herdr las teclas llegan a pcmon, así q/Esc lo cierran."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        while msvcrt.kbhit():
            if msvcrt.getwch() in ("q", "Q", "\x1b"):
                return True
        time.sleep(0.05)
    return False


def run(view):
    os.system("")  # activa el procesado de secuencias ANSI en la consola de Windows
    sp = Sampler()
    tick = 0
    title = STRIP_TITLE if view is render_strip else "pcmon"
    # \x1b]0;...\x07 = título de la terminal; \x1b[?25l = ocultar cursor.
    sys.stdout.write(f"\x1b]0;{title}\x07\x1b[?25l\x1b[2J")
    try:
        while not wait_or_quit(INTERVAL):
            s = sp.sample()
            width = shutil.get_terminal_size((80, 24)).columns
            tick += 1
            out = view(sp, s, width, tick)
            # \x1b[H = cursor a home; \x1b[K = borra el resto de la línea; \x1b[J = borra lo de abajo.
            sys.stdout.write("\x1b[H" + "\n".join(line + "\x1b[K" for line in out) + "\x1b[J")
            sys.stdout.flush()
    except KeyboardInterrupt:
        pass
    finally:
        sys.stdout.write("\x1b[?25h" + RESET + "\n")  # devolver el cursor


# --- integración con Herdr ---------------------------------------------------
def herdr(*args):
    """Llama al CLI de Herdr y devuelve el campo `result` de su JSON."""
    exe = os.environ.get("HERDR_BIN_PATH", "herdr")
    out = subprocess.run([exe, *args], capture_output=True, text=True, encoding="utf-8", check=True)
    return json.loads(out.stdout)["result"] if out.stdout.strip() else {}  # `pane run` no imprime nada


def toggle_strip():
    """Abre la tira debajo del pane enfocado, o la cierra si ya hay una en la pestaña.

    La tira se reconoce por su título de terminal (STRIP_TITLE). El split usa un
    `ratio` = parte que conserva el pane original, calculado para que la tira
    quede de STRIP_ROWS filas sin importar el alto de la ventana."""
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
        # El popup lo declara herdr-plugin.toml ([[panes]] id = "monitor").
        herdr("plugin", "pane", "open", "--plugin", "pcmon", "--entrypoint", "monitor")
    else:
        run(render_strip if "--strip" in args else render_full)


if __name__ == "__main__":
    main()
