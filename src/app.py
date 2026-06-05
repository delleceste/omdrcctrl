#!/usr/bin/env python3
import os
import platform
import re
import shlex
import subprocess
import configparser
import threading
import time
import tempfile
import markdown as md_lib
from flask import Flask, render_template, jsonify, send_from_directory

_HERE = os.path.dirname(os.path.abspath(__file__))

# ── FreeBSD /dev/sndstat fmt bitmask (sys/soundcard.h) ───────────────────────
_AFMT_BITS: list[tuple[int, str]] = [
    (0x00000008, "U8"),
    (0x00000010, "S16_LE"),
    (0x00000020, "S16_BE"),
    (0x00000040, "S8"),
    (0x00001000, "S32_LE"),
    (0x00002000, "S32_BE"),
    (0x00004000, "U32_LE"),
    (0x00100000, "CAP_ANALOGOUT"),
    (0x00200000, "CAP_ANALOGIN"),
    (0x00400000, "CAP_DIGITALOUT"),
    (0x00800000, "CAP_DIGITALIN"),
]

def _decode_afmt(val: int) -> str:
    names, rest = [], val
    for bit, name in _AFMT_BITS:
        if val & bit:
            names.append(name)
            rest &= ~bit
    if rest:
        names.append(hex(rest))
    return " | ".join(names) if names else hex(val)

app = Flask(__name__, template_folder=os.path.join(_HERE, "templates"))

# Paths to qconnect2mpd output files.
# Set by [qconnect] section in commands.conf; env vars are the fallback.
QCONNECT_STATUS_FILE = os.environ.get("QCONNECT_STATUS_FILE", "/tmp/qconnect2mpd-status.txt")
QCONNECT_LOG_FILE    = os.environ.get("QCONNECT_LOG_FILE",    "/tmp/qconnect2mpd.log")

# [monitor] section defaults
TOPCPU_THRESHOLD = 4.0   # minimum %CPU to include in the top-processes list
MONITOR_INTERVAL = 5     # seconds between MPD refreshes
TOPCPU_INTERVAL = 3      # seconds between top-CPU refreshes
_TOPCPU_CACHE: dict | None = None
_TOPCPU_CACHE_AT = 0.0

GROUP_ORDER  = ["drc", "apps", "system"]
GROUP_LABELS = {
    "drc":    "Digital Room Correction",
    "apps":   "Applications",
    "system": "System",
}

COMMANDS: list[dict] = []
CMD_MAP:  dict[str, dict] = {}


def load_config(path: str) -> None:
    global COMMANDS, CMD_MAP, QCONNECT_STATUS_FILE, QCONNECT_LOG_FILE
    global TOPCPU_THRESHOLD, MONITOR_INTERVAL, TOPCPU_INTERVAL
    cfg = configparser.ConfigParser()
    if not cfg.read(path):
        raise FileNotFoundError(f"Config file not found: {path}")

    # [qconnect] is a settings section, not a command — read and skip it.
    if cfg.has_section("qconnect"):
        QCONNECT_STATUS_FILE = cfg.get("qconnect", "status_file", fallback=QCONNECT_STATUS_FILE)
        QCONNECT_LOG_FILE    = cfg.get("qconnect", "log_file",    fallback=QCONNECT_LOG_FILE)

    # [monitor] is a settings section — read and skip it.
    if cfg.has_section("monitor"):
        TOPCPU_THRESHOLD = cfg.getfloat("monitor", "topcpu_threshold", fallback=TOPCPU_THRESHOLD)
        MONITOR_INTERVAL = max(1, cfg.getint("monitor", "monitor_interval", fallback=MONITOR_INTERVAL))
        TOPCPU_INTERVAL = max(1, cfg.getint("monitor", "topcpu_interval", fallback=TOPCPU_INTERVAL))

    _RESERVED = {"qconnect", "monitor"}
    COMMANDS = []
    for sid in cfg.sections():
        if sid in _RESERVED:
            continue
        c = dict(cfg[sid])
        c["id"] = sid
        for key in ("what", "group", "type"):
            if key not in c:
                raise ValueError(f"[{sid}] missing required key: '{key}'")
        if c["type"] not in ("READ", "WRITE", "LINK"):
            raise ValueError(f"[{sid}] type must be READ, WRITE or LINK, got: '{c['type']}'")
        if c["type"] in ("READ", "WRITE") and "cmd" not in c:
            raise ValueError(f"[{sid}] missing required key: 'cmd'")
        if c["type"] == "WRITE" and "button" not in c:
            raise ValueError(f"[{sid}] WRITE command missing 'button' key")
        if c["type"] == "LINK" and "url" not in c:
            raise ValueError(f"[{sid}] LINK command missing 'url' key")
        COMMANDS.append(c)
    CMD_MAP = {c["id"]: c for c in COMMANDS}


