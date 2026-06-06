#!/usr/bin/env python3
import glob
import os
import platform
import pwd
import re
import shutil
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
    (0x00100000, "PCM_CAP_ANALOGOUT"),
    (0x00200000, "PCM_CAP_ANALOGIN"),
    (0x00400000, "PCM_CAP_DIGITALOUT"),
    (0x00800000, "PCM_CAP_DIGITALIN"),
    (0x00000008, "AFMT_U8"),
    (0x00000010, "AFMT_S16_LE"),
    (0x00000020, "AFMT_S16_BE"),
    (0x00000040, "AFMT_S8"),
    (0x00001000, "AFMT_S32_LE"),
    (0x00002000, "AFMT_S32_BE"),
    (0x00004000, "AFMT_U32_LE"),
]

def _decode_afmt(val: int) -> str:
    names, rest = [], val
    for bit, name in _AFMT_BITS:
        if val & bit:
            names.append(name)
            rest &= ~bit
    if rest:
        names.append(hex(rest))
    return "|".join(names) if names else hex(val)


def _decode_sndstat_fmt(line: str) -> str:
    def repl(match: re.Match) -> str:
        raw = match.group(1)
        return f"fmt: {_decode_afmt(int(raw, 16))}"

    return re.sub(r'\bfmt\s+(0x[0-9a-fA-F]+)', repl, line)

app = Flask(__name__, template_folder=os.path.join(_HERE, "templates"))

# Paths to qconnect2mpd output files.
# Set by [qconnect] section in commands.conf; env vars are the fallback.
QCONNECT_STATUS_FILE = os.environ.get("QCONNECT_STATUS_FILE", "/tmp/qconnect2mpd-status.txt")
QCONNECT_LOG_FILE    = os.environ.get("QCONNECT_LOG_FILE",    "/tmp/qconnect2mpd.log")

# [monitor] section defaults
TOPCPU_THRESHOLD = 4.0   # minimum %CPU to include in the top-processes list
MONITOR_INTERVAL = 5     # seconds between MPD refreshes
TOPCPU_INTERVAL = 3      # seconds between top-CPU refreshes
SNDSTAT_INTERVAL = 5     # seconds between audio-device refreshes
BRUTEFIR_INTERVAL = 5    # seconds between brutefir CPU refreshes
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
    global SNDSTAT_INTERVAL, BRUTEFIR_INTERVAL
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
        SNDSTAT_INTERVAL = max(1, cfg.getint("monitor", "sndstat_interval", fallback=SNDSTAT_INTERVAL))
        BRUTEFIR_INTERVAL = max(1, cfg.getint("monitor", "brutefir_interval", fallback=BRUTEFIR_INTERVAL))

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
    if not e.get("HOME") or e["HOME"] == "/":
        e["HOME"] = pwd.getpwuid(os.getuid()).pw_dir
    e.setdefault("XDG_CONFIG_HOME", os.path.join(e["HOME"], ".config"))
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


def _process_name(command: str) -> str:
    try:
        parts = shlex.split(command)
    except ValueError:
        parts = command.split()
    return os.path.basename(parts[0]) if parts else ""


def _hide_from_topcpu(row: dict) -> bool:
    name = _process_name(row["name"]).lower()
    if row["pid"] == "0":
        return True
    if name in {"idle", "kernel", "ps"}:
        return True
    return name.startswith("[") and name.endswith("]")


def _ps_processes() -> list[dict]:
    candidates = [
        ["ps", "axo", "user,pid,pcpu,comm"],
        ["ps", "ax", "-o", "user", "-o", "pid", "-o", "pcpu", "-o", "comm"],
        ["ps", "ax", "-o", "user=,pid=,pcpu=,comm="],
    ]
    errors = []
    for cmd in candidates:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        if r.returncode != 0:
            errors.append((r.stderr or r.stdout).strip())
            continue
        rows = []
        for line in r.stdout.splitlines():
            parts = line.split(None, 3)
            if len(parts) < 4:
                continue
            if parts[1].upper() == "PID" or parts[2].upper() in ("%CPU", "PCPU"):
                continue
            try:
                rows.append({
                    "user": parts[0],
                    "pid": parts[1],
                    "cpu": float(parts[2]),
                    "name": parts[3].strip(),
                })
            except ValueError:
                continue
        if rows:
            return rows
    raise RuntimeError("; ".join(e for e in errors if e) or "could not parse ps output")


