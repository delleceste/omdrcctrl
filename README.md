# arkictrl

A lightweight web-based remote control panel for a Linux or FreeBSD desktop.
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
- **Dynamic details** — a READ widget can expose a **Details** button that
  appears automatically when a Markdown file is found under
  `{details_root}/{output}/README.md` (or `INDEX.md`). The file and any
  relative images are served on demand, so each configuration value can have
  its own documentation page without any static wiring.
- **Static details** — a WRITE widget can link to a fixed Markdown file;
  the Details button appears when a nominated systemctl unit is active.
- **Dark, touch-friendly UI** — works well on a phone home screen without
  installing any app.
- **No hard-coded commands** — everything lives in `commands.conf`; restart
  the service to pick up changes.
- **CMake install** — single `cmake --install` copies all files and installs
  the matching service integration: systemd on Linux, rc.d on FreeBSD.

---

## Requirements

| Dependency | Notes |
|---|---|
| Python ≥ 3.9 | `list[dict]` type hints |
| Flask ≥ 2.3 | `pip install flask` |
| Markdown ≥ 3.5 | `pip install markdown` — renders details pages |
| CMake ≥ 3.16 | build / install only |
| systemd or rc.d | service management: systemd on Linux, rc.d on FreeBSD |

---

## Project layout

```
arkictrl/
├── CMakeLists.txt
├── README.md
├── requirements.txt
├── src/
│   ├── app.py               # Flask application
│   ├── commands.conf        # command definitions (edit this)
│   ├── arkictrl.sh.in       # launcher script template
│   └── templates/
│       ├── index.html       # Jinja2 + vanilla-JS control panel
│       └── details.html     # markdown details page
├── rc.d/
│   └── arkictrl.in          # FreeBSD rc.d script template
└── systemd/
    ├── arkictrl.service.in       # Linux system service template
    └── arkictrl-user.service.in  # Linux user service template
```

---

## Build and install

```bash
# 1. install Python dependencies
pip install flask markdown

# 2. configure
mkdir build && cd build

# system-wide install (default prefix /usr/local, requires sudo for install)
cmake ..

# Linux only: user install with systemd --user (no root required, prefix defaults to ~/.local)
cmake .. -DUSER_INSTALL=ON

# override the prefix explicitly if needed
cmake .. -DUSER_INSTALL=ON -DCMAKE_INSTALL_PREFIX=~/.local

# system services run as the configuring user by default; override if needed
cmake .. -DARKICTRL_SERVICE_USER=myuser

# 3. install
sudo cmake --install .        # system install
cmake --install .             # Linux user install (no sudo)
```

On Linux, CMake installs systemd units. On FreeBSD, CMake installs an rc.d
script and rejects `-DUSER_INSTALL=ON` because systemd user services are not
available there.

### Linux system install paths (prefix `/usr/local`)

| Path | Contents |
|---|---|
| `/usr/local/bin/arkictrl` | launcher shell script |
| `/usr/local/lib/arkictrl/app.py` | Flask application |
| `/usr/local/lib/arkictrl/README.md` | this file (served at `/readme`) |
| `/usr/local/lib/arkictrl/templates/` | HTML templates |
| `/usr/local/etc/arkictrl/commands.conf` | command definitions |
| `/usr/local/lib/systemd/system/arkictrl.service` | systemd system unit |

### Linux user install paths (prefix `~/.local`)

| Path | Contents |
|---|---|
| `~/.local/bin/arkictrl` | launcher shell script |
| `~/.local/lib/arkictrl/app.py` | Flask application |
| `~/.local/lib/arkictrl/README.md` | this file (served at `/readme`) |
| `~/.local/lib/arkictrl/templates/` | HTML templates |
| `~/.local/etc/arkictrl/commands.conf` | command definitions |
| `~/.local/share/systemd/user/arkictrl.service` | systemd user unit |

### FreeBSD system install paths (prefix `/usr/local`)

