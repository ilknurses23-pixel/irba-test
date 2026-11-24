"""scrcpy entegrasyonu.

* scrcpy.exe: hızlı görüntü, ses, kayıt, OTG, kamera (ayrı süreç).
* ControlSession: scrcpy-server'a doğrudan bağlanıp yalnızca "kontrol" kanalını kullanır.
  Böylece birden çok telefona aynı anda Türkçe metin (pano + yapıştır), tuş ve
  panel komutları gönderilebilir.  (Protokol: scrcpy v5 ControlMessageReader.)
"""
import os
import random
import re
import socket
import struct
import subprocess
import threading
import time

from . import paths
from .adb import CREATE_NO_WINDOW, AdbError

DEVICE_JAR = "/data/local/tmp/irba-scrcpy-server.jar"

T_INJECT_KEYCODE, T_INJECT_TEXT = 0, 1
T_BACK_OR_SCREEN_ON, T_EXPAND_NOTIF, T_EXPAND_SETTINGS, T_COLLAPSE = 4, 5, 6, 7
T_GET_CLIPBOARD, T_SET_CLIPBOARD, T_SET_DISPLAY_POWER, T_ROTATE = 8, 9, 10, 11
T_START_APP = 16


def scrcpy_exe():
    p = paths.tool("scrcpy", "scrcpy.exe")
    return p if os.path.isfile(p) else None


def server_file():
    p = paths.tool("scrcpy", "scrcpy-server")
    return p if os.path.isfile(p) else None


_version_cache = None


def scrcpy_version():
    global _version_cache
    if _version_cache:
        return _version_cache
    exe = scrcpy_exe()
    if not exe:
        return None
    try:
        out = subprocess.run([exe, "--version"], capture_output=True, timeout=10,
                             creationflags=CREATE_NO_WINDOW).stdout.decode("utf-8", "replace")
        m = re.search(r"scrcpy (\d+\.\d+(?:\.\d+)?)", out)
        _version_cache = m.group(1) if m else None
    except (OSError, subprocess.TimeoutExpired):
        _version_cache = None
    return _version_cache