def _tail_file(path: str, limit: int = 4000) -> str:
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - limit), os.SEEK_SET)
            return f.read().decode(errors="replace").strip()
    except OSError:
        return ""


def _command_failure_output(cmd: dict, log_path: str) -> str:
    parts = []
    out = _tail_file(log_path)
    if out:
        parts.append(out)

    if "drc.sh" in cmd.get("cmd", ""):
        brutefir_out = _tail_file("/tmp/brutefir.out")
        if brutefir_out:
            parts.append("--- /tmp/brutefir.out ---\n" + brutefir_out)

    return "\n".join(parts).strip()


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


def _mpc_client() -> list[str] | None:
    preferred = ("musicpc", "mpc") if platform.system() == "FreeBSD" else ("mpc", "musicpc")
    path = _env().get("PATH")
    for name in preferred:
        exe = shutil.which(name, path=path)
        if exe:
            return [exe]
    return None


def _parse_mpc_audio(audio: str) -> dict:
    parsed = {"sample_rate": None, "bit_depth": None, "channels": None}
    m = re.search(r'(\d+(?:\.\d+)?)\s*kHz\b', audio, re.I)
    if m:
        parsed["sample_rate"] = int(round(float(m.group(1)) * 1000))
    else:
        m = re.search(r'(\d+)\s*Hz\b', audio, re.I)
        if m:
            parsed["sample_rate"] = int(m.group(1))

    m = re.search(r'(\d+)\s*bits?\b', audio, re.I)
    if m:
        parsed["bit_depth"] = int(m.group(1))

    m = re.search(r'(\d+)\s*channels?\b', audio, re.I)
    if m:
        parsed["channels"] = int(m.group(1))
    elif re.search(r'\bstereo\b', audio, re.I):
        parsed["channels"] = 2
    elif re.search(r'\bmono\b', audio, re.I):
        parsed["channels"] = 1

    if parsed["sample_rate"] is None:
        m = re.search(r'\b(\d{4,6})\s*:\s*(\d+)\s*:\s*(\d+)\b', audio)
        if m:
            parsed["sample_rate"] = int(m.group(1))
            parsed["bit_depth"] = int(m.group(2))
            parsed["channels"] = int(m.group(3))
    return parsed


def _mpd_audio_via_protocol(port: str | None) -> str:
    """Query MPD directly for the audio field.

    Modern mpc (0.35+) dropped the 'audio:' line from its default status
    output on Linux.  The MPD protocol always includes it when playing.
    """
    import socket
    try:
        p = int(port) if port else 6600
        with socket.create_connection(("localhost", p), timeout=3) as sock:
            with sock.makefile("r", encoding="utf-8", errors="replace") as f:
                if not f.readline().startswith("OK"):
                    return ""
                sock.sendall(b"status\n")
                for line in f:
                    line = line.rstrip("\n")
                    if line == "OK" or line.startswith("ACK"):
                        break
                    if line.lower().startswith("audio:"):
                        return line.split(":", 1)[1].strip()
    except Exception:
        pass
    return ""


def _mpc_status(port: str | None = None) -> dict:
    cmd = _mpc_client()
    if not cmd:
        return {"client": None, "state": "unknown", "error": "mpc/musicpc not found"}
    if port:
        cmd = cmd + ["-p", str(port)]
    cmd.append("status")
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=5, env=_env())
    text = (r.stdout + r.stderr).strip()
    info = {
        "client": os.path.basename(cmd[0]),
        "state": "stopped",
        "song": "",
        "audio": "",
        "sample_rate": None,
        "bit_depth": None,
        "channels": None,
        "error": "",
    }
    if r.returncode != 0:
        info["state"] = "unknown"
        info["error"] = text or f"exit {r.returncode}"
        return info

    lines = text.splitlines()
    state_line = next((line for line in lines if re.search(r'\[(playing|paused|stopped)\]', line)), "")
    if state_line:
        m = re.search(r'\[(playing|paused|stopped)\]', state_line)
        if m:
            info["state"] = m.group(1)
        state_idx = lines.index(state_line)
        if state_idx > 0:
            info["song"] = lines[state_idx - 1].strip()

    for line in lines:
        if re.search(r'^(audio|format)\s*:', line, re.I):
            _, _, audio = line.partition(":")
            info["audio"] = audio.strip()
            info.update(_parse_mpc_audio(info["audio"]))
            break

    if not info["audio"] and info["state"] in ("playing", "paused"):
        audio = _mpd_audio_via_protocol(port)
        if audio:
            info["audio"] = audio
            info.update(_parse_mpc_audio(audio))

    return info


