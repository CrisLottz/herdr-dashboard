# pcmon

[English](#pcmon) · [Español](#español)

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

Hand-written ANSI rendering (no TUI framework), ~420 lines of commented Python, two dependencies: `psutil` and `pywin32`.

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

1. `winget install LibreHardwareMonitor.LibreHardwareMonitor` — a portable package: no installer, no Start Menu entry. The exe lands in `%LOCALAPPDATA%\Microsoft\WinGet\Packages\LibreHardwareMonitor.LibreHardwareMonitor_*\`.
2. Run it as administrator → **Options → Remote Web Server → Run** (port 8085).
3. **Options → Run On Windows Startup**, so temperatures survive a reboot; also **Start Minimized** and **Minimize To Tray** to keep it out of the way. It then lives in the tray as a background process (~100 MB RAM).

pcmon polls `http://localhost:8085/data.json` in a background thread. If LHM is not running, everything else keeps working and the temperatures are simply hidden. Use `PCMON_LHM_URL` to point at another port.

## How it works

- **GPU** — PDH counter `\GPU Engine(*engtype_3D)\Utilization Percentage`, summed over all processes. The `*` wildcard makes PDH pick up new processes on its own.
- **VRAM total** — read from the registry (`HardwareInformation.qwMemorySize`), because WMI's `AdapterRAM` is a 32-bit field capped at 4 GB.
- **Disk busy** — `100 − \PhysicalDisk(_Total)\% Idle Time`.
- **Rendering** — every second the cursor jumps home (`ESC[H`) and each line is overwritten and cleared to the end (`ESC[K`), so there is no flicker. Bars have 1/8-character resolution (`▏▎▍▌▋▊▉█`).
- **Strip toggle** — the strip sets its terminal title to `pcmon-strip`; the action closes a pane with that title in the current tab, or splits the focused pane with a ratio computed to leave the strip a fixed number of rows.

## License

MIT

---

## Español

pcmon es un monitor de recursos en vivo para la terminal de Windows. Está hecho para vivir dentro de [Herdr](https://herdr.dev) sin quitarle espacio a los panes de tus agentes.

- **CPU:** uso total y por hilo, frecuencia y temperatura.
- **RAM** y swap.
- **GPU y VRAM:** uso de la GPU y de su memoria, en tarjetas AMD, Intel o NVIDIA. Se leen de los contadores de rendimiento de Windows, la misma fuente que usa el Administrador de tareas.
- **Disco:** % de ocupación, lectura y escritura en MB/s y espacio usado por unidad.
- **Red:** bajada y subida.
- **Temperaturas** de CPU, GPU y SSD, a través de [LibreHardwareMonitor](https://github.com/LibreHardwareMonitor/LibreHardwareMonitor). Son opcionales.

El dibujo es ANSI escrito a mano, sin framework de TUI: unas 420 líneas de Python comentado y dos dependencias, `psutil` y `pywin32`.

### Vistas

| Vista | Cómo | Espacio que ocupa |
|---|---|---|
| **Popup:** panel completo con barras, historial, barras por hilo y unidades | `prefix+m` en Herdr; `q` o `Esc` lo cierra | ninguno (flota sobre el layout) |
| **Tira:** todo en una línea | `prefix+shift+m` la abre o la cierra bajo el pane enfocado | 3 filas |
| **Independiente:** en cualquier terminal | `pcmon.cmd` o `pcmon.cmd --strip` | — |

Si el pane es angosto, la tira quita segmentos por la derecha en vez de partirse en dos líneas.

### Instalación

Requisitos: Windows 10 u 11 y Python 3.10 o superior (con el lanzador `py`).

**Como plugin de Herdr:** ejecuta `herdr plugin install CrisLottz/pcmon`. Después agrega a `%APPDATA%\herdr\config.toml` los dos bloques `[[keys.command]]` de la sección [Install](#as-a-herdr-plugin) y ejecuta `herdr server reload-config`.

**Sin Herdr:** clona el repo, crea el venv e instala `requirements.txt`, igual que en [Standalone](#standalone). Luego ejecuta `.\pcmon.cmd`.

### Temperaturas

Windows no deja que un programa normal lea las temperaturas: para eso hace falta un driver de kernel. pcmon no trae ninguno, así que las toma del servidor web local de LibreHardwareMonitor.

1. Instálalo con `winget install LibreHardwareMonitor.LibreHardwareMonitor`. Es un paquete portable: no tiene instalador ni aparece en el menú Inicio, y el exe queda en `%LOCALAPPDATA%\Microsoft\WinGet\Packages\LibreHardwareMonitor.LibreHardwareMonitor_*\`.
2. Ábrelo como administrador y activa **Options → Remote Web Server → Run** (puerto 8085).
3. Activa **Options → Run On Windows Startup** para que las temperaturas sigan después de reiniciar. Si además marcas **Start Minimized** y **Minimize To Tray**, queda en la bandeja como proceso de fondo y usa unos 100 MB de RAM.

Si LibreHardwareMonitor no está corriendo, pcmon sigue funcionando y solo oculta las temperaturas. Para usar otro puerto, define `PCMON_LHM_URL`.

### Cómo funciona

- **GPU:** suma el contador PDH `\GPU Engine(*engtype_3D)\Utilization Percentage` de todos los procesos. Gracias al comodín `*`, PDH incluye solo los procesos nuevos.
- **VRAM total:** se lee del registro (`HardwareInformation.qwMemorySize`), porque el campo `AdapterRAM` de WMI es de 32 bits y se queda en 4 GB.
- **Dibujo:** cada segundo el cursor vuelve al inicio y reescribe encima de lo anterior en vez de limpiar la pantalla, por eso no parpadea.
- **Tira:** se identifica por el título de terminal `pcmon-strip`. El atajo la cierra si ya existe en la pestaña; si no, divide el pane enfocado dejándole un alto fijo.

Licencia: MIT.
