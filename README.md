# arkictrl

A lightweight web-based remote control panel for a Linux desktop.
Commands are defined in a plain-text INI config file; the server renders a
mobile-friendly interface that can be opened in any browser on the local
network — phone, tablet, or another computer.

Originally a replacement for KDE Connect's "Run command" plugin, which
provides no feedback. Every command here either shows its output directly
(READ) or gives a clear visual confirmation on the button (WRITE).

---

## Features

- **READ widgets** — run a shell command and display its output next to a
  label; optional auto-refresh on a configurable interval.
- **WRITE widgets** — a labelled button that fires a command; button turns
  green on success, red on failure, with an optional confirmation dialog for
  destructive actions.
- **Dark, touch-friendly UI** — works well on a phone home screen without
  installing any app.
- **No hard-coded commands** — everything lives in `commands.conf`; restart
  the service to pick up changes.
- **CMake install** — single `cmake --install` copies all files and writes a
  ready-to-use systemd unit.

---

## Requirements

| Dependency | Notes |
|---|---|
| Python ≥ 3.9 | `list[dict]` type hints |
| Flask ≥ 2.3 | `pip install flask` |
| Markdown ≥ 3.5 | `pip install markdown` — renders details pages |
| CMake ≥ 3.16 | build / install only |
| systemd | service management |

---

## Project layout

```
arkictrl/
├── CMakeLists.txt
├── requirements.txt
├── src/
│   ├── app.py               # Flask application
│   ├── commands.conf        # command definitions (edit this)
│   ├── arkictrl.sh.in       # launcher script template
│   └── templates/
│       ├── index.html       # Jinja2 + vanilla-JS control panel
│       └── details.html     # markdown details page
└── systemd/
    └── arkictrl.service.in  # systemd unit template
```

---

## Build and install

```bash
# 1. install the Python dependency
pip install flask

# 2. configure (default prefix: /usr/local)
mkdir build && cd build
cmake ..

# to install somewhere else:
cmake .. -DCMAKE_INSTALL_PREFIX=/opt/arkictrl

# 3. install (writes files under the chosen prefix)
sudo cmake --install .
```

Installed paths (with the default `/usr/local` prefix):

| Path | Contents |
|---|---|
| `/usr/local/bin/arkictrl` | launcher shell script |
| `/usr/local/lib/arkictrl/app.py` | Flask application |
| `/usr/local/lib/arkictrl/templates/` | HTML template |
| `/usr/local/etc/arkictrl/commands.conf` | command definitions |
| `/usr/local/lib/systemd/system/arkictrl.service` | systemd unit |

---

## Running as a systemd service

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now arkictrl

# check that it started
sudo systemctl status arkictrl
```

The service runs as user `giacomo` (edit `systemd/arkictrl.service.in` before
installing to change this).

To restart after editing `commands.conf`:

```bash
sudo systemctl restart arkictrl
```

---

## Running manually (development)

```bash
cd src
python3 app.py                        # listens on 0.0.0.0:8080
python3 app.py --port 9090            # different port
python3 app.py --config /path/to/commands.conf
```

Open `http://<hostname>:8080` in a browser.

---

## Configuring commands

All commands are defined in `commands.conf` (INI format, parsed by Python's
`configparser`). Lines starting with `#` are comments.

Each `[section]` is one command. The section name is the internal id and must
be unique, contain no spaces.

### Details page

Any command (READ or WRITE) can expose a **Details** button that opens a
full-screen page rendering a Markdown file.  Two config keys control this:

| Key | Description |
|---|---|
| `unit` | systemctl unit name, checked with `systemctl is-active --quiet <unit>` |
| `details` | absolute path to a `.md` file |

Both keys must be present together.  The Details button is hidden while the
unit is inactive and appears automatically (within 5 s) when it becomes active.
`/status` is polled every 5 seconds for all commands that carry both keys.

The Markdown file may contain text, headings, tables, code blocks, and images.
Images should use **relative paths**; they are served from the same directory
as the `.md` file via `/details-asset/<id>/<path>`.  Example structure:

```
/home/giacomo/DRC/brutefir-conf/docs/
├── 120.blue+0dB.md
├── 120.blue+2dB.md
└── img/
    ├── freq_response_flat.png
    └── freq_response_2dB.png
```