def _ps_arg_lines() -> list[str]:
    for cmd in (["ps", "axo", "args"], ["ps", "ax", "-o", "args="]):
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        if r.returncode == 0:
            return [
                line.strip() for line in r.stdout.splitlines()
                if line.strip() and line.strip().lower() != "args"
            ]
    return []


def _virtual_oss_rate() -> int | None:
    for line in _ps_arg_lines():
        try:
            parts = shlex.split(line)
        except ValueError:
            parts = line.split()
        if not parts:
            continue
        name = os.path.basename(parts[0])
        if name == "sudo" and len(parts) > 1:
            name = os.path.basename(parts[1])
            args = parts[1:]
        else:
            args = parts
        if name != "virtual_oss":
            continue
        for i, arg in enumerate(args):
            if arg == "-r" and i + 1 < len(args):
                try:
                    return int(args[i + 1])
                except ValueError:
                    return None
    return None


def _alsa_hw_params() -> dict | None:
    """hw_params of the first active ALSA playback stream (Linux only).

    Reflects exactly what the DAC is being fed right now: format (bit depth),
    rate, channels, and the period/buffer sizes.  Returns None when no stream
    is open.
    """
    for path in sorted(glob.glob("/proc/asound/card*/pcm*p/sub*/hw_params")):
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                content = f.read()
        except OSError:
            continue
        if content.strip() in ("", "closed"):
            continue
        fields: dict[str, str] = {}
        for line in content.splitlines():
            if ":" in line:
                k, _, v = line.partition(":")
                fields[k.strip()] = v.strip()
        rate = None
        m = re.match(r'(\d+)', fields.get("rate", ""))
        if m:
            rate = int(m.group(1))
        # card/device id from the path: /proc/asound/card0/pcm0p/sub0/hw_params
        cm = re.search(r'card(\d+)/pcm(\d+)', path)
        return {
            "card": int(cm.group(1)) if cm else None,
            "device": int(cm.group(2)) if cm else None,
            "format": fields.get("format"),
            "rate": rate,
            "channels": int(fields["channels"]) if fields.get("channels", "").isdigit() else None,
            "period_size": int(fields["period_size"]) if fields.get("period_size", "").isdigit() else None,
            "buffer_size": int(fields["buffer_size"]) if fields.get("buffer_size", "").isdigit() else None,
        }
    return None


def _alsa_rate() -> int | None:
    """Rate of the first active ALSA playback stream (Linux only)."""
    hw = _alsa_hw_params()
    return hw["rate"] if hw else None


def _brutefir_rate() -> int | None:
    for line in _ps_arg_lines():
        if "brutefir" not in line:
            continue
        m = re.search(r'brutefir-(\d+)[^ /]*\.conf', line)
        if m:
            return int(m.group(1))
    return None


def _rate_status(mpd_rate: int | None, virtual_rate: int | None, brutefir_rate: int | None) -> dict:
    rates = [r for r in (virtual_rate, brutefir_rate) if r]
    if not mpd_rate or not rates:
        return {"kind": "unknown", "text": "sample-rate comparison unavailable"}
    if all(mpd_rate == r for r in rates):
        return {"kind": "match", "text": "SAMPLE RATE MATCH"}
    return {"kind": "mismatch", "text": "RESAMPLING"}


