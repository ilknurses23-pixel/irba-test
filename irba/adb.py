"""ADB komut katmanı (arayüzden bağımsız)."""
import ipaddress
import os
import re
import shutil
import socket
import subprocess
from concurrent.futures import ThreadPoolExecutor

from . import paths

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

KEY_HOME, KEY_BACK, KEY_RECENTS, KEY_POWER = 3, 4, 187, 26
KEY_VOL_UP, KEY_VOL_DOWN, KEY_MUTE = 24, 25, 164
KEY_WAKE, KEY_SLEEP, KEY_ENTER, KEY_DEL, KEY_MENU = 224, 223, 66, 67, 82

KEY_NAMES = {
    "Ana ekran": KEY_HOME, "Geri": KEY_BACK, "Son uygulamalar": KEY_RECENTS,
    "Güç": KEY_POWER, "Ses +": KEY_VOL_UP, "Ses −": KEY_VOL_DOWN,
    "Ekranı uyandır": KEY_WAKE, "Ekranı kilitle": KEY_SLEEP, "Enter": KEY_ENTER,
    "Sil (Backspace)": KEY_DEL, "Menü": KEY_MENU,
}


class AdbError(Exception):
    pass


def _listen_pid(port):
    """Yerel TCP portunu dinleyen sürecin PID'i (Windows)."""
    if os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes
    iphlp = ctypes.WinDLL("iphlpapi")
    size = wintypes.DWORD(0)
    iphlp.GetExtendedTcpTable(None, ctypes.byref(size), False, 2, 3, 0)   # AF_INET, LISTENER
    buf = (ctypes.c_ubyte * (size.value + 4096))()
    size = wintypes.DWORD(len(buf))
    if iphlp.GetExtendedTcpTable(buf, ctypes.byref(size), False, 2, 3, 0) != 0:
        return None
    n = ctypes.c_uint32.from_buffer(buf, 0).value
    for i in range(n):
        off = 4 + i * 24
        local_port = socket.ntohs(ctypes.c_uint32.from_buffer(buf, off + 8).value & 0xFFFF)
        if local_port == port:
            return ctypes.c_uint32.from_buffer(buf, off + 20).value
    return None


def _process_path(pid):
    import ctypes
    from ctypes import wintypes
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.OpenProcess.restype = wintypes.HANDLE
    h = k.OpenProcess(0x1000, False, pid)
    if not h:
        return None
    try:
        buf = ctypes.create_unicode_buffer(1024)
        n = wintypes.DWORD(1024)
        if k.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)):
            return buf.value
    finally:
        k.CloseHandle(h)
    return None


def running_server_adb(port=5037):
    """Bilgisayarda zaten çalışan ADB sunucusunun adb.exe yolu (yoksa None)."""
    try:
        pid = _listen_pid(port)
        if not pid:
            return None
        p = _process_path(pid)
        if p and os.path.basename(p).lower() == "adb.exe" and os.path.isfile(p):
            return p
    except Exception:  # noqa: BLE001
        return None
    return None


def find_adb():
    cands = [paths.tool("scrcpy", "adb.exe"), os.environ.get("ADB"), shutil.which("adb"),
             os.path.join(os.environ.get("LOCALAPPDATA", ""), "Android", "Sdk",
                          "platform-tools", "adb.exe")]
    for c in cands:
        if c and os.path.isfile(c):
            return c
    return None