def _groups() -> list[tuple]:
    d: dict[str, list] = {}
    for c in COMMANDS:
        d.setdefault(c["group"], []).append(c)
    order = GROUP_ORDER + [g for g in d if g not in GROUP_ORDER]
    return [
        (g, GROUP_LABELS.get(g, g.replace("_", " ").title()), d[g])
        for g in order if g in d
    ]


def _env() -> dict:
    e = dict(os.environ)
    e.setdefault("DISPLAY", ":0")
    # FreeBSD rc.d services start with a minimal PATH that omits /usr/local/{s,}bin
    # where brutefir, mpc, virtual_oss, pgrep, … live.
    path_dirs = e.get("PATH", "").split(":")
    for d in ("/usr/local/sbin", "/usr/local/bin"):
        if d not in path_dirs:
            path_dirs.insert(0, d)
    e["PATH"] = ":".join(path_dirs)
    return e


def _find_dyn_details(cmd: dict, config_name: str) -> str | None:
    root = cmd.get("details_root", "/home/giacomo/DRC")
    for fname in ("README.md", "INDEX.md"):
        path = os.path.join(root, config_name, fname)
        if os.path.isfile(path):
            return path
    return None


def _unit_active(unit: str) -> bool:
    try:
        r = subprocess.run(
            ["systemctl", "is-active", "--quiet", unit],
            timeout=3, capture_output=True,
        )
        return r.returncode == 0
    except Exception:
        return False


def _process_running(process: str) -> bool:
    try:
        r = subprocess.run(
            ["pgrep", "-x", process],
            timeout=3, capture_output=True,
        )
        return r.returncode == 0
    except Exception:
        return False


def _tail_file(path: str, limit: int = 4000) -> str:
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - limit), os.SEEK_SET)
            return f.read().decode(errors="replace").strip()
    except OSError:
        return ""


def _unlink_quietly(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


def _wait_and_cleanup(proc: subprocess.Popen, path: str) -> None:
    proc.wait()
    _unlink_quietly(path)


# ── MPD helpers ───────────────────────────────────────────────────────────────

def _mpd_conf_from_cmdline(cmdline: str) -> str | None:
    try:
        tokens = shlex.split(cmdline)
    except ValueError:
        tokens = cmdline.split()
    i = 1  # skip argv[0] (binary path)
    while i < len(tokens):
        t = tokens[i]
        if t in ("--config", "-c") and i + 1 < len(tokens):
            return tokens[i + 1]
        if t.startswith("--config="):
            return t.split("=", 1)[1]
        if not t.startswith("-"):
            return t   # first non-flag positional = config file
        i += 1
    return None


def _mpd_port_from_conf(conf_path: str) -> str | None:
    try:
        with open(conf_path, encoding="utf-8", errors="replace") as f:
            for line in f:
                s = line.strip()
                if s.startswith("#"):
                    continue
                m = re.match(r'^port\s+"(\d+)"', s)
                if m:
                    return m.group(1)
                m = re.match(r'^bind_to_address\s+"[^"]*:(\d+)"', s)
                if m:
                    return m.group(1)
    except OSError:
        pass
    return None


# ── page routes ───────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template(
        "index.html",
        groups=_groups(),
        topcpu_threshold=TOPCPU_THRESHOLD,
        monitor_interval=MONITOR_INTERVAL,
        topcpu_interval=TOPCPU_INTERVAL,
    )


