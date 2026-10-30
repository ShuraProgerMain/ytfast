"""ytfast — быстрое скачивание с YouTube в много соединений.

yt-dlp используется только для получения прямых ссылок на потоки,
сами потоки качаются параллельно кусками (обходит лимит скорости на одно
соединение), затем склеиваются ffmpeg без перекодирования.

    python ytfast.py URL [URL ...] [-q 1080] [-c 32] [-o папка] [--audio] [--h264] [--from 1:20] [--to 1:45]
"""
import argparse
import collections
import functools
import http.client
import http.server
import json
import os
import queue
import re
import shutil
import socket
import socketserver
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit

CHUNK = 1 << 20  # 1 MiB: при ~100 КБ/с на соединение кусок качается ~10 с
MIN_STEAL = 128 * 1024  # меньше этого остаток у другого потока не отбираем
TIMEOUT = 8  # секунд без данных — соединение считаем зависшим
DEFAULT_OUT = os.path.join(os.path.expanduser("~"), "Downloads", "YouTube")
TOOLS_DIR = os.path.join(os.environ.get("LOCALAPPDATA", ""), "YTFast", "bin")  # сюда кладёт install.ps1
APPDATA_BIN = os.path.join(os.environ.get("APPDATA", ""), "media-downloader", "bin")
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)  # без мигающей консоли из GUI


class Error(Exception):
    pass


class Cancelled(Error):
    pass


def find_tool(name, env):
    """Переменная окружения → папка установщика → PATH → media-downloader."""
    own = os.path.join(TOOLS_DIR, name + ".exe")
    path = os.environ.get(env) or (own if os.path.isfile(own) else None) or shutil.which(name)
    if not path:
        cand = os.path.join(APPDATA_BIN, name + ".exe")
        path = cand if os.path.isfile(cand) else None
    if not path:
        raise Error(f"Не найден {name}. Запусти install.cmd или укажи путь в переменной {env}.")
    return path


def format_selector(quality=None, audio=False, h264=False):
    if audio:
        return "ba[ext=m4a]/ba"
    h = f"[height<={quality}]" if quality else ""
    if h264:
        return f"bv*{h}[vcodec^=avc1]+ba[ext=m4a]/b{h}[ext=mp4]/b{h}"
    # звук — AAC (m4a): Opus, который YouTube отдаёт «лучшим», не понимают DaVinci Resolve,
    # Premiere и часть плееров — видео открывается без звука
    return f"bv*{h}+ba[ext=m4a]/bv*{h}+ba/b{h}"


@functools.lru_cache(maxsize=1)
def node_ok():
    """Node.js 20+ в PATH: yt-dlp решает им JS-задачу YouTube так же быстро, как deno."""
    node = shutil.which("node")
    if not node:
        return False
    try:
        out = subprocess.run([node, "--version"], capture_output=True, text=True, timeout=5,
                             creationflags=NO_WINDOW).stdout
        return int(out.strip().lstrip("v").split(".")[0]) >= 20
    except (OSError, ValueError, subprocess.SubprocessError):
        return False


def extract(ytdlp, url, fmt, cancel=None):
    # skip=hls: манифест HLS не нужен (качаем только DASH по https), а стоит ~1.3 с на каждый ролик
    cmd = [ytdlp, "-J", "--no-playlist", "--no-warnings", "--extractor-args", "youtube:skip=hls", "-f", fmt, url]
    # JS-движок для расшифровки ссылок YouTube: Node, если есть, иначе deno от установщика
    # (deno рядом с yt-dlp или в PATH yt-dlp находит сам)
    deno = os.path.join(TOOLS_DIR, "deno.exe")
    if node_ok():
        cmd[1:1] = ["--js-runtimes", "node"]
    elif os.path.isfile(deno):
        cmd[1:1] = ["--js-runtimes", f"deno:{deno}"]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                         encoding="utf-8", errors="replace", creationflags=NO_WINDOW)
    while True:
        try:
            out, err = p.communicate(timeout=0.2)
            break
        except subprocess.TimeoutExpired:
            if cancel and cancel.is_set():
                # yt-dlp.exe — распаковщик, настоящая работа идёт в его дочернем процессе (и в node):
                # убиваем всё дерево, иначе они доработают сами, держа наши каналы открытыми
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)], capture_output=True,
                               creationflags=NO_WINDOW)
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
            self.final_url = url
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


