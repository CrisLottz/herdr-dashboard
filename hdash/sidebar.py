"""The sidebar: Agents, Ports and Worktrees sections plus a compact System block.

Keyboard (focus the sidebar pane first):
    Tab / Shift+Tab   next / previous section
    ↑ ↓  or  j k      move the selection (details of the selected row expand below it)
    Enter             jump to the pane (agent, the pane that owns a port, an agent in a worktree)
    o                 open the port in the browser / the worktree's PR
    x                 kill the process listening on the port (asks y/n)
    a                 ports: show all, including system services
    r                 refresh everything now
    ?                 help
    q / Esc           quit (Esc first cancels a pending question)
"""

import os
import time
import webbrowser

import psutil

from . import herdr
from .collect import Agents, Ports, Worktrees
from .ui import (BLUE, BOLD, CYAN, DIM, GREEN, GREY, RED, RESET, REVERSE, SPINNER, YELLOW, bar,
                 level_color, pad, short, temp, visible_len)

SECTIONS = ("AGENTS", "PORTS", "WORKTREES")
HOME = os.path.expanduser("~")


def tilde(path):
    p = path[4:] if path.startswith("\\\\?\\") else path
    return "~" + p[len(HOME):] if os.path.normcase(p).startswith(os.path.normcase(HOME)) else p


def lr(left, right, width):
    """Left text, right text aligned to the edge; the left side is cut if needed."""
    room = width - visible_len(right) - 1
    return pad(left, max(0, room)) + " " + right


