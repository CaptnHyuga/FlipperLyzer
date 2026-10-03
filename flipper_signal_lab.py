#!/usr/bin/env python3
"""Flipper Signal Lab launcher: installs/updates rtl_433, then serves the UI locally.
Standard library only. Run:  python3 flipper_signal_lab.py"""
import errno, hashlib, json, os, platform, re, shutil, subprocess, sys, tempfile, threading, urllib.request, webbrowser, zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

ROOT = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.join(ROOT, "tools")
SRC = os.path.join(TOOLS, "rtl_433_src")
BIN = os.path.join(TOOLS, "bin")
EXE = "rtl_433.exe" if os.name == "nt" else "rtl_433"
PORT = 8765
MAX_POST_BYTES = 16 * 1024 * 1024

class RawSubError(ValueError):
    pass

def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)

def file_matches_sha256(path, digest):
    match = re.fullmatch(r"sha256:([0-9a-fA-F]{64})", digest or "")
    if not match:
        return False
    hasher = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest().lower() == match.group(1).lower()

def find_rtl433():
    for d, _, files in os.walk(BIN):  # also finds it if the zip extracted into a subfolder
        if EXE in files: return os.path.join(d, EXE)
    return shutil.which("rtl_433")

def windows_sync():
    """Windows: download the official prebuilt release from GitHub, and update it when a newer one exists."""
    req = urllib.request.Request("https://api.github.com/repos/merbanan/rtl_433/releases/latest", headers={"User-Agent": "flipper-signal-lab"})
    rel = json.load(urllib.request.urlopen(req, timeout=30))
    vfile = os.path.join(BIN, "VERSION")
    have = open(vfile).read().strip() if os.path.exists(vfile) else None
    if find_rtl433() and have in (None, rel["tag_name"]):
        return  # installed and current (or installed by hand: left untouched)
    asset = next((a for a in rel["assets"] if re.search(r"win-msvc-x64.*\.zip$", a["name"])), None)
    if not asset: print("[setup] No Windows x64 zip found in release", rel["tag_name"]); return
    print("[setup] Downloading", asset["name"]); os.makedirs(BIN, exist_ok=True)
    z = os.path.join(TOOLS, asset["name"])
    try:
        urllib.request.urlretrieve(asset["browser_download_url"], z)
        if not file_matches_sha256(z, asset.get("digest")):
            raise ValueError("Release asset has no valid matching SHA-256 digest")
        with zipfile.ZipFile(z) as zf: zf.extractall(BIN)
    finally:
        if os.path.exists(z): os.remove(z)
    open(vfile, "w").write(rel["tag_name"])

def self_update():
    if os.path.isdir(os.path.join(ROOT, ".git")) and shutil.which("git"):
        r = run(["git", "-C", ROOT, "pull", "--ff-only"])
        print("[update] this app:", (r.stdout or r.stderr).strip().splitlines()[-1:] or "ok")

def build_rtl433():
    need = [t for t in ("git", "cmake") if not shutil.which(t)] + ([] if shutil.which("cc") or shutil.which("gcc") or shutil.which("clang") else ["a C compiler"])
    if need:
        print("[setup] Missing:", ", ".join(need), "\n  Debian/Ubuntu: sudo apt install git cmake build-essential\n  macOS: brew install git cmake  (or simply: brew install rtl_433)")
        return None
    os.makedirs(BIN, exist_ok=True)
    if not os.path.isdir(SRC):
        print("[setup] Downloading rtl_433 source...")
        if run(["git", "clone", "--depth", "1", "https://github.com/merbanan/rtl_433", SRC]).returncode:
            return None
    print("[setup] Compiling rtl_433 (a few minutes the first time)...")
    b = os.path.join(SRC, "build")
    steps = [["cmake", "-S", SRC, "-B", b, "-DCMAKE_BUILD_TYPE=Release"], ["cmake", "--build", b, "-j", str(os.cpu_count() or 2)]]
    for s in steps:
        r = run(s)
        if r.returncode:
            print(r.stdout[-1500:], r.stderr[-1500:]); return None
    shutil.copy(os.path.join(b, "src", EXE), os.path.join(BIN, EXE))
    return os.path.join(BIN, EXE)

