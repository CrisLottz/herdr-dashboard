"""Background collectors for the sidebar: agents, ports and worktrees.

Each collector runs on its own thread at its own pace and publishes an immutable
snapshot (a list of dicts) that the UI reads. A slow `git` or `herdr` call can
never freeze the display, and one failing source never takes the others down:
its error is stored and shown dimmed in its section.
"""

import json
import os
import re
import shutil
import subprocess
import threading
import time

import psutil

from . import herdr

GIT_TIMEOUT = 5


class Collector:
    interval = 2.0

    def __init__(self):
        self.items, self.error, self.updated = [], None, 0.0
        self._wake = threading.Event()
        threading.Thread(target=self._loop, daemon=True).start()

    def refresh(self):
        self._wake.set()

    @property
    def loading(self):
        """True until the first collection that had all its inputs."""
        return not self.updated

    def _loop(self):
        while True:
            try:
                self.items, self.error = self.collect(), None
                self.after_collect()  # runs once `items` is published, not before
            except Exception as exc:  # noqa: BLE001 — shown in the UI, never crashes it
                self.error = str(exc).splitlines()[0][:120] if str(exc) else type(exc).__name__
            self.updated = time.monotonic()
            self._wake.wait(self.interval)
            self._wake.clear()

    def collect(self):
        raise NotImplementedError

    def after_collect(self):
        pass


def norm(path):
    """Comparable path: Herdr reports Windows paths as \\\\?\\C:\\..., git as C:/..."""
    if path.startswith("\\\\?\\"):
        path = path[4:]
    return os.path.normcase(os.path.normpath(path)).rstrip("\\/")


def inside(path, root):
    path, root = norm(path), norm(root)
    return path == root or path.startswith(root + os.sep)


# --- agents ------------------------------------------------------------------
class Agents(Collector):
    """Every Herdr pane (agent or plain shell) with the CPU and RAM of its whole
    process tree: the pane's shell plus all its descendants."""

    interval = 2.0

    def __init__(self):
        self._procs = {}       # pid -> psutil.Process, kept so cpu_percent() has a baseline
        self.pid_to_pane = {}  # used by Ports to say which pane owns a port
        self.dependents = []   # Ports and Worktrees: woken after our first collection
        self._first = True
        self.updated_first = float("inf")  # when the first pane list was published
        super().__init__()

    def _proc(self, pid):
        p = self._procs.get(pid)
        if p is None:
            p = self._procs[pid] = psutil.Process(pid)
            p.cpu_percent(None)  # first call is the baseline and returns 0
        return p

    def collect(self):
        ncpu = psutil.cpu_count() or 1
        spaces = herdr.workspaces()
        tabs = herdr.tabs()
        items, pid_map, alive = [], {}, set()
        for pane in herdr.panes():
            if herdr.is_ours(pane, herdr.SIDEBAR_TITLE) or herdr.is_ours(pane, herdr.STRIP_TITLE):
                continue
            info = herdr.process_info(pane["pane_id"])
            procs = []
            try:
                root = self._proc(info["shell_pid"])
                procs = [root] + root.children(recursive=True)
            except (psutil.Error, KeyError, TypeError):
                pass
            cpu = rss = 0.0
            tree = []
            for p in procs:
                try:
                    p = self._proc(p.pid)
                    c, m = p.cpu_percent(None) / ncpu, p.memory_info().rss
                    cpu, rss = cpu + c, rss + m
                    tree.append((p.name(), m, c))
                    pid_map[p.pid] = pane["pane_id"]
                    alive.add(p.pid)
                except psutil.Error:
                    continue
            fg = (info.get("foreground_processes") or [{}])[0]
            cwd = fg.get("cwd") or pane.get("cwd", "")
            if pane.get("agent"):
                label = pane.get("terminal_title_stripped") or pane["agent"]
            else:
                # Plain shells all say "powershell.exe" or "bash"; the folder tells them apart.
                label = os.path.basename(cwd.rstrip("\\/")) or fg.get("name") or pane["pane_id"]
            items.append({
                "pane": pane,
                "id": pane["pane_id"],
                "label": label,
                "agent": pane.get("agent"),
                "status": pane.get("agent_status", "unknown") if pane.get("agent") else "shell",
                "workspace": spaces.get(pane["workspace_id"], {}).get("label", ""),
                "tab": tabs.get(pane["tab_id"], {}).get("label", ""),
                "cwd": cwd,
                "cpu": cpu, "rss": rss, "nproc": len(tree),
                "top": sorted(tree, key=lambda t: t[1], reverse=True)[:3],
                "focused": pane.get("focused", False),
            })
        self._procs = {pid: p for pid, p in self._procs.items() if pid in alive}
        self.pid_to_pane = pid_map
        # Working agents first, then by RAM.
        order = {"working": 0, "blocked": 1, "done": 2, "idle": 3, "unknown": 4, "shell": 5}
        return sorted(items, key=lambda a: (order.get(a["status"], 9), -a["rss"]))

    def after_collect(self):
        if self._first:
            self.updated_first = time.monotonic()
            # Ports and Worktrees started with no pane data; rerun them now that
            # `items` is published instead of leaving them empty until their next interval.
            self._first = False
            for d in self.dependents:
                d.refresh()


