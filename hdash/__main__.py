"""Command line: python bootstrap.py <command>

    sidebar          Agents, Ports, Worktrees and System in a narrow column
    system           full system dashboard (what the popup shows)
    strip            one-line system strip
    toggle-sidebar   open/close the sidebar on the right edge of the current Herdr tab
    toggle-strip     open/close the strip under the focused Herdr pane
    open-popup       open the system dashboard as a Herdr popup
    status           print one plain line (CPU, RAM, GPU, temps) for Herdr's tab-bar status area
    restore          relaunch the sidebar/strip in panes Herdr restored empty (plugin startup hook)
"""

import sys
import time

from . import herdr


def status_line():
    """One sample in about half a second, for `ui.tab_bar_right` command entries.

    Herdr runs the command every few seconds, keeps the last output line and strips
    colors, so this prints plain text. CPU and GPU usage are rates, hence the short
    wait between two readings; LHM temperatures arrive on a thread, so we give it
    up to a second."""
    from .system import Sampler
    sp = Sampler()
    backend = sp.backend
    lhm = getattr(backend, "lhm", None)
    deadline = time.monotonic() + 1.0
    time.sleep(0.4)
    while lhm is not None and not lhm.online and time.monotonic() < deadline:
        time.sleep(0.05)
    s = sp.sample()
    t = s["temps"]
    deg = lambda c: f" {c:.0f}°" if c is not None else ""
    parts = [f"CPU {s['cpu']:.0f}%{deg(t['cpu'])}", f"RAM {s['mem'].percent:.0f}%"]
    if s["gpu_ok"]:
        parts.append(f"GPU {s['gpu']:.0f}%{deg(t['gpu'])}")
    sys.stdout.reconfigure(encoding="utf-8")
    print(" · ".join(parts))


def main():
    cmd = (sys.argv[1:] or ["system"])[0]
    if cmd in ("toggle-sidebar", "toggle-strip", "open-popup", "restore"):
        try:
            {"toggle-sidebar": herdr.toggle_sidebar, "toggle-strip": herdr.toggle_strip,
             "open-popup": herdr.open_popup, "restore": herdr.restore}[cmd]()
        except herdr.HerdrError as exc:
            sys.exit(f"herdr-dashboard: {exc}")
        except (KeyError, StopIteration, ValueError, TypeError) as exc:
            # Herdr answered with a shape we did not expect (other version?).
            sys.exit(f"herdr-dashboard: unexpected answer from herdr ({type(exc).__name__}: {exc}); "
                     "is it running and at least 0.9.5?")
        return
    if cmd == "status":
        status_line()
        return
    if cmd not in ("sidebar", "system", "strip"):
        sys.exit(__doc__)

    from .system import Sampler
    from .ui import run_app
    sampler = Sampler()
    if cmd == "sidebar":
        from .sidebar import Sidebar
        run_app(Sidebar(sampler), herdr.SIDEBAR_TITLE)
    elif cmd == "strip":
        from .views import StripView
        run_app(StripView(sampler), herdr.STRIP_TITLE)
    else:
        from .views import SystemView
        run_app(SystemView(sampler), "herdr-dashboard-system")


if __name__ == "__main__":
    main()
