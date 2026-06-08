# omdrcctrl

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
- **Audio health monitoring** — purpose-built for a *headless* music server:
  the MPD panel reports the full chain (MPD → BruteFIR → ALSA/virtual_oss →
  DAC), the exact stream the DAC is being fed (ALSA `hw_params`: format, rate,
  channels, period/buffer), a sample-rate match check, and a plain-language
  **bit-perfect / no-resampling verdict** so you can confirm everything is
  correct without a screen attached.
- **DRC filter analysis** — a **Filter response** page renders the live
  frequency-magnitude, phase, and group-delay of the BruteFIR FIR filters
  (`L.raw` / `R.raw`), computed on demand by FFT. See
  [DRC filter response](#drc-filter-response).
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
| NumPy ≥ 1.21 | `pip install numpy` — FFT for the filter-response page. *Optional:* if absent, every other feature works and the filter page simply reports that NumPy is required. |
| CMake ≥ 3.16 | build / install only |
| systemd or rc.d | service management: systemd on Linux, rc.d on FreeBSD |

---

## Project layout

```
omdrcctrl/
├── CMakeLists.txt
├── README.md
├── requirements.txt
├── src/
│   ├── app.py               # Flask application
│   ├── commands.conf        # command definitions (edit this)
│   ├── omdrcctrl.sh.in       # launcher script template
│   ├── templates/
│   │   ├── index.html            # Jinja2 + vanilla-JS control panel
│   │   ├── details.html          # markdown details page
│   │   └── filter_response.html  # DRC filter-response charts page
│   └── static/
│       └── chart.umd.min.js # vendored Chart.js (filter-response charts)
├── rc.d/
│   └── omdrcctrl.in          # FreeBSD rc.d script template
└── systemd/
    ├── omdrcctrl.service.in       # Linux system service template
    └── omdrcctrl-user.service.in  # Linux user service template
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
cmake .. -DOMDRCCTRL_SERVICE_USER=myuser

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
| `/usr/local/bin/omdrcctrl` | launcher shell script |
| `/usr/local/lib/omdrcctrl/app.py` | Flask application |
| `/usr/local/lib/omdrcctrl/README.md` | this file (served at `/readme`) |
| `/usr/local/lib/omdrcctrl/templates/` | HTML templates |
| `/usr/local/lib/omdrcctrl/static/` | vendored Chart.js |
| `/usr/local/etc/omdrcctrl/commands.conf` | command definitions |
| `/usr/local/lib/systemd/system/omdrcctrl.service` | systemd system unit |

### Linux user install paths (prefix `~/.local`)

| Path | Contents |
|---|---|
| `~/.local/bin/omdrcctrl` | launcher shell script |
| `~/.local/lib/omdrcctrl/app.py` | Flask application |
| `~/.local/lib/omdrcctrl/README.md` | this file (served at `/readme`) |
| `~/.local/lib/omdrcctrl/templates/` | HTML templates |
| `~/.local/lib/omdrcctrl/static/` | vendored Chart.js |
| `~/.local/etc/omdrcctrl/commands.conf` | command definitions |
| `~/.local/share/systemd/user/omdrcctrl.service` | systemd user unit |

### FreeBSD system install paths (prefix `/usr/local`)

| Path | Contents |
|---|---|
| `/usr/local/bin/omdrcctrl` | launcher shell script |
| `/usr/local/lib/omdrcctrl/app.py` | Flask application |
| `/usr/local/lib/omdrcctrl/README.md` | this file (served at `/readme`) |
| `/usr/local/lib/omdrcctrl/templates/` | HTML templates |
| `/usr/local/lib/omdrcctrl/static/` | vendored Chart.js |
| `/usr/local/etc/omdrcctrl/commands.conf` | command definitions |
| `/usr/local/etc/rc.d/omdrcctrl` | FreeBSD rc.d service script |

---

## Running as a service

### Linux systemd system service

The service runs as `OMDRCCTRL_SERVICE_USER`, which defaults to the user that
configured the CMake build. Override it during configure when needed:

```bash
cmake .. -DOMDRCCTRL_SERVICE_USER=myuser
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now omdrcctrl
sudo systemctl status omdrcctrl
```

### Linux systemd user service (`-DUSER_INSTALL=ON`)

No root required. The service runs as your own user automatically.

```bash
systemctl --user daemon-reload
systemctl --user enable --now omdrcctrl
systemctl --user status omdrcctrl
```

To keep the service running after logout (e.g. on a headless machine), enable linger once:

```bash
loginctl enable-linger $USER
```

### Restarting after config changes

```bash
sudo systemctl restart omdrcctrl          # system
systemctl --user restart omdrcctrl        # user
```

### FreeBSD rc.d service

The rc.d script uses `daemon(8)` and is installed to
`/usr/local/etc/rc.d/omdrcctrl`.

```bash
sudo sysrc omdrcctrl_enable=YES
sudo service omdrcctrl start
sudo service omdrcctrl status
```

The service user defaults to `OMDRCCTRL_SERVICE_USER`, which is set when CMake is
configured. The following rc.conf variables can be overridden with `sysrc`:

| Variable | Default | Purpose |
| --- | --- | --- |
| `omdrcctrl_user` | `OMDRCCTRL_SERVICE_USER` (set at CMake time) | User the daemon runs as. rc.subr drops privileges to this user via `su(1)` when started as root. |
| `omdrcctrl_env` | `PATH=/usr/local/sbin:/usr/local/bin:/sbin:/bin:/usr/sbin:/usr/bin DISPLAY=:0` | Environment applied via `env(1)`. Includes `/usr/local/{s,}bin` on `PATH` (rc starts with a minimal `PATH`) and `DISPLAY` for X11 access. |
| `omdrcctrl_pidfile` | `/var/run/omdrcctrl/omdrcctrl.pid` (root) or `${TMPDIR:-/tmp}/omdrcctrl-<user>.pid` (unprivileged) | Location of the pidfile. See note below. |
| `omdrcctrl_logfile` | `/var/run/omdrcctrl/omdrcctrl.log` (root) or `${TMPDIR:-/tmp}/omdrcctrl-<user>.log` (unprivileged) | Captures the app's stdout/stderr (`daemon -o`); check here if the service starts but no process runs. |

```bash
sudo sysrc omdrcctrl_user=myuser
sudo sysrc omdrcctrl_env='PATH=/usr/local/bin:/usr/bin:/bin DISPLAY=:0'
sudo sysrc omdrcctrl_pidfile=/var/run/omdrcctrl/omdrcctrl.pid
```

> **Privilege dropping:** `omdrcctrl_user` and `omdrcctrl_env` are the standard
> rc.subr `${name}_user` / `${name}_env` variables — rc.subr drops privileges
> with `su(1)` and applies the environment with `env(1)`. The script does **not**
> pass `daemon -u`; combining `${name}_user` with `daemon -u` runs
> `setusercontext()` a second time as the already-dropped user and fails with
> *"daemon: failed to set user environment"*.

> **Why the pidfile lives in a subdirectory:** the daemon writes its `-p`
> pidfile (and `-o` logfile) *after* rc.subr drops to `omdrcctrl_user`, so their
> directory must be writable by that user. `/var/run` itself is root-only, which
> is why a plain `/var/run/omdrcctrl.pid` fails with *permission denied* even
> under `sudo`. The script's `start_precmd` (which runs as root) creates
> `/var/run/omdrcctrl` owned by `omdrcctrl_user` so the unprivileged daemon can
> write there.

#### Running without root

When started by an unprivileged user the script clears `omdrcctrl_user` (so
rc.subr does not try to `su` and prompt for a password) and defaults the
pidfile/logfile to `${TMPDIR:-/tmp}/omdrcctrl-<user>.*`. Use `onestart` to bypass
the `omdrcctrl_enable` rcvar check:

```bash
/usr/local/etc/rc.d/omdrcctrl onestart
```

Restart after config changes:

```bash
sudo service omdrcctrl restart
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
sum. Uses a BSD/Linux-compatible `ps` parser and filters command names matching
`brutefir`.

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

### `GET /mpd/info`

Full audio-chain snapshot used by the MPD panel. Locates the MPD/musicpd
daemon (`pgrep -x`), reads its config to find the control port, queries it for
the playing stream, and inspects the downstream stages.

```json
{
  "ok": true,
  "running": true,
  "cpu": 3.1,
  "conf": "/etc/mpd.conf",
  "port": "6600",
  "client": "mpc",
  "state": "playing",
  "song": "Artist - Title",
  "audio": "192000:24:2",
  "sample_rate": 192000,
  "bit_depth": 24,
  "channels": 2,
  "is_linux": true,
  "virtual_oss_rate": null,
  "alsa_rate": 192000,
  "alsa": { "card": 0, "device": 0, "format": "S32_LE", "rate": 192000,
            "channels": 2, "period_size": 8192, "buffer_size": 32768 },
  "brutefir_rate": 192000,
  "rate_status": { "kind": "match", "text": "SAMPLE RATE MATCH" },
  "path_status": { "kind": "drc", "text": "Full-resolution DRC · no resampling",
                   "detail": "BruteFIR applies room correction at the native rate …" }
}
```

- **`audio` / `sample_rate` / `bit_depth` / `channels`** — the stream MPD
  reports. When modern `mpc` omits the `audio:` line, the value is read
  directly over the MPD protocol (`status` command on the control port).
- **`alsa`** (Linux) — parsed from `/proc/asound/card*/pcm*p/sub*/hw_params`;
  this is exactly what the DAC is being fed *right now*. `null` when no stream
  is open. On FreeBSD `virtual_oss_rate` is reported instead.
- **`rate_status`** — `kind` is `match` / `mismatch` / `unknown`; compares MPD
  against virtual_oss + BruteFIR.
- **`path_status`** — plain-language verdict for the bit-perfect hint:
  - `match` → **Bit-perfect passthrough** (DRC off, all rates equal)
  - `drc` → **Full-resolution DRC · no resampling** (BruteFIR engaged at native rate, 64-bit float)
  - `mismatch` → **Resampling active**
  - `unknown` → not enough information (e.g. nothing playing)

---

### `GET /filter-response`

Renders the **DRC filter response** HTML page (magnitude / phase / group-delay
charts). Linked from the DRC card on the main page.

---

### `GET /drc/filter-response`

Returns the FFT analysis of the FIR filters loaded by the **running** BruteFIR.
The active `.conf` is located from BruteFIR's command line; it carries the
absolute paths to its coeff (`.raw`) files, their sample format, and the
sampling rate — so no extra configuration is required. When BruteFIR is not
running there is no active filter and the endpoint says so.

```json
{
  "ok": true, "running": true,
  "geometry": "120.blue", "rate": 192000, "conf": "brutefir-192000.conf",
  "channels": [
    { "name": "Left", "color": "#388bfd", "file": "L.raw", "format": "FLOAT64_LE",
      "attenuation": 3.0, "taps": 524288,
      "delay_ms": 500.01,
      "freqs": [10.0, …], "mag": [-1.2, …], "phase": [-43.1, …], "gd": [12.4, …] }
  ]
}
{ "ok": false, "running": false, "error": "BruteFIR is not running — no active filter loaded." }
```

Each channel's impulse response is read with NumPy, transformed with `rfft`,
and reduced to ~700 log-spaced points in the audio correction band. `mag` is
the magnitude in dB (the raw filter transfer function, including its
`attenuation`), `phase` is the wrapped phase after removing the estimated bulk
FIR delay, and `gd` is residual group delay in milliseconds. **The filter files
are never modified** — they are generated externally with REW + SoX and only
read here.

---

### `GET /system/advanced`

FreeBSD-only diagnostic endpoint. Returns the outputs of:

```sh
sysctl dev.pcm.0
sysctl hw.usb.uaudio
```

```json
{
  "ok": true,
  "sections": [
    { "title": "sysctl dev.pcm.0", "ok": true, "output": "..." },
    { "title": "sysctl hw.usb.uaudio", "ok": true, "output": "..." }
  ]
}
```

---

## Built-in monitoring panels

In addition to the configurable command cards, fixed monitoring panels always
appear at the bottom of the page.

Auto-refreshing panels show a client-side countdown such as `refresh: 5s`.
The circular arrow button in each panel header refreshes that panel immediately
and resets the countdown.

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

### MPD

The central audio-health panel for a headless server. Backed by
[`GET /mpd/info`](#get-mpdinfo), it shows:

- Daemon state and the portable client used (`musicpc` on FreeBSD, `mpc` on Linux)
- Playback state and current song
- The stream MPD reports — sample rate, bit depth, channels
- **DAC feed** — what the DAC is actually receiving *now*: ALSA `hw_params`
  format/channels and the period/buffer sizes (Linux), or the `virtual_oss`
  rate (FreeBSD). This is read straight from `/proc/asound` and reflects the
  real hardware stream, not just what was requested.
- BruteFIR rate, and a **SAMPLE RATE MATCH** (green) / **RESAMPLING** (red)
  comparison across MPD, `virtual_oss`/ALSA, and BruteFIR
- A highlighted **path verdict** giving a plain-language bit-perfect assessment
  (`Bit-perfect passthrough`, `Full-resolution DRC · no resampling`, or
  `Resampling active`) plus a one-line explanation
- A static **signal path** reminder (`MPD → BruteFIR → ALSA/virtual_oss → DAC`)

A small **DRC status** sub-section (refreshed manually) lists the BruteFIR
`drc.sh status` rows beneath the panel.

### DRC filter response

The DRC card header carries a **Filter response ↗** button that opens a
dedicated page ([`GET /filter-response`](#get-filter-response)) charting the
loaded room-correction FIR filters. Three stacked, log-frequency Chart.js plots
show **magnitude (dB)**, **wrapped delay-compensated phase (°)**, and
**residual group delay (ms)**, with the Left and Right channels overlaid. The data comes from
[`GET /drc/filter-response`](#get-drcfilter-response), which FFTs the live
`L.raw` / `R.raw` impulse responses on demand.

The page only has something to show while BruteFIR is running (that is when a
filter is loaded); otherwise it explains that no filter is active. The
magnitude axis is clamped for readability, but the filters themselves — built
externally with REW + SoX — are read and displayed unaltered. Chart.js is
vendored locally (`static/chart.umd.min.js`), so the page works on an offline
machine.

### Brutefir CPU

Shows per-process CPU usage for every running `brutefir` process, refreshed
every `brutefir_interval` seconds from the `[monitor]` section of
`commands.conf`. When more than one process is detected (brutefir typically
spawns four worker processes) a highlighted **Total** line is appended. Displays
"not running" when brutefir is not active.

### Audio Devices

Shows `/dev/sndstat` on FreeBSD and decodes `fmt 0x...` bitfields to
`AFMT_*` / `PCM_CAP_*` labels. Refreshed every `sndstat_interval` seconds from
the `[monitor]` section of `commands.conf`.

### Advanced

FreeBSD-only diagnostic panel that gathers `sysctl dev.pcm.0` and
`sysctl hw.usb.uaudio`. It is refreshed manually with the panel button.

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