class Sidebar:
    def __init__(self, sampler):
        self.sp, self.s, self.tick = sampler, None, 0
        self.agents = Agents()
        self.ports = Ports(self.agents)
        self.trees = Worktrees(self.agents)
        self.section, self.sel = 0, [0, 0, 0]
        self.confirm = None      # (question, action) waiting for y/n
        self.flash = ("", 0.0)   # (message, expires at)
        self.help = False

    # --- data -----------------------------------------------------------------
    def update(self):
        self.s = self.sp.sample()
        self.tick += 1

    def _items(self, section):
        return (self.agents, self.ports, self.trees)[section].items

    def _selected(self):
        items = self._items(self.section)
        if not items:
            return None
        self.sel[self.section] = min(self.sel[self.section], len(items) - 1)
        return items[self.sel[self.section]]

    def _say(self, msg):
        self.flash = (msg, time.monotonic() + 3)

    def _pane_label(self, pane_id):
        return next((a["label"] for a in self.agents.items if a["id"] == pane_id), pane_id or "")

    # --- keys -----------------------------------------------------------------
    def on_key(self, key):
        if self.confirm:
            question, action = self.confirm
            self.confirm = None
            if key in ("y", "Y"):
                action()
            else:
                self._say("cancelled")
            return True
        if self.help:
            self.help = False
            return key not in ("q", "Q")
        if key in ("q", "Q", "esc"):
            return False
        if key == "tab":
            self.section = (self.section + 1) % 3
        elif key == "shift-tab":
            self.section = (self.section - 1) % 3
        elif key in ("down", "j"):
            self.sel[self.section] = min(self.sel[self.section] + 1, max(0, len(self._items(self.section)) - 1))
        elif key in ("up", "k"):
            self.sel[self.section] = max(0, self.sel[self.section] - 1)
        elif key == "?":
            self.help = True
        elif key == "r":
            for c in (self.agents, self.ports, self.trees):
                c.refresh()
            self._say("refreshing…")
        elif key == "a" and self.section == 1:
            self.ports.show_all = not self.ports.show_all
            self.ports.refresh()
            self._say("all ports" if self.ports.show_all else "dev ports only")
        else:
            item = self._selected()
            if item:
                self._act(key, item)
        return True

    def _act(self, key, item):
        try:
            if self.section == 0 and key == "enter":
                herdr.focus(item["pane"])
            elif self.section == 1 and key == "enter":
                pane = next((a["pane"] for a in self.agents.items if a["id"] == item["pane_id"]), None)
                herdr.focus(pane) if pane else self._say("no Herdr pane owns this port")
            elif self.section == 1 and key == "o":
                webbrowser.open(f"http://localhost:{item['port']}")
                self._say(f"opened :{item['port']}")
            elif self.section == 1 and key == "x" and item["pid"]:
                self.confirm = (f"kill :{item['port']} {item['name']} ({item['pid']})? y/n",
                                lambda: self._kill(item))
            elif self.section == 2 and key == "enter":
                herdr.focus(item["agents"][0]["pane"]) if item["agents"] else self._say("no pane in this worktree")
            elif self.section == 2 and key == "o":
                webbrowser.open(item["pr"]["url"]) if item["pr"] else self._say("no open PR for this branch")
        except herdr.HerdrError as exc:
            self._say(f"herdr: {exc}")

    def _kill(self, item):
        try:
            psutil.Process(item["pid"]).terminate()
            self._say(f"sent terminate to {item['pid']}")
            self.ports.refresh()
        except psutil.Error as exc:
            self._say(f"kill failed: {exc}")

    # --- rows -----------------------------------------------------------------
    def _agent_row(self, a, w):
        icon = {"working": GREEN + SPINNER[self.tick % 4], "blocked": RED + "!", "done": BLUE + "✓",
                "idle": GREY + "○", "shell": GREY + "›"}.get(a["status"], GREY + "?") + RESET
        # CPU is a share of the whole machine (all threads), so one decimal matters.
        right = f"{short(a['rss']):>5} {level_color(min(a['cpu'] * 4, 100))}{a['cpu']:4.1f}%{RESET}"
        return lr(f"{icon} {a['label']}", right, w)

    def _agent_details(self, a):
        where = " / ".join(x for x in (a["workspace"], a["tab"]) if x)
        top = ", ".join(f"{n} {short(m)}" for n, m, _ in a["top"])
        return [f"{a['agent'] or 'shell'} · {a['status']} · {where}", tilde(a["cwd"]), f"{a['nproc']} procs: {top}"]

    def _port_row(self, p, w):
        owner = self._pane_label(p["pane_id"]) if p["pane_id"] else f"{DIM}pid {p['pid'] or '?'}{RESET}"
        return lr(f"{CYAN}:{p['port']:<5}{RESET} {p['name']}", f"{DIM}{owner}{RESET}" if p["pane_id"] else owner, w)

    @staticmethod
    def _port_details(p):
        return [p["cmd"] or p["name"], f"{p['addr']}:{p['port']} · pid {p['pid'] or '?'}", "Enter go · o open · x kill"]

    def _tree_row(self, t, w):
        working = any(a["status"] == "working" for a in t["agents"])
        dot = (GREEN + SPINNER[self.tick % 4]) if working else (YELLOW + "●") if t["agents"] else (GREY + "○")
        bits = []
        if t["ahead"]:
            bits.append(f"{GREEN}↑{t['ahead']}{RESET}")
        if t["behind"]:
            bits.append(f"{RED}↓{t['behind']}{RESET}")
        if t["changes"]:
            bits.append(f"{YELLOW}✎{t['changes']}{RESET}")
        if t["pr"]:
            bits.append(f"{BLUE}#{t['pr']['number']}{RESET}")
        if t["missing"]:
            bits.append(f"{RED}gone{RESET}")
        return lr(f"{dot}{RESET} {t['branch']}", " ".join(bits) or f"{DIM}clean{RESET}", w)

    @staticmethod
    def _tree_details(t):
        who = ", ".join(f"{a['label']} ({a['status']})" for a in t["agents"]) or "no panes here"
        lines = [tilde(t["path"]), f"{t['age']} · {t['last']}" if t["last"] else "no commits", who]
        if t["pr"]:
            lines.append(f"PR #{t['pr']['number']}{' (draft)' if t['pr']['isDraft'] else ''}: {t['pr']['title']}")
        return lines

    # --- layout ---------------------------------------------------------------
    def _section_lines(self, idx, w, budget):
        """One section in at most `budget` lines (header included), scrolled so the
        selection and its details stay visible."""
        coll = (self.agents, self.ports, self.trees)[idx]
        items, focused = coll.items, idx == self.section
        title = f"{SECTIONS[idx]} {len(items)}" + (" · all" if idx == 1 and self.ports.show_all else "")
        head_color = CYAN + BOLD if focused else DIM
        out = [f"{head_color}{'▸' if focused else '─'} {title} {'─' * max(0, w - len(title) - 4)}{RESET}"]
        if coll.error:
            return out + [f"  {RED}{coll.error[: w - 2]}{RESET}"]
        if not items:
            return out + [f"  {DIM}{'loading…' if coll.loading else 'nothing here'}{RESET}"]
        row = (self._agent_row, self._port_row, self._tree_row)[idx]
        details = (self._agent_details, self._port_details, self._tree_details)[idx]
        sel = min(self.sel[idx], len(items) - 1)
        room = max(1, budget - 1)
        detail_lines = details(items[sel])[:3] if focused else []
        # Scroll window over items, leaving space for the details of the selection.
        visible = max(1, room - len(detail_lines))
        start = 0 if not focused else max(0, min(sel - visible // 2, len(items) - visible))
        last_repo = None
        for i in range(start, min(len(items), start + visible)):
            it = items[i]
            if idx == 2 and it["repo"] != last_repo:  # worktrees are grouped under their repo
                last_repo = it["repo"]
                out.append(f" {DIM}{it['repo']}{RESET}")
            line = row(it, w - 1)
            out.append(f"{REVERSE} {pad(line, w - 1)}{RESET}" if focused and i == sel else " " + line)
            if focused and i == sel:
                out += [f"   {DIM}{pad(d, w - 3)}{RESET}" for d in detail_lines]
        hidden = len(items) - min(len(items), start + visible)
        if hidden > 0:
            out.append(f"  {DIM}… +{hidden} more{RESET}")
        return out[:budget]

    def _system_lines(self, w):
        s = self.s
        t = s["temps"]
        bw = max(4, w - 22)
        lines = [f"{DIM}─ SYSTEM {'─' * max(0, w - 10)}{RESET}",
                 lr(f" CPU  {bar(s['cpu'], bw)} {level_color(s['cpu'])}{s['cpu']:3.0f}%{RESET}", temp(t["cpu"]), w),
                 lr(f" RAM  {bar(s['mem'].percent, bw)} {level_color(s['mem'].percent)}{s['mem'].percent:3.0f}%{RESET}",
                    f"{DIM}{short(s['mem'].used)}{RESET}", w)]
        if s["gpu_ok"]:
            lines.append(lr(f" GPU  {bar(s['gpu'], bw)} {level_color(s['gpu'])}{s['gpu']:3.0f}%{RESET}", temp(t["gpu"]), w))
            lines.append(lr(f" VRAM {bar(s['vram_pct'], bw)} {level_color(s['vram_pct'])}{s['vram_pct']:3.0f}%{RESET}",
                            f"{DIM}{short(s['vram'])}{RESET}", w))
        return lines

    def _help_lines(self, w):
        rows = [("Tab ⇧Tab", "switch section"), ("↑↓ j k", "move"), ("Enter", "jump to pane"),
                ("o", "open port / PR"), ("x", "kill port process"), ("a", "all ports"), ("r", "refresh"),
                ("q Esc", "quit")]
        return [f"{CYAN}{BOLD}▸ KEYS{RESET}"] + [f" {BOLD}{k:<9}{RESET}{v}" for k, v in rows] + [
            "", f" {DIM}any key closes this help{RESET}"]

    def render(self, width, height):
        w = max(20, width)
        header = lr(f" {CYAN}{BOLD}herdr-dashboard{RESET}", f"{DIM}{time.strftime('%H:%M')}{RESET} ", w)
        system = self._system_lines(w)
        msg, until = self.flash
        if self.confirm:
            footer = f" {YELLOW}{self.confirm[0]}{RESET}"
        elif msg and time.monotonic() < until:
            footer = f" {DIM}{msg}{RESET}"
        else:
            footer = f" {DIM}Tab section · ↑↓ move · ⏎ go · ? help{RESET}"
        if self.help:
            body = self._help_lines(w)
        else:
            room = height - 1 - len(system) - 1
            # Unfocused sections get up to 3 rows (+ header); the focused one gets the rest.
            small = {i: min(3, max(1, len(self._items(i)))) + 1 for i in range(3) if i != self.section}
            if sum(small.values()) + 4 > room:
                small = {i: 1 for i in small}  # very short pane: headers only
            focused_budget = max(2, room - sum(small.values()))
            body = []
            for i in range(3):
                body += self._section_lines(i, w, focused_budget if i == self.section else small[i])
        body = body[: max(0, height - 1 - len(system) - 1)]
        filler = [""] * max(0, height - 1 - len(body) - len(system) - 1)
        return [header] + body + filler + system + [footer]
