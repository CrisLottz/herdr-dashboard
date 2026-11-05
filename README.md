# pcmon

A tiny live system monitor for the Windows terminal, built to live inside [Herdr](https://herdr.dev) without stealing space from your agent panes.

```
 ◒ CPU ▎····   6% 63°C │ RAM ████▍  87% 13.8G │ GPU ·····   1% 50°C │ VRAM █▌···  30% │ DSK ▋····  12% │ ↓9K ↑3K
```

- **CPU** total and per thread, frequency, temperature
- **RAM** and swap
- **GPU** usage and **VRAM** (AMD, Intel or NVIDIA — read from Windows performance counters, the same source as Task Manager)
- **Disk** busy %, read/write MB/s and space per drive
- **Network** down/up
- **Temperatures** for CPU, GPU and SSD through [LibreHardwareMonitor](https://github.com/LibreHardwareMonitor/LibreHardwareMonitor) (optional)

Hand-written ANSI rendering (no TUI framework), ~450 lines of commented Python, two dependencies: `psutil` and `pywin32`.

## Views

| View | How | Space it takes |
|---|---|---|
| **Popup** — full dashboard with bars, sparklines, per-thread bars and drives | `prefix+m` in Herdr, `q`/`Esc` to close | none (floats over the layout) |
| **Strip** — everything on one line | `prefix+shift+m` toggles it under the focused pane | 3 rows |
| **Standalone** — in any terminal | `pcmon.cmd` or `pcmon.cmd --strip` | — |

The strip drops segments from the right when the pane is narrow, so it never wraps.

## Install

Requirements: Windows 10/11, Python 3.10+ (`py` launcher).

### As a Herdr plugin

```powershell
herdr plugin install CrisLottz/pcmon
```

Then add the keybindings to `%APPDATA%\herdr\config.toml` and run `herdr server reload-config`:

```toml
[[keys.command]]
key = "prefix+m"
type = "plugin_action"
command = "pcmon.open"
description = "pcmon popup"

[[keys.command]]
key = "prefix+shift+m"
type = "plugin_action"
command = "pcmon.toggle-strip"
description = "pcmon strip"
```

### Standalone

```powershell
git clone https://github.com/CrisLottz/pcmon
cd pcmon
py -3 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.\pcmon.cmd            # full view
.\pcmon.cmd --strip    # one line
```

## Temperatures

Windows does not expose CPU/GPU temperatures to normal programs — reading them needs a kernel driver. pcmon does not ship one; it reads them from LibreHardwareMonitor's local web server instead:

1. `winget install LibreHardwareMonitor.LibreHardwareMonitor`
2. Run it as administrator → **Options → Remote Web Server → Run** (port 8085).
3. Optional: **Options → Run On Windows Startup**, **Start Minimized** and **Minimize To Tray**.

pcmon polls `http://localhost:8085/data.json` in a background thread. If LHM is not running, everything else keeps working and the temperatures are simply hidden. Use `PCMON_LHM_URL` to point at another port.

## How it works

- **GPU** — PDH counter `\GPU Engine(*engtype_3D)\Utilization Percentage`, summed over all processes. The `*` wildcard makes PDH pick up new processes on its own.
- **VRAM total** — read from the registry (`HardwareInformation.qwMemorySize`), because WMI's `AdapterRAM` is a 32-bit field capped at 4 GB.
- **Disk busy** — `100 − \PhysicalDisk(_Total)\% Idle Time`.
- **Rendering** — every second the cursor jumps home (`ESC[H`) and each line is overwritten and cleared to the end (`ESC[K`), so there is no flicker. Bars have 1/8-character resolution (`▏▎▍▌▋▊▉█`).
- **Strip toggle** — the strip sets its terminal title to `pcmon-strip`; the action closes a pane with that title in the current tab, or splits the focused pane with a ratio computed to leave the strip a fixed number of rows.

Code comments and on-screen labels are in Spanish.

## License

MIT
