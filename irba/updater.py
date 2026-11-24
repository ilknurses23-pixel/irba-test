"""Program içi güncelleme.

Güncelleme kaynağı iki şekilde olabilir:
  * İnternet adresi:  https://…/guncelleme.json
  * Klasör:           C:\\…\\guncelleme   (yerel, ağ paylaşımı veya OneDrive/Google Drive klasörü)

Kaynakta bir `guncelleme.json` bulunur:
  {"version": "1.1.0", "file": "irba_test_Setup_1.1.0.exe", "url": "", "sha256": "…", "notes": "…"}
`url` boşsa kurulum dosyası json ile aynı yerde aranır. İndirilen dosyanın SHA-256 özeti
json'daki ile eşleşmezse kurulum çalıştırılmaz.
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import urllib.parse
import urllib.request

from . import paths

MANIFEST = "guncelleme.json"


class UpdateError(Exception):
    pass


def parse_version(v):
    nums = [int(x) for x in re.findall(r"\d+", str(v))[:4]]
    return tuple(nums + [0] * (4 - len(nums)))


def is_newer(remote, local=paths.APP_VERSION):
    return parse_version(remote) > parse_version(local)


def _is_url(s):
    return s.lower().startswith(("http://", "https://"))


def _manifest_location(source):
    source = source.strip().strip('"')
    if _is_url(source):
        return source if source.lower().endswith(".json") else source.rstrip("/") + "/" + MANIFEST
    if os.path.isdir(source):
        return os.path.join(source, MANIFEST)
    return source


def fetch_manifest(source):
    if not source or not source.strip():
        raise UpdateError("Güncelleme kaynağı ayarlanmamış (Ayarlar → Güncelleme).")
    loc = _manifest_location(source)
    try:
        if _is_url(loc):
            sep = "&" if "?" in loc else "?"
            req = urllib.request.Request(f"{loc}{sep}t={int(time.time())}", headers={"User-Agent": "irba-test-updater",
                                                       "Cache-Control": "no-cache"})
            with urllib.request.urlopen(req, timeout=20) as r:
                data = json.loads(r.read().decode("utf-8-sig"))
        else:
            with open(loc, encoding="utf-8-sig") as f:
                data = json.load(f)
    except (OSError, ValueError) as e:
        raise UpdateError(f"Güncelleme bilgisi okunamadı: {e}")
    if not data.get("version") or not (data.get("file") or data.get("url")):
        raise UpdateError("Güncelleme bilgisi eksik (version/file).")
    data["_loc"] = loc
    return data


def _setup_location(m):
    if m.get("url"):
        return m["url"]
    loc = m["_loc"]
    if _is_url(loc):
        return urllib.parse.urljoin(loc, urllib.parse.quote(m["file"]))
    return os.path.join(os.path.dirname(loc), m["file"])


def download(m, progress=None):
    """Kurulum dosyasını geçici klasöre indirir/kopyalar ve özetini doğrular."""
    src = _setup_location(m)
    name = os.path.basename(urllib.parse.urlparse(src).path) if _is_url(src) else os.path.basename(src)
    if not name.lower().endswith(".exe"):
        name = f"irba_test_Setup_{m['version']}.exe"
    dst_dir = os.path.join(tempfile.gettempdir(), "irba_test_guncelleme")
    os.makedirs(dst_dir, exist_ok=True)
    dst = os.path.join(dst_dir, name)
    h = hashlib.sha256()
    try:
        if _is_url(src):
            req = urllib.request.Request(src, headers={"User-Agent": "irba-test-updater"})
            with urllib.request.urlopen(req, timeout=60) as r, open(dst, "wb") as f:
                total = int(r.headers.get("Content-Length") or 0)
                done = 0
                while True:
                    chunk = r.read(256 * 1024)
                    if not chunk:
                        break
                    f.write(chunk)
                    h.update(chunk)
                    done += len(chunk)
                    if progress:
                        progress(done, total)
        else:
            total = os.path.getsize(src)
            done = 0
            with open(src, "rb") as fi, open(dst, "wb") as fo:
                while True:
                    chunk = fi.read(1024 * 1024)
                    if not chunk:
                        break
                    fo.write(chunk)
                    h.update(chunk)
                    done += len(chunk)
                    if progress:
                        progress(done, total)
    except OSError as e:
        raise UpdateError(f"Kurulum dosyası alınamadı: {e}")
    want = (m.get("sha256") or "").lower().strip()
    if not want:
        os.remove(dst)
        raise UpdateError("Güncelleme bilgisinde SHA-256 özeti yok; güvenlik için kurulmadı.")
    if h.hexdigest() != want:
        os.remove(dst)
        raise UpdateError("İndirilen dosya doğrulanamadı (SHA-256 eşleşmedi); kurulmadı.")
    return dst


def run_installer(path):
    """Kurulumu sessiz başlatır; kurulum bitince program kendiliğinden yeniden açılır."""
    subprocess.Popen([path, "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/FORCECLOSEAPPLICATIONS",
                      "/update=1"], close_fds=True)


def write_manifest(folder, setup_path, version, notes=""):
    """Yayınlama yardımcısı: kurulum dosyasını klasöre koyar ve guncelleme.json yazar."""
    os.makedirs(folder, exist_ok=True)
    name = os.path.basename(setup_path)
    target = os.path.join(folder, name)
    if os.path.abspath(target) != os.path.abspath(setup_path):
        shutil.copy2(setup_path, target)
    h = hashlib.sha256()
    with open(target, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    data = {"version": version, "file": name, "url": "", "sha256": h.hexdigest(), "notes": notes}
    with open(os.path.join(folder, MANIFEST), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return data