@app.route("/details/<cmd_id>")
def details_page(cmd_id):
    if cmd_id not in CMD_MAP:
        return "Unknown command", 404
    cmd = CMD_MAP[cmd_id]
    if "details" not in cmd:
        return "No details file configured for this command", 404

    path = cmd["details"]
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except FileNotFoundError:
        return f"Details file not found: {path}", 404
    except OSError as e:
        return f"Cannot read details file: {e}", 500

    html = md_lib.markdown(
        text,
        extensions=["tables", "fenced_code", "extra"],
    )
    # rewrite relative asset paths (src="..." href="...") so the browser can
    # fetch images and local links through /details-asset/<cmd_id>/...
    html = re.sub(
        r'(src|href)="(?!https?://|/)([^"]+)"',
        lambda m: f'{m.group(1)}="/details-asset/{cmd_id}/{m.group(2)}"',
        html,
    )
    return render_template("details.html", title=cmd["what"], content=html)


@app.route("/details-asset/<cmd_id>/<path:filename>")
def details_asset(cmd_id, filename):
    """Serve images and other files relative to the details .md file."""
    if cmd_id not in CMD_MAP:
        return "Not found", 404
    cmd = CMD_MAP[cmd_id]
    if "details" not in cmd:
        return "Not found", 404
    base_dir = os.path.dirname(os.path.abspath(cmd["details"]))
    return send_from_directory(base_dir, filename)


@app.route("/details-dyn/<cmd_id>/<config_name>")
def details_dyn_page(cmd_id, config_name):
    if cmd_id not in CMD_MAP:
        return "Unknown command", 404
    cmd = CMD_MAP[cmd_id]
    path = _find_dyn_details(cmd, config_name)
    if not path:
        return f"No README.md or INDEX.md found for: {config_name}", 404
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except OSError as e:
        return f"Cannot read file: {e}", 500
    html = md_lib.markdown(text, extensions=["tables", "fenced_code", "extra"])
    html = re.sub(
        r'(src|href)="(?!https?://|/)([^"]+)"',
        lambda m: f'{m.group(1)}="/details-dyn-asset/{cmd_id}/{config_name}/{m.group(2)}"',
        html,
    )
    return render_template("details.html", title=config_name, content=html)


@app.route("/details-dyn-asset/<cmd_id>/<config_name>/<path:filename>")
def details_dyn_asset(cmd_id, config_name, filename):
    if cmd_id not in CMD_MAP:
        return "Not found", 404
    cmd = CMD_MAP[cmd_id]
    path = _find_dyn_details(cmd, config_name)
    if not path:
        return "Not found", 404
    return send_from_directory(os.path.dirname(path), filename)


@app.route("/readme")
def readme_page():
    path = os.path.join(_HERE, "README.md")
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except FileNotFoundError:
        return "README not found", 404
    html = md_lib.markdown(text, extensions=["tables", "fenced_code", "extra"])
    return render_template("details.html", title="arkictrl — README", content=html)


# ── API routes ────────────────────────────────────────────────────────────────