def fetch(url, path, connections=16, on_progress=None):
    """Скачать обычный файл в много соединений — так установщик тянет yt-dlp, ffmpeg и deno."""
    c = Conn()
    try:
        resp = c.get(url, {}, 0, 0)
        resp.read()
        size = int(resp.getheader("Content-Range", "").rsplit("/", 1)[1])
        url = c.final_url  # куски сразу идут на CDN, без редиректа на каждый
    finally:
        c.close()
    part = path + ".part"
    el = Downloader([{"url": url, "headers": {}, "size": size, "path": part}], connections).run(on_progress)
    os.replace(part, path)
    return size, el


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
             log=print, on_progress=None, on_title=None, cancel=None, section=None):
    """Скачивает один ролик или его часть (section — секунды от и до), возвращает путь к готовому файлу."""
    ytdlp, ffmpeg = find_tool("yt-dlp", "YTDLP"), find_tool("ffmpeg", "FFMPEG")
    log(f"> {url}\n  получаю ссылки...")
    info, fmts = extract(ytdlp, url, format_selector(quality, audio, h264), cancel)
    if on_title:
        on_title(info.get("title") or info["id"])
    if section:
        return download_section(info, fmts, ffmpeg, section, output, connections, log, on_progress, cancel)
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
        out = reserve_path(output, title, streams[0]["ext"])
        os.replace(streams[0]["path"], out)
    else:
        exts = {s["ext"] for s in streams}
        out = reserve_path(output, title, "mp4" if exts <= {"mp4", "m4a"} else "mkv")
        cmd = [ffmpeg, "-y", "-loglevel", "error"]
        for s in streams:
            cmd += ["-i", s["path"]]
        cmd += ["-map", "0", "-map", "1", "-c", "copy", out]
        if subprocess.run(cmd, creationflags=NO_WINDOW).returncode != 0:
            remove_quietly(out)
            raise Error("ffmpeg не смог склеить потоки, части оставлены в папке")
        for s in streams:
            os.remove(s["path"])
    log(f"  готово: {out}")
    return out


def video_duration(url, cancel=None):
    """Длительность ролика в секундах — для таймлайна."""
    info, _ = extract(find_tool("yt-dlp", "YTDLP"), url, format_selector(), cancel)
    return info.get("duration") or 0


