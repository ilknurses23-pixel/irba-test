"""irba test — Android cihazları bilgisayardan toplu yönetme programı.

Düzen: solda tek kontrol paneli, ortada tüm telefonların canlı ekranları,
sağda (açılınca) seçilen telefonun büyük ekranı. Diğer araçlar ayrı pencerelerde.
"""
import base64
import json
import math
import os
import queue
import random
import subprocess
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

from . import adb as A
from . import paths
from . import scrcpy as S
from . import updater as U
from . import winutil as W

APP = paths.APP_NAME

C = {
    "bg": "#0f1a2e", "side": "#0c1526", "panel": "#15223a", "panel2": "#1c2c48", "hover": "#24385c",
    "fg": "#e8eef8", "muted": "#8b9bb8", "accent": "#3d7cf2", "accent2": "#2f69d6", "accentbg": "#1f3f78",
    "ok": "#3ecf8e", "warn": "#f5a524", "err": "#f05252", "screen": "#05080f", "line": "#22314f",
}

TILE_W = {"Küçük": 150, "Orta": 185, "Büyük": 240}
QUALITY = {
    "Yüksek (1920p)": ["-m", "1920", "-b", "8M", "--max-fps", "60"],
    "Orta (1280p)": ["-m", "1280", "-b", "4M", "--max-fps", "60"],
    "Düşük (800p)": ["-m", "800", "-b", "2M", "--max-fps", "30"],
}
KEY_LABEL = {v: k for k, v in A.KEY_NAMES.items()}

DEFAULT_CFG = {
    "aliases": {}, "groups": {}, "group_names": [], "order": [], "adb_path": None,
    "close_to_tray": True, "tray_hint_shown": False, "quality": "Orta (1280p)",
    "audio": False, "stay_awake": True, "media_dir": None, "preview_live": True,
    "tile_size": "Orta", "preview_fps": 15, "shot_interval": 5,
    "update_source": paths.UPDATE_URL, "update_on_start": True,
}


def cfg_path():
    return os.path.join(paths.data_dir(), "ayarlar.json")


def load_cfg():
    cfg = json.loads(json.dumps(DEFAULT_CFG))
    try:
        with open(cfg_path(), encoding="utf-8") as f:
            cfg.update(json.load(f))
    except Exception:  # noqa: BLE001
        pass
    return cfg


def save_cfg(cfg):
    try:
        with open(cfg_path(), "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


def png_to_photo(data):
    return tk.PhotoImage(data=base64.b64encode(data))


def safe_name(name):
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in name)[:40]


class ToolWindow:
    """Araç penceresi: bir kez oluşturulur, kapatılınca gizlenir."""

    def __init__(self, app, title, builder, size):
        self.win = tk.Toplevel(app)
        self.win.withdraw()
        self.win.title(f"{title} — {APP}")
        self.win.configure(bg=C["panel"])
        self.win.geometry(size)
        self.win.protocol("WM_DELETE_WINDOW", self.win.withdraw)
        ico = paths.asset("irba.ico")
        if os.path.isfile(ico):
            try:
                self.win.iconbitmap(ico)
            except tk.TclError:
                pass
        canvas = tk.Canvas(self.win, bg=C["panel"], highlightthickness=0, bd=0)
        sb = ttk.Scrollbar(self.win, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=sb.set)
        canvas._irba_scroll = True
        inner = tk.Frame(canvas, bg=C["panel"])
        wid = canvas.create_window((0, 0), window=inner, anchor="nw")
        frame = builder(inner)
        frame.pack(fill="both", expand=True)
        inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(
            wid, width=e.width, height=max(e.height, inner.winfo_reqheight())))
        sb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        self.win.update_idletasks()
        dw, dh = (int(x) for x in size.split("x"))
        sw, sh = self.win.winfo_screenwidth(), self.win.winfo_screenheight()
        w = min(max(dw, inner.winfo_reqwidth() + 24), sw - 40)
        h = min(max(dh, inner.winfo_reqheight() + 4), sh - 80)
        self.win.geometry(f"{w}x{h}+{max(0, (sw - w) // 2)}+{max(0, (sh - h) // 2 - 20)}")

    def show(self):
        self.win.deiconify()
        self.win.lift()
        self.win.focus_force()


