"""Dosya yolları: geliştirme ortamında ve kurulu (PyInstaller) programda aynı çalışır."""
import os
import sys

APP_NAME = "irba test"
APP_VERSION = "1.1.1"
UPDATE_URL = "https://raw.githubusercontent.com/ilknurses23-pixel/irba-test/main/guncelleme/guncelleme.json"


def base_dir():
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def tool(*parts):
    return os.path.join(base_dir(), "tools", *parts)


def asset(*parts):
    return os.path.join(base_dir(), "assets", *parts)


def data_dir():
    d = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "irba test")
    os.makedirs(d, exist_ok=True)
    return d


def default_media_dir():
    d = os.path.join(os.path.expanduser("~"), "Videos", "irba test")
    return d
