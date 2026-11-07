"""Thin wrapper over the Herdr CLI, plus the toggles that open our panes.

Every CLI call prints JSON ({"id": ..., "result": {...}}); call() returns the
`result` part. Herdr injects HERDR_BIN_PATH into plugin commands and panes, so
we use it when present and fall back to `herdr` on PATH.
"""

import json
import os
import subprocess
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SIDEBAR_TITLE = "herdr-dashboard"
STRIP_TITLE = "herdr-dashboard-strip"
SIDEBAR_COLS = 38
STRIP_ROWS = 2


class HerdrError(RuntimeError):
    pass


def call(*args, timeout=5):
    exe = os.environ.get("HERDR_BIN_PATH", "herdr")
    try:
        out = subprocess.run([exe, *args], capture_output=True, text=True, encoding="utf-8",
                             errors="replace", timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        raise HerdrError(str(exc)) from exc
    if out.returncode != 0:
        raise HerdrError((out.stderr or out.stdout).strip()[:200])
    return json.loads(out.stdout)["result"] if out.stdout.strip() else {}  # `pane run` prints nothing


def panes():
    return call("pane", "list")["panes"]


def workspaces():
    return {w["workspace_id"]: w for w in call("workspace", "list")["workspaces"]}


def tabs():
    return {t["tab_id"]: t for t in call("tab", "list")["tabs"]}


def process_info(pane_id):
    return call("pane", "process-info", "--pane", pane_id)["process_info"]


def focus(pane):
    """Jump to a pane. Agents can be focused directly; for other panes Herdr
    only has neighbor focus, so we at least bring their tab to the front."""
    if pane.get("agent"):
        call("agent", "focus", pane["pane_id"])
    else:
        call("tab", "focus", pane["tab_id"])


def _launch(pane_id, command):
    # `pane run` types the command into the pane's shell (PowerShell, bash, zsh,
    # fish...). A bare `python "path" arg` parses the same in all of them.
    boot = os.path.join(ROOT, "bootstrap.py")
    call("pane", "run", pane_id, f'python "{boot}" {command}')


def _current_pane():
    return os.environ.get("HERDR_PANE_ID") or call("pane", "current")["pane"]["pane_id"]


def is_ours(pane, title):
    """Our panes carry `title` as their Herdr label (set right after the split, so a
    quick second keypress still finds it) and, once Python starts, as terminal title."""
    return title in (pane.get("label"), pane.get("terminal_title_stripped"))


def _running_python(pane_id):
    """True if one of our views runs under the pane's shell, False if the shell is
    idle, None if the shell has not started yet.

    Herdr's `foreground_processes` is not enough: on Windows (ConPTY) it reports
    the shell even while our Python runs in front of it. Typing the launch command
    into a running dashboard would turn into key presses ('o' opens a browser), so
    we look at the shell's process tree instead."""
    import psutil  # imported here: the toggles run before psutil is needed elsewhere
    pid = call("pane", "process-info", "--pane", pane_id)["process_info"].get("shell_pid")
    if not pid:
        return None
    try:
        for child in psutil.Process(pid).children(recursive=True):
            try:
                if "hdash" in " ".join(child.cmdline()) or "bootstrap.py" in " ".join(child.cmdline()):
                    return True
            except psutil.Error:
                continue
    except psutil.NoSuchProcess:
        return None
    return False


def restore(wait=15.0):
    """Relaunch our views in panes Herdr restored without their process.

    Herdr persists the layout and pane labels across restarts but not what ran in
    each pane, so after a reboot the sidebar pane comes back as an empty shell.
    The plugin's [[startup]] hook calls this once the server is ready. Panes whose
    shell has not started yet are retried for up to `wait` seconds."""
    views = {SIDEBAR_TITLE: "sidebar", STRIP_TITLE: "strip"}
    pending = {p["pane_id"]: views[p["label"]] for p in panes() if p.get("label") in views}
    deadline = time.monotonic() + wait
    while pending and time.monotonic() < deadline:
        for pane_id, view in list(pending.items()):
            try:
                state = _running_python(pane_id)
            except HerdrError:
                pending.pop(pane_id)  # pane closed meanwhile
                continue
            if state is None:
                continue
            if state is False:
                _launch(pane_id, view)
            pending.pop(pane_id)
        if pending:
            time.sleep(1)


def _close_existing(title, tab_id, all_panes):
    found = [p for p in all_panes if p["tab_id"] == tab_id and is_ours(p, title)]
    for p in found:
        call("pane", "close", p["pane_id"])
    return bool(found)


def toggle_sidebar():
    """Open the sidebar on the right edge of the current tab, or close it.

    Herdr can only split relative to a pane (right or down), not the whole tab.
    To get a full-height column we look for a pane that touches the right edge
    and spans the full height; if the layout has none, we use the top-right
    pane, so the column is shorter but still on the edge."""
    target = _current_pane()
    all_panes = panes()
    tab = next(p["tab_id"] for p in all_panes if p["pane_id"] == target)
    if _close_existing(SIDEBAR_TITLE, tab, all_panes):
        return
    layout = call("pane", "layout", "--pane", target)["layout"]
    area = layout["area"]
    right_edge = [p for p in layout["panes"]
                  if p["rect"]["x"] + p["rect"]["width"] >= area["x"] + area["width"]]
    full = [p for p in right_edge if p["rect"]["height"] >= area["height"]]
    host = (full or sorted(right_edge, key=lambda p: p["rect"]["y"]))[0]
    width = host["rect"]["width"]
    ratio = max(0.3, (width - SIDEBAR_COLS) / width)
    new = call("pane", "split", "--pane", host["pane_id"], "--direction", "right",
               "--ratio", f"{ratio:.4f}", "--no-focus")["pane"]["pane_id"]
    call("pane", "rename", new, SIDEBAR_TITLE)
    _launch(new, "sidebar")


def toggle_strip():
    """Open a one-line strip under the focused pane, or close it if the tab has one.
    The split ratio is the share the original pane keeps; Herdr clamps tiny panes
    to its minimum height."""
    target = _current_pane()
    all_panes = panes()
    tab = next(p["tab_id"] for p in all_panes if p["pane_id"] == target)
    if _close_existing(STRIP_TITLE, tab, all_panes):
        return
    layout = call("pane", "layout", "--pane", target)["layout"]
    height = next(p["rect"]["height"] for p in layout["panes"] if p["pane_id"] == target)
    ratio = max(0.5, (height - STRIP_ROWS) / height)
    new = call("pane", "split", "--pane", target, "--direction", "down",
               "--ratio", f"{ratio:.4f}", "--no-focus")["pane"]["pane_id"]
    call("pane", "rename", new, STRIP_TITLE)
    _launch(new, "strip")


def open_popup():
    # Declared in herdr-plugin.toml ([[panes]] id = "system").
    call("plugin", "pane", "open", "--plugin", "herdr-dashboard", "--entrypoint", "system")