def launch(adb_path, serial, extra_args, title):
    """scrcpy.exe'yi başlatır ve Popen döndürür."""
    exe = scrcpy_exe()
    if not exe:
        raise AdbError("scrcpy bulunamadı (tools\\scrcpy).")
    env = dict(os.environ)
    if adb_path:
        env["ADB"] = adb_path
    args = [exe, "--window-title", title] + (["-s", serial] if serial else []) + list(extra_args)
    return subprocess.Popen(args, env=env, cwd=os.path.dirname(exe),
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            creationflags=CREATE_NO_WINDOW)


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class ControlSession:
    def __init__(self, adb, serial):
        self.adb = adb
        self.serial = serial
        self.sock = None
        self.proc = None
        self.port = None
        self.lock = threading.Lock()
        self.seq = 0
        self.clip_event = threading.Event()
        self.clip_text = None
        self.alive = False

    # ---------------------------------------------------------------- açılış
    def start(self):
        ver = scrcpy_version()
        srv = server_file()
        if not ver or not srv:
            raise AdbError("scrcpy-server bulunamadı.")
        code, out, err = self.adb.push(self.serial, srv, DEVICE_JAR)
        if code != 0:
            raise AdbError("Sunucu telefona gönderilemedi: " + (err or out))
        scid = random.randint(1, 0x7FFFFFFF)
        name = f"scrcpy_{scid:08x}"
        self.port = _free_port()
        code, out, err = self.adb.run(["forward", f"tcp:{self.port}", f"localabstract:{name}"],
                                      serial=self.serial, timeout=10)
        if code != 0:
            raise AdbError("Port yönlendirilemedi: " + (err or out))
        cmd = [self.adb.path, "-s", self.serial, "shell", f"CLASSPATH={DEVICE_JAR}",
               "app_process", "/", "com.genymobile.scrcpy.Server", ver, f"scid={scid:08x}",
               "log_level=warn", "video=false", "audio=false", "control=true",
               "tunnel_forward=true", "send_dummy_byte=true", "send_device_meta=false",
               "clipboard_autosync=false", "cleanup=false", "power_on=false"]
        self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     creationflags=CREATE_NO_WINDOW)
        deadline = time.time() + 12
        last = None
        while time.time() < deadline:
            if self.proc.poll() is not None:
                msg = self.proc.stdout.read().decode("utf-8", "replace").strip()
                raise AdbError("Kontrol sunucusu kapandı: " + (msg[-300:] or "bilinmiyor"))
            try:
                s = socket.create_connection(("127.0.0.1", self.port), timeout=2)
                s.settimeout(2)
                b = s.recv(1)
                if b:
                    s.settimeout(None)
                    self.sock = s
                    break
                s.close()
            except OSError as e:
                last = e
            time.sleep(0.15)
        if not self.sock:
            self.close()
            raise AdbError(f"Kontrol bağlantısı kurulamadı ({last}).")
        self.alive = True
        threading.Thread(target=self._reader, daemon=True).start()
        return self

    def _recv_exact(self, n):
        buf = b""
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise OSError("bağlantı kapandı")
            buf += chunk
        return buf

    def _reader(self):
        try:
            while self.alive:
                t = self._recv_exact(1)[0]
                if t == 0:      # CLIPBOARD
                    ln = struct.unpack(">I", self._recv_exact(4))[0]
                    self.clip_text = self._recv_exact(ln).decode("utf-8", "replace")
                    self.clip_event.set()
                elif t == 1:    # ACK_CLIPBOARD
                    self._recv_exact(8)
                elif t == 2:    # UHID_OUTPUT
                    _id, ln = struct.unpack(">HH", self._recv_exact(4))
                    self._recv_exact(ln)
                else:
                    break
        except OSError:
            pass
        self.alive = False

    def close(self):
        self.alive = False
        try:
            if self.sock:
                self.sock.close()
        except OSError:
            pass
        try:
            if self.proc and self.proc.poll() is None:
                self.proc.kill()
        except OSError:
            pass
        if self.port:
            try:
                self.adb.run(["forward", "--remove", f"tcp:{self.port}"], serial=self.serial, timeout=5)
            except Exception:  # noqa: BLE001
                pass
        self.sock = None

    # ---------------------------------------------------------------- mesajlar
    def _send(self, data):
        if not self.alive or not self.sock:
            raise AdbError("Kontrol bağlantısı yok.")
        with self.lock:
            self.sock.sendall(data)

    def set_clipboard(self, text, paste=False):
        b = text.encode("utf-8")
        self.seq += 1
        self._send(struct.pack(">BQ?I", T_SET_CLIPBOARD, self.seq, paste, len(b)) + b)

    def get_clipboard(self, timeout=3.0):
        self.clip_event.clear()
        self._send(struct.pack(">BB", T_GET_CLIPBOARD, 0))
        if self.clip_event.wait(timeout):
            return self.clip_text
        raise AdbError("Telefon panosu okunamadı.")

    def key(self, keycode):
        for action in (0, 1):   # basma, bırakma
            self._send(struct.pack(">BBiii", T_INJECT_KEYCODE, action, keycode, 0, 0))

    def inject_text(self, text):
        b = text.encode("utf-8")
        self._send(struct.pack(">BI", T_INJECT_TEXT, len(b)) + b)

    def simple(self, msg_type):
        self._send(struct.pack(">B", msg_type))

    def display_power(self, on):
        self._send(struct.pack(">B?", T_SET_DISPLAY_POWER, on))


class ControlManager:
    """Her cihaz için tek bir ControlSession tutar (ihtiyaç olunca açar)."""

    def __init__(self, adb):
        self.adb = adb
        self.sessions = {}
        self.lock = threading.Lock()

    def get(self, serial):
        with self.lock:
            s = self.sessions.get(serial)
            if s and s.alive:
                return s
            if s:
                s.close()
            s = ControlSession(self.adb, serial).start()
            self.sessions[serial] = s
            return s

    def drop(self, serial):
        with self.lock:
            s = self.sessions.pop(serial, None)
        if s:
            s.close()

    def close_all(self):
        with self.lock:
            items = list(self.sessions.values())
            self.sessions.clear()
        for s in items:
            s.close()

    def paste_text(self, serial, text):
        """Unicode metni (Türkçe dahil) telefona yazar: pano + yapıştır."""
        self.get(serial).set_clipboard(text, paste=True)
        return "yazıldı"