class Adb:
    def __init__(self, path):
        self.path = path

    def run(self, args, serial=None, timeout=30, binary=False):
        if not self.path or not os.path.isfile(self.path):
            raise AdbError("adb.exe bulunamadı. Ayarlar sekmesinden adb yolunu seçin.")
        cmd = [self.path] + (["-s", serial] if serial else []) + list(args)
        try:
            p = subprocess.run(cmd, capture_output=True, timeout=timeout,
                               creationflags=CREATE_NO_WINDOW)
        except subprocess.TimeoutExpired:
            raise AdbError("Zaman aşımı: adb " + " ".join(args)[:80])
        if binary:
            if p.returncode != 0:
                raise AdbError(p.stderr.decode("utf-8", "replace").strip() or "adb hatası")
            return p.stdout
        return (p.returncode, p.stdout.decode("utf-8", "replace").strip(),
                p.stderr.decode("utf-8", "replace").strip())

    def shell(self, serial, command, timeout=30):
        return self.run(["shell", command], serial=serial, timeout=timeout)

    def out(self, serial, command, timeout=30):
        code, out, err = self.shell(serial, command, timeout)
        return out

    # ---- sunucu ve cihazlar --------------------------------------------
    def start_server(self):
        return self.run(["start-server"], timeout=30)

    def kill_server(self):
        return self.run(["kill-server"], timeout=15)

    def version(self):
        code, out, err = self.run(["version"], timeout=10)
        m = re.search(r"Version ([\w\.\-]+)", out)
        return m.group(1) if m else out.splitlines()[0] if out else "?"

    def devices(self):
        code, out, err = self.run(["devices", "-l"], timeout=15)
        res = []
        for line in out.splitlines()[1:]:
            line = line.strip()
            if not line or line.startswith("*"):
                continue
            parts = line.split()
            extra = dict(p.split(":", 1) for p in parts[2:] if ":" in p)
            res.append({"serial": parts[0], "state": parts[1] if len(parts) > 1 else "?",
                        "model": extra.get("model", "").replace("_", " ")})
        return res

    def info(self, serial):
        cmd = ("echo MODEL=$(getprop ro.product.model);"
               "echo BRAND=$(getprop ro.product.manufacturer);"
               "echo ANDROID=$(getprop ro.build.version.release);"
               "echo SDK=$(getprop ro.build.version.sdk);"
               "echo ABI=$(getprop ro.product.cpu.abi);"
               "echo BATT=$(dumpsys battery | grep -m1 level);"
               "echo CHG=$(dumpsys battery | grep -m1 -E 'AC powered|USB powered' | grep -c true);"
               "echo SIZE=$(wm size | tail -n1);"
               "echo DENS=$(wm density | tail -n1);"
               "echo STOR=$(df /data 2>/dev/null | tail -n1);"
               "echo IP=$(ip -f inet addr show wlan0 2>/dev/null | grep -m1 inet)")
        code, out, err = self.shell(serial, cmd, timeout=20)
        d = {}
        for line in out.splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                d[k.strip()] = v.strip()
        info = {"model": d.get("MODEL", ""), "brand": d.get("BRAND", ""),
                "android": d.get("ANDROID", ""), "sdk": d.get("SDK", ""),
                "abi": d.get("ABI", ""), "battery": "", "charging": d.get("CHG", "0") not in ("", "0"),
                "width": 0, "height": 0, "density": "", "storage": "", "ip": ""}
        m = re.search(r"(\d+)", d.get("BATT", ""))
        if m:
            info["battery"] = m.group(1) + "%"
        m = re.search(r"(\d+)x(\d+)", d.get("SIZE", ""))
        if m:
            info["width"], info["height"] = int(m.group(1)), int(m.group(2))
        m = re.search(r"(\d+)\s*$", d.get("DENS", ""))
        if m:
            info["density"] = m.group(1) + " dpi"
        parts = d.get("STOR", "").split()
        if len(parts) >= 4 and parts[1].isdigit():
            total, used = int(parts[1]) / 1048576, int(parts[2]) / 1048576
            info["storage"] = f"{used:.1f} / {total:.1f} GB dolu"
        m = re.search(r"inet (\d+\.\d+\.\d+\.\d+)", d.get("IP", ""))
        if m:
            info["ip"] = m.group(1)
        return info

    def orientation(self, serial):
        out = self.out(serial, "dumpsys input | grep -m1 -i SurfaceOrientation", timeout=10)
        m = re.search(r"(\d)", out)
        return int(m.group(1)) if m else 0

    # ---- bağlantı --------------------------------------------------------
    def connect(self, addr):
        code, out, err = self.run(["connect", addr], timeout=15)
        return out or err

    def disconnect(self, addr):
        code, out, err = self.run(["disconnect", addr], timeout=10)
        return out or err

    def pair(self, addr, code_):
        code, out, err = self.run(["pair", addr, code_], timeout=30)
        return out or err

    def tcpip(self, serial, port=5555):
        code, out, err = self.run(["tcpip", str(port)], serial=serial, timeout=15)
        return out or err

    # ---- kontrol ---------------------------------------------------------
    def screencap(self, serial):
        data = self.run(["exec-out", "screencap", "-p"], serial=serial, timeout=15, binary=True)
        if not data.startswith(b"\x89PNG"):
            raise AdbError("Ekran görüntüsü alınamadı (ekran kilitli/korumalı olabilir).")
        return data

    def tap(self, serial, x, y):
        return self.shell(serial, f"input tap {int(x)} {int(y)}")

    def swipe(self, serial, x1, y1, x2, y2, ms=300):
        return self.shell(serial, f"input swipe {int(x1)} {int(y1)} {int(x2)} {int(y2)} {int(ms)}")

    def key(self, serial, keycode):
        return self.shell(serial, f"input keyevent {int(keycode)}")

    def text_ascii(self, serial, text):
        safe = "".join(c for c in text if 32 <= ord(c) < 127)
        safe = safe.replace("\\", "\\\\").replace("'", "'\\''").replace(" ", "%s")
        return self.shell(serial, f"input text '{safe}'")

    # ---- uygulamalar -----------------------------------------------------
    def packages(self, serial, system=False):
        flag = "" if system else "-3"
        out = self.out(serial, f"pm list packages {flag}", timeout=30)
        return sorted(l[8:].strip() for l in out.splitlines() if l.startswith("package:"))

    def launch(self, serial, pkg):
        return self.shell(serial, f"monkey -p {pkg} -c android.intent.category.LAUNCHER 1")

    def force_stop(self, serial, pkg):
        return self.shell(serial, f"am force-stop {pkg}")

    def clear_data(self, serial, pkg):
        return self.shell(serial, f"pm clear {pkg}")

    def uninstall(self, serial, pkg):
        return self.run(["uninstall", pkg], serial=serial, timeout=120)

    def pull_apk(self, serial, pkg, dest_dir):
        out = self.out(serial, f"pm path {pkg}")
        paths_ = [l[8:].strip() for l in out.splitlines() if l.startswith("package:")]
        if not paths_:
            raise AdbError("APK yolu bulunamadı.")
        results = []
        for i, p in enumerate(paths_):
            name = f"{pkg}.apk" if i == 0 else f"{pkg}_{os.path.basename(p)}"
            dst = os.path.join(dest_dir, name)
            code, o, e = self.run(["pull", p, dst], serial=serial, timeout=600)
            if code != 0:
                raise AdbError(e or o)
            results.append(dst)
        return results

    def install(self, serial, apk):
        return self.run(["install", "-r", "-d", apk], serial=serial, timeout=900)

    def push(self, serial, src, dst):
        return self.run(["push", src, dst], serial=serial, timeout=900)

    def reboot(self, serial, mode=None):
        return self.run(["reboot"] + ([mode] if mode else []), serial=serial, timeout=15)


# ---- ağ taraması --------------------------------------------------------
def local_ipv4s():
    ips = set()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ips.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    try:
        for ip in socket.gethostbyname_ex(socket.gethostname())[2]:
            ips.add(ip)
    except OSError:
        pass
    return [ip for ip in ips if ipaddress.ip_address(ip).is_private and not ip.startswith("127.")]


def scan_network(port=5555, timeout=0.35, progress=None):
    """Yerel /24 ağlarında ADB portu açık olan adresleri bulur."""
    targets = []
    for ip in local_ipv4s():
        net = ipaddress.ip_network(ip + "/24", strict=False)
        targets += [str(h) for h in net.hosts() if str(h) != ip]

    def probe(h):
        try:
            with socket.create_connection((h, port), timeout=timeout):
                return h
        except OSError:
            return None
    found = []
    with ThreadPoolExecutor(max_workers=96) as ex:
        for i, r in enumerate(ex.map(probe, targets)):
            if r:
                found.append(f"{r}:{port}")
            if progress and i % 32 == 0:
                progress(i, len(targets))
    return found