def ensure_rtl433():
    if os.name == "nt":
        try: windows_sync()
        except Exception as e: print("[setup] Could not check/download rtl_433:", e)
        exe = find_rtl433()
        print("[ok] rtl_433:", exe or "not available (built-in analyzer only)")
        return exe
    exe = find_rtl433()
    if exe and os.path.isdir(os.path.join(SRC, ".git")):  # we built it: check for updates
        r = run(["git", "-C", SRC, "pull", "--ff-only"])
        if "Already up to date" not in r.stdout:
            print("[update] rtl_433 has updates, rebuilding..."); exe = build_rtl433() or exe
    elif not exe:
        if platform.system() == "Darwin" and shutil.which("brew"):
            print("[setup] brew install rtl_433"); run(["brew", "install", "rtl_433"]); exe = find_rtl433()
        else:
            exe = build_rtl433()
    print("[ok] rtl_433:", exe or "not available (built-in analyzer only)")
    return exe

def parse_raw_sub(text, warning_sink=None):
    fields = {}
    values = []
    warnings = []
    for line_number, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            raise RawSubError("line %d is not a Flipper Format field" % line_number)
        key, value = (part.strip() for part in line.split(":", 1))
        if key == "RAW_Data":
            tokens = value.split()
            if not tokens:
                raise RawSubError("RAW_Data on line %d is empty" % line_number)
            if len(tokens) > 512:
                warnings.append("RAW_Data line %d has %d values (Flipper documents a 512-value limit)." % (line_number, len(tokens)))
            for token in tokens:
                if not re.fullmatch(r"[+-]?[0-9]+", token):
                    raise RawSubError("RAW_Data on line %d contains a non-integer value" % line_number)
                duration = int(token)
                if duration == 0 or not -(2 ** 31) <= duration < 2 ** 31:
                    raise RawSubError("RAW_Data values must be nonzero signed 32-bit integers")
                values.append(duration)
        else:
            if key in fields:
                raise RawSubError("duplicate %s field" % key)
            fields[key] = value

    required = ("Filetype", "Version", "Frequency", "Preset", "Protocol")
    missing = [key for key in required if not fields.get(key)]
    if missing:
        raise RawSubError("missing required field(s): %s" % ", ".join(missing))
    if fields["Filetype"] != "Flipper SubGhz RAW File":
        raise RawSubError("Filetype must be Flipper SubGhz RAW File")
    if fields["Version"] != "1":
        raise RawSubError("unsupported RAW file version: %s" % fields["Version"])
    if fields["Protocol"] != "RAW":
        raise RawSubError("Protocol must be RAW")
    if not re.fullmatch(r"[0-9]+", fields["Frequency"]):
        raise RawSubError("Frequency must be an unsigned integer in hertz")
    frequency = int(fields["Frequency"])
    if not 0 < frequency <= 2 ** 32 - 1:
        raise RawSubError("Frequency is outside the Flipper uint32 range")
    if fields["Preset"] == "FuriHalSubGhzPresetCustom":
        if not fields.get("Custom_preset_module") or not fields.get("Custom_preset_data"):
            raise RawSubError("custom presets require Custom_preset_module and Custom_preset_data")
        if not re.fullmatch(r"[0-9A-Fa-f]{2}(?:\s+[0-9A-Fa-f]{2})*", fields["Custom_preset_data"]):
            raise RawSubError("Custom_preset_data must be space-separated hexadecimal bytes")
    if not values:
        raise RawSubError("RAW_Data is required")
    leading_count = 0
    leading_gap = 0
    while leading_count < len(values) and values[leading_count] < 0:
        leading_gap -= values[leading_count]
        leading_count += 1
    if leading_count:
        values = values[leading_count:]
    if not values:
        raise RawSubError("RAW_Data has no positive pulse after the leading gap")
    if leading_count:
        warnings.append("Dropped %d leading negative timing(s) totaling %d us." % (leading_count, leading_gap))
    merged = []
    merged_signs = 0
    for duration in values:
        if merged and (merged[-1] > 0) == (duration > 0):
            combined = merged[-1] + duration
            if not -(2 ** 31) <= combined < 2 ** 31:
                raise RawSubError("merged RAW_Data duration exceeds signed 32-bit range")
            merged[-1] = combined
            merged_signs += 1
        else:
            merged.append(duration)
    if merged_signs:
        warnings.append("Merged %d repeated-sign timing(s) into continuous levels." % merged_signs)
    if merged[-1] > 0:
        closing_gap = max(10000, max((-value for value in merged if value < 0), default=0))
        merged.append(-closing_gap)
        warnings.append("Added a synthetic %d us closing gap for the final pulse." % closing_gap)
    if warning_sink is not None:
        warning_sink.extend(warnings)
    return fields, merged