class App(tk.Tk):
    # =================================================================== kurulum
    def __init__(self):
        super().__init__()
        self.title(APP)
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        w, h = min(1600, sw - 60), min(960, sh - 90)
        self.geometry(f"{w}x{h}+{(sw - w) // 2}+{max(0, (sh - h) // 2 - 20)}")
        self.minsize(min(1100, sw - 40), min(680, sh - 80))
        if sw < 2200:
            self.after(10, lambda: self.state("zoomed"))
        self.configure(bg=C["bg"])
        ico = paths.asset("irba.ico")
        if os.path.isfile(ico):
            try:
                self.iconbitmap(default=ico)
            except tk.TclError:
                pass

        self.cfg = load_cfg()
        if not (self.cfg.get("update_source") or "").strip():
            self.cfg["update_source"] = paths.UPDATE_URL
        if not self.cfg.get("media_dir"):
            self.cfg["media_dir"] = paths.default_media_dir()
        p = self.cfg.get("adb_path")
        self.adb = A.Adb(p if p and os.path.isfile(p) else A.find_adb())
        self._shared_note = None
        if not (p and os.path.isfile(p)):
            shared = A.running_server_adb()
            if shared and os.path.normcase(shared) != os.path.normcase(self.adb.path or ""):
                self.adb.path = shared
                self._shared_note = shared
        self.ctl = S.ControlManager(self.adb)

        self.q = queue.Queue()
        self.devices = {}
        self.selected = set()
        self.focus_serial = None
        self.group_filter = None        # None: tümü, "": grupsuz, "ad": grup
        self.tiles = {}
        self.orients = {}
        self.big = None
        self.overlays = {}
        self._press = None
        self._kbuf = ""
        self._kbuf_job = None
        self._refreshing = False
        self._last_dev_err = None
        self._tick = 0
        self._drag_mode = None
        self._anchor = None
        self.macro = []
        self.rec_on = False
        self._rec_last = 0
        self.play_stop = threading.Event()
        self.playing = 0
        self.app_list = []
        self.tray = None
        self.updating = False

        self._style()
        self._build()
        self.tools = {}
        for key, title, builder, size in (
                ("wifi", "Wi-Fi bağlantısı", self._build_wifi, "620x520"),
                ("batch", "Toplu işlemler", self._build_batch, "900x700"),
                ("apps", "Uygulamalar", self._build_apps, "820x620"),
                ("macro", "Otomasyon", self._build_macro, "980x660"),
                ("info", "Cihaz bilgisi", self._build_info, "620x640"),
                ("settings", "Ayarlar", self._build_settings, "860x760")):
            self.tools[key] = ToolWindow(self, title, builder, size)

        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.after(30, self._pump)
        self.drop = W.DropTarget(self, lambda files: self.q.put((self.on_drop, files, None)))
        self._start_tray()

        self.log(f"{APP} {paths.APP_VERSION} başlatıldı.", "muted")
        if self._shared_note:
            self.log(f"Başka bir programın ADB sunucusu çalışıyor; çakışmayı önlemek için ortak kullanılıyor: "
                     f"{self._shared_note}", "muted")
        if not self.adb.path:
            self.log("adb.exe bulunamadı. Ayarlar'dan seçin.", "err")
        if not S.scrcpy_exe():
            self.log("scrcpy bulunamadı; canlı ekran çalışmaz.", "err")
        self.refresh_devices()
        self.after(4000, self._auto_loop)
        self.after(300, self._overlay_loop)
        if self.cfg.get("update_on_start") and self.cfg.get("update_source"):
            self.after(6000, lambda: self.check_update(silent=True))

    # =================================================================== altyapı
    def bg(self, fn, *args, done=None, fail=None):
        def work():
            try:
                self.q.put((done, fn(*args), None))
            except Exception as e:  # noqa: BLE001
                self.q.put((fail, None, e))
        threading.Thread(target=work, daemon=True).start()

    def _pump(self):
        try:
            for _ in range(200):
                cb, res, err = self.q.get_nowait()
                try:
                    if err is not None:
                        cb(err) if cb else self.log(f"Hata: {err}", "err")
                    elif cb:
                        cb(res)
                except Exception as e:  # noqa: BLE001
                    self.log(f"Arayüz hatası: {e}", "err")
        except queue.Empty:
            pass
        self.after(30, self._pump)

    def ui(self, fn, *args):
        self.q.put((lambda _r: fn(*args), None, None))

    def log(self, msg, tag=None):
        self.log_txt.configure(state="normal")
        self.log_txt.insert("end", time.strftime("%H:%M:%S") + "  ", "muted")
        self.log_txt.insert("end", str(msg) + "\n", tag or ())
        n = int(self.log_txt.index("end-1c").split(".")[0])
        if n > 1000:
            self.log_txt.delete("1.0", f"{n - 1000}.0")
        self.log_txt.see("end")
        self.log_txt.configure(state="disabled")
        self.status_lbl.configure(text=str(msg).split("\n")[0][:90],
                                  fg={"err": C["err"], "warn": C["warn"], "ok": C["ok"]}.get(tag, C["muted"]))

    def num(self, s):
        try:
            return self.cfg["order"].index(s) + 1
        except ValueError:
            return 0

    def name(self, s):
        d = self.devices.get(s, {})
        info = d.get("info") or {}
        return self.cfg["aliases"].get(s) or info.get("model") or d.get("model") or s

    def label(self, s):
        return f"{self.num(s):02d} {self.cfg['aliases'].get(s) or s}"

    def online(self):
        return [s for s in self.ordered() if self.devices[s].get("state") == "device"]

    def ordered(self):
        return sorted(self.devices, key=lambda s: self.num(s) or 9999)

    def is_online(self, s):
        return self.devices.get(s, {}).get("state") == "device"

    def save(self):
        save_cfg(self.cfg)

    # =================================================================== stil
    def _style(self):
        s = ttk.Style(self)
        s.theme_use("clam")
        s.configure(".", background=C["panel"], foreground=C["fg"], font=("Segoe UI", 10),
                    fieldbackground=C["panel2"], bordercolor=C["line"], lightcolor=C["panel"],
                    darkcolor=C["panel"], troughcolor=C["panel"], focuscolor=C["accent"])
        s.configure("TFrame", background=C["panel"])
        s.configure("TLabel", background=C["panel"], foreground=C["fg"])
        s.configure("M.TLabel", background=C["panel"], foreground=C["muted"])
        s.configure("H.TLabel", background=C["panel"], font=("Segoe UI Semibold", 13))
        s.configure("Big.TLabel", background=C["panel"], font=("Segoe UI Semibold", 16))
        s.configure("Acc.TLabel", background=C["panel"], foreground=C["accent"], font=("Segoe UI Semibold", 10))
        s.configure("TButton", background=C["panel2"], foreground=C["fg"], padding=(12, 6), borderwidth=0)
        s.map("TButton", background=[("disabled", C["panel"]), ("active", C["hover"])],
              foreground=[("disabled", C["muted"])])
        s.configure("A.TButton", background=C["accent"], foreground="white")
        s.map("A.TButton", background=[("active", C["accent2"])])
        s.configure("D.TButton", background="#5a2a2e", foreground="#ffd7d7")
        s.map("D.TButton", background=[("active", "#6e3136")])
        s.configure("N.TButton", padding=(8, 7))
        s.configure("TEntry", fieldbackground=C["panel2"], foreground=C["fg"], insertcolor=C["fg"], padding=6, borderwidth=0)
        s.configure("TSpinbox", fieldbackground=C["panel2"], foreground=C["fg"], arrowcolor=C["fg"], background=C["panel2"], padding=4)
        s.configure("TCombobox", fieldbackground=C["panel2"], background=C["panel2"], foreground=C["fg"], arrowcolor=C["fg"], padding=4)
        s.map("TCombobox", fieldbackground=[("readonly", C["panel2"])], foreground=[("readonly", C["fg"])],
              selectbackground=[("readonly", C["panel2"])], selectforeground=[("readonly", C["fg"])])
        self.option_add("*TCombobox*Listbox.background", C["panel2"])
        self.option_add("*TCombobox*Listbox.foreground", C["fg"])
        self.option_add("*TCombobox*Listbox.selectBackground", C["accent"])
        for w in ("TCheckbutton", "TRadiobutton"):
            s.configure(w, background=C["panel"], foreground=C["fg"])
            s.map(w, background=[("active", C["panel"])],
                  indicatorcolor=[("selected", C["accent"]), ("!selected", C["panel2"])])
        s.configure("Side.TCheckbutton", background=C["side"], foreground=C["fg"], font=("Segoe UI", 10))
        s.map("Side.TCheckbutton", background=[("active", C["side"])],
              indicatorcolor=[("selected", C["accent"]), ("!selected", C["panel2"])])
        s.configure("Treeview", background=C["panel"], fieldbackground=C["panel"], foreground=C["fg"], rowheight=28, borderwidth=0)
        s.map("Treeview", background=[("selected", C["accentbg"])], foreground=[("selected", "white")])
        s.configure("Treeview.Heading", background=C["panel2"], foreground=C["muted"], relief="flat", padding=(6, 5))
        s.map("Treeview.Heading", background=[("active", C["panel2"])])
        s.configure("TLabelframe", background=C["panel"], bordercolor=C["line"], relief="solid", borderwidth=1)
        s.configure("TLabelframe.Label", background=C["panel"], foreground=C["muted"], font=("Segoe UI Semibold", 10))
        s.configure("Vertical.TScrollbar", background=C["panel2"], troughcolor=C["bg"], arrowcolor=C["muted"], borderwidth=0)

    def menu(self, parent):
        return tk.Menu(parent, tearoff=0, bg=C["panel2"], fg=C["fg"], activebackground=C["accent"],
                       activeforeground="white", bd=0, relief="flat")

    def box(self, parent, title):
        lf = ttk.LabelFrame(parent, text="  " + title + "  ", padding=12)
        lf.pack(fill="x", pady=(12, 0))
        return lf

    def row(self, parent, **pack):
        r = ttk.Frame(parent)
        r.pack(fill="x", **pack)
        return r

    def need_device(self):
        messagebox.showinfo(APP, "Önce bir cihaz seçin.")

    # =================================================================== iskelet
    def _build(self):
        main = tk.Frame(self, bg=C["bg"])
        main.pack(fill="both", expand=True)
        self.left = self._build_left(main)
        self.left.pack(side="left", fill="y")
        tk.Frame(main, bg=C["line"], width=1).pack(side="left", fill="y")
        self.dock = self._build_dock(main)          # sağ büyük ekran (gizli)
        self.center = self._build_center(main)
        self.center.pack(side="left", fill="both", expand=True)

    # ------------------------------------------------------------ sol panel
    def _build_left(self, parent):
        outer = tk.Frame(parent, bg=C["side"], width=400)
        outer.pack_propagate(False)
        sb = C["side"]
        self.left_canvas = tk.Canvas(outer, bg=sb, highlightthickness=0, bd=0)
        f = tk.Frame(self.left_canvas, bg=sb)
        win_id = self.left_canvas.create_window((0, 0), window=f, anchor="nw")
        f.bind("<Configure>", lambda e: self.left_canvas.configure(scrollregion=self.left_canvas.bbox("all")))
        self.left_canvas.bind("<Configure>", lambda e: self.left_canvas.itemconfigure(win_id, width=e.width))

        head = tk.Frame(f, bg=sb)
        head.pack(fill="x", padx=18, pady=(18, 6))
        try:
            self._logo = tk.PhotoImage(file=paths.asset("irba_40.png"))
            tk.Label(head, image=self._logo, bg=sb).pack(side="left")
        except tk.TclError:
            pass
        tk.Label(head, text="irba test", bg=sb, fg=C["fg"], font=("Segoe UI Semibold", 17)).pack(side="left", padx=10)
        tk.Label(head, text=f"v{paths.APP_VERSION}", bg=sb, fg=C["muted"], font=("Segoe UI", 9)).pack(side="left", pady=(8, 0))

        tk.Label(f, text="Cihaz araçları", bg=sb, fg=C["fg"], font=("Segoe UI Semibold", 12)).pack(anchor="w", padx=14, pady=(14, 8))
        grid = tk.Frame(f, bg=sb)
        grid.pack(fill="x", padx=10)
        for i in range(3):
            grid.columnconfigure(i, weight=1, uniform="t")
        self.tool_btns = {}
        items = (("preview", "◉", "Önizleme", self.toggle_preview),
                 ("wifi", "≋", "WiFi", lambda: self.tools["wifi"].show()),
                 ("apk", "⬇", "Toplu APK", self.quick_apk),
                 ("batch", "▤", "Toplu işlem", lambda: self.tools["batch"].show()),
                 ("apps", "▦", "Uygulamalar", self.open_apps),
                 ("macro", "↻", "Otomasyon", lambda: self.tools["macro"].show()),
                 ("shot", "▣", "Ekran resmi", self.batch_screenshots),
                 ("settings", "⚙", "Ayarlar", lambda: self.tools["settings"].show()),
                 ("update", "⟳", "Güncelle", lambda: self.check_update(silent=False)))
        for i, (key, glyph, text, cmd) in enumerate(items):
            self.tool_btns[key] = self._tool_button(grid, glyph, text, cmd)
            self.tool_btns[key].grid(row=i // 3, column=i % 3, sticky="nsew", padx=3, pady=3)
        self._set_tool_active("preview", self.cfg.get("preview_live", True))

        self.sync_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(f, text="Seçili cihazları birlikte kontrol et", variable=self.sync_var,
                        style="Side.TCheckbutton", command=self._sync_changed).pack(anchor="w", padx=14, pady=(14, 2))
        self.sync_hint = tk.Label(f, text="", bg=sb, fg=C["accent"], font=("Segoe UI", 9), justify="left")
        self.sync_hint.pack(anchor="w", padx=40)

        # video kalite (açılır)
        self.vq_open = tk.BooleanVar(value=False)
        self.vq_btn = tk.Label(f, text="›  Video kalite ayarları", bg=sb, fg=C["fg"], font=("Segoe UI", 10), cursor="hand2")
        self.vq_btn.pack(anchor="w", padx=14, pady=(8, 0))
        self.vq_btn.bind("<Button-1>", lambda e: self._toggle_vq())
        self.vq = tk.Frame(f, bg=sb)
        r = tk.Frame(self.vq, bg=sb)
        r.pack(fill="x", pady=2)
        tk.Label(r, text="Önizleme boyutu", bg=sb, fg=C["muted"], width=16, anchor="w").pack(side="left")
        self.tile_var = tk.StringVar(value=self.cfg.get("tile_size", "Orta"))
        cb = ttk.Combobox(r, textvariable=self.tile_var, values=list(TILE_W), state="readonly", width=12)
        cb.pack(side="left")
        cb.bind("<<ComboboxSelected>>", lambda e: self._tile_size_changed())
        r = tk.Frame(self.vq, bg=sb)
        r.pack(fill="x", pady=2)
        tk.Label(r, text="Önizleme kare/sn", bg=sb, fg=C["muted"], width=16, anchor="w").pack(side="left")
        self.fps_var = tk.StringVar(value=str(self.cfg.get("preview_fps", 15)))
        cb = ttk.Combobox(r, textvariable=self.fps_var, values=("5", "10", "15", "24", "30"), state="readonly", width=12)
        cb.pack(side="left")
        cb.bind("<<ComboboxSelected>>", lambda e: (self._set_cfg("preview_fps", int(self.fps_var.get())), self.restart_previews()))
        r = tk.Frame(self.vq, bg=sb)
        r.pack(fill="x", pady=2)
        tk.Label(r, text="Büyük ekran kalitesi", bg=sb, fg=C["muted"], width=16, anchor="w").pack(side="left")
        self.quality_var = tk.StringVar(value=self.cfg.get("quality", "Orta (1280p)"))
        cb = ttk.Combobox(r, textvariable=self.quality_var, values=list(QUALITY), state="readonly", width=12)
        cb.pack(side="left")
        cb.bind("<<ComboboxSelected>>", lambda e: self._set_cfg("quality", self.quality_var.get()))
        self.audio_var = tk.BooleanVar(value=self.cfg.get("audio", False))
        ttk.Checkbutton(self.vq, text="Büyük ekranda sesi aktar", variable=self.audio_var, style="Side.TCheckbutton",
                        command=lambda: self._set_cfg("audio", self.audio_var.get())).pack(anchor="w", pady=2)

        tk.Frame(f, bg=C["line"], height=1).pack(fill="x", padx=10, pady=12)
        self.after_vq = tk.Frame(f, bg=sb)
        g = self.after_vq

        r = tk.Frame(g, bg=sb)
        r.pack(fill="x", padx=14)
        tk.Label(r, text="Gruplar", bg=sb, fg=C["fg"], font=("Segoe UI Semibold", 12)).pack(side="left")
        add = tk.Label(r, text="＋", bg=sb, fg=C["muted"], font=("Segoe UI", 13), cursor="hand2")
        add.pack(side="right")
        add.bind("<Button-1>", lambda e: self.new_group())
        self.groups_frame = tk.Frame(g, bg=sb)
        self.groups_frame.pack(fill="x", padx=10, pady=(6, 0))

        tk.Frame(g, bg=C["line"], height=1).pack(fill="x", padx=10, pady=12)
        r = tk.Frame(g, bg=sb)
        r.pack(fill="x", padx=14)
        self.sel_lbl = tk.Label(r, text="0 / 0 seçili", bg=sb, fg=C["fg"], font=("Segoe UI", 11))
        self.sel_lbl.pack(side="left")
        mb = tk.Label(r, text="☰", bg=sb, fg=C["muted"], font=("Segoe UI", 13), cursor="hand2")
        mb.pack(side="right")
        mb.bind("<Button-1>", self._selection_menu)
        sa = tk.Label(r, text="Tümünü seç", bg=sb, fg=C["fg"], font=("Segoe UI Semibold", 10), cursor="hand2")
        sa.pack(side="right", padx=10)
        sa.bind("<Button-1>", lambda e: self.select_all())
        self.num_grid = tk.Frame(g, bg=sb)
        self.num_grid.pack(fill="x", padx=10, pady=(8, 0))
        self.num_btns = {}
        tk.Label(g, text="Tıklayarak seçin; sürükleyerek veya Shift ile aralık seçin.\n"
                         "Çift tık: büyük ekran · Sağ tık: menü", bg=sb, fg=C["muted"],
                 font=("Segoe UI", 9), justify="left").pack(anchor="w", padx=14, pady=(6, 0))

        bottom = tk.Frame(outer, bg=sb)
        bottom.pack(side="bottom", fill="x")
        tk.Frame(bottom, bg=C["line"], height=1).pack(fill="x")
        r = tk.Frame(bottom, bg=sb)
        r.pack(fill="x", padx=14, pady=10)
        self.conn_lbl = tk.Label(r, text="ADB hazırlanıyor…", bg=sb, fg=C["muted"], font=("Segoe UI", 9))
        self.conn_lbl.pack(side="left")
        lg = tk.Label(r, text="Kayıt ▴", bg=sb, fg=C["fg"], font=("Segoe UI", 10), cursor="hand2")
        lg.pack(side="right")
        lg.bind("<Button-1>", lambda e: self.toggle_log())
        self.after_vq.pack(fill="x", pady=(0, 12))
        self.left_canvas.pack(side="top", fill="both", expand=True)
        return outer

    def _tool_button(self, parent, glyph, text, cmd):
        b = tk.Frame(parent, bg=C["panel"], cursor="hand2", height=70)
        b.pack_propagate(False)
        g = tk.Label(b, text=glyph, bg=C["panel"], fg=C["accent"], font=("Segoe UI Symbol", 16))
        g.pack(pady=(8, 0))
        t = tk.Label(b, text=text, bg=C["panel"], fg=C["fg"], font=("Segoe UI Semibold", 9))
        t.pack()
        b._parts = (b, g, t)
        b._active = False

        def paint(hover=False):
            col = C["accentbg"] if b._active else (C["hover"] if hover else C["panel"])
            for w in b._parts:
                w.configure(bg=col)
        b._paint = paint
        for w in b._parts:
            w.bind("<Enter>", lambda e: paint(True))
            w.bind("<Leave>", lambda e: paint(False))
            w.bind("<Button-1>", lambda e: cmd())
        return b

    def _set_tool_active(self, key, on):
        b = self.tool_btns[key]
        b._active = on
        b._paint()

    def _toggle_vq(self):
        if self.vq.winfo_ismapped():
            self.vq.pack_forget()
            self.vq_btn.configure(text="›  Video kalite ayarları")
        else:
            self.vq.pack(fill="x", padx=16, pady=(4, 0), after=self.vq_btn)
            self.vq_btn.configure(text="⌄  Video kalite ayarları")

    # ------------------------------------------------------------ orta alan
    def _build_center(self, parent):
        f = tk.Frame(parent, bg=C["bg"])
        top = tk.Frame(f, bg=C["bg"])
        top.pack(fill="x", padx=12, pady=(10, 4))
        self.center_title = tk.Label(top, text="Tüm cihazlar", bg=C["bg"], fg=C["fg"], font=("Segoe UI Semibold", 12))
        self.center_title.pack(side="left")
        self.status_lbl = tk.Label(top, text="", bg=C["bg"], fg=C["muted"], font=("Segoe UI", 9))
        self.status_lbl.pack(side="left", padx=14)
        rb = tk.Label(top, text="⟳ Yenile", bg=C["bg"], fg=C["fg"], font=("Segoe UI", 10), cursor="hand2")
        rb.pack(side="right")
        rb.bind("<Button-1>", lambda e: self.refresh_devices())

        wrap = tk.Frame(f, bg=C["bg"])
        wrap.pack(fill="both", expand=True)
        self.gridc = tk.Canvas(wrap, bg=C["bg"], highlightthickness=0)
        sc = ttk.Scrollbar(wrap, orient="vertical", command=self._grid_scroll)
        self.gridc.configure(yscrollcommand=sc.set)
        sc.pack(side="right", fill="y")
        self.gridc.pack(side="left", fill="both", expand=True, padx=(8, 0))
        self.grid_inner = tk.Frame(self.gridc, bg=C["bg"])
        self.gridc.create_window((0, 0), window=self.grid_inner, anchor="nw")
        self.grid_inner.bind("<Configure>", lambda e: self.gridc.configure(scrollregion=self.gridc.bbox("all")))
        self.gridc.bind("<Configure>", lambda e: self._layout_tiles())
        self.bind_all("<MouseWheel>", self._wheel_any, add="+")
        self.grid_empty = tk.Label(self.grid_inner, bg=C["bg"], fg=C["muted"], justify="left",
                                   font=("Segoe UI", 11), text=(
            "Bağlı telefon yok.\n\n"
            "1. Telefonda Ayarlar → Telefon hakkında → “Yapım numarası”na 7 kez dokunun.\n"
            "2. Geliştirici seçenekleri → USB hata ayıklama’yı açın.\n"
            "3. Telefonu USB ile takın ve telefondaki izni onaylayın.\n\n"
            "Wi-Fi ile bağlamak için soldaki “WiFi” düğmesini kullanın."))
        self.grid_empty.grid(row=0, column=0, padx=30, pady=30)

        self.log_frame = tk.Frame(f, bg=C["panel"])
        self.log_txt = tk.Text(self.log_frame, height=8, bg=C["panel"], fg=C["fg"], bd=0, highlightthickness=0,
                               font=("Consolas", 9), wrap="word", state="disabled")
        self.log_txt.pack(fill="both", expand=True, padx=10, pady=8)
        for t, c in (("ok", C["ok"]), ("err", C["err"]), ("warn", C["warn"]), ("muted", C["muted"])):
            self.log_txt.tag_configure(t, foreground=c)
        return f

    def toggle_log(self):
        if self.log_frame.winfo_ismapped():
            self.log_frame.pack_forget()
        else:
            self.log_frame.pack(fill="x", side="bottom")

    def _grid_scroll(self, *a):
        self.gridc.yview(*a)

    def _wheel_any(self, e):
        w = self.winfo_containing(e.x_root, e.y_root)
        p = w
        while p is not None:
            if p is self.gridc:
                self.gridc.yview_scroll(-e.delta // 120, "units")
                return
            if p is self.left_canvas or getattr(p, "_irba_scroll", False):
                box = p.bbox("all")
                if box and box[3] > p.winfo_height():
                    p.yview_scroll(-e.delta // 120, "units")
                return
            p = p.master if hasattr(p, "master") else None

    # ------------------------------------------------------------ büyük ekran
    def _build_dock(self, parent):
        d = tk.Frame(parent, bg=C["panel"], width=620)
        d.pack_propagate(False)
        top = tk.Frame(d, bg=C["panel"])
        top.pack(fill="x", padx=12, pady=10)
        self.dock_title = tk.Label(top, text="", bg=C["panel"], fg=C["fg"], font=("Segoe UI Semibold", 13))
        self.dock_title.pack(side="left")
        x = tk.Label(top, text="✕", bg=C["panel"], fg=C["muted"], font=("Segoe UI", 13), cursor="hand2")
        x.pack(side="right")
        x.bind("<Button-1>", lambda e: self.close_big())
        self.dock_sub = tk.Label(d, text="", bg=C["panel"], fg=C["muted"], font=("Segoe UI", 9))
        self.dock_sub.pack(anchor="w", padx=12)

        body = tk.Frame(d, bg=C["panel"])
        body.pack(fill="both", expand=True, padx=12, pady=10)
        self.big_box = tk.Frame(body, bg=C["screen"], highlightthickness=2, highlightbackground=C["accent"])
        self.big_box.pack(side="left", fill="both", expand=True)
        self.big_box.bind("<Configure>", lambda e: self.big and W.move(self.big.get("hwnd"), e.width, e.height))
        self.big_msg = tk.Label(self.big_box, bg=C["screen"], fg=C["muted"], font=("Segoe UI", 10), text="")
        self.big_msg.place(relx=0.5, rely=0.5, anchor="center")

        side = ttk.Frame(body, padding=(10, 0, 0, 0))
        side.pack(side="right", fill="y")
        keys = (("◀ Geri", A.KEY_BACK), ("● Ana ekran", A.KEY_HOME), ("▢ Son uyg.", A.KEY_RECENTS),
                ("Güç", A.KEY_POWER), ("Ses +", A.KEY_VOL_UP), ("Ses −", A.KEY_VOL_DOWN),
                ("☀ Uyandır", A.KEY_WAKE), ("Kilitle", A.KEY_SLEEP))
        g = ttk.Frame(side)
        g.pack(fill="x")
        for i, (t, k) in enumerate(keys):
            ttk.Button(g, text=t, style="N.TButton", width=10,
                       command=lambda k=k: self.big_step({"t": "key", "code": k})).grid(row=i // 2, column=i % 2, padx=1, pady=1, sticky="ew")
        g = ttk.Frame(side)
        g.pack(fill="x", pady=(6, 0))
        for i, (t, m) in enumerate((("Bildirimler", S.T_EXPAND_NOTIF), ("Hızlı ayarlar", S.T_EXPAND_SETTINGS),
                                    ("Panel kapat", S.T_COLLAPSE), ("Döndür", S.T_ROTATE))):
            ttk.Button(g, text=t, style="N.TButton", width=10,
                       command=lambda m=m: self.big_step({"t": "ctl", "msg": m})).grid(row=i // 2, column=i % 2, padx=1, pady=1, sticky="ew")
        ttk.Label(side, text="Metin gönder (Türkçe olur)", style="M.TLabel").pack(anchor="w", pady=(12, 4))
        self.text_var = tk.StringVar()
        te = ttk.Entry(side, textvariable=self.text_var)
        te.pack(fill="x")
        te.bind("<Return>", lambda e: self.send_text())
        r = ttk.Frame(side)
        r.pack(fill="x", pady=4)
        ttk.Button(r, text="Gönder", style="A.TButton", command=self.send_text).pack(side="left", fill="x", expand=True)
        ttk.Button(r, text="+ Enter", command=lambda: self.send_text(enter=True)).pack(side="left", padx=(4, 0))
        ttk.Label(side, text="Pano", style="M.TLabel").pack(anchor="w", pady=(10, 4))
        ttk.Button(side, text="Bilgisayar → telefon", command=self.clip_to_phone).pack(fill="x", pady=1)
        ttk.Button(side, text="Telefon → bilgisayar", command=self.clip_from_phone).pack(fill="x", pady=1)
        ttk.Label(side, text="Diğer", style="M.TLabel").pack(anchor="w", pady=(10, 4))
        self.rec_video_btn = ttk.Button(side, text="● Video kaydı başlat", command=self.toggle_video_record)
        self.rec_video_btn.pack(fill="x", pady=1)
        ttk.Button(side, text="Ekran resmi al", command=lambda: self.save_shot(self.big and self.big["serial"])).pack(fill="x", pady=1)
        ttk.Button(side, text="Ayrı pencerede aç", command=lambda: self.open_separate([self.big["serial"]] if self.big else None)).pack(fill="x", pady=1)
        ttk.Button(side, text="Cihaz bilgisi", command=lambda: self.show_info(self.big and self.big["serial"])).pack(fill="x", pady=1)
        ttk.Label(side, text="Sağ tık: Geri · Orta tık: Ana ekran\nCtrl+V: bilgisayardan yapıştır",
                  style="M.TLabel", justify="left").pack(anchor="w", pady=(10, 0))
        return d

    # =================================================================== gruplar
    def _render_groups(self):
        for w in self.groups_frame.winfo_children():
            w.destroy()
        on = set(self.online())
        rows = [(None, "Tüm cihazlar", list(self.devices)),
                ("", "Grupsuz cihazlar", [s for s in self.devices if not self.cfg["groups"].get(s)])]
        rows += [(g, g, [s for s in self.devices if self.cfg["groups"].get(s) == g]) for g in self.cfg["group_names"]]
        sb = C["side"]
        for key, text, members in rows:
            r = tk.Frame(self.groups_frame, bg=sb)
            r.pack(fill="x", pady=2)
            mon = [s for s in members if s in on]
            allsel = bool(mon) and all(s in self.selected for s in mon)
            box = tk.Label(r, text="☑" if allsel else "☐", bg=sb, fg=C["accent"] if allsel else C["muted"],
                           font=("Segoe UI Symbol", 13), cursor="hand2")
            box.pack(side="left", padx=(4, 8))
            box.bind("<Button-1>", lambda e, m=mon, a=allsel: self._group_check(m, a))
            active = self.group_filter == key
            lbl = tk.Label(r, text=f"{text} ({len(members)})", bg=C["accentbg"] if active else sb, fg=C["fg"],
                           font=("Segoe UI Semibold" if active else "Segoe UI", 10), anchor="w", padx=12, pady=6,
                           cursor="hand2")
            lbl.pack(side="left", fill="x", expand=True)
            lbl.bind("<Button-1>", lambda e, k=key: self.set_filter(k))
            if key:
                lbl.bind("<Button-3>", lambda e, k=key: self._group_menu(e, k))

    def _group_check(self, members, allsel):
        if allsel:
            self.selected -= set(members)
        else:
            self.selected |= set(members)
        self._selection_changed()

    def set_filter(self, key):
        self.group_filter = key
        self.center_title.configure(text="Tüm cihazlar" if key is None else ("Grupsuz cihazlar" if key == "" else f"Grup: {key}"))
        self._render_groups()
        self._layout_tiles()

    def _visible(self, s):
        g = self.cfg["groups"].get(s)
        if self.group_filter is None:
            return True
        if self.group_filter == "":
            return not g
        return g == self.group_filter

    def _group_menu(self, e, g):
        m = self.menu(self)
        m.add_command(label="Seçilileri bu gruba ekle", command=lambda: self.add_to_group(g))
        m.add_command(label="Gruptaki cihazları seç", command=lambda: self.select_group(g))
        m.add_separator()
        m.add_command(label="Yeniden adlandır…", command=lambda: self.rename_group(g))
        m.add_command(label="Grubu sil", command=lambda: self.delete_group(g))
        m.tk_popup(e.x_root, e.y_root)

    def new_group(self):
        name = simpledialog.askstring(APP, "Yeni grubun adı:", parent=self)
        if not name or not name.strip():
            return
        name = name.strip()
        if name in self.cfg["group_names"]:
            messagebox.showinfo(APP, "Bu adda bir grup zaten var.")
            return
        self.cfg["group_names"].append(name)
        self.save()
        if self.selected:
            self.add_to_group(name)
        self._render_groups()
        self.log(f"Grup oluşturuldu: {name}", "ok")

    def add_to_group(self, g, serials=None):
        ss = serials or list(self.selected)
        if not ss:
            messagebox.showinfo(APP, "Önce gruba eklenecek cihazları seçin.")
            return
        for s in ss:
            self.cfg["groups"][s] = g
        self.save()
        self._render_groups()
        self._layout_tiles()
        self.log(f"{len(ss)} cihaz “{g}” grubuna eklendi.", "ok")

    def remove_from_group(self, serials=None):
        for s in serials or list(self.selected):
            self.cfg["groups"].pop(s, None)
        self.save()
        self._render_groups()
        self._layout_tiles()

    def select_group(self, g):
        self.selected = {s for s in self.online() if self.cfg["groups"].get(s) == g}
        self._selection_changed()

    def rename_group(self, g):
        new = simpledialog.askstring(APP, f"“{g}” grubunun yeni adı:", initialvalue=g, parent=self)
        if not new or not new.strip() or new.strip() == g:
            return
        new = new.strip()
        names = self.cfg["group_names"]
        names[names.index(g)] = new
        for s, gg in list(self.cfg["groups"].items()):
            if gg == g:
                self.cfg["groups"][s] = new
        if self.group_filter == g:
            self.group_filter = new
        self.save()
        self._render_groups()
        self._layout_tiles()

    def delete_group(self, g):
        if not messagebox.askyesno(APP, f"“{g}” grubu silinsin mi?\n(Cihazlar silinmez, yalnızca gruptan çıkar.)"):
            return
        self.cfg["group_names"].remove(g)
        for s, gg in list(self.cfg["groups"].items()):
            if gg == g:
                del self.cfg["groups"][s]
        if self.group_filter == g:
            self.group_filter = None
        self.save()
        self.set_filter(self.group_filter)

    # =================================================================== seçim
    def _render_numbers(self):
        for w in self.num_grid.winfo_children():
            w.destroy()
        self.num_btns = {}
        for i, s in enumerate(self.ordered()):
            st = self.devices[s].get("state")
            b = tk.Label(self.num_grid, text=f"{self.num(s):02d}", width=3, font=("Segoe UI Semibold", 10),
                         pady=4, cursor="hand2", bd=0)
            b.grid(row=i // 9, column=i % 9, padx=2, pady=2)
            b._serial = s
            b.bind("<Button-1>", lambda e, s=s: self._num_press(s, e))
            b.bind("<B1-Motion>", self._num_drag)
            b.bind("<Double-1>", lambda e, s=s: self.open_big(s))
            b.bind("<Button-3>", lambda e, s=s: self._device_menu(e, s))
            self.num_btns[s] = b
            self._paint_num(s)
        self._update_sel_lbl()

    def _paint_num(self, s):
        b = self.num_btns.get(s)
        if not b:
            return
        st = self.devices.get(s, {}).get("state")
        if s in self.selected:
            b.configure(bg=C["accent"], fg="white")
        elif st == "device":
            b.configure(bg=C["panel2"], fg=C["fg"])
        elif st == "unauthorized":
            b.configure(bg=C["panel"], fg=C["warn"])
        else:
            b.configure(bg=C["panel"], fg=C["muted"])

    def _num_press(self, s, e):
        self.focus_serial = s
        if e.state & 0x0001 and self._anchor in self.devices:     # Shift
            order = self.ordered()
            a, b = sorted((order.index(self._anchor), order.index(s)))
            for x in order[a:b + 1]:
                if self.is_online(x):
                    self.selected.add(x)
            self._selection_changed()
            return
        self._anchor = s
        self._drag_mode = s not in self.selected
        self._toggle(s, self._drag_mode)

    def _num_drag(self, e):
        w = self.winfo_containing(e.x_root, e.y_root)
        s = getattr(w, "_serial", None)
        if s and self._drag_mode is not None:
            self._toggle(s, self._drag_mode)

    def _toggle(self, s, on):
        if on and self.devices.get(s, {}).get("state") != "device":
            return
        changed = (s in self.selected) != on
        if on:
            self.selected.add(s)
        else:
            self.selected.discard(s)
        if changed:
            self._selection_changed()

    def select_all(self):
        vis = [s for s in self.online() if self._visible(s)]
        if vis and all(s in self.selected for s in vis):
            self.selected -= set(vis)
        else:
            self.selected |= set(vis)
        self._selection_changed()

    def _selection_changed(self):
        self.selected &= set(self.online())
        for s in self.num_btns:
            self._paint_num(s)
        for s in self.tiles:
            self._paint_tile(s)
        self._render_groups()
        self._update_sel_lbl()

    def _update_sel_lbl(self):
        self.sel_lbl.configure(text=f"{len(self.selected)} / {len(self.devices)} seçili")
        n = len(self.selected)
        self.sync_hint.configure(text=(f"Tıklama, kaydırma ve klavye seçili {n} cihaza gider." if n else
                                       "Önce cihaz seçin.") if self.sync_var.get() else "")
        text = f"Seçili: {n} cihaz" if n else "Seçili cihaz yok (işlem sorulursa tümüne uygulanır)"
        for name in ("batch_target_lbl", "macro_target_lbl", "apps_target_lbl", "wifi_target_lbl"):
            if hasattr(self, name):
                getattr(self, name).configure(text=text)

    def _selection_menu(self, e):
        m = self.menu(self)
        m.add_command(label="Tümünü seç", command=self.select_all)
        m.add_command(label="Seçimi temizle", command=lambda: (self.selected.clear(), self._selection_changed()))
        m.add_command(label="Seçimi ters çevir", command=lambda: (
            setattr(self, "selected", set(self.online()) - self.selected), self._selection_changed()))
        m.add_separator()
        gm = self.menu(m)
        for g in self.cfg["group_names"]:
            gm.add_command(label=g, command=lambda g=g: self.add_to_group(g))
        gm.add_separator()
        gm.add_command(label="Yeni grup…", command=self.new_group)
        m.add_cascade(label="Seçilileri gruba ekle", menu=gm)
        m.add_command(label="Seçilileri gruptan çıkar", command=self.remove_from_group)
        m.add_separator()
        m.add_command(label="Seçilileri ayrı pencerelerde aç", command=self.open_separate)
        m.tk_popup(e.x_root, e.y_root)

    def _device_menu(self, e, s):
        self.focus_serial = s
        m = self.menu(self)
        on = self.is_online(s)
        st = "normal" if on else "disabled"
        m.add_command(label=f"{self.label(s)}", state="disabled")
        m.add_separator()
        m.add_command(label="Büyük ekranda aç", command=lambda: self.open_big(s), state=st)
        m.add_command(label="Ayrı pencerede aç", command=lambda: self.open_separate([s]), state=st)
        m.add_command(label="Ad ver…", command=lambda: self.rename_device(s))
        gm = self.menu(m)
        for g in self.cfg["group_names"]:
            gm.add_command(label=g, command=lambda g=g: self.add_to_group(g, [s]))
        gm.add_separator()
        gm.add_command(label="Yeni grup…", command=self.new_group)
        m.add_cascade(label="Gruba ekle", menu=gm)
        m.add_command(label="Gruptan çıkar", command=lambda: self.remove_from_group([s]))
        m.add_separator()
        m.add_command(label="Ekran resmi al", command=lambda: self.save_shot(s), state=st)
        m.add_command(label="Cihaz bilgisi", command=lambda: self.show_info(s))
        m.add_command(label="Uygulamalar", command=lambda: self.open_apps(s), state=st)
        if ":" in s:
            m.add_command(label="Wi-Fi bağlantısını kes", command=lambda: self.wifi_disconnect([s]))
        elif on:
            m.add_command(label="Kablosuz bağlantıya geçir", command=lambda: self.go_wireless(s))
        m.add_separator()
        m.add_command(label="Yeniden başlat…", command=lambda: self.batch_reboot([s]), state=st)
        m.tk_popup(e.x_root, e.y_root)

    def rename_device(self, s):
        new = simpledialog.askstring(APP, f"“{self.label(s)}” için yeni ad:",
                                     initialvalue=self.cfg["aliases"].get(s, ""), parent=self)
        if new is None:
            return
        if new.strip():
            self.cfg["aliases"][s] = new.strip()
        else:
            self.cfg["aliases"].pop(s, None)
        self.save()
        for t in self.tiles:
            self._paint_tile(t)
        if self.big and self.big["serial"] == s:
            self.dock_title.configure(text=self.label(s))
        self._show_info()

    # =================================================================== cihazlar
    def refresh_devices(self):
        if self._refreshing or not self.adb.path:
            return
        self._refreshing = True
        self.bg(self.adb.devices, done=self._got_devices, fail=self._devices_failed)

    def _follow_shared_adb(self, shared="?"):
        """Başka bir program ADB sunucusunu başlattıysa onun adb.exe'sine geç (sürüm çakışmasını önler)."""
        if self.cfg.get("adb_path"):
            return False
        if shared == "?":
            shared = A.running_server_adb()
        if shared and os.path.normcase(shared) != os.path.normcase(self.adb.path or ""):
            self.adb.path = shared
            self.ctl.close_all()
            if hasattr(self, "adb_lbl"):
                self.adb_lbl.configure(text=f"{shared}  (başka programla ortak)")
            self.log(f"ADB sunucusu başka bir programa ait; ortak kullanılıyor: {shared}", "warn")
            return True
        if not shared and self.adb.path and not os.path.isfile(self.adb.path):
            self.adb.path = A.find_adb()
            return True
        return False

    def _devices_failed(self, e):
        self._refreshing = False
        if self._follow_shared_adb():
            self.after(500, self.refresh_devices)
            return
        self.conn_lbl.configure(text="ADB hatası", fg=C["err"])
        if str(e) != self._last_dev_err:
            self._last_dev_err = str(e)
            self.log(f"Cihaz listesi alınamadı: {e}", "err")

    def _got_devices(self, lst):
        self._refreshing = False
        self._last_dev_err = None
        seen, changed = set(), False
        for d in lst:
            s = d["serial"]
            seen.add(s)
            if s not in self.cfg["order"]:
                self.cfg["order"].append(s)
                self.save()
            cur = self.devices.get(s)
            if cur is None:
                cur = self.devices[s] = {"info": None, "loading": False, "stale": False, "state": None, "model": ""}
                changed = True
                self.log(f"Cihaz bulundu: {self.label(s)} {d['model']}", "ok")
                if self.tray and self.state() == "withdrawn":
                    self.tray.notify(APP, f"Cihaz bağlandı: {d['model'] or s}")
            if cur["state"] != d["state"]:
                changed = True
                if d["state"] == "unauthorized":
                    self.log(f"{self.label(s)}: telefonda “USB hata ayıklamaya izin ver” penceresini onaylayın.", "warn")
            cur["state"] = d["state"]
            cur["model"] = d["model"] or cur["model"]
            if d["state"] == "device" and (cur["info"] is None or cur["stale"]) and not cur["loading"]:
                cur["loading"] = True
                self.bg(self.adb.info, s, done=lambda i, s=s: self._got_info(s, i),
                        fail=lambda e, s=s: self._info_failed(s, e))
        for s in list(self.devices):
            if s not in seen:
                changed = True
                self.log(f"Cihaz ayrıldı: {self.label(s)}", "warn")
                del self.devices[s]
                self.selected.discard(s)
                self.ctl.drop(s)
                self._remove_tile(s)
                if self.big and self.big["serial"] == s:
                    self.close_big()
        n, on = len(self.devices), len(self.online())
        self.conn_lbl.configure(text=f"ADB hazır · {on}/{n} çevrimiçi", fg=C["muted"])
        if changed:
            self._render_numbers()
            self._render_groups()
            self._sync_tiles()

    def _got_info(self, s, info):
        d = self.devices.get(s)
        if not d:
            return
        d.update(info=info, loading=False, stale=False)
        if s in self.tiles:
            self._paint_tile(s)
            self._resize_tile(s)
        if s == self.focus_serial:
            self._show_info()

    def _info_failed(self, s, e):
        if s in self.devices:
            self.devices[s]["loading"] = False

    # =================================================================== kutular
    def _tile_dims(self, s=None):
        w = TILE_W.get(self.tile_var.get(), 185)
        info = (self.devices.get(s, {}).get("info") or {}) if s else {}
        dw, dh = info.get("width") or 1080, info.get("height") or 2340
        ratio = min(max(dh / dw if dh > dw else dw / dh, 1.6), 2.25)
        return w, int(w * ratio)

    def _sync_tiles(self):
        for s in list(self.tiles):
            if s not in self.devices:
                self._remove_tile(s)
        for s in self.ordered():
            if s not in self.tiles:
                self._create_tile(s)
            self._paint_tile(s)
            self._update_tile_stream(s)
        self._layout_tiles()

    def _create_tile(self, s):
        w, h = self._tile_dims(s)
        cell = tk.Frame(self.grid_inner, bg=C["line"], padx=2, pady=2)
        hdr = tk.Frame(cell, bg=C["panel"], cursor="hand2")
        hdr.pack(fill="x")
        name = tk.Label(hdr, text="", bg=C["panel"], fg=C["fg"], font=("Segoe UI Semibold", 10), anchor="w", padx=6, pady=3)
        name.pack(side="left", fill="x", expand=True)
        bat = tk.Label(hdr, text="", bg=C["panel"], fg=C["muted"], font=("Segoe UI", 8), padx=4)
        bat.pack(side="right")
        body = tk.Frame(cell, bg=C["screen"], width=w, height=h)
        body.pack()
        body.pack_propagate(False)
        msg = tk.Label(body, text="", bg=C["screen"], fg=C["muted"], font=("Segoe UI", 9), wraplength=w - 16)
        msg.place(relx=0.5, rely=0.5, anchor="center")
        img = tk.Label(body, bg=C["screen"], bd=0)
        t = {"cell": cell, "hdr": hdr, "name": name, "bat": bat, "body": body, "msg": msg, "img": img,
             "proc": None, "hwnd": None, "title": None, "photo": None, "busy": False, "fails": 0, "next_try": 0}
        for wdg in (hdr, name, bat, img, msg):
            wdg.bind("<Button-1>", lambda e, s=s: self._tile_click(s, e))
            wdg.bind("<Double-1>", lambda e, s=s: self.open_big(s))
            wdg.bind("<Button-3>", lambda e, s=s: self._device_menu(e, s))
        self.tiles[s] = t

    def _tile_click(self, s, e):
        self.focus_serial = s
        if e.state & 0x0004:          # Ctrl ile tıklama: yalnızca bunu seç/çıkar
            self._toggle(s, s not in self.selected)
        else:
            self._toggle(s, s not in self.selected)
        self._show_info()

    def _paint_tile(self, s):
        t = self.tiles.get(s)
        if not t:
            return
        sel = s in self.selected
        t["cell"].configure(bg=C["accent"] if sel else C["line"])
        col = C["accent"] if sel else C["panel"]
        for w in (t["hdr"], t["name"], t["bat"]):
            w.configure(bg=col)
        info = self.devices.get(s, {}).get("info") or {}
        bat = info.get("battery", "")
        if bat and info.get("charging"):
            bat += "⚡"
        t["name"].configure(text=self.label(s))
        t["bat"].configure(text=bat, fg="white" if sel else C["muted"])
        st = self.devices.get(s, {}).get("state")
        if st != "device":
            t["msg"].configure(text={"unauthorized": "Telefonda USB hata ayıklama\niznini onaylayın",
                                     "offline": "Çevrimdışı"}.get(st, st or ""))
            t["msg"].lift()

    def _resize_tile(self, s):
        t = self.tiles.get(s)
        if not t:
            return
        w, h = self._tile_dims(s)
        if int(t["body"].cget("width")) != w or int(t["body"].cget("height")) != h:
            t["body"].configure(width=w, height=h)
            t["msg"].configure(wraplength=w - 16)
            W.move(t["hwnd"], w, h)

    def _remove_tile(self, s):
        t = self.tiles.pop(s, None)
        if t:
            self._stop_tile_stream(t)
            t["cell"].destroy()
            self._layout_tiles()

    def _layout_tiles(self):
        vis = [s for s in self.ordered() if s in self.tiles and self._visible(s)]
        for s, t in self.tiles.items():
            if s not in vis:
                t["cell"].grid_forget()
        if not vis:
            self.grid_empty.grid(row=0, column=0, padx=30, pady=30)
            return
        self.grid_empty.grid_forget()
        tw = TILE_W.get(self.tile_var.get(), 185) + 14
        cols = max(1, (self.gridc.winfo_width() - 10) // tw)
        for i, s in enumerate(vis):
            self.tiles[s]["cell"].grid(row=i // cols, column=i % cols, padx=5, pady=5, sticky="n")

    def _tile_size_changed(self):
        self._set_cfg("tile_size", self.tile_var.get())
        for s in self.tiles:
            self._resize_tile(s)
        self._layout_tiles()
        self.restart_previews()

    # ---------------------------------------------------- canlı önizleme akışı
    def toggle_preview(self):
        on = not self.cfg.get("preview_live", True)
        self._set_cfg("preview_live", on)
        self._set_tool_active("preview", on)
        self.log("Canlı önizleme açıldı." if on else "Canlı önizleme kapatıldı (5 sn'de bir ekran resmi gösterilir).", "muted")
        for s in self.tiles:
            if not on:
                self._stop_tile_stream(self.tiles[s])
            self._update_tile_stream(s)

    def restart_previews(self):
        for s, t in self.tiles.items():
            if t["proc"]:
                self._stop_tile_stream(t)
                self._update_tile_stream(s)

    def _update_tile_stream(self, s):
        t = self.tiles.get(s)
        if not t:
            return
        live_ok = (self.cfg.get("preview_live", True) and self.is_online(s) and S.scrcpy_exe()
                   and not (self.big and self.big["serial"] == s))
        if self.big and self.big["serial"] == s:
            t["msg"].configure(text="Büyük ekranda açık")
            t["img"].place_forget()
            t["msg"].lift()
            return
        if live_ok and not t["proc"] and time.time() >= t["next_try"]:
            connecting = sum(1 for x in self.tiles.values() if x["proc"] and not x["hwnd"])
            if connecting >= 3:          # telefonları sırayla bağla, ADB'yi boğma
                t["msg"].configure(text="Sırada bekliyor…")
                self.after(700, lambda s=s: self._update_tile_stream(s))
                return
            self._start_tile_stream(s)
        elif not self.cfg.get("preview_live", True) and self.is_online(s):
            if not t["photo"]:
                t["msg"].configure(text="Ekran alınıyor…")
            self._shot_tile(s)

    def _start_tile_stream(self, s):
        t = self.tiles[s]
        w, h = self._tile_dims(s)
        title = f"irba-on-{random.randint(10 ** 7, 10 ** 8 - 1)}"
        args = ["-m", str(min(1024, max(320, int(h * 1.6)))), "-b", "1500K",
                "--max-fps", str(self.cfg.get("preview_fps", 15)), "--no-audio", "--window-borderless",
                "--no-window-aspect-ratio-lock", "--window-x", "-32000", "--window-y", "-32000",
                "--no-clipboard-autosync"]
        if self.cfg.get("stay_awake", True) and ":" not in s:
            args.append("--stay-awake")
        try:
            proc = S.launch(self.adb.path, s, args, title)
        except Exception as e:  # noqa: BLE001
            t["msg"].configure(text=f"Önizleme açılamadı\n{e}")
            return
        t.update(proc=proc, title=title, hwnd=None, t0=time.time(), out=[])
        t["img"].place_forget()
        t["msg"].configure(text="Bağlanıyor…")
        t["msg"].lift()
        threading.Thread(target=self._read_proc, args=(proc, t, s), daemon=True).start()
        self.after(150, lambda: self._find_tile_window(s, proc))

    def _read_proc(self, proc, holder, s):
        for raw in iter(proc.stdout.readline, b""):
            line = raw.decode("utf-8", "replace").rstrip()
            if line:
                holder["out"] = (holder.get("out", []) + [line])[-12:]
        proc.wait()
        self.ui(self._proc_exited, s, proc)

    def _find_tile_window(self, s, proc):
        t = self.tiles.get(s)
        if not t or t["proc"] is not proc:
            return
        hwnd = W.find_window(t["title"])
        if hwnd:
            t["hwnd"] = hwnd
            self.update_idletasks()
            w, h = self._tile_dims(s)
            W.embed(hwnd, t["body"].winfo_id(), w, h)
            t["msg"].configure(text="")
            t["fails"] = 0
            return
        if proc.poll() is None and time.time() - t["t0"] < 25:
            self.after(150, lambda: self._find_tile_window(s, proc))

    def _proc_exited(self, s, proc):
        t = self.tiles.get(s)
        if t and t["proc"] is proc:
            t.update(proc=None, hwnd=None)
            t["fails"] += 1
            t["next_try"] = time.time() + min(60, 4 * t["fails"])
            last = (t.get("out") or [""])[-1]
            if self.is_online(s):
                t["msg"].configure(text="Önizleme kapandı, yeniden denenecek…" +
                                   (f"\n\n{last[-120:]}" if "ERROR" in last else ""))
                t["msg"].lift()
            if t["fails"] == 3:
                self.log(f"{self.label(s)}: canlı önizleme açılamıyor. {last[-160:]}", "err")
        if self.big and self.big.get("proc") is proc:
            self._big_exited(proc)

    def _stop_tile_stream(self, t):
        proc, hwnd = t.get("proc"), t.get("hwnd")
        t.update(proc=None, hwnd=None)
        if proc:
            W.close_window(hwnd)

            def kill():
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
            threading.Thread(target=kill, daemon=True).start()

    def _shot_tile(self, s):
        t = self.tiles.get(s)
        if not t or t["busy"]:
            return
        t["busy"] = True
        self.bg(self.adb.screencap, s, done=lambda d: self._got_shot(s, d), fail=lambda e: self._shot_fail(s))

    def _got_shot(self, s, data):
        t = self.tiles.get(s)
        if not t:
            return
        t["busy"] = False
        if self.cfg.get("preview_live", True) and t["proc"]:
            return
        img = png_to_photo(data)
        w, h = self._tile_dims(s)
        k = max(1, math.ceil(max(img.height() / h, img.width() / w)))
        if k > 1:
            img = img.subsample(k)
        t["photo"] = img
        t["img"].configure(image=img)
        t["img"].place(relx=0.5, rely=0.5, anchor="center")
        t["img"].lift()

    def _shot_fail(self, s):
        t = self.tiles.get(s)
        if t:
            t["busy"] = False

    # =================================================================== büyük ekran
    def open_big(self, s, record=False):
        if not s or not self.is_online(s):
            return
        self.focus_serial = s
        if self.big:
            self.close_big(reopen_tile=self.big["serial"] != s)
        t = self.tiles.get(s)
        if t:
            self._stop_tile_stream(t)
        self.dock.pack(side="right", fill="y", before=self.center)
        self.dock_title.configure(text=self.label(s))
        info = self.devices[s].get("info") or {}
        self.dock_sub.configure(text="  ·  ".join(x for x in (info.get("brand", "").capitalize(), info.get("model"),
                                                             f"Android {info.get('android')}" if info.get("android") else "",
                                                             info.get("battery"), self.cfg["groups"].get(s)) if x))
        args = list(QUALITY.get(self.quality_var.get(), QUALITY["Orta (1280p)"]))
        if not self.audio_var.get():
            args.append("--no-audio")
        if self.cfg.get("stay_awake", True) and ":" not in s:
            args.append("--stay-awake")
        args += ["--window-borderless", "--no-window-aspect-ratio-lock", "--window-x", "-32000", "--window-y", "-32000"]
        rec = None
        if record:
            os.makedirs(self.cfg["media_dir"], exist_ok=True)
            rec = os.path.join(self.cfg["media_dir"], f"{safe_name(self.label(s))}_{time.strftime('%Y%m%d_%H%M%S')}.mp4")
            args += ["--record", rec]
        title = f"irba-buyuk-{random.randint(10 ** 7, 10 ** 8 - 1)}"
        try:
            proc = S.launch(self.adb.path, s, args, title)
        except Exception as e:  # noqa: BLE001
            self.log(f"Büyük ekran açılamadı: {e}", "err")
            return
        self.big = {"serial": s, "proc": proc, "title": title, "hwnd": None, "rec": rec, "t0": time.time(), "out": []}
        self.rec_video_btn.configure(text="■ Video kaydını bitir" if rec else "● Video kaydı başlat")
        self.big_msg.configure(text="Bağlanıyor…")
        self.big_msg.place(relx=0.5, rely=0.5, anchor="center")
        threading.Thread(target=self._read_proc, args=(proc, self.big, s), daemon=True).start()
        self.after(150, lambda: self._find_big(proc))
        self._update_tile_stream(s)
        self.bg(self.adb.orientation, s, done=lambda o: self.orients.__setitem__(s, o), fail=lambda e: None)
        self.after(50, self._layout_tiles)
        if rec:
            self.log(f"Video kaydı başladı: {rec}", "warn")

    def _find_big(self, proc):
        b = self.big
        if not b or b["proc"] is not proc:
            return
        hwnd = W.find_window(b["title"])
        if hwnd:
            b["hwnd"] = hwnd
            self.update_idletasks()
            W.embed(hwnd, self.big_box.winfo_id(), self.big_box.winfo_width(), self.big_box.winfo_height())
            self.big_msg.place_forget()
            self._overlay_update()
            return
        if proc.poll() is None and time.time() - b["t0"] < 25:
            self.after(150, lambda: self._find_big(proc))

    def _big_exited(self, proc):
        b = self.big
        if not b or b["proc"] is not proc:
            return
        last = "\n".join(b.get("out", [])[-3:])
        self.big_msg.configure(text="Bağlantı kapandı.\n\n" + last[-300:])
        self.big_msg.place(relx=0.5, rely=0.5, anchor="center")
        if b.get("rec"):
            self.log(f"Video kaydedildi: {b['rec']}", "ok")
            b["rec"] = None
            self.rec_video_btn.configure(text="● Video kaydı başlat")

    def close_big(self, reopen_tile=True):
        b = self.big
        if not b:
            self.dock.pack_forget()
            return
        self.big = None
        self._stop_tile_stream(b)
        if b.get("rec"):
            self.log(f"Video kaydedildi: {b['rec']}", "ok")
        self.dock.pack_forget()
        self._overlay_update()
        if reopen_tile and b["serial"] in self.tiles:
            t = self.tiles[b["serial"]]
            t["next_try"] = time.time() + 1.5
            t["msg"].configure(text="")
            self.after(1600, lambda s=b["serial"]: self._update_tile_stream(s))
        self.after(50, self._layout_tiles)

    def toggle_video_record(self):
        if not self.big:
            return
        s, rec = self.big["serial"], bool(self.big.get("rec"))
        self.close_big(reopen_tile=False)
        self.after(1500, lambda: self.open_big(s, record=not rec))

    def open_separate(self, serials=None):
        ts = [s for s in (serials or list(self.selected)) if self.is_online(s)]
        if not ts:
            self.need_device()
            return
        args = list(QUALITY.get(self.quality_var.get(), QUALITY["Orta (1280p)"]))
        if not self.audio_var.get():
            args.append("--no-audio")
        for i, s in enumerate(ts):
            try:
                S.launch(self.adb.path, s, args + ["--window-x", str(40 + (i % 8) * 60), "--window-y", str(40 + (i % 8) * 40)],
                         f"{self.label(s)} — irba test")
            except Exception as e:  # noqa: BLE001
                self.log(f"{self.label(s)}: açılamadı ({e})", "err")
        self.log(f"{len(ts)} cihaz ayrı pencerede açıldı.", "ok")

    # =================================================================== birlikte kontrol katmanı
    def _sync_changed(self):
        self._update_sel_lbl()
        self._overlay_update()
        if self.sync_var.get():
            self.log("Birlikte kontrol açık: bir ekrana yaptığınız tıklama, kaydırma ve klavye girişi "
                     "seçili tüm cihazlara gider.", "muted")
            for s in self.selected:
                self.bg(self.adb.orientation, s, done=lambda o, s=s: self.orients.__setitem__(s, o), fail=lambda e: None)

    def _overlay_update(self):
        want = {}
        if self.sync_var.get() or self.rec_on:
            want["grid"] = self.gridc
            if self.big and self.big.get("hwnd"):
                want["big"] = self.big_box
        for k in list(self.overlays):
            if k not in want:
                self.overlays.pop(k).destroy()
        for k, widget in want.items():
            if k not in self.overlays:
                ov = tk.Toplevel(self)
                ov.overrideredirect(True)
                ov.configure(bg="black", cursor="crosshair")
                ov.attributes("-alpha", 0.02)
                ov.transient(self)
                ov.bind("<ButtonPress-1>", lambda e, k=k: self._ov_press(k, e))
                ov.bind("<ButtonRelease-1>", lambda e, k=k: self._ov_release(k, e))
                ov.bind("<MouseWheel>", lambda e, k=k: self._ov_wheel(k, e))
                ov.bind("<Button-3>", lambda e, k=k: self._ov_key(k, e, A.KEY_BACK))
                ov.bind("<Button-2>", lambda e, k=k: self._ov_key(k, e, A.KEY_HOME))
                ov.bind("<Key>", self._ov_keypress)
                ov._widget = widget
                self.overlays[k] = ov
        self._overlay_place()

    def _overlay_place(self):
        normal = self.state() in ("normal", "zoomed")
        for k, ov in self.overlays.items():
            w = ov._widget
            if not normal or not w.winfo_ismapped():
                ov.withdraw()
                continue
            geo = f"{w.winfo_width()}x{w.winfo_height()}+{w.winfo_rootx()}+{w.winfo_rooty()}"
            if ov.state() == "withdrawn":
                ov.deiconify()
            if ov.geometry() != geo:
                ov.geometry(geo)

    def _overlay_loop(self):
        self._overlay_place()
        self._ov_tick = getattr(self, "_ov_tick", 0) + 1
        if self.overlays and self._ov_tick % 20 == 0:
            for s in list(self.selected)[:30] + ([self.big["serial"]] if self.big else []):
                self.bg(self.adb.orientation, s, done=lambda o, s=s: self.orients.__setitem__(s, o), fail=lambda e: None)
        self.after(300, self._overlay_loop)

    def _frame_size(self, s, orient=None):
        info = self.devices.get(s, {}).get("info") or {}
        w, h = info.get("width") or 1080, info.get("height") or 2340
        o = self.orients.get(s, 0) if orient is None else orient
        if (w > h) != (o in (1, 3)):
            w, h = h, w
        return w, h

    def _video_rect(self, s, widget):
        """Widget içindeki görüntü alanı (siyah kenarlar hariç), ekran koordinatlarında."""
        cw, ch = widget.winfo_width(), widget.winfo_height()
        fw, fh = self._frame_size(s)
        a = fw / fh
        if cw / ch > a:
            h, w = ch, ch * a
        else:
            w, h = cw, cw / a
        return widget.winfo_rootx() + (cw - w) / 2, widget.winfo_rooty() + (ch - h) / 2, w, h

    def _hit(self, k, xr, yr):
        if k == "big":
            return (self.big["serial"], self._video_rect(self.big["serial"], self.big_box)) if self.big else None
        for s, t in self.tiles.items():
            b = t["body"]
            if not b.winfo_ismapped():
                continue
            x, y = b.winfo_rootx(), b.winfo_rooty()
            if x <= xr < x + b.winfo_width() and y <= yr < y + b.winfo_height():
                return s, self._video_rect(s, b)
        return None

    def _ov_press(self, k, e):
        self.overlays[k].focus_force()
        hit = self._hit(k, e.x_root, e.y_root)
        self._press = (hit, e.x_root, e.y_root, time.time()) if hit else None

    def _ov_release(self, k, e):
        if not self._press:
            return
        (s, (rx, ry, rw, rh)), x0, y0, t0 = self._press
        self._press = None
        self.focus_serial = s
        fx0, fy0 = (x0 - rx) / rw, (y0 - ry) / rh
        if not (0 <= fx0 < 1 and 0 <= fy0 < 1):
            return
        fx1 = min(max((e.x_root - rx) / rw, 0), 0.999)
        fy1 = min(max((e.y_root - ry) / rh, 0), 0.999)
        ms = int(min(max((time.time() - t0) * 1000, 60), 4000))
        if math.hypot(e.x_root - x0, e.y_root - y0) < 6:
            step = {"t": "tap", "x": fx0, "y": fy0} if ms < 550 else \
                {"t": "swipe", "x": fx0, "y": fy0, "x2": fx0, "y2": fy0, "ms": ms}
        else:
            step = {"t": "swipe", "x": fx0, "y": fy0, "x2": fx1, "y2": fy1, "ms": ms}
        self.do_step(step, s)

    def _ov_wheel(self, k, e):
        if k == "grid":
            self.gridc.yview_scroll(-e.delta // 120, "units")
            return
        if self.big:
            up = e.delta > 0
            self.do_step({"t": "swipe", "x": 0.5, "y": 0.35 if up else 0.65, "x2": 0.5,
                          "y2": 0.65 if up else 0.35, "ms": 220}, self.big["serial"])

    def _ov_key(self, k, e, code):
        hit = self._hit(k, e.x_root, e.y_root)
        if hit:
            self.do_step({"t": "key", "code": code}, hit[0])

    def _ov_keypress(self, e):
        master = self.focus_serial
        if not master:
            return
        if e.keysym == "BackSpace":
            self._flush_kbuf()
            self.do_step({"t": "key", "code": A.KEY_DEL}, master)
        elif e.keysym in ("Return", "KP_Enter"):
            self._flush_kbuf()
            self.do_step({"t": "key", "code": A.KEY_ENTER}, master)
        elif e.keysym == "Escape":
            self._flush_kbuf()
            self.do_step({"t": "key", "code": A.KEY_BACK}, master)
        elif e.state & 0x0004 and e.keysym.lower() == "v":
            try:
                self._kbuf += self.clipboard_get()
            except tk.TclError:
                pass
            self._flush_kbuf()
        elif e.char and e.char.isprintable():
            self._kbuf += e.char
            if self._kbuf_job:
                self.after_cancel(self._kbuf_job)
            self._kbuf_job = self.after(350, self._flush_kbuf)

    def _flush_kbuf(self):
        if self._kbuf_job:
            self.after_cancel(self._kbuf_job)
            self._kbuf_job = None
        if self._kbuf and self.focus_serial:
            self.do_step({"t": "text", "text": self._kbuf}, self.focus_serial)
        self._kbuf = ""

    def control_targets(self, master):
        ts = [master] if master and self.is_online(master) else []
        if self.sync_var.get():
            ts += [s for s in self.online() if s in self.selected and s not in ts]
        return ts

    def do_step(self, step, master):
        ts = self.control_targets(master)
        if not ts:
            return
        if self.rec_on:
            self._record(step)
        for s in ts:
            self.bg(self._exec_step, s, step, None, fail=lambda e, s=s: self.log(f"{self.label(s)}: {e}", "err"))

    def big_step(self, step):
        if not self.big:
            return
        self.do_step(step, self.big["serial"])

    def _exec_step(self, s, step, orient):
        t = step["t"]
        if t in ("tap", "swipe"):
            w, h = self._frame_size(s, orient)
            if t == "tap":
                return self.adb.tap(s, step["x"] * w, step["y"] * h)
            return self.adb.swipe(s, step["x"] * w, step["y"] * h, step["x2"] * w, step["y2"] * h, step["ms"])
        if t == "key":
            return self.adb.key(s, step["code"])
        if t == "text":
            try:
                return self.ctl.paste_text(s, step["text"])
            except Exception:  # noqa: BLE001
                return self.adb.text_ascii(s, step["text"])
        if t == "app":
            return self.adb.launch(s, step["pkg"])
        if t == "ctl":
            return self.ctl.get(s).simple(step["msg"])
        return None

    def send_text(self, enter=False):
        t = self.text_var.get()
        if not self.big:
            return
        if t:
            self.big_step({"t": "text", "text": t})
        if enter:
            self.after(400, lambda: self.big_step({"t": "key", "code": A.KEY_ENTER}))
        self.text_var.set("")

    def clip_to_phone(self, targets=None):
        try:
            text = self.clipboard_get()
        except tk.TclError:
            messagebox.showinfo(APP, "Bilgisayar panosunda metin yok.")
            return
        ts = targets if targets is not None else (self.control_targets(self.big["serial"]) if self.big else [])
        if not ts:
            self.need_device()
            return
        for s in ts:
            self.bg(lambda s=s: self.ctl.get(s).set_clipboard(text, False),
                    done=lambda r, s=s: self.log(f"Pano gönderildi: {self.label(s)}", "ok"),
                    fail=lambda e, s=s: self.log(f"{self.label(s)}: pano gönderilemedi ({e})", "err"))

    def clip_from_phone(self):
        s = self.big["serial"] if self.big else self.focus_serial
        if not s or not self.is_online(s):
            self.need_device()
            return

        def done(text):
            self.clipboard_clear()
            self.clipboard_append(text or "")
            self.log(f"Telefon panosu alındı ({len(text or '')} karakter).", "ok")
        self.bg(lambda: self.ctl.get(s).get_clipboard(), done=done, fail=lambda e: self.log(f"Pano alınamadı: {e}", "err"))

    def save_shot(self, s):
        if not s or not self.is_online(s):
            self.need_device()
            return
        os.makedirs(self.cfg["media_dir"], exist_ok=True)
        p = os.path.join(self.cfg["media_dir"], f"{safe_name(self.label(s))}_{time.strftime('%Y%m%d_%H%M%S')}.png")

        def work():
            with open(p, "wb") as f:
                f.write(self.adb.screencap(s))
            return p
        self.bg(work, done=lambda r: self.log(f"Ekran resmi kaydedildi: {r}", "ok"))

    # =================================================================== araç pencereleri
    def _build_wifi(self, win):
        f = ttk.Frame(win, padding=20)
        r = self.row(f)
        ttk.Label(r, text="Wi-Fi bağlantısı", style="H.TLabel").pack(side="left")
        self.wifi_target_lbl = ttk.Label(r, text="", style="Acc.TLabel")
        self.wifi_target_lbl.pack(side="right")
        lf = self.box(f, "IP adresiyle bağlan")
        r = self.row(lf)
        self.connect_var = tk.StringVar()
        e = ttk.Entry(r, textvariable=self.connect_var)
        e.pack(side="left", fill="x", expand=True)
        e.bind("<Return>", lambda ev: self.wifi_connect())
        ttk.Button(r, text="Bağlan", style="A.TButton", command=self.wifi_connect).pack(side="left", padx=(8, 0))
        ttk.Label(lf, text="Örnek: 192.168.1.25 veya 192.168.1.25:5555", style="M.TLabel").pack(anchor="w", pady=(6, 0))
        lf = self.box(f, "Otomatik")
        for text, cmd in (("Ağda telefon ara", self.scan_network),
                          ("Seçili USB cihazları kablosuza geçir", self.batch_wireless),
                          ("Seçili Wi-Fi bağlantılarını kes", self.wifi_disconnect)):
            ttk.Button(lf, text=text, command=cmd).pack(fill="x", pady=2)
        lf = self.box(f, "Android 11 ve üzeri: kablosuz eşleştirme")
        ttk.Label(lf, style="M.TLabel", justify="left", wraplength=540, text=(
            "Telefonda: Geliştirici seçenekleri → Kablosuz hata ayıklama → “Eşleme koduyla cihaz eşle”. "
            "Ekranda çıkan IP:port ve 6 haneli kodu aşağıya yazın.")).pack(anchor="w")
        r = self.row(lf, pady=(8, 0))
        ttk.Label(r, text="IP:port").pack(side="left")
        self.pair_addr = tk.StringVar()
        ttk.Entry(r, textvariable=self.pair_addr, width=22).pack(side="left", padx=6)
        ttk.Label(r, text="Kod").pack(side="left", padx=(8, 0))
        self.pair_code = tk.StringVar()
        ttk.Entry(r, textvariable=self.pair_code, width=10).pack(side="left", padx=6)
        ttk.Button(r, text="Eşleştir", style="A.TButton", command=self.pair_device).pack(side="left", padx=(6, 0))
        return f

    def _build_batch(self, win):
        f = ttk.Frame(win, padding=18)
        r = self.row(f)
        ttk.Label(r, text="Toplu işlemler", style="H.TLabel").pack(side="left")
        self.batch_target_lbl = ttk.Label(r, text="", style="Acc.TLabel")
        self.batch_target_lbl.pack(side="right")
        ttk.Label(f, text="İşlemler soldaki panelde seçili cihazlara uygulanır. Dosyaları ana pencereye sürükleyip "
                          "bırakarak da kurabilir/gönderebilirsiniz.", style="M.TLabel", wraplength=820,
                  justify="left").pack(anchor="w", pady=(4, 0))
        lf = self.box(f, "ADB komutu  (adb shell)")
        r = self.row(lf)
        self.cmd_var = tk.StringVar()
        e = ttk.Entry(r, textvariable=self.cmd_var)
        e.pack(side="left", fill="x", expand=True)
        e.bind("<Return>", lambda ev: self.batch_shell())
        ttk.Button(r, text="Çalıştır", style="A.TButton", command=self.batch_shell).pack(side="left", padx=(8, 0))
        r = self.row(lf, pady=(6, 0))
        ttk.Label(r, text="Hazır:", style="M.TLabel").pack(side="left")
        for t, c in (("Pil", "dumpsys battery"), ("Parlaklık", "settings get system screen_brightness"),
                     ("Uygulamalar", "pm list packages -3"), ("Depolama", "df -h /data"), ("Model", "getprop ro.product.model")):
            ttk.Button(r, text=t, command=lambda c=c: self.cmd_var.set(c)).pack(side="left", padx=(6, 0))
        lf = self.box(f, "Uygulama (APK) kur")
        r = self.row(lf)
        self.apk_var = tk.StringVar()
        ttk.Entry(r, textvariable=self.apk_var).pack(side="left", fill="x", expand=True)
        ttk.Button(r, text="Seç…", command=lambda: self._pick_many(self.apk_var, [("APK", "*.apk")])).pack(side="left", padx=8)
        ttk.Button(r, text="Kur", style="A.TButton", command=self.batch_install).pack(side="left")
        lf = self.box(f, "Dosya gönder")
        r = self.row(lf)
        self.push_var = tk.StringVar()
        ttk.Entry(r, textvariable=self.push_var).pack(side="left", fill="x", expand=True)
        ttk.Button(r, text="Seç…", command=lambda: self._pick_many(self.push_var, [("Tüm dosyalar", "*.*")])).pack(side="left", padx=8)
        r = self.row(lf, pady=(8, 0))
        ttk.Label(r, text="Telefondaki klasör", style="M.TLabel").pack(side="left")
        self.push_dst = tk.StringVar(value="/sdcard/Download/")
        ttk.Combobox(r, textvariable=self.push_dst, width=28, values=(
            "/sdcard/Download/", "/sdcard/DCIM/Camera/", "/sdcard/Pictures/", "/sdcard/Movies/",
            "/sdcard/Music/", "/sdcard/Documents/")).pack(side="left", padx=8)
        ttk.Button(r, text="Gönder", style="A.TButton", command=self.batch_push).pack(side="left")
        lf = self.box(f, "Hızlı işlemler")
        r = self.row(lf)
        for text, cmd in (("Ana ekran", lambda: self.batch_key(A.KEY_HOME, "Ana ekran")),
                          ("Uyandır", lambda: self.batch_key(A.KEY_WAKE, "Uyandır")),
                          ("Kilitle", lambda: self.batch_key(A.KEY_SLEEP, "Kilitle")),
                          ("Sessiz", lambda: self.batch_key(A.KEY_MUTE, "Sessiz")),
                          ("Ekran resimleri", self.batch_screenshots)):
            ttk.Button(r, text=text, command=cmd).pack(side="left", padx=(0, 6))
        r = self.row(lf, pady=(8, 0))
        for text, cmd in (("Metni hepsine yaz…", self.batch_text_dialog),
                          ("Panoyu hepsine gönder", self.batch_clipboard),
                          ("Kablosuza geçir", self.batch_wireless)):
            ttk.Button(r, text=text, command=cmd).pack(side="left", padx=(0, 6))
        ttk.Button(r, text="Yeniden başlat", style="D.TButton", command=self.batch_reboot).pack(side="left")
        return f

    def _build_apps(self, win):
        f = ttk.Frame(win, padding=18)
        r = self.row(f)
        self.apps_title = ttk.Label(r, text="Uygulamalar", style="H.TLabel")
        self.apps_title.pack(side="left")
        ttk.Button(r, text="⟳ Listeyi getir", style="A.TButton", command=self.load_apps).pack(side="right")
        self.apps_sys = tk.BooleanVar(value=False)
        ttk.Checkbutton(r, text="Sistem uygulamaları", variable=self.apps_sys, command=self.load_apps).pack(side="right", padx=10)
        r = self.row(f, pady=(10, 0))
        ttk.Label(r, text="Ara", style="M.TLabel").pack(side="left")
        self.apps_search = tk.StringVar()
        self.apps_search.trace_add("write", lambda *a: self._render_apps())
        ttk.Entry(r, textvariable=self.apps_search, width=34).pack(side="left", padx=8)
        self.apps_count = ttk.Label(r, text="", style="M.TLabel")
        self.apps_count.pack(side="left")
        body = ttk.Frame(f)
        body.pack(fill="both", expand=True, pady=(10, 0))
        self.apps_tree = ttk.Treeview(body, columns=("pkg",), show="headings", selectmode="extended")
        self.apps_tree.heading("pkg", text="Paket adı", anchor="w")
        sc = ttk.Scrollbar(body, orient="vertical", command=self.apps_tree.yview)
        self.apps_tree.configure(yscrollcommand=sc.set)
        self.apps_tree.pack(side="left", fill="both", expand=True)
        sc.pack(side="left", fill="y")
        self.apps_tree.bind("<Double-1>", lambda e: self.app_action("launch"))
        side = ttk.Frame(body, padding=(12, 0, 0, 0))
        side.pack(side="left", fill="y")
        self.apps_all = tk.BooleanVar(value=False)
        ttk.Checkbutton(side, text="Seçili tüm cihazlarda\nuygula", variable=self.apps_all).pack(anchor="w")
        self.apps_target_lbl = ttk.Label(side, text="", style="Acc.TLabel", wraplength=200)
        self.apps_target_lbl.pack(anchor="w", pady=(2, 10))
        for t, a, st in (("▶  Aç", "launch", "A.TButton"), ("■  Durdur", "stop", "TButton"),
                         ("Verileri temizle", "clear", "TButton"), ("APK'yı kaydet", "pull", "TButton"),
                         ("Kaldır", "uninstall", "D.TButton")):
            ttk.Button(side, text=t, style=st, width=20, command=lambda a=a: self.app_action(a)).pack(fill="x", pady=2)
        return f

    def _build_macro(self, win):
        f = ttk.Frame(win, padding=18)
        r = self.row(f)
        ttk.Label(r, text="Otomasyon (makro)", style="H.TLabel").pack(side="left")
        self.macro_info = ttk.Label(r, text="", style="M.TLabel")
        self.macro_info.pack(side="right")
        ttk.Label(f, style="M.TLabel", justify="left", wraplength=920, text=(
            "“Kaydı başlat”tan sonra ana penceredeki bir ekranda yaptığınız dokunma, kaydırma, tuş ve yazı "
            "işlemleri adım adım kaydedilir. Sonra bu adımları seçili tüm cihazlarda istediğiniz kadar oynatabilirsiniz.")
        ).pack(anchor="w", pady=(4, 0))
        r = self.row(f, pady=(12, 0))
        self.rec_btn = ttk.Button(r, text="●  Kaydı başlat", style="A.TButton", command=self.toggle_record)
        self.rec_btn.pack(side="left")
        ttk.Button(r, text="Bekleme ekle", command=self.macro_add_wait).pack(side="left", padx=(10, 0))
        r = self.row(f, pady=(8, 0))
        ttk.Label(r, text="Elle adım ekle:", style="M.TLabel").pack(side="left")
        self.macro_key = tk.StringVar(value="Ana ekran")
        ttk.Combobox(r, textvariable=self.macro_key, state="readonly", width=15, values=list(A.KEY_NAMES)).pack(side="left", padx=(10, 4))
        ttk.Button(r, text="Tuş ekle", command=self.macro_add_key).pack(side="left")
        ttk.Button(r, text="Metin ekle", command=self.macro_add_text).pack(side="left", padx=(10, 0))
        ttk.Button(r, text="Uygulama aç ekle", command=self.macro_add_app).pack(side="left", padx=(10, 0))
        body = ttk.Frame(f)
        body.pack(fill="both", expand=True, pady=(10, 0))
        self.macro_tree = ttk.Treeview(body, columns=("n", "islem", "detay", "bekle"), show="headings")
        for c, t, w in (("n", "#", 40), ("islem", "İşlem", 110), ("detay", "Ayrıntı", 380), ("bekle", "Önce bekle", 100)):
            self.macro_tree.heading(c, text=t, anchor="w")
            self.macro_tree.column(c, width=w, anchor="w", stretch=(c == "detay"))
        self.macro_tree.pack(side="left", fill="both", expand=True)
        self.macro_tree.bind("<Double-1>", lambda e: self.macro_edit_wait())
        side = ttk.Frame(body, padding=(12, 0, 0, 0))
        side.pack(side="left", fill="y")
        for t, cmd in (("Yukarı taşı", lambda: self.macro_move(-1)), ("Aşağı taşı", lambda: self.macro_move(1)),
                       ("Beklemeyi değiştir", self.macro_edit_wait), ("Seçili adımı sil", self.macro_delete),
                       ("Tümünü temizle", self.macro_clear), ("Kaydet…", self.macro_save), ("Aç…", self.macro_load)):
            ttk.Button(side, text=t, width=18, command=cmd).pack(fill="x", pady=2)
        r = self.row(f, pady=(12, 0))
        ttk.Label(r, text="Tekrar").pack(side="left")
        self.macro_rep = tk.StringVar(value="1")
        ttk.Spinbox(r, from_=1, to=100000, textvariable=self.macro_rep, width=7).pack(side="left", padx=(6, 14))
        ttk.Label(r, text="Hız").pack(side="left")
        self.macro_speed = tk.StringVar(value="1x")
        ttk.Combobox(r, textvariable=self.macro_speed, state="readonly", width=6, values=("0.5x", "1x", "2x", "4x")).pack(side="left", padx=(6, 14))
        ttk.Label(r, text="Rastgele başlangıç gecikmesi (ms)").pack(side="left")
        self.macro_jitter = tk.StringVar(value="0")
        ttk.Spinbox(r, from_=0, to=10000, increment=100, textvariable=self.macro_jitter, width=7).pack(side="left", padx=6)
        r = self.row(f, pady=(10, 0))
        self.macro_target_lbl = ttk.Label(r, text="", style="Acc.TLabel")
        self.macro_target_lbl.pack(side="left")
        self.play_btn = ttk.Button(r, text="▶  Seçililerde oynat", style="A.TButton", command=self.toggle_play)
        self.play_btn.pack(side="right")
        return f

    def _build_info(self, win):
        f = ttk.Frame(win, padding=20)
        self.info_title = ttk.Label(f, text="Cihaz seçilmedi", style="Big.TLabel")
        self.info_title.pack(anchor="w")
        grid = ttk.Frame(f)
        grid.pack(anchor="w", pady=14)
        self.info_vals = {}
        for i, (k, t) in enumerate((("brand", "Marka"), ("model", "Model"), ("android", "Android sürümü"),
                                    ("sdk", "API seviyesi"), ("abi", "İşlemci mimarisi"), ("battery", "Pil"),
                                    ("res", "Çözünürlük"), ("density", "Yoğunluk"), ("storage", "Depolama"),
                                    ("ip", "Wi-Fi IP adresi"), ("conn", "Bağlantı"), ("group", "Grup"), ("serial", "Seri no"))):
            ttk.Label(grid, text=t, style="M.TLabel").grid(row=i, column=0, sticky="w", pady=3, padx=(0, 30))
            v = ttk.Label(grid, text="—")
            v.grid(row=i, column=1, sticky="w")
            self.info_vals[k] = v
        r = self.row(f)
        ttk.Button(r, text="Bilgileri yenile", command=self.reload_info).pack(side="left")
        ttk.Button(r, text="Ad ver", command=lambda: self.focus_serial and self.rename_device(self.focus_serial)).pack(side="left", padx=8)
        ttk.Button(r, text="Kablosuza geç", style="A.TButton", command=lambda: self.go_wireless(self.focus_serial)).pack(side="left")
        lf = self.box(f, "Özel modlar")
        r = self.row(lf)
        ttk.Button(r, text="OTG modu (ADB'siz)", command=self.open_otg).pack(side="left")
        ttk.Button(r, text="Arka kamera", command=lambda: self.open_camera("back")).pack(side="left", padx=8)
        ttk.Button(r, text="Ön kamera", command=lambda: self.open_camera("front")).pack(side="left")
        ttk.Label(lf, style="M.TLabel", justify="left", wraplength=540, text=(
            "OTG: telefon yalnızca USB ile bağlıyken, USB hata ayıklama kapalı olsa bile fare ve klavye ile kontrol "
            "sağlar (görüntü yoktur). Kamera yansıtma Android 12+ gerektirir.")).pack(anchor="w", pady=(8, 0))
        return f

    def _build_settings(self, win):
        f = ttk.Frame(win, padding=20)
        ttk.Label(f, text="Ayarlar", style="H.TLabel").pack(anchor="w")
        lf = self.box(f, "Güncelleme")
        ttk.Label(lf, text="Güncelleme kaynağı (internet adresi veya klasör):", style="M.TLabel").pack(anchor="w")
        r = self.row(lf, pady=(4, 0))
        self.upd_var = tk.StringVar(value=self.cfg.get("update_source", ""))
        ttk.Entry(r, textvariable=self.upd_var).pack(side="left", fill="x", expand=True)
        ttk.Button(r, text="Klasör seç…", command=self._pick_update_dir).pack(side="left", padx=6)
        ttk.Button(r, text="Kaydet", command=self._save_update_source).pack(side="left")
        r = self.row(lf, pady=(8, 0))
        self.upd_start_var = tk.BooleanVar(value=self.cfg.get("update_on_start", True))
        ttk.Checkbutton(r, text="Program açılınca güncellemeleri kontrol et", variable=self.upd_start_var,
                        command=lambda: self._set_cfg("update_on_start", self.upd_start_var.get())).pack(side="left")
        ttk.Button(r, text="Şimdi kontrol et", style="A.TButton", command=lambda: self.check_update(False)).pack(side="right")
        lf = self.box(f, "ADB")
        self.adb_lbl = ttk.Label(lf, text=self.adb.path or "bulunamadı", wraplength=780, justify="left")
        self.adb_lbl.pack(anchor="w")
        r = self.row(lf, pady=(8, 0))
        ttk.Button(r, text="adb.exe seç…", command=self.choose_adb).pack(side="left")
        ttk.Button(r, text="Varsayılana dön", command=self.reset_adb).pack(side="left", padx=8)
        ttk.Button(r, text="ADB sunucusunu yeniden başlat", command=self.restart_adb).pack(side="left")
        ttk.Label(lf, text="Laixi, Panda gibi başka programlar açıksa ADB çakışabilir; onları kapatıp ADB sunucusunu "
                           "yeniden başlatın.", style="M.TLabel", wraplength=780, justify="left").pack(anchor="w", pady=(6, 0))
        lf = self.box(f, "Genel")
        self.tray_var = tk.BooleanVar(value=self.cfg.get("close_to_tray", True))
        ttk.Checkbutton(lf, text="Pencere kapatılınca sistem tepsisinde çalışmaya devam et", variable=self.tray_var,
                        command=lambda: self._set_cfg("close_to_tray", self.tray_var.get())).pack(anchor="w")
        self.awake_var = tk.BooleanVar(value=self.cfg.get("stay_awake", True))
        ttk.Checkbutton(lf, text="Ekran açıkken USB'deki telefonların uyumasını engelle", variable=self.awake_var,
                        command=lambda: self._set_cfg("stay_awake", self.awake_var.get())).pack(anchor="w", pady=(4, 0))
        r = self.row(lf, pady=(6, 0))
        ttk.Label(r, text="Canlı önizleme kapalıyken ekran resmi aralığı (sn)").pack(side="left")
        self.shot_int_var = tk.StringVar(value=str(self.cfg.get("shot_interval", 5)))
        sp = ttk.Spinbox(r, from_=2, to=120, textvariable=self.shot_int_var, width=5, command=self._shot_interval_changed)
        sp.pack(side="left", padx=8)
        sp.bind("<FocusOut>", lambda e: self._shot_interval_changed())
        lf = self.box(f, "Ekran resmi ve video kayıt klasörü")
        r = self.row(lf)
        self.media_lbl = ttk.Label(r, text=self.cfg["media_dir"])
        self.media_lbl.pack(side="left")
        ttk.Button(r, text="Aç", command=self.open_media_dir).pack(side="right")
        ttk.Button(r, text="Değiştir…", command=self.choose_media_dir).pack(side="right", padx=8)
        lf = self.box(f, "Hakkında")
        self.about_lbl = ttk.Label(lf, style="M.TLabel", justify="left", text="")
        self.about_lbl.pack(anchor="w")
        self.bg(lambda: (S.scrcpy_version(), self.adb.version() if self.adb.path else "?"),
                done=self._show_about, fail=lambda e: self._show_about(("?", "?")))
        return f

    def _show_about(self, vers):
        sv, av = vers
        self.about_lbl.configure(text=(f"irba test {paths.APP_VERSION}\n"
                                       f"Ekran motoru: scrcpy {sv or '?'} (Apache 2.0, Genymobile)\nADB: {av}\n"
                                       f"Ayarlar: {cfg_path()}"))

    def open_apps(self, s=None):
        if s:
            self.focus_serial = s
        self.tools["apps"].show()
        self.load_apps()

    def show_info(self, s):
        if s:
            self.focus_serial = s
        self._show_info()
        self.tools["info"].show()

    def _show_info(self):
        if not hasattr(self, "info_vals"):
            return
        s = self.focus_serial
        v = self.info_vals
        if not s or s not in self.devices:
            self.info_title.configure(text="Cihaz seçilmedi")
            for lbl in v.values():
                lbl.configure(text="—")
            return
        d = self.devices[s]
        info = d.get("info") or {}
        self.info_title.configure(text=f"{self.label(s)}")
        for k in ("brand", "android", "sdk", "abi", "battery", "density", "storage", "ip"):
            v[k].configure(text=info.get(k) or "—")
        v["model"].configure(text=info.get("model") or d.get("model") or "—")
        w, h = info.get("width"), info.get("height")
        v["res"].configure(text=f"{w} × {h}" if w else "—")
        v["conn"].configure(text="Wi-Fi" if ":" in s else "USB")
        v["group"].configure(text=self.cfg["groups"].get(s) or "—")
        v["serial"].configure(text=s)

    def reload_info(self):
        for s in (self.selected or ({self.focus_serial} if self.focus_serial else set())):
            if s in self.devices:
                self.devices[s]["stale"] = True
        self.refresh_devices()

    def open_otg(self):
        s = self.focus_serial
        if s and ":" in s:
            messagebox.showinfo(APP, "OTG modu yalnızca USB ile bağlı cihazlarda çalışır.")
            return
        try:
            S.launch(self.adb.path, s, ["--otg"], f"OTG — {self.label(s) if s else 'USB'}")
            self.log("OTG modu açıldı.", "ok")
        except Exception as e:  # noqa: BLE001
            self.log(f"OTG açılamadı: {e}", "err")

    def open_camera(self, facing):
        s = self.focus_serial
        if not s or not self.is_online(s):
            self.need_device()
            return
        try:
            S.launch(self.adb.path, s, ["--video-source=camera", f"--camera-facing={facing}", "--camera-size=1280x720",
                                        "--no-audio"], f"Kamera — {self.label(s)}")
        except Exception as e:  # noqa: BLE001
            self.log(f"Kamera açılamadı: {e}", "err")

    # =================================================================== Wi-Fi
    def wifi_connect(self, addr=None):
        addr = (addr or self.connect_var.get()).strip()
        if not addr:
            messagebox.showinfo(APP, "Telefonun IP adresini yazın. Örnek: 192.168.1.25")
            return
        if ":" not in addr:
            addr += ":5555"
        self.log(f"Bağlanılıyor: {addr}")
        self.bg(self.adb.connect, addr, done=lambda r: (
            self.log(r, "ok" if "connected" in r and "cannot" not in r and "failed" not in r else "err"),
            self.refresh_devices()))

    def wifi_disconnect(self, serials=None):
        ts = [s for s in (serials or list(self.selected)) if ":" in s]
        if not ts:
            messagebox.showinfo(APP, "Seçili cihazlar arasında Wi-Fi ile bağlı olan yok.")
            return
        for s in ts:
            self.bg(self.adb.disconnect, s, done=lambda r: (self.log(r), self.refresh_devices()))

    def pair_device(self):
        addr, code = self.pair_addr.get().strip(), self.pair_code.get().strip()
        if not addr or not code:
            messagebox.showinfo(APP, "Eşleme IP:port ve kodunu yazın.")
            return
        self.log(f"Eşleştiriliyor: {addr}")

        def done(r):
            ok = "Success" in r
            self.log(r, "ok" if ok else "err")
            if ok:
                messagebox.showinfo(APP, "Eşleşme tamam. Şimdi telefonun “Kablosuz hata ayıklama” ekranındaki IP adresi "
                                         "ve bağlantı noktasını (eşleme portundan farklıdır) yukarıdaki “Bağlan” kutusuna yazın.")
        self.bg(self.adb.pair, addr, code, done=done)

    def scan_network(self):
        ips = A.local_ipv4s()
        if not ips:
            messagebox.showwarning(APP, "Bilgisayarın yerel ağ adresi bulunamadı.")
            return
        self.log("Ağ taranıyor (port 5555)…")

        def done(found):
            if not found:
                self.log("Ağda ADB'si açık telefon bulunamadı. Önce USB ile “Kablosuza geçir” yapın veya "
                         "Android 11+ için eşleştirmeyi kullanın.", "warn")
                return
            self.log(f"Bulunan: {', '.join(found)}", "ok")
            for a in found:
                self.wifi_connect(a)
        self.bg(A.scan_network, done=done)

    def go_wireless(self, s):
        if not s or not self.is_online(s):
            self.need_device()
            return
        if ":" in s:
            return
        ip = (self.devices[s].get("info") or {}).get("ip")
        if not ip:
            self.log(f"{self.label(s)}: Wi-Fi IP adresi yok (telefon Wi-Fi'ye bağlı mı?).", "err")
            return

        def work():
            r1 = self.adb.tcpip(s)
            time.sleep(2.5)
            return f"{r1} → {self.adb.connect(ip + ':5555')}"
        self.log(f"{self.label(s)} kablosuza geçiriliyor ({ip})…")
        self.bg(work, done=lambda r: (self.log(r, "ok"), self.refresh_devices()))

    def batch_wireless(self):
        ts = [s for s in self.selected if ":" not in s]
        if not ts:
            messagebox.showinfo(APP, "Seçili USB cihaz yok.")
            return
        for s in ts:
            self.go_wireless(s)

    # =================================================================== toplu işlemler
    def targets(self, ask=True):
        ts = [s for s in self.online() if s in self.selected]
        if ts or not ask:
            return ts
        on = self.online()
        if on and messagebox.askyesno(APP, f"Seçili cihaz yok.\nİşlem tüm çevrimiçi {len(on)} cihaza uygulansın mı?"):
            return on
        return []

    def _report(self, s, r):
        if isinstance(r, tuple) and len(r) == 3:
            code, out, err = r
            text, ok = (out or err or "tamam").strip(), code == 0
        else:
            text, ok = str(r if r is not None else "tamam"), True
        if len(text) > 1500:
            text = text[:1500] + " …"
        self.log(f"  {'✓' if ok else '✗'} {self.label(s)}: " + text.replace("\n", "\n      "), "ok" if ok else "err")

    def run_batch(self, title, fn, targets=None):
        ts = targets if targets is not None else self.targets()
        if not ts:
            if not self.online():
                messagebox.showwarning(APP, "Çevrimiçi cihaz yok.")
            return False
        self.log(f"▶ {title} — {len(ts)} cihaz")
        if not self.log_frame.winfo_ismapped():
            self.toggle_log()
        for s in ts:
            self.bg(fn, s, done=lambda r, s=s: self._report(s, r), fail=lambda e, s=s: self.log(f"  ✗ {self.label(s)}: {e}", "err"))
        return True

    def batch_shell(self):
        cmd = self.cmd_var.get().strip()
        if cmd:
            self.run_batch(f"Komut: {cmd}", lambda s: self.adb.shell(s, cmd, timeout=180))

    def batch_key(self, code, title):
        self.run_batch(title, lambda s: self.adb.key(s, code))

    def _files(self, var):
        return [p for p in (x.strip() for x in var.get().split(";")) if p]

    def quick_apk(self):
        ps = filedialog.askopenfilenames(title="Kurulacak APK dosyaları", filetypes=[("APK", "*.apk")])
        if ps:
            self.apk_var.set(";".join(ps))
            self.batch_install(list(ps))

    def batch_install(self, files=None):
        apks = [a for a in (files or self._files(self.apk_var)) if os.path.isfile(a)]
        if not apks:
            messagebox.showwarning(APP, "Önce APK dosyası seçin.")
            return

        def work(s):
            res = []
            for a in apks:
                code, out, err = self.adb.install(s, a)
                res.append(f"{os.path.basename(a)}: {'kuruldu' if code == 0 and 'Success' in out else (err or out)}")
            return "\n".join(res)
        self.run_batch(f"Kurulum: {len(apks)} APK", work)

    def batch_push(self, files=None):
        src = [p for p in (files or self._files(self.push_var)) if os.path.exists(p)]
        dst = self.push_dst.get().strip() or "/sdcard/Download/"
        if not dst.endswith("/"):
            dst += "/"
        if not src:
            messagebox.showwarning(APP, "Önce gönderilecek dosyaları seçin.")
            return
        media = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".mp4", ".mov", ".mkv", ".3gp", ".mp3", ".m4a", ".wav")

        def work(s):
            res = []
            for p in src:
                code, out, err = self.adb.push(s, p, dst)
                if code == 0 and p.lower().endswith(media):
                    self.adb.shell(s, f"am broadcast -a android.intent.action.MEDIA_SCANNER_SCAN_FILE -d 'file://{dst}{os.path.basename(p)}'")
                res.append(f"{os.path.basename(p)}: {'gönderildi' if code == 0 else (err or out)}")
            return "\n".join(res)
        self.run_batch(f"Dosya gönderimi → {dst}", work)

    def batch_reboot(self, serials=None):
        ts = serials or self.targets()
        if ts and messagebox.askyesno(APP, f"{len(ts)} cihaz yeniden başlatılsın mı?"):
            self.run_batch("Yeniden başlatma", self.adb.reboot, ts)

    def batch_screenshots(self):
        ts = self.targets()
        if not ts:
            return
        folder = os.path.join(self.cfg["media_dir"], time.strftime("ekranlar_%Y%m%d_%H%M%S"))
        os.makedirs(folder, exist_ok=True)

        def work(s):
            p = os.path.join(folder, f"{safe_name(self.label(s))}.png")
            with open(p, "wb") as f:
                f.write(self.adb.screencap(s))
            return p
        if self.run_batch("Ekran resimleri", work, ts):
            self.after(3500, lambda: os.startfile(folder))

    def batch_text_dialog(self):
        ts = self.targets()
        if not ts:
            return
        t = simpledialog.askstring(APP, "Seçili cihazlarda açık yazı alanına yazılacak metin (Türkçe olur):", parent=self)
        if t:
            self.run_batch("Metin yazma", lambda s: self.ctl.paste_text(s, t), ts)

    def batch_clipboard(self):
        ts = self.targets()
        if ts:
            self.clip_to_phone(ts)

    def on_drop(self, files):
        if not files:
            return
        ts = self.targets()
        if not ts:
            return
        apks = [f for f in files if f.lower().endswith(".apk")]
        if apks and len(apks) == len(files):
            if messagebox.askyesno(APP, f"{len(apks)} APK, {len(ts)} cihaza kurulsun mu?"):
                self.apk_var.set(";".join(apks))
                self.batch_install(apks)
        elif messagebox.askyesno(APP, f"{len(files)} dosya, {len(ts)} cihazın {self.push_dst.get()} klasörüne gönderilsin mi?"):
            self.push_var.set(";".join(files))
            self.batch_push(files)

    # =================================================================== uygulamalar
    def load_apps(self):
        s = self.focus_serial or (sorted(self.selected)[0] if self.selected else None)
        if not s or not self.is_online(s):
            self.apps_title.configure(text="Uygulamalar — önce bir cihaza tıklayın")
            return
        self.apps_title.configure(text=f"Uygulamalar — {self.label(s)}")
        self.apps_count.configure(text="yükleniyor…")
        self._apps_serial = s
        self.bg(self.adb.packages, s, self.apps_sys.get(), done=lambda lst, s=s: self._got_apps(s, lst))

    def _got_apps(self, s, lst):
        if s != getattr(self, "_apps_serial", None):
            return
        self.app_list = lst
        self._render_apps()

    def _render_apps(self):
        q = self.apps_search.get().strip().lower()
        self.apps_tree.delete(*self.apps_tree.get_children())
        shown = [p for p in self.app_list if q in p.lower()]
        for p in shown:
            self.apps_tree.insert("", "end", iid=p, values=(p,))
        self.apps_count.configure(text=f"{len(shown)} / {len(self.app_list)} uygulama")

    def app_action(self, action):
        pkgs = list(self.apps_tree.selection())
        if not pkgs:
            messagebox.showinfo(APP, "Listeden bir uygulama seçin.")
            return
        base = getattr(self, "_apps_serial", None)
        ts = self.targets() if self.apps_all.get() else ([base] if base else [])
        if not ts:
            return
        names = {"launch": "Aç", "stop": "Durdur", "clear": "Verileri temizle", "uninstall": "Kaldır", "pull": "APK kaydet"}
        if action in ("clear", "uninstall"):
            what = "verileri silinsin" if action == "clear" else "kaldırılsın"
            if not messagebox.askyesno(APP, f"{len(pkgs)} uygulama, {len(ts)} cihazda {what} mı?\n\n"
                                            + "\n".join(pkgs[:8]) + ("\n…" if len(pkgs) > 8 else "")):
                return
        folder = None
        if action == "pull":
            folder = filedialog.askdirectory(title="APK dosyaları nereye kaydedilsin?")
            if not folder:
                return
            ts = ts[:1]
        fn = {"launch": self.adb.launch, "stop": self.adb.force_stop, "clear": self.adb.clear_data,
              "uninstall": self.adb.uninstall}.get(action)

        def work(s):
            out = []
            for p in pkgs:
                if action == "pull":
                    out.append(", ".join(self.adb.pull_apk(s, p, folder)))
                else:
                    code, o, e = fn(s, p)
                    out.append(f"{p}: {'tamam' if code == 0 else (e or o)}")
            return "\n".join(out)
        if self.run_batch(f"{names[action]}: {', '.join(pkgs[:3])}{'…' if len(pkgs) > 3 else ''}", work, ts) and action == "uninstall":
            self.after(2500, self.load_apps)

    # =================================================================== otomasyon
    def _record(self, step):
        now = time.time()
        wait = int((now - self._rec_last) * 1000) if self.macro else 0
        self._rec_last = now
        st = dict(step)
        st["wait"] = min(wait, 60000)
        self.macro.append(st)
        self._render_macro()

    def toggle_record(self):
        if self.rec_on:
            self.rec_on = False
            self.rec_btn.configure(text="●  Kaydı başlat")
            self.log(f"Makro kaydı durdu ({len(self.macro)} adım).", "ok")
        else:
            if not self.online():
                messagebox.showinfo(APP, "Kayıt için çevrimiçi bir cihaz gerekli.")
                return
            self.rec_on = True
            self._rec_last = time.time()
            self.rec_btn.configure(text="■  Kaydı durdur")
            self.log("Makro kaydı başladı: ana penceredeki bir telefon ekranında işlemlerinizi yapın.", "warn")
            self.lift()
        self._overlay_update()

    def _step_text(self, st):
        t = st["t"]
        if t == "tap":
            return "Dokun", f"x %{st['x'] * 100:.1f}   y %{st['y'] * 100:.1f}"
        if t == "swipe":
            if st["x"] == st["x2"] and st["y"] == st["y2"]:
                return "Uzun bas", f"x %{st['x'] * 100:.1f}   y %{st['y'] * 100:.1f}   ({st['ms']} ms)"
            return "Kaydır", f"(%{st['x'] * 100:.0f}, %{st['y'] * 100:.0f}) → (%{st['x2'] * 100:.0f}, %{st['y2'] * 100:.0f})   {st['ms']} ms"
        if t == "key":
            return "Tuş", KEY_LABEL.get(st["code"], str(st["code"]))
        if t == "text":
            return "Metin", st["text"]
        if t == "app":
            return "Uygulama aç", st["pkg"]
        if t == "wait":
            return "Bekle", ""
        if t == "ctl":
            return "Panel", {S.T_EXPAND_NOTIF: "Bildirimler", S.T_EXPAND_SETTINGS: "Hızlı ayarlar",
                             S.T_COLLAPSE: "Panelleri kapat", S.T_ROTATE: "Döndür"}.get(st["msg"], "?")
        return t, ""

    def _render_macro(self):
        self.macro_tree.delete(*self.macro_tree.get_children())
        total = 0
        for i, st in enumerate(self.macro):
            a, b = self._step_text(st)
            total += st.get("wait", 0)
            self.macro_tree.insert("", "end", iid=str(i), values=(i + 1, a, b, f"{st.get('wait', 0)} ms"))
        if self.macro:
            self.macro_tree.see(str(len(self.macro) - 1))
        self.macro_info.configure(text=f"{len(self.macro)} adım · yaklaşık {total / 1000:.1f} sn")

    def _sel_index(self):
        sel = self.macro_tree.selection()
        return int(sel[0]) if sel else None

    def macro_add_wait(self):
        ms = simpledialog.askinteger(APP, "Bekleme süresi (milisaniye):", initialvalue=1000, minvalue=0, maxvalue=3600000, parent=self)
        if ms is not None:
            self.macro.append({"t": "wait", "wait": ms})
            self._render_macro()

    def macro_add_key(self):
        self.macro.append({"t": "key", "code": A.KEY_NAMES[self.macro_key.get()], "wait": 500})
        self._render_macro()

    def macro_add_text(self):
        t = simpledialog.askstring(APP, "Yazılacak metin (Türkçe olur):", parent=self)
        if t:
            self.macro.append({"t": "text", "text": t, "wait": 500})
            self._render_macro()

    def macro_add_app(self):
        sel = self.apps_tree.selection()
        p = simpledialog.askstring(APP, "Açılacak uygulamanın paket adı (ör. com.android.chrome):",
                                   initialvalue=sel[0] if sel else "", parent=self)
        if p and p.strip():
            self.macro.append({"t": "app", "pkg": p.strip(), "wait": 500})
            self._render_macro()

    def macro_move(self, d):
        i = self._sel_index()
        if i is None or not (0 <= i + d < len(self.macro)):
            return
        self.macro[i], self.macro[i + d] = self.macro[i + d], self.macro[i]
        self._render_macro()
        self.macro_tree.selection_set(str(i + d))

    def macro_edit_wait(self):
        i = self._sel_index()
        if i is None:
            return
        ms = simpledialog.askinteger(APP, "Bu adımdan önce bekleme (ms):", initialvalue=self.macro[i].get("wait", 0),
                                     minvalue=0, maxvalue=3600000, parent=self)
        if ms is not None:
            self.macro[i]["wait"] = ms
            self._render_macro()
            self.macro_tree.selection_set(str(i))

    def macro_delete(self):
        for i in sorted((int(x) for x in self.macro_tree.selection()), reverse=True):
            del self.macro[i]
        self._render_macro()

    def macro_clear(self):
        if self.macro and messagebox.askyesno(APP, "Tüm adımlar silinsin mi?"):
            self.macro = []
            self._render_macro()

    def macro_save(self):
        if not self.macro:
            return
        p = filedialog.asksaveasfilename(defaultextension=".irba", initialfile="makro.irba",
                                         filetypes=[("irba makro", "*.irba"), ("JSON", "*.json")])
        if p:
            with open(p, "w", encoding="utf-8") as f:
                json.dump({"irba_macro": 1, "steps": self.macro}, f, ensure_ascii=False, indent=1)
            self.log(f"Makro kaydedildi: {p}", "ok")

    def macro_load(self):
        p = filedialog.askopenfilename(filetypes=[("irba makro", "*.irba"), ("JSON", "*.json")])
        if not p:
            return
        try:
            with open(p, encoding="utf-8") as f:
                steps = json.load(f).get("steps", [])
            assert all(isinstance(s, dict) and "t" in s for s in steps)
        except Exception as e:  # noqa: BLE001
            messagebox.showerror(APP, f"Makro okunamadı: {e}")
            return
        self.macro = steps
        self._render_macro()
        self.log(f"Makro yüklendi: {p} ({len(steps)} adım)", "ok")

    def toggle_play(self):
        if self.playing:
            self.play_stop.set()
            self.log("Makro durduruluyor…", "warn")
            return
        if not self.macro:
            messagebox.showinfo(APP, "Önce adım kaydedin veya bir makro açın.")
            return
        ts = self.targets()
        if not ts:
            return
        try:
            reps = max(1, int(self.macro_rep.get()))
            jitter = max(0, int(self.macro_jitter.get()))
        except ValueError:
            reps, jitter = 1, 0
        speed = float(self.macro_speed.get().rstrip("x"))
        steps = [dict(s) for s in self.macro]
        self.play_stop.clear()
        self.playing = len(ts)
        self.play_btn.configure(text="■  Durdur")
        self.log(f"▶ Makro: {len(steps)} adım × {reps} tekrar, {len(ts)} cihaz")
        for s in ts:
            threading.Thread(target=self._play_on, args=(s, steps, reps, speed, jitter), daemon=True).start()

    def _play_on(self, s, steps, reps, speed, jitter):
        err, done_reps = None, 0
        try:
            if jitter:
                time.sleep(random.randint(0, jitter) / 1000)
            orient = self.adb.orientation(s)
            for _ in range(reps):
                for st in steps:
                    end = time.time() + st.get("wait", 0) / speed / 1000
                    while time.time() < end:
                        if self.play_stop.is_set():
                            raise InterruptedError
                        time.sleep(max(0, min(0.1, end - time.time())))
                    if self.play_stop.is_set():
                        raise InterruptedError
                    if st["t"] != "wait":
                        self._exec_step(s, st, orient)
                done_reps += 1
        except InterruptedError:
            err = "durduruldu"
        except Exception as e:  # noqa: BLE001
            err = str(e)
        self.ui(self._play_done, s, done_reps, err)

    def _play_done(self, s, reps, err):
        self.log(f"  {'✗' if err else '✓'} {self.label(s)}: {reps} tekrar" + (f" ({err})" if err else ""),
                 "err" if err and err != "durduruldu" else "ok")
        self.playing -= 1
        if self.playing <= 0:
            self.playing = 0
            self.play_btn.configure(text="▶  Seçililerde oynat")

    # =================================================================== güncelleme
    def _pick_update_dir(self):
        d = filedialog.askdirectory(title="Güncelleme klasörü (içinde guncelleme.json olan)")
        if d:
            self.upd_var.set(d)
            self._save_update_source()

    def _save_update_source(self):
        self._set_cfg("update_source", self.upd_var.get().strip())
        self.log("Güncelleme kaynağı kaydedildi.", "ok")

    def check_update(self, silent=False):
        if self.updating:
            return
        src = self.cfg.get("update_source", "").strip()
        if not src:
            if not silent:
                messagebox.showinfo(APP, "Güncelleme kaynağı henüz ayarlanmadı.\n\nAyarlar penceresindeki "
                                         "“Güncelleme” bölümüne güncelleme adresini veya klasörünü yazın.")
                self.tools["settings"].show()
            return
        self.updating = True
        self._set_tool_active("update", True)
        if not silent:
            self.log("Güncellemeler kontrol ediliyor…")
        self.bg(U.fetch_manifest, src, done=lambda m: self._got_manifest(m, silent), fail=lambda e: self._update_failed(e, silent))

    def _update_failed(self, e, silent):
        self.updating = False
        self._set_tool_active("update", False)
        self.log(f"Güncelleme: {e}", "err" if not silent else "muted")
        if not silent:
            messagebox.showerror(APP, str(e))

    def _got_manifest(self, m, silent):
        if not U.is_newer(m["version"]):
            self.updating = False
            self._set_tool_active("update", False)
            if not silent:
                self.log(f"Program güncel (sürüm {paths.APP_VERSION}).", "ok")
                messagebox.showinfo(APP, f"irba test güncel.\nKurulu sürüm: {paths.APP_VERSION}")
            return
        notes = (m.get("notes") or "").strip()
        if not messagebox.askyesno(APP, f"Yeni sürüm var: {m['version']}  (kurulu: {paths.APP_VERSION})\n\n"
                                        + (notes[:800] + "\n\n" if notes else "")
                                        + "Şimdi indirilip kurulsun mu? Program kapanıp yeni sürümle yeniden açılacak."):
            self.updating = False
            self._set_tool_active("update", False)
            return
        self.log(f"Sürüm {m['version']} indiriliyor…")

        def prog(done, total):
            if total:
                self.ui(lambda: self.status_lbl.configure(text=f"Güncelleme indiriliyor… %{done * 100 // total}", fg=C["accent"]))
        self.bg(U.download, m, prog, done=self._install_update, fail=lambda e: self._update_failed(e, False))

    def _install_update(self, path):
        self.log("Güncelleme doğrulandı, kuruluyor…", "ok")
        try:
            U.run_installer(path)
        except OSError as e:
            self._update_failed(e, False)
            return
        self.after(800, self.quit_app)

    # =================================================================== ayarlar
    def _set_cfg(self, k, v):
        self.cfg[k] = v
        self.save()

    def _shot_interval_changed(self):
        try:
            self._set_cfg("shot_interval", max(2, int(self.shot_int_var.get())))
        except ValueError:
            pass

    def choose_adb(self):
        p = filedialog.askopenfilename(title="adb.exe seçin", filetypes=[("adb", "adb.exe"), ("Programlar", "*.exe")])
        if p:
            self._apply_adb(p, p)

    def reset_adb(self):
        self._apply_adb(A.find_adb(), None)

    def _apply_adb(self, path, store):
        self.ctl.close_all()
        self.adb.path = path
        self.cfg["adb_path"] = store
        self.save()
        self.adb_lbl.configure(text=path or "bulunamadı")
        self.refresh_devices()

    def restart_adb(self):
        self.ctl.close_all()
        for t in self.tiles.values():
            self._stop_tile_stream(t)

        def work():
            self.adb.kill_server()
            time.sleep(1)
            return self.adb.start_server()
        self.log("ADB sunucusu yeniden başlatılıyor…")
        self.bg(work, done=lambda r: (self.log("ADB sunucusu hazır.", "ok"), self.refresh_devices()))

    def choose_media_dir(self):
        d = filedialog.askdirectory(title="Kayıt klasörü")
        if d:
            self._set_cfg("media_dir", d)
            self.media_lbl.configure(text=d)

    def open_media_dir(self):
        os.makedirs(self.cfg["media_dir"], exist_ok=True)
        os.startfile(self.cfg["media_dir"])

    def _pick_many(self, var, types):
        ps = filedialog.askopenfilenames(filetypes=types)
        if ps:
            var.set(";".join(ps))

    # =================================================================== döngüler/kapanış
    def _auto_loop(self):
        self._tick += 1
        if self._tick % 15 == 0:
            for d in self.devices.values():
                d["stale"] = True
        if self._tick % 5 == 0:
            self.bg(A.running_server_adb, done=self._follow_shared_adb, fail=lambda e: None)
        if self.state() != "withdrawn":
            self.refresh_devices()
            interval = max(1, round(self.cfg.get("shot_interval", 5) / 4))
            for s in list(self.tiles):
                t = self.tiles.get(s)
                if not t:
                    continue
                if self.cfg.get("preview_live", True):
                    if not t["proc"] and self.is_online(s):
                        self._update_tile_stream(s)
                elif self._tick % interval == 0:
                    self._update_tile_stream(s)
        self.after(4000, self._auto_loop)

    def _start_tray(self):
        ico = paths.asset("irba.ico")
        if not (W.IS_WIN and os.path.isfile(ico)):
            return
        self.tray = W.Tray(ico, APP, on_show=lambda: self.q.put((self.show_window, None, None)),
                           on_exit=lambda: self.q.put((self.quit_app, None, None)))
        self.tray.start()

    def show_window(self, _=None):
        self.deiconify()
        self.state("zoomed" if self.winfo_screenwidth() < 2200 else "normal")
        self.lift()
        self.focus_force()

    def on_close(self):
        if self.cfg.get("close_to_tray", True) and self.tray and self.tray.hwnd:
            self.withdraw()
            self._overlay_place()
            if not self.cfg.get("tray_hint_shown"):
                self.tray.notify(APP, "Program arka planda çalışıyor. Açmak için tepsideki simgeye tıklayın; "
                                      "kapatmak için sağ tık → Çıkış.")
                self._set_cfg("tray_hint_shown", True)
        else:
            self.quit_app()

    def quit_app(self, _=None):
        self.play_stop.set()
        self.rec_on = False
        if self.big:
            self._stop_tile_stream(self.big)
        for t in self.tiles.values():
            self._stop_tile_stream(t)
        self.ctl.close_all()
        if self.tray:
            self.tray.stop()
        self.save()
        self.destroy()


def main():
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("irba.test")
        except Exception:  # noqa: BLE001
            pass
    App().mainloop()
