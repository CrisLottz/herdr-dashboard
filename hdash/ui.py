"""Hand-written ANSI drawing helpers and the shared app loop.

No TUI framework: every frame moves the cursor home (ESC[H), rewrites each line,
clears the rest of it (ESC[K) and everything below (ESC[J). Nothing is cleared
first, so there is no flicker.
"""

import os
import re
import shutil
import sys
import time

from .keys import Keys

RESET, BOLD, DIM, REVERSE = "\x1b[0m", "\x1b[1m", "\x1b[2m", "\x1b[7m"
SPARK = " ▁▂▃▄▅▆▇█"     # 9 levels: 0 = empty, 8 = full
SPINNER = "◐◓◑◒"
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def fg(n):
    return f"\x1b[38;5;{n}m"


GREEN, YELLOW, RED, CYAN, ORANGE, GREY, BLUE = (fg(n) for n in (42, 220, 196, 45, 214, 245, 75))


def level_color(pct):
    """Green < 60 %, yellow < 85 %, red >= 85 %."""
    return GREEN if pct < 60 else YELLOW if pct < 85 else RED


def temp_color(c):
    """Green < 70 °C, yellow < 85 °C, red >= 85 °C."""
    return GREEN if c < 70 else YELLOW if c < 85 else RED


def visible_len(s):
    return len(ANSI_RE.sub("", s))


def fit(s, width):
    """Cut `s` to `width` visible columns without breaking ANSI codes."""
    if visible_len(s) <= width:
        return s
    out, seen, i = [], 0, 0
    while i < len(s) and seen < width:
        m = ANSI_RE.match(s, i)
        if m:
            out.append(m.group())
            i = m.end()
            continue
        out.append(s[i])
        seen += 1
        i += 1
    return "".join(out) + RESET


def pad(s, width):
    """Right-pad to `width` visible columns (cuts if longer)."""
    s = fit(s, width)
    return s + " " * (width - visible_len(s))


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
    """Compact size: 934B, 41K, 13M, 2.5G."""
    for unit in ("B", "K", "M", "G", "T"):
        if abs(n) < 1000:
            return f"{n:.0f}{unit}" if unit in "BKM" else f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}P"


def temp(c):
    return f"{temp_color(c)}{c:.0f}°C{RESET}" if c is not None else ""


def run_app(app, title, interval=1.0):
    """Shared loop for every view.

    `app` needs three methods: update() refreshes data (called every `interval`
    seconds), on_key(key) returns False to quit, render(width, height) returns
    the lines of one frame. Keys redraw immediately, so navigation feels instant
    even though data only refreshes once per interval."""
    if os.name == "nt":
        os.system("")  # enable ANSI escape processing in the Windows console
    # ESC]0;...BEL = terminal title (Herdr shows it and we use it to find our panes);
    # ESC[?25l = hide cursor; ESC[2J = clear once at start.
    sys.stdout.write(f"\x1b]0;{title}\x07\x1b[?25l\x1b[2J")
    next_update, dirty = 0.0, True
    try:
        with Keys() as keys:
            while True:
                now = time.monotonic()
                if now >= next_update:
                    app.update()
                    next_update = now + interval
                    dirty = True
                for key in keys.poll(max(0.0, min(0.05, next_update - now))):
                    if app.on_key(key) is False:
                        return
                    dirty = True
                if dirty:
                    size = shutil.get_terminal_size((80, 24))
                    # Leave the last column empty: writing into it makes the Windows
                    # console (and some terminals) wrap, which eats a character.
                    cols = max(10, size.columns - 1)
                    lines = app.render(cols, size.lines)[: size.lines]
                    sys.stdout.write("\x1b[H" + "\n".join(fit(l, cols) + "\x1b[K" for l in lines) + "\x1b[J")
                    sys.stdout.flush()
                    dirty = False
    except KeyboardInterrupt:
        pass
    finally:
        sys.stdout.write("\x1b[?25h" + RESET + "\n")  # restore the cursor