def reserve_path(folder, name, ext):
    """Свободное имя файла: «имя.mkv», «имя (2).mkv»... Файл сразу создаётся пустым, чтобы
    параллельная загрузка не выбрала то же имя; прежние файлы никогда не перезаписываются."""
    n = 1
    while True:
        path = os.path.join(folder, f"{name}.{ext}" if n == 1 else f"{name} ({n}).{ext}")
        try:
            os.close(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
            return path
        except FileExistsError:
            n += 1


def remove_quietly(*paths):
    for path in paths:
        try:
            os.remove(path)
        except OSError:
            pass


def parse_time(text):
    """«80», «1:20», «1:02:03.5» → секунды. ValueError, если это не время."""
    parts = text.strip().replace(",", ".").split(":")
    if not 1 <= len(parts) <= 3 or not all(re.fullmatch(r"\d+(\.\d+)?", x) for x in parts):
        raise ValueError(f"Непонятное время: {text.strip() or 'пусто'}. Пример: 1:20")
    seconds = 0.0
    for x in parts:
        seconds = seconds * 60 + float(x)
    return seconds


def parse_section(start, end):
    """Тексты «от» и «до» → (секунды, секунды или None). None — полей нет, качаем целиком."""
    start, end = start.strip(), end.strip()
    if not start and not end:
        return None
    a = parse_time(start) if start else 0.0
    b = parse_time(end) if end else None
    if b is not None and b <= a:
        raise ValueError("Конец фрагмента должен быть позже начала")
    return a, b


def format_time(seconds, sep=":"):
    s = int(seconds)
    h, m, s = s // 3600, s // 60 % 60, s % 60
    return (f"{h}{sep}{m:02d}" if h else f"{m}") + f"{sep}{s:02d}"


class RangeProxy:
    """Локальный мост для ffmpeg: он просит диапазоны байт и читает их по порядку, а мост качает
    блоки наперёд в много соединений. Так фрагмент обходит тот же лимит на соединение, что и весь ролик."""

    def __init__(self, streams, connections):
        self.streams, self.connections = streams, connections
        self.conns = queue.LifoQueue()  # keep-alive: новый запрос ffmpeg не платит за рукопожатие TLS
        self.bytes = 0
        self.token = os.urandom(8).hex()  # в мосту подписанные ссылки: чужой процесс на этой машине их не получит
        proxy = self

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def do_GET(self):
                m = re.fullmatch(rf"/{proxy.token}/(\d+)", self.path)
                if not m or int(m.group(1)) >= len(proxy.streams):
                    self.send_error(404)
                    return
                s = proxy.streams[int(m.group(1))]
                r = re.match(r"bytes=(\d+)-(\d*)", self.headers.get("Range", "bytes=0-"))
                a, b = int(r.group(1)), min(int(r.group(2) or s["size"] - 1), s["size"] - 1)
                self.send_response(206)
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Range", f"bytes {a}-{b}/{s['size']}")
                self.send_header("Content-Length", str(b - a + 1))
                self.end_headers()
                try:
                    proxy.serve(s, a, b, self.wfile)
                except OSError:  # ffmpeg перешёл к другому месту файла и закрыл соединение
                    self.close_connection = True

        class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
            daemon_threads = True

        self.server = Server(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def url(self, i):
        return f"http://127.0.0.1:{self.server.server_address[1]}/{self.token}/{i}"

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        while not self.conns.empty():
            self.conns.get().close()

    def block(self, s, a, b):
        for attempt in range(4):
            try:
                c = self.conns.get_nowait()
            except queue.Empty:
                c = Conn()
            try:
                data = c.get(s["url"], s["headers"], a, b).read()
                if len(data) == b - a + 1:
                    self.conns.put(c)
                    return data
            except (OSError, http.client.HTTPException):
                pass
            c.close()
        raise OSError("не удалось скачать блок")

    def serve(self, s, a, b, out):
        offsets = list(range(a, b + 1, CHUNK))
        ready, cond = {}, threading.Condition()
        # окно растёт с каждым отданным блоком: ffmpeg часто читает пару мегабайт и прыгает дальше
        st = {"next": 0, "written": 0, "window": 2, "stop": False, "failed": False}

        def worker():
            while True:
                with cond:
                    while not st["stop"] and st["next"] < len(offsets) and st["next"] - st["written"] >= st["window"]:
                        cond.wait()
                    if st["stop"] or st["next"] >= len(offsets):
                        return
                    i = st["next"]
                    st["next"] += 1
                try:
                    data = self.block(s, offsets[i], min(offsets[i] + CHUNK, b + 1) - 1)
                except OSError:
                    with cond:
                        st["failed"] = True
                        cond.notify_all()
                    return
                with cond:
                    ready[i] = data
                    cond.notify_all()

        for _ in range(min(self.connections, len(offsets))):
            threading.Thread(target=worker, daemon=True).start()
        try:
            for i in range(len(offsets)):
                with cond:
                    while i not in ready and not st["failed"]:
                        cond.wait()
                    if i not in ready:
                        raise OSError("не удалось скачать блок")
                    data = ready.pop(i)
                out.write(data)
                self.bytes += len(data)
                with cond:
                    st["written"] = i + 1
                    st["window"] = min(st["window"] * 2, self.connections)
                    cond.notify_all()
        finally:
            with cond:
                st["stop"] = True
                cond.notify_all()


def run_ffmpeg(cmd, cancel, on_time=None):
    """Запуск ffmpeg с отменой; on_time(секунды) — сколько уже записано."""
    p = subprocess.Popen(cmd[:1] + ["-nostats", "-progress", "pipe:1"] + cmd[1:], stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
                         creationflags=NO_WINDOW)
    errors = []
    threading.Thread(target=lambda: errors.extend(p.stderr), daemon=True).start()

    def read_progress():
        for line in p.stdout:
            if on_time and line.startswith("out_time_us=") and line[12:].strip().isdigit():
                on_time(int(line[12:]) / 1e6)

    reader = threading.Thread(target=read_progress, daemon=True)
    reader.start()
    while p.poll() is None:
        if cancel and cancel.is_set():
            p.kill()
            p.wait()
            raise Cancelled("Загрузка отменена")
        time.sleep(0.05)
    reader.join(1)
    if p.returncode != 0:
        raise Error("ffmpeg не смог вырезать фрагмент:\n" + "".join(errors).strip()[-300:])


def first_video_time(ffmpeg, path):
    """Время первого кадра в файле, сохранённом с -copyts: на самом деле фрагмент начинается с него."""
    out = subprocess.run([ffmpeg, "-v", "error", "-i", path, "-copyts", "-map", "0:v:0", "-c", "copy", "-frames:v", "1",
                          "-f", "framecrc", "-"], capture_output=True, text=True, encoding="utf-8", errors="replace",
                         creationflags=NO_WINDOW).stdout
    tb = re.search(r"#tb 0: (\d+)/(\d+)", out)
    row = next((x for x in out.splitlines() if x and not x.startswith("#")), None)
    if not tb or not row:
        raise Error("ffmpeg не смог определить начало фрагмента")
    return int(row.split(",")[2]) * int(tb.group(1)) / int(tb.group(2))


def download_section(info, fmts, ffmpeg, section, output, connections, log, on_progress, cancel):
    """Только часть ролика, без перекодирования. Резать без перекодирования можно лишь по ключевым
    кадрам, поэтому начало может оказаться на пару секунд раньше запрошенного, но не позже."""
    duration = info.get("duration") or 0
    start, end = section
    end = min(end, duration) if end and duration else (end or duration)
    if not end or start >= end:
        raise Error(f"В ролике нет такого фрагмента: длительность {format_time(duration)}")
    streams = []
    for f in fmts:
        if f.get("protocol", "https") not in ("https", "http"):
            raise Error(f"Формат {f['format_id']} ({f.get('protocol')}) не поддерживается, выбери другое качество")
        headers = dict(f.get("http_headers") or {})
        streams.append({"url": f["url"], "headers": headers, "ext": f["ext"], "video": f.get("vcodec") != "none",
                        "size": f.get("filesize") or content_length(f["url"], headers)})
    streams.sort(key=lambda s: not s["video"])  # видео первым: по его первому кадру выравниваем звук
    name = safe_name(f"{info.get('title') or info['id']} [{format_time(start, '.')}-{format_time(end, '.')}]")
    os.makedirs(output, exist_ok=True)
    uniq = os.urandom(3).hex()
    length = end - start
    share = length / duration if duration else 1
    total = sum(s["size"] for s in streams) * share
    parts = [os.path.join(output, f"{name}.{uniq}.part{i}.mkv") for i in range(len(streams))]
    if len(streams) == 1:
        container = streams[0]["ext"]
    else:
        container = "mp4" if {s["ext"] for s in streams} <= {"mp4", "m4a"} else "mkv"
    out = reserve_path(output, name, container)
    proxy = RangeProxy(streams, connections)
    t0 = time.time()
    finished = 0  # байт в уже готовых потоках

    def progress(i):
        def on_time(seconds):
            if on_progress:
                done = finished + min(seconds / length, 1) * streams[i]["size"] * share
                on_progress(min(done, total), total, proxy.bytes / max(time.time() - t0, 0.1), connections)
        return on_time

    try:
        begin = start
        for i, s in enumerate(streams):
            target = out if len(streams) == 1 else parts[i]
            copyts = ["-copyts"] if s["video"] and len(streams) > 1 else []
            run_ffmpeg([ffmpeg, "-y", "-loglevel", "error", *copyts, "-ss", f"{begin:.3f}", "-to", f"{end:.3f}",
                        "-i", proxy.url(i), "-c", "copy", target], cancel, progress(i))
            finished += s["size"] * share
            if copyts:
                begin = first_video_time(ffmpeg, target)  # звук — с того же момента, иначе начало будет без звука
        if len(streams) > 1:
            run_ffmpeg([ffmpeg, "-y", "-loglevel", "error", "-i", parts[0], "-i", parts[1], "-map", "0", "-map", "1",
                        "-c", "copy", out], cancel)
    except Error:
        remove_quietly(out, *parts)
        raise
    finally:
        proxy.close()
    remove_quietly(*parts)
    size = os.path.getsize(out)
    el = time.time() - t0
    log(f"\r  фрагмент {format_time(begin)}–{format_time(end)}: {size / 2**20:.1f} MiB за {el:.1f} с{' ' * 30}")
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
    p.add_argument("--from", dest="start", default="", metavar="ВРЕМЯ", help="начало фрагмента: 1:20")
    p.add_argument("--to", dest="end", default="", metavar="ВРЕМЯ", help="конец фрагмента: 1:45")
    p.add_argument("--fetch", metavar="ФАЙЛ", help="скачать ссылку как обычный файл (нужно установщику)")
    args = p.parse_args()
    if args.fetch:
        try:
            size, el = fetch(args.urls[0], args.fetch, args.connections)
        except (Error, OSError, ValueError) as e:
            sys.exit(f"Не удалось скачать {args.urls[0]}: {e}")
        print(f"\r  {size / 2**20:.1f} MiB за {el:.1f} с{' ' * 50}")
        return
    try:
        section = parse_section(args.start, args.end)
    except ValueError as e:
        sys.exit(str(e))
    for url in args.urls:
        try:
            download(url, args.output, args.quality, args.audio, args.h264, args.connections, section=section,
                     on_progress=print_progress if section else None)
        except Error as e:
            sys.exit(str(e))


if __name__ == "__main__":
    main()