def sub_to_ook(text, warning_sink=None):
    warnings = []
    fields, vals = parse_raw_sub(text, warnings)
    if warning_sink is not None:
        warning_sink.extend(warnings)
    if not fields["Preset"].startswith("FuriHalSubGhzPresetOok"):
        raise RawSubError("rtl_433 OOK analysis is only valid for a standard OOK preset")
    out = [";pulse data", ";version 1", ";timescale 1us", ";freq1 %s" % fields["Frequency"], ";freq2 0"]
    out += ["%d %d" % (vals[i], -vals[i + 1]) for i in range(0, len(vals) - 1, 2)]
    return "\n".join(out + [";end"]) + "\n", len(vals)

def is_local_http_request(host_header, origin_header, port):
    local_hosts = {"127.0.0.1", "localhost"}
    try:
        if "@" in (host_header or ""):
            return False
        host = urlsplit("//" + (host_header or "")).hostname
        host_port = urlsplit("//" + (host_header or "")).port
        if not host or host.lower().rstrip(".") not in local_hosts or host_port not in (None, port):
            return False
        if not origin_header:
            return True
        origin = urlsplit(origin_header)
        return (
            origin.scheme == "http"
            and origin.hostname is not None
            and origin.hostname.lower().rstrip(".") in local_hosts
            and origin.port == port
        )
    except ValueError:
        return False

def analyze_with_rtl433(text):
    warnings = []
    try:
        ook, _ = sub_to_ook(text, warnings)
    except RawSubError as e:
        return "RAW analysis unavailable: %s" % e
    note = "\n".join(warnings) + ("\n\n" if warnings else "")
    exe = find_rtl433()
    if not exe: return note + "rtl_433 is not installed, so only the built-in analysis is shown."
    with tempfile.NamedTemporaryFile("w", suffix=".ook", delete=False) as f: f.write(ook)
    try:
        r = run([exe, "-r", f.name, "-A", "-F", "json"], timeout=30)
        out = (r.stdout + "\n" + r.stderr).strip()
    except Exception as e:
        out = "rtl_433 failed: %s" % e
    finally: os.unlink(f.name)
    return note + (out or "rtl_433 produced no output.")

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        if not is_local_http_request(self.headers.get("Host"), self.headers.get("Origin"), self.server.server_port):
            self.send_error(403)
            return
        body = open(os.path.join(ROOT, "ui", "index.html"), "rb").read()
        self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8"); self.end_headers(); self.wfile.write(body)
    def do_POST(self):
        if not is_local_http_request(self.headers.get("Host"), self.headers.get("Origin"), self.server.server_port):
            self.send_error(403)
            return
        if self.path != "/api/rtl433":
            self.send_error(404)
            return
        length = self.headers.get("Content-Length", "")
        if not length.isdigit():
            self.send_error(411)
            return
        if int(length) > MAX_POST_BYTES:
            self.send_error(413)
            return
        text = self.rfile.read(int(length)).decode("utf-8", "replace")
        body = json.dumps({"output": analyze_with_rtl433(text)}).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(body)

class LocalThreadingHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = False

def create_server():
    try:
        return LocalThreadingHTTPServer(("127.0.0.1", PORT), H)
    except OSError as e:
        if e.errno not in (errno.EADDRINUSE, errno.EACCES):
            raise
        return LocalThreadingHTTPServer(("127.0.0.1", 0), H)

if __name__ == "__main__":
    if sys.version_info < (3, 8): sys.exit("Python 3.8+ required")
    self_update(); ensure_rtl433()
    srv = create_server()
    if srv.server_port != PORT:
        print("[run] Port %d is busy; using %d" % (PORT, srv.server_port))
    url = "http://127.0.0.1:%d" % srv.server_port
    print("[run] Open", url, "(Ctrl+C to stop)"); threading.Timer(1, lambda: webbrowser.open(url)).start()
    try: srv.serve_forever()
    except KeyboardInterrupt: pass