# --- ports -------------------------------------------------------------------
class Ports(Collector):
    """TCP ports in LISTEN state, with their process and the Herdr pane that owns it.

    By default only "dev" ports are shown: those owned by a Herdr pane, plus
    well-known database/dev-server ports (a MySQL service runs outside any pane,
    and as SYSTEM its process name is hidden from us, hence the names below).
    `show_all` lists everything, including OS services and apps like Steam."""

    interval = 3.0
    KNOWN = {1433: "mssql", 3000: "dev", 3001: "dev", 3306: "mysql", 33060: "mysqlx", 4200: "angular",
             5000: "dev", 5173: "vite", 5432: "postgres", 5672: "rabbitmq", 6379: "redis", 8000: "dev",
             8080: "dev", 8081: "dev", 8888: "jupyter", 9000: "dev", 9200: "elastic", 11434: "ollama",
             27017: "mongodb"}

    def __init__(self, agents):
        self.agents = agents
        self.show_all = False
        agents.dependents.append(self)
        super().__init__()

    @property
    def loading(self):
        return not self.updated or self.updated < self.agents.updated_first

    def collect(self):
        seen, items = set(), []
        for c in psutil.net_connections(kind="tcp"):
            if c.status != psutil.CONN_LISTEN or not c.laddr:
                continue
            port, pid = c.laddr.port, c.pid
            if (port, pid) in seen:
                continue  # same socket on IPv4 and IPv6
            seen.add((port, pid))
            name, cmd = "?", ""
            if pid:
                try:
                    p = psutil.Process(pid)
                    name, cmd = p.name(), " ".join(p.cmdline())
                except psutil.Error:
                    pass
            pane = self.agents.pid_to_pane.get(pid)
            if not self.show_all and not pane and port not in self.KNOWN:
                continue
            if name == "?" and port in self.KNOWN:
                name = self.KNOWN[port]
            items.append({"id": f"{port}/{pid}", "port": port, "pid": pid, "name": name,
                          "cmd": cmd, "addr": c.laddr.ip, "pane_id": pane})
        # Ports owned by a Herdr pane first, then by number.
        return sorted(items, key=lambda p: (p["pane_id"] is None, p["port"]))


# --- worktrees ---------------------------------------------------------------
def git(cwd, *args, timeout=GIT_TIMEOUT):
    out = subprocess.run(["git", "-C", cwd, *args], capture_output=True, text=True,
                         encoding="utf-8", errors="replace", timeout=timeout)
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip()[:120])
    return out.stdout


BRANCH_RE = re.compile(r"^## (?P<branch>[^.\s]+)(?:\.\.\.\S+)?(?: \[(?P<track>[^\]]+)\])?")