| Path | Contents |
|---|---|
| `/usr/local/bin/arkictrl` | launcher shell script |
| `/usr/local/lib/arkictrl/app.py` | Flask application |
| `/usr/local/lib/arkictrl/README.md` | this file (served at `/readme`) |
| `/usr/local/lib/arkictrl/templates/` | HTML templates |
| `/usr/local/etc/arkictrl/commands.conf` | command definitions |
| `/usr/local/etc/rc.d/arkictrl` | FreeBSD rc.d service script |

---

## Running as a service

### Linux systemd system service

The service runs as `ARKICTRL_SERVICE_USER`, which defaults to the user that
configured the CMake build. Override it during configure when needed:

```bash
cmake .. -DARKICTRL_SERVICE_USER=myuser
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now arkictrl
sudo systemctl status arkictrl
```

### Linux systemd user service (`-DUSER_INSTALL=ON`)

No root required. The service runs as your own user automatically.

```bash
systemctl --user daemon-reload
systemctl --user enable --now arkictrl
systemctl --user status arkictrl
```

To keep the service running after logout (e.g. on a headless machine), enable linger once:

```bash
loginctl enable-linger $USER
```

### Restarting after config changes

```bash
sudo systemctl restart arkictrl          # system
systemctl --user restart arkictrl        # user
```

### FreeBSD rc.d service

The rc.d script uses `daemon(8)` and is installed to
`/usr/local/etc/rc.d/arkictrl`.

```bash
sudo sysrc arkictrl_enable=YES
sudo service arkictrl start
sudo service arkictrl status
```

The service user defaults to `ARKICTRL_SERVICE_USER`, which is set when CMake is
configured. The following rc.conf variables can be overridden with `sysrc`:

| Variable | Default | Purpose |
| --- | --- | --- |
| `arkictrl_user` | `ARKICTRL_SERVICE_USER` (set at CMake time) | User the daemon runs as. Only applied when the service is started as root, since `daemon -u` requires superuser. |
| `arkictrl_env` | `DISPLAY=:0` | Environment passed to the daemon, e.g. for X11 access. |
| `arkictrl_pidfile` | `/var/run/arkictrl/arkictrl.pid` (root) or `${TMPDIR:-/tmp}/arkictrl-<user>.pid` (unprivileged) | Location of the pidfile. See note below. |
| `arkictrl_logfile` | `/var/run/arkictrl/arkictrl.log` (root) or `${TMPDIR:-/tmp}/arkictrl-<user>.log` (unprivileged) | Captures the app's stdout/stderr (`daemon -o`); check here if the service starts but no process runs. |

```bash
sudo sysrc arkictrl_user=myuser
sudo sysrc arkictrl_env='DISPLAY=:0'
sudo sysrc arkictrl_pidfile=/var/run/arkictrl/arkictrl.pid
```

> **Why the pidfile lives in a subdirectory:** `daemon(8)` drops to
> `arkictrl_user` *before* writing the `-p` pidfile, so the pidfile's directory
> must be writable by that user. `/var/run` itself is root-only, which is why a
> plain `/var/run/arkictrl.pid` fails with *permission denied* even under
> `sudo`. The script's `start_precmd` creates `/var/run/arkictrl` owned by
> `arkictrl_user` so the unprivileged daemon can write the pidfile there.

#### Running without root

`/var/run` is only writable by root and `daemon -u` needs superuser, so when the
service is started by an unprivileged user the script automatically drops `-u`
and defaults the pidfile to `${TMPDIR:-/tmp}/arkictrl-<user>.pid`. Use
`onestart` to bypass the `arkictrl_enable` rcvar check:

```bash
/usr/local/etc/rc.d/arkictrl onestart
```

Restart after config changes:

```bash
sudo service arkictrl restart
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
be unique and contain no spaces.

### Reserved section: `[qconnect]`

`[qconnect]` is not a command — it configures the Qobuz Connect integration.
Both keys are optional; the defaults match the qobuzconnect2mpd defaults.

```ini
[qconnect]
status_file = /tmp/qconnect2mpd-status.txt
log_file    = /tmp/qconnect2mpd.log
```

These paths are consumed by the `/qconnect/status` and `/qconnect/log` API
endpoints.  Change them only if you set non-default paths in qobuzconnect2mpd's
config (`qconnectstatusfile` / `qconnectlogfile`).

### Keys common to all commands

| Key | Required | Description |
|---|---|---|
| `what` | yes | Label text shown in the UI |
| `group` | yes | Card/section name (`drc`, `apps`, `system`, or any custom name) |
| `type` | yes | `READ`, `WRITE`, or `LINK` |
| `cmd` | READ / WRITE | Shell command to execute |

### WRITE-only keys

| Key | Required | Description |
|---|---|---|
| `button` | yes | Text on the action button |
| `confirm` | no | `yes` — show a confirmation dialog before running (default: `no`) |

### READ-only keys

| Key | Required | Description |
|---|---|---|
| `refresh` | no | Auto-refresh interval in seconds; `0` or omitted = manual only |
| `details_root` | no | Root directory for dynamic details lookup (see below) |

### LINK-only keys

| Key | Required | Description |
|---|---|---|
| `url` | yes | URL opened in a new tab when the user taps **Open ↗** |

### Optional detail/status keys (READ or WRITE)

Any command can expose a **Details** button. Three mutually exclusive approaches:

| Key | Description |
|---|---|
| `process` | Process name checked with `pgrep -x`; Details button appears when the process is running. Prefer over `unit` for portable configs and to avoid systemd side-effects. |
| `unit` | Linux/systemd unit name; Details button appears when `systemctl is-active <unit>` exits 0. |
| `details_link` | External URL for a Details button that is *always* visible (not conditional on process/unit). |
| `details` | Absolute path to a `.md` file rendered on the details page (required with `process` or `unit`). |

`/status` is polled every 5 seconds for all commands that carry `process` or
`unit` together with `details`.

### Dynamic details (READ commands)

A READ command with `details_root` gets a **Details** button that appears
whenever the command's current output matches a directory containing
`README.md` or `INDEX.md` inside the root:

```
{details_root}/{output}/README.md   ← checked first
{details_root}/{output}/INDEX.md    ← fallback
```

Every time `/read/<id>` is called, the server checks whether the file exists
and includes `details_url` in the JSON response if it does. The frontend shows
or hides the button accordingly.

Example — DRC active-config status widget:

```ini
[drc_status]
what         = Active config
group        = drc
type         = READ
refresh      = 5
cmd          = ps -C brutefir -o args= 2>/dev/null \
               | sed -n 's|.*brutefir-\([^ ]*\)\.conf.*|\1|p' \
               | grep . || echo off
details_root = /home/giacomo/DRC
```

When brutefir is running with `brutefir-120.blue+0dB.conf` the command
outputs `120.blue+0dB`. The server then looks for
`/home/giacomo/DRC/120.blue+0dB/README.md`. If found, the Details button
appears and opens that file rendered as HTML.

Markdown files may use **relative paths** for images and links; they are served
from the same directory as the `.md` file via
`/details-dyn-asset/<id>/<config>/<path>`. Example layout:

```
/home/giacomo/DRC/
├── 120.blue+0dB/
│   ├── README.md
│   └── img/
│       └── freq_response.png
└── 120.blue+2dB/
    ├── README.md
    └── img/
        └── freq_response.png