def _path_status(rate_status: dict, brutefir_running: bool) -> dict:
    """Plain-language verdict on the audio path, for the bit-perfect hint.

    Honest framing: only the DRC-off + rates-matched case is truly
    bit-transparent.  With DRC engaged the signal is intentionally modified,
    but still at native rate in 64-bit float with no resampling stage.
    """
    kind = rate_status.get("kind")
    if kind == "mismatch":
        return {
            "kind": "mismatch",
            "text": "Resampling active",
            "detail": "Sample-rate conversion is in the chain — the stream is not bit-transparent.",
        }
    if kind == "match":
        if brutefir_running:
            return {
                "kind": "drc",
                "text": "Full-resolution DRC · no resampling",
                "detail": "BruteFIR applies room correction at the native rate in 64-bit float. "
                          "No sample-rate conversion and no lossy stage between MPD and the DAC.",
            }
        return {
            "kind": "match",
            "text": "Bit-perfect passthrough",
            "detail": "DRC is off and every stage runs at the same rate — samples reach the DAC unaltered.",
        }
    return {"kind": "unknown", "text": "Path status unavailable", "detail": ""}


# ── BruteFIR filter (FIR coefficient) inspection ───────────────────────────────

# numpy dtype for each BruteFIR coeff `format:` string.
_RAW_DTYPES: dict[str, str] = {
    "FLOAT64_LE": "<f8", "FLOAT64_BE": ">f8",
    "FLOAT_LE":   "<f4", "FLOAT_BE":   ">f4",
    "FLOAT32_LE": "<f4", "FLOAT32_BE": ">f4",
    "S32_LE": "<i4", "S32_BE": ">i4",
    "S16_LE": "<i2", "S16_BE": ">i2",
}


def _active_brutefir_conf() -> str | None:
    """Absolute path of the .conf the running BruteFIR was started with."""
    for line in _ps_arg_lines():
        if "brutefir" not in line:
            continue
        try:
            parts = shlex.split(line)
        except ValueError:
            parts = line.split()
        for p in parts:
            if p.endswith(".conf") and "brutefir" in os.path.basename(p):
                return p
    return None


def _parse_brutefir_conf(path: str) -> dict:
    """Extract sampling_rate and coeff (filename/format/attenuation) blocks."""
    with open(path, encoding="utf-8", errors="replace") as f:
        text = f.read()
    m = re.search(r'sampling_rate:\s*(\d+)', text)
    rate = int(m.group(1)) if m else None
    coeffs = []
    for cm in re.finditer(r'coeff\s+"([^"]+)"\s*\{([^}]*)\}', text):
        label, body = cm.group(1), cm.group(2)
        fn  = re.search(r'filename:\s*"([^"]+)"', body)
        fmt = re.search(r'format:\s*"([^"]+)"', body)
        att = re.search(r'attenuation:\s*([-\d.]+)', body)
        if not fn:
            continue
        coeffs.append({
            "label":       label,
            "filename":    fn.group(1),
            "format":      fmt.group(1) if fmt else "FLOAT64_LE",
            "attenuation": float(att.group(1)) if att else 0.0,
        })
    return {"rate": rate, "coeffs": coeffs}


def _coeff_channel(coeff: dict) -> str:
    """Human channel name from coeff label / filename (Left / Right / fallback)."""
    blob = (coeff["label"] + " " + os.path.basename(coeff["filename"])).lower()
    if re.search(r'\b(l|left|fl)\b', blob) or blob.startswith("l") or "/l." in blob or "l.raw" in blob:
        return "Left"
    if re.search(r'\b(r|right|fr)\b', blob) or blob.startswith("r") or "r.raw" in blob:
        return "Right"
    return coeff["label"]


