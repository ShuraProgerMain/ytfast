"""ytfast — быстрое скачивание с YouTube в много соединений.

yt-dlp используется только для получения прямых ссылок на потоки,
сами потоки качаются параллельно кусками (обходит лимит скорости на одно
соединение), затем склеиваются ffmpeg без перекодирования.

    python ytfast.py URL [URL ...] [-q 1080] [-c 32] [-o папка] [--audio] [--h264]
"""
import argparse
import collections
import http.client
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit

CHUNK = 1 << 20  # 1 MiB: при ~100 КБ/с на соединение кусок качается ~10 с
MIN_STEAL = 128 * 1024  # меньше этого остаток у другого потока не отбираем
TIMEOUT = 8  # секунд без данных — соединение считаем зависшим
DEFAULT_OUT = os.path.join(os.path.expanduser("~"), "Downloads", "YouTube")
APPDATA_BIN = os.path.join(os.environ.get("APPDATA", ""), "media-downloader", "bin")
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)  # без мигающей консоли из GUI


class Error(Exception):
    pass


class Cancelled(Error):
    pass


def find_tool(name, env):
    path = os.environ.get(env) or shutil.which(name)
    if not path:
        cand = os.path.join(APPDATA_BIN, name + ".exe")
        path = cand if os.path.isfile(cand) else None
    if not path:
        raise Error(f"Не найден {name}. Укажи путь в переменной {env}.")
    return path


def format_selector(quality=None, audio=False, h264=False):
    if audio:
        return "ba[ext=m4a]/ba"
    h = f"[height<={quality}]" if quality else ""
    if h264:
        return f"bv*{h}[vcodec^=avc1]+ba[ext=m4a]/b{h}[ext=mp4]/b{h}"
    return f"bv*{h}+ba/b{h}"


def extract(ytdlp, url, fmt, cancel=None):
    cmd = [ytdlp, "-J", "--no-playlist", "--no-warnings", "-f", fmt, url]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                         encoding="utf-8", errors="replace", creationflags=NO_WINDOW)
    while True:
        try:
            out, err = p.communicate(timeout=0.2)
            break
        except subprocess.TimeoutExpired:
            if cancel and cancel.is_set():
                p.kill()
                p.communicate()
                raise Cancelled("Загрузка отменена")
    if p.returncode != 0:
        raise Error(f"yt-dlp не смог получить ссылки:\n{err.strip()}")
    info = json.loads(out)
    return info, info.get("requested_formats") or [info]


class Conn:
    """Keep-alive соединение одного потока; сам ходит по редиректам."""

    def __init__(self):
        self.host = None
        self.c = None

    def get(self, url, headers, start, end):
        for _ in range(5):
            u = urlsplit(url)
            if u.netloc != self.host or self.c is None:
                self.close()
                self.c = http.client.HTTPSConnection(u.netloc, timeout=TIMEOUT)
                self.host = u.netloc
            path = u.path + ("?" + u.query if u.query else "")
            self.c.request("GET", path, headers={**headers, "Range": f"bytes={start}-{end}"})
            resp = self.c.getresponse()
            if resp.status in (301, 302, 303, 307, 308):
                resp.read()
                url = resp.getheader("Location")
                continue
            if resp.status not in (200, 206):
                resp.read()
                raise IOError(f"HTTP {resp.status}")
            return resp
        raise IOError("слишком много редиректов")

    def close(self):
        if self.c:
            self.c.close()
        self.c = None


def content_length(url, headers):
    c = Conn()
    try:
        resp = c.get(url, headers, 0, 0)
        resp.read()
        cr = resp.getheader("Content-Range", "")
        return int(cr.rsplit("/", 1)[1])
    finally:
        c.close()