@app.route("/run/<cmd_id>", methods=["POST"])
def run_command(cmd_id):
    if cmd_id not in CMD_MAP:
        return jsonify({"ok": False, "error": "Unknown command"}), 404
    cmd = CMD_MAP[cmd_id]
    if cmd["type"] != "WRITE":
        return jsonify({"ok": False, "error": "Not a WRITE command"}), 400

    log = tempfile.NamedTemporaryFile(
        mode="w+b",
        prefix=f"arkictrl-{cmd_id}-",
        suffix=".log",
        delete=False,
    )
    log_path = log.name
    proc = subprocess.Popen(
        cmd["cmd"], shell=True, env=_env(),
        stdin=subprocess.DEVNULL,
        stdout=log, stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    log.close()
    try:
        rc = proc.wait(timeout=5)
        if rc != 0:
            err = _tail_file(log_path)
            _unlink_quietly(log_path)
            return jsonify({"ok": False, "error": err or f"exit code {rc}"})
        _unlink_quietly(log_path)
        return jsonify({"ok": True})
    except subprocess.TimeoutExpired:
        # Keep waiting in the background so the child is reaped, but leave its
        # stdio detached from the HTTP request.
        threading.Thread(target=_wait_and_cleanup, args=(proc, log_path), daemon=True).start()
        return jsonify({"ok": True})  # still running → launched successfully


@app.route("/read/<cmd_id>")
def read_command(cmd_id):
    if cmd_id not in CMD_MAP:
        return jsonify({"ok": False, "error": "Unknown command"}), 404
    cmd = CMD_MAP[cmd_id]
    if cmd["type"] != "READ":
        return jsonify({"ok": False, "error": "Not a READ command"}), 400

    try:
        result = subprocess.run(
            cmd["cmd"], shell=True, env=_env(),
            capture_output=True, text=True, timeout=10,
        )
        ok     = result.returncode == 0
        output = (result.stdout + result.stderr).strip()
        resp   = {"ok": ok, "output": output or (None if ok else f"exit {result.returncode}")}
        if ok and output and "details_root" in cmd and _find_dyn_details(cmd, output):
            resp["details_url"] = f"/details-dyn/{cmd_id}/{output}"
        return jsonify(resp)
    except subprocess.TimeoutExpired:
        return jsonify({"ok": False, "output": "timeout"})


@app.route("/qconnect/status")
def qconnect_status():
    try:
        with open(QCONNECT_STATUS_FILE, encoding="utf-8") as f:
            lines = f.read().splitlines()
        return jsonify({
            "ok":    True,
            "line1": lines[0] if len(lines) > 0 else "",
            "line2": lines[1] if len(lines) > 1 else "",
        })
    except FileNotFoundError:
        return jsonify({"ok": False, "line1": "", "line2": ""})
    except OSError as e:
        return jsonify({"ok": False, "line1": "", "line2": "", "error": str(e)})


@app.route("/qconnect/restart", methods=["POST"])
def qconnect_restart():
    try:
        r = subprocess.run(
            ["systemctl", "--user", "restart", "qobuzconnect2mpd"],
            capture_output=True, text=True, timeout=10,
        )
        if r.returncode != 0:
            return jsonify({"ok": False, "error": (r.stderr or r.stdout).strip()})
        return jsonify({"ok": True})
    except subprocess.TimeoutExpired:
        return jsonify({"ok": False, "error": "timeout"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.route("/qconnect/log")
def qconnect_log():
    try:
        with open(QCONNECT_LOG_FILE, encoding="utf-8") as f:
            content = f.read()
        return jsonify({"ok": True, "content": content})
    except FileNotFoundError:
        return jsonify({"ok": True, "content": "(log file not found)"})
    except OSError as e:
        return jsonify({"ok": False, "content": str(e)})


@app.route("/mpd/info")
def mpd_info():
    try:
        # pgrep -x is reliable on both Linux and FreeBSD; avoids ps flag
        # incompatibilities. musicpd is the FreeBSD port binary name.
        pid = None
        for name in ("musicpd", "mpd"):
            r = subprocess.run(["pgrep", "-x", name],
                               capture_output=True, text=True, timeout=3)
            if r.returncode == 0:
                pids = r.stdout.strip().split()
                if pids:
                    pid = pids[0]
                    break

        running = pid is not None
        cpu_total = 0.0
        conf = None

        if running:
            r2 = subprocess.run(["ps", "-p", pid, "-o", "pcpu=,args="],
                                capture_output=True, text=True, timeout=3)
            for line in r2.stdout.splitlines():
                parts = line.split(None, 1)
                if not parts:
                    continue
                try:
                    cpu_total += float(parts[0])
                except ValueError:
                    pass
                if conf is None and len(parts) > 1:
                    conf = _mpd_conf_from_cmdline(parts[1].strip())

        # Fallback: probe common default config paths (Linux and FreeBSD)
        if not conf:
            for p in ("/usr/local/etc/musicpd.conf",
                      "/usr/local/etc/mpd.conf",
                      "/etc/mpd.conf",
                      os.path.expanduser("~/.config/mpd/mpd.conf"),
                      os.path.expanduser("~/.mpdconf")):
                if os.path.isfile(p):
                    conf = p
                    break

        port = _mpd_port_from_conf(conf) if conf else None
        return jsonify({
            "ok":      True,
            "running": running,
            "cpu":     round(cpu_total, 1),
            "conf":    conf  or "(unknown)",
            "port":    port  or "6600",
        })
    except subprocess.TimeoutExpired:
        return jsonify({"ok": False, "error": "timeout"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


def _read_memory() -> dict:
    try:
        system = platform.system()
        if system == "Linux":
            info: dict[str, int] = {}
            with open("/proc/meminfo") as f:
                for line in f:
                    k, v = line.split(":", 1)
                    info[k.strip()] = int(v.strip().split()[0]) * 1024
            total     = info["MemTotal"]
            available = info["MemAvailable"]
            free      = info["MemFree"]
        elif system == "FreeBSD":
            r = subprocess.run(
                ["sysctl", "-n",
                 "hw.physmem",
                 "vm.stats.vm.v_page_size",
                 "vm.stats.vm.v_free_count",
                 "vm.stats.vm.v_inactive_count",
                 "vm.stats.vm.v_cache_count"],
                capture_output=True, text=True, timeout=5,
            )
            vals = [int(x) for x in r.stdout.split()]
            physmem, psize, v_free, v_inactive, v_cache = vals
            total     = physmem
            free      = v_free * psize
            available = (v_free + v_inactive + v_cache) * psize
        else:
            return {"ok": False, "error": f"unsupported platform: {system}"}
        used = total - available
        return {"ok": True, "total": total, "used": used, "free": free, "available": available}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.route("/system/sndstat")
def system_sndstat():
    try:
        sys = platform.system()
        if sys == "FreeBSD":
            with open("/dev/sndstat", errors="replace") as f:
                raw = f.read()
            lines = []
            for line in raw.splitlines():
                m = re.search(r'\bfmt\s+(0x[0-9a-fA-F]+)', line)
                if m:
                    decoded = _decode_afmt(int(m.group(1), 16))
                    line = line + f"   [{decoded}]"
                lines.append(line)
            return jsonify({"ok": True, "lines": lines})
        elif sys == "Linux":
            r = subprocess.run(["aplay", "-l"],
                               capture_output=True, text=True, timeout=5)
            return jsonify({"ok": True, "lines": r.stdout.splitlines()})
        else:
            return jsonify({"ok": False, "error": f"unsupported platform: {sys}"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.route("/system/memory")
def system_memory():
    return jsonify(_read_memory())


@app.route("/system/topcpu")
def system_topcpu():
    global _TOPCPU_CACHE, _TOPCPU_CACHE_AT
    now = time.monotonic()
    if _TOPCPU_CACHE is not None and now - _TOPCPU_CACHE_AT < TOPCPU_INTERVAL:
        return jsonify(_TOPCPU_CACHE)

    try:
        # BSD-style 'ax': includes processes without a controlling terminal
        # on both Linux and FreeBSD. See mpd_info for the full explanation.
        r = subprocess.run(
            ["ps", "ax", "-o", "user=,pid=,pcpu=,comm="],
            capture_output=True, text=True, timeout=5,
        )
        procs = []
        for line in r.stdout.splitlines():
            parts = line.split(None, 3)
            if len(parts) < 4:
                continue
            try:
                cpu = float(parts[2])
            except ValueError:
                continue
            name = parts[3].strip()
            if name == "ps":
                continue
            if cpu >= TOPCPU_THRESHOLD:
                procs.append({"user": parts[0], "pid": parts[1], "cpu": cpu, "name": name})
        procs.sort(key=lambda p: p["cpu"], reverse=True)
        _TOPCPU_CACHE = {
            "ok": True,
            "procs": procs,
            "threshold": TOPCPU_THRESHOLD,
            "interval": TOPCPU_INTERVAL,
        }
        _TOPCPU_CACHE_AT = now
        return jsonify(_TOPCPU_CACHE)
    except subprocess.TimeoutExpired:
        return jsonify({"ok": False, "error": "timeout"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.route("/drc/status")
def drc_status_api():
    # drc.sh lives alongside drc-status.sh; derive its path from the
    # already-configured drc_status command rather than a WRITE command.
    drc_status_cmd = CMD_MAP.get("drc_status")
    if not drc_status_cmd:
        return jsonify({"ok": False, "error": "drc_status not configured"})
    script = os.path.join(
        os.path.dirname(drc_status_cmd["cmd"].strip()), "drc.sh"
    )
    try:
        r = subprocess.run(
            [script, "status"],
            capture_output=True, text=True, timeout=10, env=_env(),
        )
        rows = []
        for line in r.stdout.splitlines():
            line = line.strip()
            if not line or ':' not in line:
                continue
            k, _, v = line.partition(':')
            rows.append({"key": k.strip(), "value": v.strip()})
        return jsonify({"ok": True, "rows": rows})
    except subprocess.TimeoutExpired:
        return jsonify({"ok": False, "error": "timeout"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.route("/drc/geometry")
def drc_geometry():
    cmd = CMD_MAP.get("drc_status")
    if not cmd:
        return jsonify({"ok": False, "error": "drc_status not configured"})
    try:
        r = subprocess.run(
            cmd["cmd"] + " --geometry",
            shell=True, env=_env(),
            capture_output=True, text=True, timeout=5,
        )
        geo = r.stdout.strip()
        if r.returncode == 0 and geo:
            return jsonify({"ok": True, "geometry": geo})
        return jsonify({"ok": False, "error": r.stderr.strip() or "empty"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.route("/brutefir/cpu")
def brutefir_cpu():
    try:
        # BSD-style 'ax': see mpd_info for explanation.
        r = subprocess.run(
            ["ps", "ax", "-o", "pid=,pcpu=,comm="],
            capture_output=True, text=True, timeout=5,
        )
        procs = []
        total = 0.0
        for line in r.stdout.splitlines():
            parts = line.split(None, 2)
            if len(parts) < 3 or parts[2].strip() != "brutefir":
                continue
            try:
                cpu = float(parts[1])
                procs.append({"pid": parts[0], "cpu": cpu})
                total += cpu
            except ValueError:
                pass
        return jsonify({"ok": True, "procs": procs, "total": round(total, 1)})
    except subprocess.TimeoutExpired:
        return jsonify({"ok": False, "error": "timeout"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.route("/status")
def status():
    units = {}
    for c in COMMANDS:
        if "details" not in c:
            continue
        if "process" in c:
            units[c["id"]] = "active" if _process_running(c["process"]) else "inactive"
        elif "unit" in c:
            units[c["id"]] = "active" if _unit_active(c["unit"]) else "inactive"
    return jsonify({"ok": True, "units": units})


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Arki Control web interface")
    parser.add_argument("--host",   default="0.0.0.0")
    parser.add_argument("--port",   type=int, default=8080)
    parser.add_argument("--config", default=os.path.join(_HERE, "commands.conf"))
    args = parser.parse_args()
    load_config(args.config)
    app.run(host=args.host, port=args.port)