```

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

All responses are JSON unless noted.

### `GET /`

Returns the rendered HTML control panel.

---

### `POST /run/<id>`

Execute a WRITE command.

```json
{ "ok": true }
{ "ok": false, "error": "stderr output or description" }
```

---

### `GET /read/<id>`

Execute a READ command and return its output. When the command has
`details_root` set and a matching Markdown file is found, `details_url` is
included.

```json
{ "ok": true,  "output": "120.blue+0dB", "details_url": "/details-dyn/drc_status/120.blue+0dB" }
{ "ok": true,  "output": "off" }
{ "ok": false, "output": "command not found: sensors" }
```

---

### `GET /status`

Server health check and status query. For every command that has both
`process` and `details` configured the server checks `pgrep -x <process>`.
For Linux/systemd commands that have both `unit` and `details` configured, the
server runs `systemctl is-active --quiet <unit>`. The browser polls this every
5 seconds.

```json
{ "ok": true, "units": { "drc_flat": "active", "drc_2db": "inactive" } }
```

---

### `GET /details/<id>`

Renders the static `details` Markdown file for command `<id>` as HTML.
Returns `404` if the command has no `details` key or the file is missing.

---

### `GET /details-asset/<id>/<path>`

Serves files relative to the static `details` Markdown file directory.

---

### `GET /details-dyn/<id>/<config>`

Renders `{details_root}/{config}/README.md` (or `INDEX.md`) as HTML for
a READ command with `details_root`. Returns `404` if no file is found.

---

### `GET /details-dyn-asset/<id>/<config>/<path>`

Serves files relative to the dynamic Markdown file directory (images, etc.).

---

### `GET /readme`

Renders this README as an HTML page.

---

### `GET /qconnect/status`

Reads the qobuzconnect2mpd status file and returns the two display lines.

```json
{ "ok": true, "line1": "[playing] Artist - Title  [1:23 / 4:56]", "line2": "FLAC 16 bit 44.1 kHz" }
{ "ok": false, "line1": "", "line2": "" }
```

---

### `GET /qconnect/log`

Returns the full content of the qobuzconnect2mpd log file as a string.

```json
{ "ok": true, "content": "2026-05-15 14:32:01 [OUT] ..." }
```

---

### `POST /qconnect/restart`

Restarts the qobuzconnect2mpd user service via
`systemctl --user restart qobuzconnect2mpd`. This endpoint is Linux/systemd
specific; on FreeBSD, use regular command widgets for service actions.

```json
{ "ok": true }
{ "ok": false, "error": "..." }
```

---

### `GET /brutefir/cpu`

Returns per-process CPU usage for all running `brutefir` instances, plus the
sum. Uses `ps -C brutefir -o pid,pcpu`.

```json
{
  "ok": true,
  "procs": [
    { "pid": "12345", "cpu": 24.5 },
    { "pid": "12346", "cpu": 23.8 }
  ],
  "total": 48.3
}
```

`procs` is empty (not an error) when brutefir is not running.

---

## Built-in monitoring panels

In addition to the configurable command cards, two fixed panels always appear
at the bottom of the page.

### Qobuz Connect

Shows the track currently playing via qobuzconnect2mpd, updated every second:

- Line 1: playback state + artist/title + position/duration
  (`[playing] Artist - Title  [1:23 / 4:56]`)
- Line 2: audio format (`FLAC 24 bit, stereo, 96.0 kHz`)

Two buttons in the panel header:
- **Restart** — calls `POST /qconnect/restart`; shows a toast on success/failure
- **Log** — toggles a scrollable log viewer (auto-refreshed every 5 s while
  open) with colour-coded lines: red for `[ERR]`, green for `[OUT]`

File paths are configured via the `[qconnect]` section in `commands.conf`.

### Brutefir CPU

Shows per-process CPU usage for every running `brutefir` process, refreshed
every 5 seconds. When more than one process is detected (brutefir typically
spawns four worker processes) a highlighted **Total** line is appended.
Displays "not running" when brutefir is not active.

### Top CPU

Shows processes above `topcpu_threshold`, refreshed every `topcpu_interval`
seconds from the `[monitor]` section of `commands.conf`. The server caches this
result for the same interval so multiple browser clients do not run extra `ps`
commands.

---

## Security note

The server executes arbitrary shell commands from `commands.conf` as the
service user. It should only be exposed on a trusted local network.
Do not bind it to a public interface or use it without a firewall.
The `--host` argument defaults to `0.0.0.0` (all interfaces); pass
`--host 127.0.0.1` if you want to restrict it to localhost and proxy
through nginx or similar.
