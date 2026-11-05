"""System views: the full dashboard (popup) and the one-line strip."""

import time

import psutil

from .ui import (BOLD, CYAN, DIM, GREEN, ORANGE, RESET, SPINNER, bar, fit, human, level_color, short,
                 sparkline, temp)


def row(label, pct, width, extra=""):
    """A row that always fits in `width` columns, so it never wraps.

    Fixed columns: ' ' + label(5) + ' ' + bar + ' ' + '100.0%'(6) + ' ' = 15.
    The bar takes what is left after the extra text (8 to 40 characters);
    if it still does not fit, the extra text is truncated."""
    fixed = 15
    bar_w = max(8, min(40, width - fixed - len(extra)))
    extra = extra[: max(0, width - fixed - bar_w)]
    return f" {BOLD}{label:<5}{RESET} {bar(pct, bar_w)} {level_color(pct)}{pct:5.1f}%{RESET} {DIM}{extra}{RESET}"


class SystemView:
    """Full view for the popup or a large pane. q/Esc quits."""

    def __init__(self, sampler):
        self.sp, self.s, self.tick = sampler, None, 0

    def update(self):
        self.s = self.sp.sample()
        self.tick += 1

    def on_key(self, key):
        return key not in ("q", "Q", "esc")

    def render(self, width, height):
        s, sp = self.s, self.sp
        spark_w = max(10, width - 9)
        freq = s["freq"]
        cpu_extra = f"{len(s['cores'])} threads" + (f" · {freq.current / 1000:.2f} GHz" if freq else "")
        out = [
            f" {CYAN}{BOLD}{SPINNER[self.tick % 4]} herdr-dashboard{RESET} {DIM}{time.strftime('%H:%M:%S')} · q/Esc to quit{RESET}",
            f" {DIM}{s['temps_hint']}{RESET}" if s["temps_hint"] else "",
            row("CPU", s["cpu"], width, cpu_extra),
            f"       {sparkline(sp.hist['cpu'], spark_w)}",
            row("RAM", s["mem"].percent, width, f"{human(s['mem'].used)} / {human(s['mem'].total)}"),
            f"       {sparkline(sp.hist['ram'], spark_w)}",
        ]
        if s["gpu_ok"]:
            out += [row("GPU", s["gpu"], width, s["gpu_name"]),
                    f"       {sparkline(sp.hist['gpu'], spark_w)}",
                    row("VRAM", s["vram_pct"], width, f"{human(s['vram'])} / {human(s['vram_total'] or 0)}")]
        out.append(row("DISK", s["disk"], width, f"R {human(s['rd'])}/s  W {human(s['wr'])}/s"))
        if s["disk_ok"]:
            out.append(f"       {sparkline(sp.hist['disk'], spark_w)}")
        out += [row("SWAP", s["swap"].percent, width, f"{human(s['swap'].used)} / {human(s['swap'].total)}"),
                "",
                f" {BOLD}NET{RESET}   {GREEN}↓ {human(s['rx'])}/s{RESET}   {ORANGE}↑ {human(s['tx'])}/s{RESET}"]
        t = s["temps"]
        parts = [f"{name} {temp(t[k])}" for name, k in (("CPU", "cpu"), ("GPU", "gpu"), ("SSD", "disk")) if t[k] is not None]
        if parts:
            out.append(f" {BOLD}TEMP{RESET}  " + "   ".join(parts))
        out += ["", f" {BOLD}Threads{RESET}"]
        per_line = max(1, (width - 2) // 16)
        cells = [f"{i:>2} {bar(p, 6)} {p:3.0f}%" for i, p in enumerate(s["cores"])]
        for i in range(0, len(cells), per_line):
            out.append(" " + "  ".join(cells[i:i + per_line]))
        out += ["", f" {BOLD}Drives{RESET}"]
        for part in psutil.disk_partitions(all=False):
            if "cdrom" in part.opts or not part.fstype or part.fstype in ("squashfs", "tmpfs", "overlay"):
                continue
            try:
                u = psutil.disk_usage(part.mountpoint)
            except OSError:
                continue
            out.append(row(part.mountpoint[:5], u.percent, width, f"{human(u.used)} / {human(u.total)}"))
        return [fit(line, width) for line in out]


class StripView:
    """A single line. Each segment carries its visible length (without ANSI codes)
    to know how many fit; trailing ones are dropped in narrow panes."""

    def __init__(self, sampler):
        self.sp, self.s, self.tick = sampler, None, 0

    def update(self):
        self.s = self.sp.sample()
        self.tick += 1

    def on_key(self, key):
        return key not in ("q", "Q", "esc")

    def render(self, width, height):
        s, t = self.s, self.s["temps"]

        def seg(label, pct, extra_plain="", extra_ansi=""):
            plain = f"{label} {'x' * 5} {pct:3.0f}%" + (f" {extra_plain}" if extra_plain else "")
            ansi = (f"{BOLD}{label}{RESET} {bar(pct, 5)} {level_color(pct)}{pct:3.0f}%{RESET}"
                    + (f" {extra_ansi}" if extra_ansi else ""))
            return plain, ansi

        def tseg(c):
            return (f"{c:.0f}°C", temp(c)) if c is not None else ("", "")

        segments = [seg("CPU", s["cpu"], *tseg(t["cpu"])),
                    seg("RAM", s["mem"].percent, short(s["mem"].used), f"{DIM}{short(s['mem'].used)}{RESET}")]
        if s["gpu_ok"]:
            segments += [seg("GPU", s["gpu"], *tseg(t["gpu"])), seg("VRAM", s["vram_pct"])]
        if s["disk_ok"]:
            segments.append(seg("DSK", s["disk"]))
        segments.append((f"↓{short(s['rx'])} ↑{short(s['tx'])}",
                         f"{GREEN}↓{short(s['rx'])}{RESET} {ORANGE}↑{short(s['tx'])}{RESET}"))

        line_plain, line_ansi = f" {SPINNER[self.tick % 4]} ", f" {CYAN}{SPINNER[self.tick % 4]}{RESET} "
        for i, (plain, ansi) in enumerate(segments):
            add = (" │ " if i else "") + plain
            if len(line_plain) + len(add) > width:
                break
            line_plain += add
            line_ansi += (f" {DIM}│{RESET} " if i else "") + ansi
        return [line_ansi]