def _fir_response(filename: str, fmt: str, rate: int,
                  npoints: int = 700, fmin: float = 10.0) -> dict:
    """FFT of a raw FIR impulse response → log-spaced magnitude/phase/group-delay."""
    import numpy as np
    dtype = _RAW_DTYPES.get(fmt.upper(), "<f8")
    ir = np.fromfile(filename, dtype=dtype)
    if ir.size == 0:
        raise ValueError(f"empty or unreadable filter: {filename}")
    if np.issubdtype(np.dtype(dtype), np.integer):
        ir = ir.astype(np.float64) / np.iinfo(np.dtype(dtype)).max
    else:
        ir = ir.astype(np.float64)

    n = ir.size
    spec  = np.fft.rfft(ir)
    freqs = np.fft.rfftfreq(n, d=1.0 / rate)
    mag   = 20.0 * np.log10(np.abs(spec) + 1e-12)
    angle = np.angle(spec)            # wrapped phase, (-π, π]

    # group delay (ms) = -d(phase)/d(omega) needs the *unwrapped* phase;
    # leave bin 0 (omega=0) at 0.
    omega = 2.0 * np.pi * freqs
    unwrapped = np.unwrap(angle)
    gd = np.zeros_like(unwrapped)
    if n > 2:
        gd[1:] = -np.gradient(unwrapped, omega)[1:]
    gd_ms = gd * 1000.0

    fmax = rate / 2.0
    lo = max(1, int(np.searchsorted(freqs, fmin)))
    targets = np.logspace(np.log10(freqs[lo]), np.log10(fmax), npoints)
    idx = np.unique(np.clip(np.searchsorted(freqs, targets), lo, len(freqs) - 1))

    return {
        "taps":  int(n),
        "freqs": [round(float(freqs[i]), 3) for i in idx],
        "mag":   [round(float(mag[i]),   3) for i in idx],
        # wrapped phase in degrees, range (-180, +180]
        "phase": [round(float(np.degrees(angle[i])), 2) for i in idx],
        "gd":    [round(float(gd_ms[i]), 4) for i in idx],
    }


def _format_read_output(cmd_id: str, output: str) -> str:
    if cmd_id == "drc_status" and output:
        parts = output.split()
        if not parts:
            return output
        if parts[-1].lower() == "off":
            return "Off"
        if len(parts) > 1:
            return " ".join(parts[1:])
    return output


def _control_title() -> str:
    try:
        r = subprocess.run(
            ["uname", "-sr"], capture_output=True, text=True, timeout=3,
        )
        label = r.stdout.strip()
        if r.returncode == 0 and label:
            return f"{label} Control"
    except Exception:
        pass
    return "System Control"


