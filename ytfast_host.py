"""Мост между расширением браузера и фоновым процессом (Native Messaging).

Браузер запускает этот скрипт на каждое нажатие кнопки «Скачать» и передаёт ссылку
в stdin: 4 байта длины + JSON. Скрипт отправляет ссылку в канал фонового процесса,
отвечает в stdout тем же форматом и завершается. Запустить его может только наше
расширение: его ID указан в манифесте хоста (allowed_origins).
"""
import json
import os
import struct
import subprocess
import sys
import time
from urllib.parse import urlparse

PIPE = r"\\.\pipe\ytfast-" + os.environ.get("USERNAME", "user")
HOSTS = {"www.youtube.com", "youtube.com", "m.youtube.com", "music.youtube.com"}


def valid_link(url):
    """Только ролики YouTube: сюда не должно попасть ничего другого, даже от своего расширения."""
    if not isinstance(url, str):
        return False
    u = urlparse(url)
    return u.scheme == "https" and u.hostname in HOSTS and (u.path == "/watch" or u.path.startswith("/shorts/"))


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
    url = request.get("url") if isinstance(request, dict) else None
    if not valid_link(url):
        return {"ok": False, "error": "Это не ссылка на ролик YouTube"}
    if not deliver(b"download " + url.encode("utf-8")):
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