class Downloader:
    def __init__(self, streams, connections, cancel=None):
        self.cancel = cancel or threading.Event()
        self.conns = []
        self.pending = collections.deque()
        self.active = []  # задачи в работе: [stream, pos, end], end может уменьшить «вор»
        self.done = 0
        self.lock = threading.Lock()
        self.error = None
        self.total = sum(s["size"] for s in streams)
        # маленький файл режем мельче, чтобы работали все соединения
        chunk = max(min(CHUNK, self.total // connections), 2 * MIN_STEAL)
        for s in streams:
            with open(s["path"], "wb") as f:
                f.truncate(s["size"])
            for off in range(0, s["size"], chunk):
                self.pending.append([s, off, min(off + chunk, s["size"]) - 1])
        self.n = min(connections, len(self.pending))

    def take(self):
        """Следующий кусок; когда очередь пуста — забираем половину самого большого остатка."""
        with self.lock:
            if self.pending:
                t = self.pending.popleft()
            else:
                victim = max(self.active, key=lambda t: t[2] - t[1], default=None)
                if victim is None or victim[2] - victim[1] < 2 * MIN_STEAL:
                    return None
                mid = (victim[1] + victim[2]) // 2
                t = [victim[0], mid + 1, victim[2]]
                victim[2] = mid
            self.active.append(t)
            return t

    def worker(self):
        conn = Conn()
        with self.lock:
            self.conns.append(conn)
        files = {}
        try:
            while self.error is None and not self.cancel.is_set():
                t = self.take()
                if t is None:
                    return
                s = t[0]
                f = files.get(s["path"]) or files.setdefault(s["path"], open(s["path"], "r+b"))
                tries = 0
                while t[1] <= t[2] and not self.cancel.is_set():
                    try:
                        resp = conn.get(s["url"], s["headers"], t[1], t[2])
                        f.seek(t[1])
                        while t[1] <= t[2]:
                            buf = resp.read(64 * 1024)
                            if not buf:
                                break
                            with self.lock:
                                buf = buf[: t[2] - t[1] + 1]  # конец мог забрать другой поток
                                t[1] += len(buf)
                                self.done += len(buf)
                            f.write(buf)
                        if t[1] <= t[2]:
                            raise IOError("соединение оборвалось")
                        if resp.length:  # прервали чтение раньше — соединение не переиспользовать
                            conn.close()
                    except Exception as e:
                        conn.close()
                        if self.cancel.is_set():
                            return
                        tries += 1
                        if tries > 8:
                            self.error = f"{s['path']}: {e}"
                            return
                        time.sleep(min(2 ** tries, 10) * 0.25)
                with self.lock:
                    self.active = [a for a in self.active if a is not t]
        finally:
            conn.close()
            for f in files.values():
                f.close()

    def run(self, on_progress=None):
        """Качает всё, on_progress(done, total, speed, n) зовётся раз в 0.5 с; возвращает время."""
        on_progress = on_progress or print_progress
        threads = [threading.Thread(target=self.worker, daemon=True) for _ in range(self.n)]
        t0 = time.time()
        for t in threads:
            t.start()
        last_t, last_b, speed = t0, 0, 0.0
        while any(t.is_alive() for t in threads):
            if self.cancel.wait(0.5):
                self.abort()
                for t in threads:
                    t.join(TIMEOUT + 1)
                raise Cancelled("Загрузка отменена")
            now = time.time()
            inst = (self.done - last_b) / max(now - last_t, 1e-6)
            speed = inst if speed == 0 else speed * 0.7 + inst * 0.3
            last_t, last_b = now, self.done
            on_progress(self.done, self.total, speed, self.n)
        if self.cancel.is_set():
            raise Cancelled("Загрузка отменена")
        if self.error:
            raise Error(f"Ошибка загрузки: {self.error}")
        return time.time() - t0

    def abort(self):
        """Рвёт сокеты, чтобы потоки не ждали таймаута на чтении."""
        with self.lock:
            for c in self.conns:
                sock = c.c.sock if c.c else None
                try:
                    if sock:
                        sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass


def print_progress(done, total, speed, n):
    eta = (total - done) / speed if speed > 0 else 0
    sys.stdout.write(
        f"\r  {done / 2**20:7.1f} / {total / 2**20:.1f} MiB "
        f"{100 * done / total:5.1f}%  {speed / 2**20:6.2f} MiB/s  "
        f"ETA {int(eta) // 60:02d}:{int(eta) % 60:02d}  [{n} соед.]  "
    )
    sys.stdout.flush()


def safe_name(s):
    bad = '<>:"/\\|?*'
    return "".join("_" if c in bad or ord(c) < 32 else c for c in s).strip(" .")[:150]


def download(url, output=DEFAULT_OUT, quality=None, audio=False, h264=False, connections=32,
             log=print, on_progress=None, on_title=None, cancel=None):
    """Скачивает один ролик, возвращает путь к готовому файлу."""
    ytdlp, ffmpeg = find_tool("yt-dlp", "YTDLP"), find_tool("ffmpeg", "FFMPEG")
    log(f"> {url}\n  получаю ссылки...")
    info, fmts = extract(ytdlp, url, format_selector(quality, audio, h264), cancel)
    if on_title:
        on_title(info.get("title") or info["id"])
    title = safe_name(info.get("title") or info["id"])
    os.makedirs(output, exist_ok=True)
    uniq = os.urandom(3).hex()

    streams = []
    for i, f in enumerate(fmts):
        headers = dict(f.get("http_headers") or {})
        if f.get("protocol", "https") not in ("https", "http"):
            raise Error(f"Формат {f['format_id']} ({f.get('protocol')}) не поддерживается, выбери другое качество")
        size = f.get("filesize") or content_length(f["url"], headers)
        streams.append({
            "url": f["url"], "headers": headers, "size": size, "ext": f["ext"],
            # уникальный суффикс: два одновременных скачивания одного ролика не пишут в одни файлы
            "path": os.path.join(output, f"{title}.{uniq}.part{i}.{f['ext']}"),
        })
        desc = f.get("resolution") if f.get("vcodec") != "none" else f"audio {f.get('abr') or ''}k"
        log(f"  [{f['format_id']}] {desc} {f.get('vcodec', '')}/{f.get('acodec', '')} "
            f"{size / 2**20:.1f} MiB")

    total = sum(s["size"] for s in streams)
    try:
        el = Downloader(streams, connections, cancel).run(on_progress)
    except Cancelled:
        for s in streams:
            try:
                os.remove(s["path"])
            except OSError:
                pass
        raise
    log(f"\r  {total / 2**20:.1f} MiB за {el:.1f} с — в среднем "
        f"{total / 2**20 / el:.2f} MiB/s{' ' * 30}")

    if len(streams) == 1:
        out = os.path.join(output, f"{title}.{streams[0]['ext']}")
        os.replace(streams[0]["path"], out)
    else:
        exts = {s["ext"] for s in streams}
        container = "mp4" if exts <= {"mp4", "m4a"} else "mkv"
        out = os.path.join(output, f"{title}.{container}")
        cmd = [ffmpeg, "-y", "-loglevel", "error"]
        for s in streams:
            cmd += ["-i", s["path"]]
        cmd += ["-map", "0", "-map", "1", "-c", "copy", out]
        if subprocess.run(cmd, creationflags=NO_WINDOW).returncode != 0:
            raise Error("ffmpeg не смог склеить потоки, части оставлены в папке")
        for s in streams:
            os.remove(s["path"])
    log(f"  готово: {out}")
    return out


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    p = argparse.ArgumentParser(description="Быстрое скачивание с YouTube в много соединений")
    p.add_argument("urls", nargs="+")
    p.add_argument("-q", "--quality", type=int, help="макс. высота кадра: 720, 1080, 2160...")
    p.add_argument("-c", "--connections", type=int, default=32, help="число соединений (32)")
    p.add_argument("-o", "--output", default=DEFAULT_OUT, help=f"папка для сохранения ({DEFAULT_OUT})")
    p.add_argument("--audio", action="store_true", help="только звук (m4a)")
    p.add_argument("--h264", action="store_true", help="только H.264 + AAC (mp4 для любых плееров)")
    args = p.parse_args()
    for url in args.urls:
        try:
            download(url, args.output, args.quality, args.audio, args.h264, args.connections)
        except Error as e:
            sys.exit(str(e))


if __name__ == "__main__":
    main()