class Worktrees(Collector):
    """Git worktrees of every repo that has a Herdr pane open inside it, plus any
    repo listed in HDASH_REPOS (os.pathsep-separated). For each worktree: branch,
    uncommitted changes, ahead/behind its upstream, last commit, open PR (if the
    GitHub CLI is installed) and which panes are working in it."""

    interval = 10.0
    PR_EVERY = 60.0

    def __init__(self, agents):
        self.agents = agents
        self._prs, self._pr_time = {}, {}
        self.gh = shutil.which("gh")
        agents.dependents.append(self)
        super().__init__()

    @property
    def loading(self):
        # Until a collection has run with the agents' pane folders, an empty list
        # means "not looked yet", not "no worktrees".
        return not self.updated or self.updated < self.agents.updated_first

    def _repo_roots(self):
        cwds = [a["cwd"] for a in self.agents.items if a["cwd"]]
        cwds += [p for p in os.environ.get("HDASH_REPOS", "").split(os.pathsep) if p]
        roots = {}
        for cwd in cwds:
            try:
                common = git(cwd, "rev-parse", "--path-format=absolute", "--git-common-dir").strip()
            except (RuntimeError, OSError, subprocess.SubprocessError):
                continue
            # The common dir is <main repo>/.git for the main checkout and every linked worktree.
            roots.setdefault(norm(common), os.path.dirname(common.rstrip("/\\")))
        return list(roots.values())

    def _pr_map(self, repo):
        """Open PRs by branch. `gh` can take seconds per repo, so it runs on its own
        thread and the worktree list shows up immediately; PRs fill in later."""
        now = time.monotonic()
        if self.gh and now - self._pr_time.get(repo, -1e9) > self.PR_EVERY:
            self._pr_time[repo] = now
            threading.Thread(target=self._fetch_prs, args=(repo,), daemon=True).start()
        return self._prs.get(repo, {})

    def _fetch_prs(self, repo):
        try:
            out = subprocess.run(["gh", "pr", "list", "--state", "open", "--limit", "50", "--json",
                                  "number,headRefName,url,isDraft,title"],
                                 cwd=repo, capture_output=True, text=True, encoding="utf-8", timeout=20)
            self._prs[repo] = {p["headRefName"]: p for p in json.loads(out.stdout or "[]")}
        except (OSError, subprocess.SubprocessError, ValueError):
            self._prs[repo] = {}
        self.refresh()  # redraw the list with the PR numbers

    def collect(self):
        items = []
        for repo in self._repo_roots():
            prs = self._pr_map(repo)
            for block in git(repo, "worktree", "list", "--porcelain").strip().split("\n\n"):
                fields = dict((line.split(" ", 1) + [""])[:2] for line in block.splitlines())
                path = fields.get("worktree")
                if not path or "bare" in fields:
                    continue
                branch = fields.get("branch", "").replace("refs/heads/", "") or "(detached)"
                item = {"id": norm(path), "repo": os.path.basename(repo), "path": path, "branch": branch,
                        "changes": 0, "ahead": 0, "behind": 0, "last": "", "age": "", "pr": prs.get(branch),
                        "main": norm(path) == norm(repo), "missing": not os.path.isdir(path)}
                if not item["missing"]:
                    status = git(path, "status", "--porcelain=v1", "-b").splitlines()
                    m = BRANCH_RE.match(status[0]) if status else None
                    track = m.group("track") if m and m.group("track") else ""
                    item["ahead"] = int((re.search(r"ahead (\d+)", track) or [0, 0])[1])
                    item["behind"] = int((re.search(r"behind (\d+)", track) or [0, 0])[1])
                    item["changes"] = len(status) - 1
                    log = git(path, "log", "-1", "--format=%cr%x1f%s").strip().split("\x1f")
                    if len(log) == 2:
                        item["age"], item["last"] = log
                item["agents"] = [a for a in self.agents.items if a["cwd"] and inside(a["cwd"], path)]
                items.append(item)
        # A pane inside a linked worktree is also "inside" the main checkout's folder when
        # worktrees live under it; keep each pane only in its deepest worktree.
        for it in items:
            it["agents"] = [a for a in it["agents"]
                            if not any(o is not it and inside(a["cwd"], o["path"]) and len(o["path"]) > len(it["path"])
                                       for o in items)]
        return sorted(items, key=lambda w: (w["repo"], not w["agents"], not w["main"], w["branch"]))
