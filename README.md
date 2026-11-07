# herdr-dashboard

[English](#herdr-dashboard) · [Español](#español)

A live sidebar for [Herdr](https://herdr.dev): see what every agent is costing your machine, which dev servers are listening, and which git worktrees are being worked on — without leaving the terminal.

```
 herdr-dashboard            02:11
─ AGENTS 5 ──────────────────────
 ◓ notes chat           194M  0.2%
 ○ frontend             261M  0.0%
 ○ api refactor         179M  0.0%
─ PORTS 2 ───────────────────────
 :3306  mysql             pid 5856
 :8000  python    api refactor
▸ WORKTREES 5 ───────────────────
 shop-api
 ● develop                      ↑5
 ○ feat/checkout             clean
   ~/code/shop-api_wt/checkout
   2 hours ago · Add checkout totals
   no panes here
 dotfiles
 ◓ main                         ✎2
─ SYSTEM ────────────────────────
 CPU  ██▏·········  18%       67°C
 RAM  █████████▊··  82%      12.9G
 GPU  ▏···········   1%       48°C
 VRAM ███▌········  30%       2.4G
 Tab section · ↑↓ move · ⏎ go · ?
```

- **Agents** — every Herdr pane with the CPU and RAM of its whole process tree (the shell plus everything it started), its status (working, idle, blocked, done) and its biggest processes.
- **Ports** — TCP ports in `LISTEN` with their process and **the pane that owns them**, so `:8000` tells you which worktree's Django it is. Databases and common dev ports (MySQL, Postgres, Redis, Vite…) are shown even when they run as services.
- **Worktrees** — every worktree of every repo that has a pane open in it: branch, uncommitted changes (`✎`), commits ahead/behind (`↑↓`), last commit, open PR (`#41`, via the GitHub CLI) and a live dot when an agent is working there.
- **System** — CPU, RAM, GPU and VRAM with temperatures, plus a full popup with sparklines, per-thread bars, disk I/O, network and drives.

Hand-written ANSI and keyboard only (no TUI framework), Python with a single dependency (`psutil`, plus `pywin32` on Windows).

## Views and keys

| View | Default key | Space it takes |
|---|---|---|
| **Sidebar** — Agents, Ports, Worktrees, System | `prefix+d` (press again to close) | a 38-column column on the right edge |
| **System popup** — the full system dashboard | `prefix+m`, `q`/`Esc` to close | none (floats over the layout) |
| **Strip** — system on one line | `prefix+shift+m` (press again to close) | 2–3 rows under the focused pane |

Inside the sidebar (focus its pane first):

| Key | Action |
|---|---|
| `Tab` / `Shift+Tab` | next / previous section |
| `↑` `↓` or `j` `k` | move; the selected row expands with details |
| `Enter` | jump to the pane: the agent, the pane that owns the port, an agent in the worktree |
| `o` | open the port in the browser / the worktree's PR |
| `x` | kill the process listening on the port (asks `y/n`) |
| `a` | ports: show everything, including OS services |
| `r` | refresh now |
| `?` | help |
| `q` / `Esc` | quit |

## Install

Requirements: Herdr ≥ 0.9.5, Python ≥ 3.10 available as **`python`** on `PATH`, and `git`. Optional: the [GitHub CLI](https://cli.github.com) (`gh`) for PR numbers.

```sh
herdr plugin install CrisLottz/herdr-dashboard
```

The first run creates a private `.venv` inside the plugin folder; nothing is installed globally. Then add the keys to Herdr's config (`~/.config/herdr/config.toml` on Linux, `%APPDATA%\herdr\config.toml` on Windows) and run `herdr server reload-config`:

```toml
[[keys.command]]
key = "prefix+d"
type = "plugin_action"
command = "herdr-dashboard.toggle-sidebar"
description = "dashboard sidebar"

[[keys.command]]
key = "prefix+m"
type = "plugin_action"
command = "herdr-dashboard.open-system"
description = "system dashboard popup"

[[keys.command]]
key = "prefix+shift+m"
type = "plugin_action"
command = "herdr-dashboard.toggle-strip"
description = "system strip"
```

Without Herdr, the system views still work in any terminal: `python bootstrap.py system` or `python bootstrap.py strip`.

**After a restart.** Herdr restores the layout but not what ran inside each pane. The plugin's startup hook (`python bootstrap.py restore`) finds the panes labelled `herdr-dashboard` / `herdr-dashboard-strip` and relaunches the view in them; it checks the shell's process tree first, so it never types into a dashboard that is already running.

### Native tab-bar status (no pane at all)

Herdr can run a command every few seconds and show its last line at the right of the tab row. `python bootstrap.py status` prints `CPU 9% 65° · RAM 78% · GPU 1% 48°` for exactly that. Add it under `[ui]`, using the plugin's folder (`herdr plugin list` shows it):

```toml
[ui]
tab_bar_right = [
  { type = "command", command = "python \"/path/to/herdr-dashboard/bootstrap.py\" status", interval_seconds = 5, timeout_seconds = 10 },
  { type = "datetime", format = "%H:%M" },
]
```

Each run takes 1–3 s (Python start-up plus a short wait, because CPU and GPU usage are rates). Herdr runs it in the background without blocking anything.

## Platform support

| | Windows 10/11 | Linux | macOS |
|---|---|---|---|
| Agents, Ports, Worktrees | ✅ | ✅ | ✅ (untested) |
| CPU, RAM, disk I/O, network | ✅ | ✅ | ✅ |
| GPU usage + VRAM | ✅ AMD, Intel, NVIDIA (performance counters) | ✅ AMD (`amdgpu` sysfs), ✅ NVIDIA (`nvidia-smi`), ❌ Intel | ❌ |
| Temperatures | ✅ via LibreHardwareMonitor | ✅ via `hwmon` (lm_sensors drivers) | ❌ |
| Disk busy % | ✅ | ✅ | ❌ |

Windows is used daily. Linux is tested end to end in CI and in a container: venv bootstrap, every collector against a fake Herdr, the sidebar in a real pty with `termios` keyboard input, and the GPU/temperature readers against a fake `/sys` tree.

### Linux, step by step

1. **Python, venv support and git.** The plugin calls `python`, so make sure that name exists:

   | Distro | Command |
   |---|---|
   | Arch, CachyOS, Manjaro | `sudo pacman -S --needed python git` (`python` is Python 3) |
   | Debian, Ubuntu, Mint | `sudo apt install python3 python3-venv python-is-python3 git` |
   | Fedora | `sudo dnf install python3 git` (`python` is Python 3) |
   | openSUSE | `sudo zypper install python3 git` and `sudo ln -s /usr/bin/python3 /usr/local/bin/python` |

   Check with `python --version`.

2. **Install the plugin and the keys** as described in [Install](#install).

3. **GPU:**
   - **AMD (amdgpu)** — nothing to do; the kernel driver exposes `gpu_busy_percent` and VRAM in `/sys/class/drm/card*/device/`.
   - **NVIDIA** — use the proprietary driver, which ships `nvidia-smi`. Nouveau has no usage counter.
   - **Intel** — usage needs root (`intel_gpu_top`), so the GPU rows are hidden.

4. **Temperatures:** most kernels load the sensor drivers on their own (`coretemp` for Intel, `k10temp` for AMD, `amdgpu`, `nvme`). Check with `ls /sys/class/hwmon/*/name`. If it lists nothing useful:
   ```sh
   sudo pacman -S lm_sensors      # or: sudo apt install lm-sensors
   sudo sensors-detect --auto
   sudo systemctl restart systemd-modules-load
   ```

5. **Ports:** without root, Linux hides the PID of sockets owned by other users (system services, Docker), so they show as `pid ?`. Ports of your own processes — everything started from Herdr panes — always show with their pane.

6. **Try it:** press `prefix+d`, or run `python bootstrap.py sidebar` inside a Herdr pane.

**Troubleshooting**
- *"could not prepare the venv"* — install `python3-venv` (Debian/Ubuntu), then `rm -rf .venv` in the plugin folder.
- *No GPU row* — check `cat /sys/class/drm/card*/device/gpu_busy_percent` (AMD) or `nvidia-smi` (NVIDIA).
- *Override the sysfs root* (for testing): `HDASH_SYSFS=/path/to/fake/sys`.

### Windows temperatures

Windows does not expose temperatures to normal programs — reading them needs a kernel driver. herdr-dashboard reads them from LibreHardwareMonitor's local web server instead:

1. `winget install LibreHardwareMonitor.LibreHardwareMonitor` — a portable package: no installer, no Start Menu entry; the exe lands in `%LOCALAPPDATA%\Microsoft\WinGet\Packages\LibreHardwareMonitor.LibreHardwareMonitor_*\`.
2. Run it as administrator → **Options → Remote Web Server → Run** (port 8085).
3. **Options → Run On Windows Startup**, **Start Minimized** and **Minimize To Tray**. It stays in the tray as a background process (~100 MB RAM).

Without it, everything else works and temperatures are hidden. `HDASH_LHM_URL` points to another port.

## How it works

```
bootstrap.py           every command enters here: creates/updates .venv, then runs `python -m hdash`
hdash/
  __main__.py          commands: sidebar, system, strip, status, restore, toggle-sidebar, toggle-strip, open-popup
  herdr.py             Herdr CLI wrapper and the toggles that split/close our panes
  collect.py           background collectors: Agents, Ports, Worktrees
  sidebar.py           the sidebar: sections, selection, details, keys
  views.py             system popup and strip
  ui.py / keys.py      ANSI drawing, the app loop, Windows + POSIX keyboard input
  system/              psutil sampling + one backend per OS (windows.py, linux.py, generic.py)
tests/                 unit tests, including the Linux backend on a fake /sys
```

- **One argv for every OS.** Herdr manifests reject duplicate action ids, so there is no "use the `.cmd` on Windows and the shell script on Linux". Every manifest command is `python bootstrap.py <command>`; `bootstrap.py` only uses the standard library.
- **Collectors never block the UI.** Each runs on its own thread at its own pace (agents 2 s, ports 3 s, worktrees 10 s, PRs 60 s) and publishes a snapshot. A failing source shows its error in its section; the others keep going.
- **Agents → processes.** `herdr pane process-info` gives each pane's shell PID; psutil walks its descendants. The same map tells Ports which pane owns a socket.
- **Worktrees.** Repos are found from the panes' folders (`git rev-parse --git-common-dir` groups a repo with its linked worktrees); add others with `HDASH_REPOS` (`os.pathsep`-separated).
- **Sidebar placement.** Herdr only splits relative to a pane (right or down), not the whole tab, so the toggle looks for a pane touching the right edge at full height; if there is none, it uses the top-right pane. Plugins cannot draw inside Herdr's own sidebar yet (plugin API v1).
- **Rendering.** Each frame moves the cursor home (`ESC[H`) and overwrites every line (`ESC[K`); the last column stays empty because writing into it makes the Windows console wrap.

## Development

```sh
python bootstrap.py --install
.venv/bin/python -m unittest discover -s tests -t .        # Windows: .venv\Scripts\python.exe
```

To support another OS's GPU or temperatures, add `hdash/system/<os>.py` with the four methods described in `hdash/system/__init__.py`.

## License

MIT

---

## Español

herdr-dashboard es una barra lateral en vivo para [Herdr](https://herdr.dev). Te muestra cuánto le cuesta cada agente a tu máquina, qué servidores de desarrollo están escuchando y en qué worktrees de git se está trabajando, sin salir de la terminal.

- **Agents:** cada pane de Herdr con el CPU y la RAM de todo su árbol de procesos (el shell y todo lo que lanzó), su estado (trabajando, inactivo, bloqueado, terminado) y sus procesos más pesados.
- **Ports:** los puertos TCP en `LISTEN` con su proceso y **el pane al que pertenecen**, así `:8000` te dice de qué worktree es ese Django. Las bases de datos y los puertos de desarrollo comunes (MySQL, Postgres, Redis, Vite…) aparecen aunque corran como servicio.
- **Worktrees:** cada worktree de cada repo que tenga un pane abierto adentro. Muestra la rama, los cambios sin commitear (`✎`), los commits adelante o atrás (`↑↓`), el último commit y el PR abierto (`#41`, vía GitHub CLI). Un punto animado indica que hay un agente trabajando ahí.
- **System:** CPU, RAM, GPU y VRAM con temperaturas, más un popup completo con historial, barras por hilo, E/S de disco, red y unidades.

Está escrito en ANSI a mano y se maneja solo con teclado, sin framework de TUI. Es Python con una sola dependencia: `psutil`, más `pywin32` en Windows.

### Vistas y teclas

| Vista | Tecla | Espacio |
|---|---|---|
| **Barra lateral:** Agents, Ports, Worktrees, System | `prefix+d` (otra vez para cerrar) | una columna de 38 caracteres en el borde derecho |
| **Popup de sistema** | `prefix+m`; `q` o `Esc` lo cierra | ninguno |
| **Tira:** el sistema en una línea | `prefix+shift+m` (otra vez para cerrar) | 2–3 filas bajo el pane enfocado |

Dentro de la barra (primero enfoca su pane):

| Tecla | Acción |
|---|---|
| `Tab` / `Shift+Tab` | siguiente o anterior sección |
| `↑` `↓` o `j` `k` | moverse; la fila seleccionada despliega sus detalles |
| `Enter` | ir al pane: el agente, el dueño del puerto o un agente de ese worktree |
| `o` | abrir el puerto en el navegador o el PR del worktree |
| `x` | matar el proceso que escucha en el puerto (pide confirmación con `y/n`) |
| `a` | puertos: mostrar todos, incluidos los servicios del sistema |
| `r` | refrescar ya |
| `?` | ayuda |
| `q` / `Esc` | salir |

### Instalación

Requisitos: Herdr 0.9.5 o superior, Python 3.10 o superior disponible como **`python`** en el `PATH`, y `git`. Opcional: [GitHub CLI](https://cli.github.com) (`gh`) para ver los números de PR.

Instálalo con `herdr plugin install CrisLottz/herdr-dashboard`. La primera vez se crea un `.venv` privado dentro de la carpeta del plugin; no se instala nada global. Después copia los tres bloques `[[keys.command]]` de la sección [Install](#install) en la config de Herdr (`~/.config/herdr/config.toml` en Linux, `%APPDATA%\herdr\config.toml` en Windows) y ejecuta `herdr server reload-config`.

Sin Herdr, las vistas de sistema funcionan en cualquier terminal: `python bootstrap.py system` o `python bootstrap.py strip`.

**Después de reiniciar:** Herdr restaura el layout, pero no lo que corría dentro de cada pane. El hook de arranque del plugin (`python bootstrap.py restore`) busca los panes marcados `herdr-dashboard` y vuelve a lanzar la vista en ellos. Antes revisa el árbol de procesos del shell, así que nunca escribe dentro de un dashboard que ya está corriendo.

**Estado nativo en la barra de pestañas:** Herdr puede correr un comando cada pocos segundos y mostrar su última línea a la derecha de las pestañas. `python bootstrap.py status` imprime justo eso: `CPU 9% 65° · RAM 78% · GPU 1% 48°`, sin ocupar ningún pane. El bloque `tab_bar_right` está en la sección [Native tab-bar status](#native-tab-bar-status-no-pane-at-all).

### Linux, paso a paso

1. **Python, soporte de venv y git.** El plugin llama a `python`, así que ese nombre tiene que existir:
   - Arch, CachyOS, Manjaro: `sudo pacman -S --needed python git`.
   - Debian, Ubuntu, Mint: `sudo apt install python3 python3-venv python-is-python3 git`.
   - Fedora: `sudo dnf install python3 git`.
   - openSUSE: `sudo zypper install python3 git` y luego `sudo ln -s /usr/bin/python3 /usr/local/bin/python`.

   Compruébalo con `python --version`.
2. **Instala el plugin y los atajos** como en [Install](#install).
3. **GPU:**
   - AMD: no hace falta nada.
   - NVIDIA: usa el driver propietario, que trae `nvidia-smi`.
   - Intel: el uso de la GPU necesita root, así que esas filas se ocultan.
4. **Temperaturas:** casi siempre el kernel carga solo los drivers de sensores. Compruébalo con `ls /sys/class/hwmon/*/name`. Si no aparece nada útil, instala `lm_sensors` y ejecuta `sudo sensors-detect --auto`.
5. **Puertos:** sin root, Linux oculta el PID de los sockets de otros usuarios (servicios del sistema, Docker), así que aparecen como `pid ?`. Los puertos de tus propios procesos, que es todo lo que lanzas desde Herdr, siempre aparecen con su pane.
6. **Pruébalo:** `prefix+d`, o ejecuta `python bootstrap.py sidebar` dentro de un pane de Herdr.

Si sale *"could not prepare the venv"*, instala `python3-venv` y borra la carpeta `.venv` del plugin.

### Temperaturas en Windows

Windows no deja que un programa normal lea las temperaturas. Instala LibreHardwareMonitor (`winget install LibreHardwareMonitor.LibreHardwareMonitor`), ábrelo como administrador y activa:
- **Options → Remote Web Server → Run**
- **Run On Windows Startup**
- **Start Minimized** y **Minimize To Tray**

Queda en la bandeja como proceso de fondo y usa unos 100 MB de RAM. Sin él, todo lo demás funciona y las temperaturas se ocultan.

### Cómo funciona

- **Mismo comando en todos los sistemas:** cada comando del manifiesto es `python bootstrap.py <comando>`, porque Herdr no permite ids de acción duplicados por sistema operativo.
- **Los colectores nunca bloquean la interfaz:** cada uno corre en su propio hilo, a su propio ritmo, y publica una foto de sus datos. Si una fuente falla, muestra su error en su sección y las demás siguen funcionando.
- **Del pane a sus procesos:** `herdr pane process-info` da el PID del shell de cada pane y psutil recorre todos sus descendientes. Ese mismo mapa le dice a Ports a qué pane pertenece cada socket.
- **Posición de la barra:** Herdr solo divide respecto de un pane, no de toda la pestaña. Por eso el atajo busca un pane pegado al borde derecho que ocupe todo el alto. Los plugins todavía no pueden dibujar dentro de la barra nativa de Herdr.

Licencia: MIT.