Inside `120.blue+0dB.md`:
```markdown
# DRC Flat profile

Measured with miniDSP UMIK-1 at 1 m, 0 dB target.

![Frequency response](img/freq_response_flat.png)
*After correction — ±1.5 dB 80 Hz – 16 kHz*
```

---

### Keys common to all commands

| Key | Required | Description |
|---|---|---|
| `what` | yes | Label text shown in the UI |
| `group` | yes | Card/section name (`drc`, `apps`, `system`, or any custom name) |
| `type` | yes | `READ` or `WRITE` |
| `cmd` | yes | Shell command to execute |

### WRITE-only keys

| Key | Required | Description |
|---|---|---|
| `button` | yes | Text on the action button |
| `confirm` | no | `yes` — show a confirmation dialog before running (default: `no`) |

### READ-only keys

| Key | Required | Description |
|---|---|---|
| `refresh` | no | Auto-refresh interval in seconds; `0` or omitted = manual only |

### Optional keys (READ or WRITE)

| Key | Description |
|---|---|
| `unit` | systemctl unit name — Details button appears when `systemctl is-active <unit>` exits 0 |
| `details` | absolute path to a `.md` file rendered on the details page |

Both must be present together; specifying only one has no effect.

### Widget behaviour

**WRITE** — clicking the button fires the command via `POST /run/<id>`.
The server uses `Popen` and waits up to 5 seconds for the process to exit.
If it exits within that window the button turns green (success) or red
(failure, stderr shown in a toast). If it does not exit within 5 seconds
(e.g. a GUI app that keeps running) it is assumed to have launched
successfully and the button turns green.

**READ** — on page load the UI calls `GET /read/<id>`, runs the command
synchronously (10-second timeout), and displays the combined stdout+stderr
next to the label. The `↻` button triggers a manual re-fetch. With
`refresh = N` the output is also polled automatically every N seconds.

### Group ordering

The groups `drc`, `apps`, and `system` always appear in that order.
Any additional group names appear after them in the order they are first
encountered in the config file.

### Example: adding a READ status widget

```ini
[cpu_temp]
what    = CPU temperature
group   = system
type    = READ
refresh = 10
cmd     = sensors | awk '/Core 0/{print $3}'
```

```ini
[brutefir_status]
what    = Brutefir running
group   = drc
type    = READ
refresh = 5
cmd     = pgrep -x brutefir > /dev/null && echo yes || echo no
```

### Example: a custom app launcher

```ini
[vlc]
what   = VLC media player
group  = apps
type   = WRITE
button = Launch
cmd    = vlc
```

### Example: a destructive action with confirmation

```ini
[stop_jack]
what    = Stop JACK audio server
group   = system
type    = WRITE
button  = Stop
confirm = yes
cmd     = systemctl --user stop jack
```

---

## HTTP API

The server exposes three endpoints; all responses are JSON.

### `GET /`

Returns the rendered HTML page.

---

### `POST /run/<id>`

Execute a WRITE command.

**Response**

```json
{ "ok": true }
```

```json
{ "ok": false, "error": "stderr output or description" }
```

---

### `GET /read/<id>`

Execute a READ command and return its output.

**Response**

```json
{ "ok": true,  "output": "56.2°C" }
```

```json
{ "ok": false, "output": "command not found: sensors" }
```

---

### `GET /status`

Server health check and unit-active query.  For every command that has both
`unit` and `details` configured the server runs `systemctl is-active --quiet
<unit>` and includes the result.  The browser polls this endpoint every 5
seconds and shows or hides each Details button accordingly.

**Response**

```json
{
  "ok": true,
  "units": {
    "drc_flat": "active",
    "drc_2db":  "inactive",
    "drc_sox":  "inactive",
    "drc_off":  "inactive"
  }
}
```

---

### `GET /details/<id>`

Renders the `details` Markdown file for command `<id>` as a full HTML page.
Returns `404` if the command has no `details` key or the file is missing.

---

### `GET /details-asset/<id>/<path>`

Serves files (images, etc.) from the same directory as the `details` Markdown
file, allowing relative image references inside the `.md` to resolve correctly.
`<path>` may include subdirectory components (e.g. `img/freq_response.png`).

---

## Security note

The server executes arbitrary shell commands from `commands.conf` as the
service user. It should only be exposed on a trusted local network.
Do not bind it to a public interface or use it without a firewall.
The `--host` argument defaults to `0.0.0.0` (all interfaces); pass
`--host 127.0.0.1` if you want to restrict it to localhost and proxy
through nginx or similar.