# ── page routes ───────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template(
        "index.html",
        control_title=_control_title(),
        groups=_groups(),
        topcpu_threshold=TOPCPU_THRESHOLD,
        monitor_interval=MONITOR_INTERVAL,
        topcpu_interval=TOPCPU_INTERVAL,
        sndstat_interval=SNDSTAT_INTERVAL,
        brutefir_interval=BRUTEFIR_INTERVAL,
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
    # Installed layout keeps README.md next to app.py; the source tree keeps it
    # one level up (repo root).  Try both so the link works either way.
    text = None
    for path in (os.path.join(_HERE, "README.md"),
                 os.path.join(_HERE, os.pardir, "README.md")):
        try:
            with open(path, encoding="utf-8") as f:
                text = f.read()
            break
        except FileNotFoundError:
            continue
    if text is None:
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
            err = _command_failure_output(cmd, log_path)
            _unlink_quietly(log_path)
            return jsonify({
                "ok": False,
                "error": err or f"exit code {rc}",
                "output": err,
            })
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
        if ok:
            output = _format_read_output(cmd_id, output)
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
        mpc = _mpc_status(port)
        is_linux = platform.system() == "Linux"
        voss_rate = _virtual_oss_rate() if not is_linux else None
        alsa_hw   = _alsa_hw_params()   if is_linux     else None
        alsa_rate = alsa_hw["rate"] if alsa_hw else None
        bf_rate = _brutefir_rate()
        rate_status = _rate_status(mpc["sample_rate"], voss_rate, bf_rate)
        path_status = _path_status(rate_status, bf_rate is not None)
        return jsonify({
            "ok":      True,
            "running": running,
            "cpu":     round(cpu_total, 1),
            "conf":    conf  or "(unknown)",
            "port":    port  or "6600",
            "client":  mpc["client"] or "(not found)",
            "state":   mpc["state"],
            "song":    mpc["song"],
            "audio":   mpc["audio"],
            "sample_rate": mpc["sample_rate"],
            "bit_depth": mpc["bit_depth"],
            "channels": mpc["channels"],
            "mpc_error": mpc["error"],
            "is_linux": is_linux,
            "virtual_oss_rate": voss_rate,
            "alsa_rate": alsa_rate,
            "alsa": alsa_hw,
            "brutefir_rate": bf_rate,
            "rate_status": rate_status,
            "path_status": path_status,
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
                lines.append(_decode_sndstat_fmt(line))
            return jsonify({"ok": True, "lines": lines})
        elif sys == "Linux":
            r = subprocess.run(["aplay", "-l"],
                               capture_output=True, text=True, timeout=5)
            return jsonify({"ok": True, "lines": r.stdout.splitlines()})
        else:
            return jsonify({"ok": False, "error": f"unsupported platform: {sys}"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.route("/system/advanced")
def system_advanced():
    if platform.system() != "FreeBSD":
        return jsonify({"ok": False, "error": "FreeBSD only"})

    sections = []
    for cmd in (["sysctl", "dev.pcm.0"], ["sysctl", "hw.usb.uaudio"]):
        try:
            r = subprocess.run(
                cmd,
                capture_output=True, text=True, timeout=5, env=_env(),
            )
            output = (r.stdout + r.stderr).strip()
            sections.append({
                "title": " ".join(cmd),
                "ok": r.returncode == 0,
                "output": output or f"exit {r.returncode}",
            })
        except subprocess.TimeoutExpired:
            sections.append({
                "title": " ".join(cmd),
                "ok": False,
                "output": "timeout",
            })

    return jsonify({"ok": True, "sections": sections})


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
        procs = []
        for row in _ps_processes():
            if _hide_from_topcpu(row):
                continue
            if row["cpu"] >= TOPCPU_THRESHOLD:
                procs.append(row)
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


@app.route("/filter-response")
def filter_response_page():
    return render_template("filter_response.html")


@app.route("/drc/filter-response")
def drc_filter_response():
    """FFT analysis of the FIR filters loaded by the *running* BruteFIR.

    The active .conf carries absolute paths to its coeff (.raw) files and the
    sampling rate, so no extra configuration is needed.  When BruteFIR is not
    running there is no active filter to analyse.
    """
    conf_path = _active_brutefir_conf()
    if not conf_path:
        return jsonify({
            "ok": False, "running": False,
            "error": "BruteFIR is not running — no active filter loaded.",
        })
    try:
        parsed = _parse_brutefir_conf(conf_path)
        rate = parsed["rate"]
        if not rate or not parsed["coeffs"]:
            return jsonify({"ok": False, "running": True,
                            "error": f"no coeff/sampling_rate in {conf_path}"})
        # geometry = the configs/<geometry>/ directory name, when present.
        geometry = os.path.basename(os.path.dirname(conf_path))
        channels = []
        palette = {"Left": "#388bfd", "Right": "#d29922"}
        for c in parsed["coeffs"]:
            ch = _coeff_channel(c)
            resp = _fir_response(c["filename"], c["format"], rate)
            resp.update({
                "name": ch,
                "color": palette.get(ch, "#3fb950"),
                "attenuation": c["attenuation"],
                "format": c["format"],
                "file": os.path.basename(c["filename"]),
            })
            channels.append(resp)
        return jsonify({
            "ok": True, "running": True,
            "geometry": geometry,
            "rate": rate,
            "conf": os.path.basename(conf_path),
            "channels": channels,
        })
    except FileNotFoundError as e:
        return jsonify({"ok": False, "running": True, "error": f"filter file not found: {e}"})
    except ImportError:
        return jsonify({"ok": False, "running": True, "error": "numpy is required for filter analysis"})
    except Exception as e:
        return jsonify({"ok": False, "running": True, "error": str(e)})


@app.route("/brutefir/cpu")
def brutefir_cpu():
    try:
        procs = []
        total = 0.0
        for row in _ps_processes():
            if _process_name(row["name"]) != "brutefir":
                continue
            procs.append({"pid": row["pid"], "cpu": row["cpu"]})
            total += row["cpu"]
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
