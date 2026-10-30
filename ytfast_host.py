"""Мост между расширением браузера и фоновым процессом (Native Messaging).

Браузер запускает этот скрипт на каждое нажатие кнопки «Скачать» и передаёт ссылку
в stdin: 4 байта длины + JSON. Скрипт отправляет ссылку в канал фонового процесса,
отвечает в stdout тем же форматом и завершается. Запрос {"type": "folders"} вместо
ссылки возвращает сохранённые папки. Запустить скрипт может только наше расширение:
его ID указан в манифесте хоста (allowed_origins).
"""
import json
import math
import os
import struct
import subprocess
import sys
import time
from urllib.parse import urlparse

PIPE = r"\\.\pipe\ytfast-" + os.environ.get("USERNAME", "user")
HOSTS = {"www.youtube.com", "youtube.com", "m.youtube.com", "music.youtube.com"}
SETTINGS = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "ytfast", "settings.json")
DEFAULT_OUT = os.path.join(os.path.expanduser("~"), "Downloads", "YouTube")


def valid_link(url):
    """Только ролики YouTube: сюда не должно попасть ничего другого, даже от своего расширения."""
    if not isinstance(url, str):
        return False
    u = urlparse(url)
    return u.scheme == "https" and u.hostname in HOSTS and (u.path == "/watch" or u.path.startswith("/shorts/"))


def saved_folders():
    """Те же значения по умолчанию, что в load_settings окна."""
    try:
        with open(SETTINGS, encoding="utf-8") as f:
            s = json.load(f)
    except (OSError, ValueError):
        s = {}
    folders = s.get("folders", [{"path": DEFAULT_OUT, "color": None}])
    return folders, s.get("folder", folders[0]["path"] if folders else DEFAULT_OUT)


def valid_time(t):
    return t is None or (isinstance(t, (int, float)) and not isinstance(t, bool) and math.isfinite(t) and t >= 0)


def same_path(a, b):
    return os.path.normcase(os.path.normpath(a)) == os.path.normcase(os.path.normpath(b))


def send_to_daemon(msg):
    for _ in range(50):
        try:
            with open(PIPE, "r+b", buffering=0) as pipe:
                pipe.write(msg)
            return True
        except FileNotFoundError:
            return False  # фонового процесса нет
        except OSError:
            time.sleep(0.01)  # канал занят другим клиентом
    return False


def start_daemon():
    folder = os.path.dirname(os.path.abspath(__file__))
    pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    cmd = [pythonw, os.path.join(folder, "ytfast_gui.pyw"), "--background"]
    detached = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    try:  # браузер может держать своих потомков в job-объекте: фоновый процесс должен его пережить
        subprocess.Popen(cmd, cwd=folder, creationflags=detached | 0x01000000)  # CREATE_BREAKAWAY_FROM_JOB
    except OSError:
        subprocess.Popen(cmd, cwd=folder, creationflags=detached)


def deliver(msg):
    if send_to_daemon(msg):
        return True
    start_daemon()
    deadline = time.time() + 10
    while time.time() < deadline:
        time.sleep(0.1)
        if send_to_daemon(msg):
            return True
    return False


def handle(request):
    if not isinstance(request, dict):
        return {"ok": False, "error": "Непонятный запрос"}
    if request.get("type") == "folders":
        folders, current = saved_folders()
        return {"ok": True, "folders": folders, "current": current}
    url, folder = request.get("url"), request.get("folder")
    if not valid_link(url):
        return {"ok": False, "error": "Это не ссылка на ролик YouTube"}
    if folder is not None and not (isinstance(folder, str) and any(same_path(f["path"], folder) for f in saved_folders()[0])):
        return {"ok": False, "error": "Этой папки больше нет в списке"}
    start, end = request.get("start"), request.get("end")
    if not valid_time(start) or not valid_time(end) or (end is not None and end <= (start or 0)):
        return {"ok": False, "error": "Неверное время фрагмента"}
    section = [start or 0, end] if start is not None or end is not None else None
    if not deliver(b"download " + json.dumps({"url": url, "folder": folder, "section": section}).encode("utf-8")):
        return {"ok": False, "error": "Фоновый процесс YT Fast не запустился"}
    return {"ok": True}


def main():
    header = sys.stdin.buffer.read(4)
    if len(header) != 4:
        return
    body = sys.stdin.buffer.read(struct.unpack("<I", header)[0])
    try:
        reply = handle(json.loads(body))
    except ValueError:
        reply = {"ok": False, "error": "Непонятный запрос"}
    data = json.dumps(reply).encode("utf-8")
    sys.stdout.buffer.write(struct.pack("<I", len(data)) + data)
    sys.stdout.buffer.flush()


if __name__ == "__main__":
    main()
