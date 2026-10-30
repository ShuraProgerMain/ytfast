"""Строка в стиле Spotlight из macOS Big Sur: вставил ссылку, выбрал качество, Enter — качается.

Enter — скачать, ↓ — выбор качества, Esc или клик мимо окна — закрыть.
Во время загрузки окно вместо закрытия сворачивается в полоску слева внизу;
при наведении на неё — «развернуть» и «остановить».
"""
import os
import sys
import time

PIPE = r"\\.\pipe\ytfast-" + os.environ.get("USERNAME", "user")


def forward_to_daemon(msg):
    """Если фоновый процесс уже работает — передать ему команду и выйти.
    Стоит до тяжёлых импортов: повторный запуск должен занимать миллисекунды."""
    for _ in range(50):
        try:
            with open(PIPE, "r+b", buffering=0) as pipe:
                if msg == b"show":
                    import ctypes
                    ctypes.windll.user32.AllowSetForegroundWindow(-1)  # пусть фоновый процесс выйдет вперёд
                pipe.write(msg)
            return True
        except FileNotFoundError:
            return False  # фонового процесса нет
        except OSError:
            time.sleep(0.01)  # канал занят другим клиентом
    return False


if __name__ == "__main__" and forward_to_daemon(b"ping" if "--background" in sys.argv else b"show"):
    sys.exit(0)

# дальше — только для самого фонового процесса
import ctypes  # noqa: E402
import ctypes.wintypes as wt  # noqa: E402
import json  # noqa: E402
import queue  # noqa: E402
import subprocess  # noqa: E402
import threading  # noqa: E402
import tkinter as tk  # noqa: E402
import tkinter.font as tkfont  # noqa: E402
import traceback  # noqa: E402
from tkinter import colorchooser, filedialog  # noqa: E402
from multiprocessing.connection import Listener  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import macui as ui  # noqa: E402
import ytfast  # noqa: E402

QUALITIES = [("Лучшее", None), ("2160p", 2160), ("1440p", 1440), ("1080p", 1080),
             ("720p", 720), ("480p", 480), ("Только звук", "audio")]
SETTINGS = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "ytfast", "settings.json")
LOG = os.path.join(os.path.dirname(SETTINGS), "gui.log")

# размеры в дизайн-пикселях (аналог pt в macOS)
W, ROW, RADIUS = 680, 56, 12  # строка Spotlight
STRIP_W, STRIP_H = 330, 60  # полоска загрузки
MENU_ITEM, MENU_PAD = 22, 5
FOLDER_ROW, FOLDERS_W = 26, 340  # список папок
DLG_W, DLG_H = 440, 190  # окно «добавить папку»
SECT_H, TIME_W = 50, 90  # строка «от — до» и её поля
TAG_COLORS = [None, "red", "orange", "yellow", "green", "blue", "purple", "gray"]  # как метки в Finder


def load_settings():
    """quality — индекс в QUALITIES; folders — [{path, color}]; folder — последняя папка сохранения."""
    try:
        with open(SETTINGS, encoding="utf-8") as f:
            s = json.load(f)
    except (OSError, ValueError):
        s = {}
    s.setdefault("quality", 3)  # 1080p
    s.setdefault("folders", [{"path": ytfast.DEFAULT_OUT, "color": None}])
    s.setdefault("folder", s["folders"][0]["path"] if s["folders"] else ytfast.DEFAULT_OUT)
    return s


def save_settings(**changes):
    s = load_settings()
    s.update(changes)
    try:
        os.makedirs(os.path.dirname(SETTINGS), exist_ok=True)
        with open(SETTINGS, "w", encoding="utf-8") as f:
            json.dump(s, f, ensure_ascii=False, indent=1)
    except OSError:
        pass


def same_path(a, b):
    return os.path.normcase(os.path.normpath(a)) == os.path.normcase(os.path.normpath(b))


def folder_color(path):
    return next((f.get("color") for f in load_settings()["folders"] if same_path(f["path"], path)), None)


def open_folder(path=None, folder=ytfast.DEFAULT_OUT):
    """Показать файл в проводнике, а без файла — открыть папку (если её ещё нет — создать)."""
    if path and os.path.exists(path):
        subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
    else:
        os.makedirs(folder, exist_ok=True)
        os.startfile(folder)


def elide_path(path, font, width):
    """D:\\…\\Видео\\Шортсы — диск и хвост пути, середина сокращается."""
    if font.measure(path) <= width:
        return path
    parts = path.split("\\")
    for i in range(1, len(parts)):
        cand = parts[0] + "\\…\\" + "\\".join(parts[i:])
        if font.measure(cand) <= width:
            return cand
    return elide("…\\" + parts[-1], font, width)


def elide(text, font, width):
    if font.measure(text) <= width:
        return text
    while text and font.measure(text + "…") > width:
        text = text[:-1]
    return text.rstrip() + "…"


class Resources:
    """Всё дорогое — тема, шрифты, иконки, кадры крутилки — готовится один раз на процесс."""

    def __init__(self, root, dark):
        self.S = S = root.winfo_fpixels("1i") / 96
        self.T = T = ui.Theme(dark)
        fams = set(tkfont.families())
        fam = lambda *n: next((x for x in n if x in fams), "Segoe UI")  # noqa: E731
        px = lambda n: int(round(n * S))  # noqa: E731
        self.f_field = tkfont.Font(family=fam("Inter Light", "Inter", "Segoe UI Light"), size=-px(22))
        self.f_text = tkfont.Font(family=fam("Inter", "Segoe UI"), size=-px(13))
        self.f_semi = tkfont.Font(family=fam("Inter SemiBold", "Segoe UI Semibold"), size=-px(13))
        self.f_small = tkfont.Font(family=fam("Inter", "Segoe UI"), size=-px(11))
        sec, acc = ui.rgba(T.secondary), ui.rgba(T.accent)
        self.img = {
            "search": ui.sym_magnifier(sec, S),
            "folder": ui.sym_folder(sec, S),
            "stop": ui.sym_xmark_circle(ui.rgba(T.secondary, 0.85), S),
            "done": ui.sym_check_circle(ui.rgba(T.green), S),
            "error": ui.sym_exclamation_circle(ui.rgba(T.red), S),
            "done_big": ui.sym_check_circle(ui.rgba(T.green), S, 30),
            "error_big": ui.sym_exclamation_circle(ui.rgba(T.red), S, 30),
            "expand": ui.sym_expand(ui.rgba(T.label), S),
            "xmark": ui.sym_xmark(ui.rgba(T.label), S),
            "hover30": ui.plate(30, 30, 7, T.hover, S),
            "clear30": ui.plate(30, 30, 7, (0, 0, 0, 0), S),
            "circle26": ui.plate(26, 26, 13, T.circle, S),
            "circle26h": ui.plate(26, 26, 13, tuple(list(T.circle[:3]) + [T.circle[3] * 2]), S),
            "check": ui.sym_check(ui.rgba(T.label), S),
            "check_on": ui.sym_check((255, 255, 255, 1), S),
            "chev": ui.sym_chevron_down(ui.rgba(T.secondary, 0.8), S),
            "sep": ui.solid(px(W), max(1, px(1)), T.separator),
            "track": ui.plate(W - 36, 6, 3, T.track, S),
            "seg": ui.plate(120, 6, 3, acc, S),
            "dots": ui.sym_dots(sec, S),
            "cut": ui.sym_scissors(sec, S),
            "cut_on": ui.sym_scissors(acc, S),
            "hover18": ui.plate(18, 30, 6, T.hover, S),
            "clear18": ui.plate(18, 30, 6, (0, 0, 0, 0), S),
            "plus": ui.sym_plus(ui.rgba(T.label), S),
            "plus_on": ui.sym_plus((255, 255, 255, 1), S),
            "xmark_on": ui.sym_xmark((255, 255, 255, 1), S),
        }
        # крутилки (неизвестный прогресс): 12 кадров для двух размеров
        self.spinner = {sz: [ui.ring(sz, w, T.track, acc, S, start=i / 12) for i in range(12)]
                        for sz, w in ((22, 2.4), (30, 3))}
        self.sel_plates = {}  # подложки селектора по ширине текста
        self.cache = {}  # цветные папки, метки, кружки выбора цвета

    def color_hex(self, color):
        """Метка → #RRGGBB: имя из палитры Finder ("red") или свой цвет ("#3fa9f5"); None — без цвета."""
        if color and color.startswith("#"):
            return color
        return self.T.tags.get(color)

    def folder_icon(self, color):
        """Иконка папки в цвет метки; без цвета — обычная серая."""
        key = ("folder", color)
        if key not in self.cache:
            hexc = self.color_hex(color)
            self.cache[key] = ui.sym_folder(ui.rgba(hexc), self.S, fill=0.22) if hexc else self.img["folder"]
        return self.cache[key]

    def tag_dot(self, color):
        key = ("dot", color)
        if key not in self.cache:
            hexc = self.color_hex(color)
            self.cache[key] = ui.dot(ui.rgba(hexc) if hexc else None, self.S)
        return self.cache[key]

    def tag_swatch(self, color, selected):
        key = ("swatch", color, selected)
        if key not in self.cache:
            hexc = self.color_hex(color)
            self.cache[key] = ui.swatch(ui.rgba(hexc) if hexc else None, self.S, selected)
        return self.cache[key]

    def custom_swatch(self, color, selected):
        key = ("custom", color, selected)
        if key not in self.cache:
            self.cache[key] = ui.swatch_custom(ui.rgba(color) if color else None, self.S, selected)
        return self.cache[key]

    def sel_plate(self, w):
        if w not in self.sel_plates:
            self.sel_plates[w] = (ui.plate(w, 28, 7, self.T.hover, self.S),
                                  ui.plate(w, 28, 7, (0, 0, 0, 0), self.S))  # прозрачная, но ловит мышь
        return self.sel_plates[w]


class App:
    """Одно окно: строка Spotlight, которая при загрузке сворачивается в полоску."""

    def __init__(self, daemon, res, monitor, url=None, folder=None, section=None):
        self.daemon, self.res = daemon, res
        self.screen, self.work_area = monitor  # монитор, где нажали Ctrl+Alt+Y: окно и полоска живут на нём
        self.root = tk.Toplevel(daemon.root)
        self.S, self.T = res.S, res.T
        self.events = queue.Queue()
        self.threads = []
        self.mode = "input"  # input | busy | done | error
        self.view = "full"  # full | strip
        self.job = 0
        self.cancel = None
        self.result = None
        self.title = ""
        self.progress = None  # 0..1 или None, пока неизвестно
        self.status = ""
        self.had_focus = False
        self.menu = None
        self.menu_hl = 0
        self.strip_hover = None
        self.close_at = None
        self.closed = False
        self.spin = 0
        settings = load_settings()
        self.q = min(max(settings["quality"], 0), len(QUALITIES) - 1)
        self.folder = folder or settings["folder"]  # куда сохранять: по умолчанию — последняя папка сохранения
        self.folders_pop = None  # выпадающий список папок
        self.dialog = None  # окно «добавить папку»
        self.modal = False  # открыт системный диалог выбора папки
        self.section = section  # (от, до) в секундах от кнопки в браузере
        self.sect_open = False  # открыта строка «от — до»
        self.dots_on = False
        self.dots_armed = True
        self.dots_since = self.leave_since = None

        self.f_field, self.f_text, self.f_semi, self.f_small = res.f_field, res.f_text, res.f_semi, res.f_small
        self.img = dict(res.img)  # общие иконки + свои меняющиеся картинки (кольцо, полоса)
        self.spinner = res.spinner

        self.panel = ui.Panel(self.root, self.T, RADIUS, self.S)
        self.c = self.panel.canvas
        self.build_full()
        self.build_strip()

        root = self.root
        root.bind("<Return>", lambda e: self.on_enter())
        root.bind("<KP_Enter>", lambda e: self.on_enter())
        root.bind("<Escape>", lambda e: self.on_escape())
        root.bind("<Down>", lambda e: self.menu_move(1))
        root.bind("<Up>", lambda e: self.menu_move(-1))
        self.c.bind("<Button-1>", self.click_canvas, add="+")

        if url:  # кнопка в браузере: сразу качаем в полоске, фокус остаётся в браузере
            self.view = "strip"
            self.field.set(url)
            self.start()
        else:
            self.prefill()
            self.layout()
            ui.to_foreground(root)
            self.field.focus()
        root.after(60, self.poll)

    def px(self, n):
        return int(round(n * self.S))

    def quit(self):
        if self.closed:
            return
        self.closed = True
        self.close_menu(refocus=False)
        self.close_folders(refocus=False)
        self.close_dialog(refocus=False)
        self.panel.destroy()
        self.daemon.window_closed(self)

    # ---------- построение ----------
    def build_full(self):
        px, c, T = self.px, self.c, self.T
        cy = px(ROW / 2)
        self.lead = c.create_image(px(29), cy, image=self.img["search"], tags="full")

        # справа налево: «⋯» (видно при наведении на папку), папка/отмена, селектор качества
        self.dots_x = px(W - 8 - 18)
        bx = self.btn_x = self.dots_x - px(2 + 30)
        self.btn = c.create_image(bx, cy - px(15), anchor="nw", image=self.img["clear30"], tags=("full", "btn"))
        self.btn_icon = c.create_image(bx + px(15), cy, image=self.folder_img(), tags=("full", "btn"))
        c.tag_bind("btn", "<Enter>", lambda e: c.itemconfigure(self.btn, image=self.img["hover30"]))
        c.tag_bind("btn", "<Leave>", lambda e: c.itemconfigure(self.btn, image=self.img["clear30"]))
        c.tag_bind("btn", "<ButtonRelease-1>", lambda e: self.on_button())
        self.dots_bg = c.create_image(self.dots_x, cy - px(15), anchor="nw", image=self.img["clear18"],
                                      tags=("full", "dots"))
        c.create_image(self.dots_x + px(9), cy, image=self.img["dots"], tags=("full", "dots"))
        c.tag_bind("dots", "<ButtonRelease-1>", lambda e: self.toggle_folders())

        # ножницы: скачать только фрагмент
        cut_x = bx - px(2 + 30)
        self.cut_bg = c.create_image(cut_x, cy - px(15), anchor="nw", image=self.img["clear30"], tags=("full", "cut"))
        self.cut_icon = c.create_image(cut_x + px(15), cy, image=self.img["cut"], tags=("full", "cut"))
        c.tag_bind("cut", "<Enter>", lambda e: c.itemconfigure(self.cut_bg, image=self.img["hover30"]))
        c.tag_bind("cut", "<Leave>", lambda e: c.itemconfigure(self.cut_bg, image=self.img["clear30"]))
        c.tag_bind("cut", "<ButtonRelease-1>", lambda e: self.toggle_section())

        # селектор качества: просто текст и неяркая стрелка, подложка — только при наведении
        self.sel_r = cut_x - px(4)
        self.sel_hover = False
        self.pop = c.create_image(0, cy - px(14), anchor="nw", tags=("full", "pop"))
        self.pop_text = c.create_text(self.sel_r - px(22), cy, anchor="e", text=QUALITIES[self.q][0],
                                      font=self.f_text, fill=T.secondary, tags=("full", "pop"))
        c.create_image(self.sel_r - px(12), cy + px(1), image=self.img["chev"], tags=("full", "pop"))
        c.tag_bind("pop", "<Enter>", lambda e: self.hover_sel(True))
        c.tag_bind("pop", "<Leave>", lambda e: self.hover_sel(False))
        c.tag_bind("pop", "<ButtonPress-1>", lambda e: self.toggle_menu())
        self.sel_plate()

        fx = px(50)
        self.field_x, self.field_y, self.field_h = fx, cy - px(17), px(34)
        widest = max(self.f_text.measure(n) for n, _ in QUALITIES) + px(32)
        self.field_w = self.sel_r - widest - px(10) - fx
        self.field = ui.TextField(c, T, self.f_field, self.field_w, self.field_h, "Вставьте ссылку на видео")
        self.field_item = c.create_window(fx, self.field_y, anchor="nw", window=self.field.c, tags="full")

        self.build_section()

        # подробности: появляются под строкой во время загрузки, после неё и при ошибке
        x0 = px(18)
        self.sep = c.create_image(0, px(ROW), anchor="nw", tags=("full", "details"))
        self.d_title = c.create_text(x0, 0, anchor="nw", font=self.f_semi, fill=T.label, tags=("full", "details"))
        self.bar_track = c.create_image(x0, 0, anchor="nw", tags=("full", "details", "bar"))
        self.bar_fill = c.create_image(x0, 0, anchor="nw", tags=("full", "details", "bar"))
        self.d_cap = c.create_text(x0, 0, anchor="nw", font=self.f_small, fill=T.secondary,
                                   width=px(W - 36), tags=("full", "details"))
        self.bar_w = W - 36
        c.itemconfigure(self.sep, image=self.img["sep"])
        c.itemconfigure(self.bar_track, image=self.img["track"])

    def build_section(self):
        """Строка «от — до» под ссылкой."""
        px, c, T = self.px, self.c, self.T
        y = ROW + SECT_H / 2
        self.sect_sep = c.create_image(0, px(ROW), anchor="nw", image=self.img["sep"], tags=("full", "sect"))
        self.img["time_field"] = ui.raster(TIME_W, 30, [(ui.rrect(0, 0, TIME_W, 30, 6), T.input_edge),
                                                       (ui.rrect(1, 1, TIME_W - 1, 29, 5), ui.rgba(T.input))], self.S)
        self.sect_fields = []
        x = 18
        for label, hint in (("От", "начало"), ("До", "конец")):
            text = c.create_text(px(x), px(y), anchor="w", text=label, font=self.f_text, fill=T.secondary, tags=("full", "sect"))
            fx = x + 28
            c.create_image(px(fx), px(y - 15), anchor="nw", image=self.img["time_field"], tags=("full", "sect"))
            field = ui.TextField(c, T, self.f_text, px(TIME_W - 18), px(24), hint)
            field.set_bg(ui.solid(px(TIME_W - 18), px(24), ui.rgba(T.input)))
            item = c.create_window(-10000, -10000, anchor="nw", window=field.c)
            self.sect_fields.append((field, item, px(fx + 9), px(y - 12)))
            x = fx + TIME_W + 18
        c.create_text(px(x), px(y), anchor="w", text="Например, 1:20. Пусто — от начала или до конца",
                      font=self.f_small, fill=T.secondary, tags=("full", "sect"))
        self.sec_from, self.sec_to = self.sect_fields[0][0], self.sect_fields[1][0]
        for w in (self.field, self.sec_from, self.sec_to):
            w.c.bind("<Tab>", lambda e, w=w: self.next_field(w))

    def next_field(self, current):
        order = [self.field, self.sec_from, self.sec_to] if self.sect_open else [self.field]
        order[(order.index(current) + 1) % len(order)].focus()
        return "break"

    def place_section(self, show):
        self.c.itemconfigure("sect", state="normal" if show else "hidden")
        for field, item, x, y in self.sect_fields:  # окна-элементы не скрываются через state
            self.c.coords(item, x if show else -10000, y if show else -10000)

    def toggle_section(self):
        self.sect_open = not self.sect_open
        self.c.itemconfigure(self.cut_icon, image=self.img["cut_on" if self.sect_open else "cut"])
        self.layout()
        (self.sec_from if self.sect_open else self.field).focus()

    def folder_img(self):
        return self.res.folder_icon(folder_color(self.folder))

    def update_folder_icon(self):
        if self.mode != "busy":
            self.c.itemconfigure(self.btn_icon, image=self.folder_img())

    def sel_plate(self):
        """Подложка селектора по ширине текущего пункта."""
        w = round(self.f_text.measure(QUALITIES[self.q][0]) / self.S) + 32
        self.img["sel_on"], self.img["sel_off"] = self.res.sel_plate(w)
        self.c.coords(self.pop, self.sel_r - self.px(w), self.c.coords(self.pop)[1])
        self.hover_sel(self.sel_hover)

    def hover_sel(self, on):
        self.sel_hover = on
        self.c.itemconfigure(self.pop, image=self.img["sel_on" if on or self.menu else "sel_off"])

    def build_strip(self):
        px, c, T = self.px, self.c, self.T
        cy = px(STRIP_H / 2)
        self.s_icon = c.create_image(px(29), cy, tags="strip")
        self.s_title = c.create_text(px(56), cy - px(9), anchor="w", font=self.f_semi, fill=T.label, tags="strip")
        self.s_cap = c.create_text(px(56), cy + px(10), anchor="w", font=self.f_small, fill=T.secondary, tags="strip")
        self.s_pct = c.create_text(px(STRIP_W - 16), cy, anchor="e", font=self.f_text, fill=T.secondary,
                                   tags=("strip", "s_idle"))
        b2 = px(STRIP_W - 14 - 26)
        b1 = b2 - px(8 + 26)
        self.s_btns = []
        for i, bx in enumerate((b1, b2)):
            tag = f"sbtn{i}"
            bg = c.create_image(bx, cy - px(13), anchor="nw", image=self.img["circle26"], tags=("strip", "s_hover", tag))
            ic = c.create_image(bx + px(13), cy, tags=("strip", "s_hover", tag))
            c.tag_bind(tag, "<Enter>", lambda e, bg=bg: c.itemconfigure(bg, image=self.img["circle26h"]))
            c.tag_bind(tag, "<Leave>", lambda e, bg=bg: c.itemconfigure(bg, image=self.img["circle26"]))
            c.tag_bind(tag, "<ButtonRelease-1>", lambda e, i=i: self.strip_action(i))
            self.s_btns.append(ic)
        c.itemconfigure("strip", state="hidden")

    # ---------- раскладка ----------
    def layout(self):
        if self.view == "strip":
            self.layout_strip()
            return
        px, c = self.px, self.c
        c.itemconfigure("strip", state="hidden")
        c.itemconfigure("full", state="normal")
        c.coords(self.field_item, self.field_x, self.field_y)
        T = self.T
        lead = {"busy": None, "done": "done", "error": "error"}.get(self.mode, "search")
        if lead:
            c.itemconfigure(self.lead, image=self.img[lead])
        else:
            self.draw_progress()
        editable = self.mode in ("input", "error")
        self.field.set_readonly(not editable)
        c.itemconfigure("pop", state="normal" if editable else "hidden")
        c.itemconfigure("cut", state="normal" if editable else "hidden")
        show_section = self.sect_open and editable
        self.place_section(show_section)
        c.itemconfigure(self.btn_icon, image=self.img["stop"] if self.mode == "busy" else self.folder_img())
        c.itemconfigure("dots", state="normal" if self.dots_on and editable else "hidden")

        h = top = ROW + SECT_H if show_section else ROW
        if self.mode in ("busy", "done") or (self.mode == "error" and self.status):
            c.itemconfigure("details", state="normal")
            c.coords(self.sep, 0, px(top))
            y = top + 14
            c.coords(self.d_title, px(18), px(y))
            y += 17 + 8
            show_bar = self.mode in ("busy", "done")
            c.itemconfigure("bar", state="normal" if show_bar else "hidden")
            if show_bar:
                c.coords(self.bar_track, px(18), px(y))
                c.coords(self.bar_fill, px(18), px(y))
                y += 6 + 9
            c.itemconfigure(self.d_cap, text=self.status)
            c.coords(self.d_cap, px(18), px(y))
            self.root.update_idletasks()
            bottom = c.bbox(self.d_cap)[3] if c.bbox(self.d_cap) else px(y + 14)
            h = bottom / self.S + 15
            c.itemconfigure(self.d_title, fill=T.red if self.mode == "error" else T.label)
        else:
            c.itemconfigure("details", state="hidden")
        w, hh = px(W), px(h)
        sl, st, sr, sb = self.screen
        geom = (sl + (sr - sl - w) // 2, st + int((sb - st) * 0.22), w, hh)
        if self.panel.geom != geom:
            self.panel.place(*geom)
            self.field.set_bg(self.panel.crop(self.field_x, self.field_y,
                                              self.field_x + self.field_w, self.field_y + self.field_h))

    def layout_strip(self):
        px, c = self.px, self.c
        c.itemconfigure("full", state="hidden")
        c.coords(self.field_item, -10000, -10000)  # окна-элементы не скрываются через state
        self.place_section(False)
        c.itemconfigure("strip", state="normal")
        self.refresh_strip()
        l, t, r, b = self.work_area
        w, h, m = px(STRIP_W), px(STRIP_H), px(14)
        slot = self.daemon.strip_slot(self)  # несколько загрузок — полоски стопкой
        geom = (l + m, b - m - h - slot * (h + px(10)), w, h)
        if self.panel.geom != geom:
            self.panel.place(*geom)
        self.strip_hover = None
        self.update_strip_hover()

    # ---------- прогресс ----------
    def draw_progress(self):
        """Кольцо в строке/полоске и полоса в подробностях."""
        c, S, T = self.c, self.S, self.T
        if self.mode in ("done", "error"):
            c.itemconfigure(self.lead, image=self.img[self.mode])
            c.itemconfigure(self.s_icon, image=self.img[self.mode + "_big"])
            if self.mode == "done":
                self.img["fill"] = ui.plate(self.bar_w, 6, 3, ui.rgba(T.green), S)
                c.itemconfigure(self.bar_fill, image=self.img["fill"])
                c.coords(self.bar_fill, self.px(18), c.coords(self.bar_track)[1])
            return
        if self.progress is None:
            c.itemconfigure(self.lead, image=self.spinner[22][self.spin])
            c.itemconfigure(self.s_icon, image=self.spinner[30][self.spin])
            span = self.bar_w - 120
            phase = (self.spin * 0.04) % 2
            pos = span * (phase if phase <= 1 else 2 - phase)
            c.itemconfigure(self.bar_fill, image=self.img["seg"])
            c.coords(self.bar_fill, self.px(18 + pos), c.coords(self.bar_track)[1])
            return
        v = self.progress
        acc = ui.rgba(T.green if self.mode == "done" else T.accent)
        if self.view == "full":
            self.img["ring22"] = ui.ring(22, 2.4, T.track, acc, S, value=v)
            c.itemconfigure(self.lead, image=self.img["ring22"])
        else:
            self.img["ring30"] = ui.ring(30, 3, T.track, acc, S, value=v)
            c.itemconfigure(self.s_icon, image=self.img["ring30"])
        fw = max(6, self.bar_w * v)
        self.img["fill"] = ui.plate(fw, 6, 3, acc, S)
        c.itemconfigure(self.bar_fill, image=self.img["fill"])
        c.coords(self.bar_fill, self.px(18), c.coords(self.bar_track)[1])

    # ---------- фокус ----------
    def check_blur(self):
        """Закрываемся, когда активным становится окно другой программы (после того как было наше).
        Свои окна — меню, список папок, окно добавления, системный выбор папки — не в счёт."""
        if self.view != "full":
            return
        owner = ui.foreground_owner()
        if owner == os.getpid():
            self.had_focus = True
        elif owner and self.had_focus and not self.modal:
            self.dismiss()

    def dismiss(self):
        self.close_menu(refocus=False)
        self.close_folders(refocus=False)
        self.close_dialog(refocus=False)
        if self.mode == "busy":
            self.to_strip()
        else:
            self.quit()

    def on_escape(self):
        if self.menu:
            self.close_menu()
        elif self.folders_pop:
            self.close_folders()
        elif self.view == "full":
            self.dismiss()

    def on_enter(self):
        if self.menu:
            self.choose(self.menu_hl)
        elif self.view == "full":
            if self.mode == "done":
                self.reset()
            elif self.mode in ("input", "error"):
                self.start()

    def click_canvas(self, e):
        if self.view != "full":
            return
        if self.menu:
            x0, y0, x1, y1 = self.c.bbox("pop")
            if not (x0 <= e.x <= x1 and y0 <= e.y <= y1):
                self.close_menu()
        if self.folders_pop and not (self.dots_x - self.px(2) <= e.x < self.dots_x + self.px(18)):
            self.close_folders()

    def on_button(self):
        if self.mode == "busy":
            self.cancel_job()
        else:
            open_folder(self.result if self.mode == "done" else None, self.folder)

    # ---------- меню качества (как NSPopUpButton: текущий пункт поверх кнопки) ----------
    def toggle_menu(self):
        if self.menu:
            self.close_menu()
            return
        if self.mode not in ("input", "error"):
            return
        px, T = self.px, self.T
        top = tk.Toplevel(self.root)
        self.menu = ui.Panel(top, T, 7, self.S)
        mc = self.menu.canvas
        tw = max(self.f_text.measure(n) for n, _ in QUALITIES)
        mw = px(26 + 22) + tw
        mh = px(MENU_PAD * 2 + MENU_ITEM * len(QUALITIES))
        rx, ry = self.root.winfo_rootx(), self.root.winfo_rooty()
        x = rx + self.c.bbox(self.pop_text)[0] - px(26)  # текст пунктов — ровно над текстом селектора
        y = ry + self.c.coords(self.pop_text)[1] - px(MENU_PAD + MENU_ITEM * self.q + MENU_ITEM / 2)
        st, sb = self.screen[1], self.screen[3]
        y = int(max(st + px(4), min(y, sb - mh - px(4))))
        self.menu_rows = []
        iw = mw / self.S - 2 * MENU_PAD
        self.img["menu_hl"] = ui.plate(iw, MENU_ITEM, 4, ui.rgba(T.accent), self.S)
        self.menu_hl_item = mc.create_image(px(MENU_PAD), 0, anchor="nw", image=self.img["menu_hl"], state="hidden")
        for i, (name, _) in enumerate(QUALITIES):
            yc = px(MENU_PAD + MENU_ITEM * i + MENU_ITEM / 2)
            chk = mc.create_image(px(15), yc, image=self.img["check"], state="normal" if i == self.q else "hidden")
            txt = mc.create_text(px(26), yc, anchor="w", text=name, font=self.f_text, fill=T.label)
            self.menu_rows.append((chk, txt))
        mc.bind("<Motion>", lambda e: self.menu_highlight(self.menu_row(e.y)))
        mc.bind("<Leave>", lambda e: self.menu_highlight(None))
        mc.bind("<ButtonRelease-1>", lambda e: self.menu_click(e.y))
        self.menu.place(int(x), y, mw, mh)
        self.menu_highlight(self.q)
        self.hover_sel(True)
        ui.to_foreground(self.root)  # фокус клавиатуры остаётся в строке…
        self.menu.raise_()  # …а меню — поверх неё

    def menu_row(self, y):
        i = int((y / self.S - MENU_PAD) // MENU_ITEM)
        return i if 0 <= i < len(QUALITIES) else None

    def menu_click(self, y):
        i = self.menu_row(y)
        if i is not None:
            self.choose(i)

    def menu_highlight(self, hl):
        if not self.menu:
            return
        mc, T = self.menu.canvas, self.T
        self.menu_hl = self.q if hl is None else hl
        if hl is None:
            mc.itemconfigure(self.menu_hl_item, state="hidden")
        else:
            mc.coords(self.menu_hl_item, self.px(MENU_PAD), self.px(MENU_PAD + MENU_ITEM * hl))
            mc.itemconfigure(self.menu_hl_item, state="normal")
        for i, (chk, txt) in enumerate(self.menu_rows):
            on = i == hl
            mc.itemconfigure(txt, fill="white" if on else T.label)
            mc.itemconfigure(chk, image=self.img["check_on" if on else "check"])

    def menu_move(self, d):
        if self.view != "full" or self.mode not in ("input", "error"):
            return
        if not self.menu:
            if d > 0:
                self.toggle_menu()
            return
        self.menu_highlight((self.menu_hl + d) % len(QUALITIES))

    def choose(self, i):
        self.q = i
        save_settings(quality=i)
        self.c.itemconfigure(self.pop_text, text=QUALITIES[i][0])
        self.close_menu()
        self.sel_plate()

    def close_menu(self, refocus=True):
        if not self.menu:
            return
        if refocus:  # сначала вернуть фокус себе, иначе Windows отдаст его чужому окну
            ui.to_foreground(self.root)
            self.field.focus()
        self.menu.destroy()
        self.menu = None
        if not self.closed:
            self.hover_sel(self.sel_hover)

    # ---------- папки: «⋯» у иконки папки, список сохранённых путей, добавление ----------
    def set_dots(self, on):
        on = on and self.mode in ("input", "error") and self.view == "full"
        if on != self.dots_on:
            self.dots_on = on
            self.c.itemconfigure("dots", state="normal" if on else "hidden")

    def update_folder_hover(self):
        """«⋯» видно, пока мышь над папкой, «⋯» или списком; наведение на «⋯» раскрывает список,
        уход мыши со «⋯» и списка — сворачивает."""
        if self.mode not in ("input", "error") or self.dialog:
            self.set_dots(False)
            return
        x, y = self.root.winfo_pointerxy()
        x, y = x - self.root.winfo_rootx(), y - self.root.winfo_rooty()
        top, bottom = self.px(ROW / 2 - 15), self.px(ROW / 2 + 15)
        over_btn = self.btn_x <= x < self.btn_x + self.px(30) and top <= y < bottom
        over_dots = self.dots_x - self.px(2) <= x < self.dots_x + self.px(18) and top <= y < bottom
        over_pop = False
        if self.folders_pop:
            gx, gy, gw, gh = self.folders_pop.geom
            px_, py_ = x + self.root.winfo_rootx(), y + self.root.winfo_rooty()
            over_pop = gx <= px_ < gx + gw and gy <= py_ < gy + gh
        self.set_dots(over_btn or over_dots or over_pop or bool(self.folders_pop))
        self.c.itemconfigure(self.dots_bg, image=self.img["hover18" if over_dots or self.folders_pop else "clear18"])
        now = time.monotonic()
        if not over_dots:
            self.dots_armed = True  # после закрытия кликом список не раскрывается, пока мышь не уйдёт
        if over_dots and not self.folders_pop and self.dots_armed:
            self.dots_since = self.dots_since or now
            if now - self.dots_since > 0.15:
                self.open_folders()
        else:
            self.dots_since = None
        if self.folders_pop:
            # подсветка строки по реальному положению мыши — не зависит от событий движения
            row = self.folders_row(y + self.root.winfo_rooty() - self.folders_pop.geom[1]) if over_pop else None
            self.folders_highlight(row)
            if over_dots or over_pop:
                self.leave_since = None
            else:
                self.leave_since = self.leave_since or now
                if now - self.leave_since > 0.4:
                    self.close_folders()

    def toggle_folders(self):
        if self.folders_pop:
            self.close_folders()
            self.dots_armed = False
        else:
            self.open_folders()

    def open_folders(self):
        if self.folders_pop or self.mode not in ("input", "error") or self.view != "full":
            return
        self.close_menu()
        px, T = self.px, self.T
        self.folder_list = load_settings()["folders"]
        n = len(self.folder_list)
        self.fold_sep = 9 if n else 0
        ph = MENU_PAD * 2 + FOLDER_ROW * (n + 1) + self.fold_sep
        self.folders_pop = pop = ui.Panel(tk.Toplevel(self.root), T, 8, self.S)
        pc = pop.canvas
        self.img["fold_hl"] = ui.plate(FOLDERS_W - 2 * MENU_PAD, FOLDER_ROW, 5, ui.rgba(T.accent), self.S)
        self.fold_hl = pc.create_image(px(MENU_PAD), 0, anchor="nw", image=self.img["fold_hl"], state="hidden")
        self.fold_hl_i = None
        self.fold_rows = []
        for i, f in enumerate(self.folder_list):
            yc = px(MENU_PAD + FOLDER_ROW * i + FOLDER_ROW / 2)
            current = same_path(f["path"], self.folder)
            chk = pc.create_image(px(15), yc, image=self.img["check"], state="normal" if current else "hidden")
            pc.create_image(px(31), yc, image=self.res.tag_dot(f.get("color")))
            txt = pc.create_text(px(43), yc, anchor="w", font=self.f_text, fill=T.label,
                                 text=elide_path(f["path"], self.f_text, px(FOLDERS_W - 43 - 36)))
            xm = pc.create_image(px(FOLDERS_W - 20), yc, image=self.img["xmark_on"], state="hidden")
            self.fold_rows.append((chk, txt, xm))
        if n:
            self.img["fold_sep"] = ui.solid(px(FOLDERS_W - 2 * MENU_PAD - 16), max(1, px(1)), T.separator)
            pc.create_image(px(MENU_PAD + 8), px(MENU_PAD + FOLDER_ROW * n + 4), anchor="nw", image=self.img["fold_sep"])
        ya = px(MENU_PAD + FOLDER_ROW * n + self.fold_sep + FOLDER_ROW / 2)
        self.fold_add = (pc.create_image(px(31), ya, image=self.img["plus"]),
                         pc.create_text(px(43), ya, anchor="w", text="Добавить папку…", font=self.f_text, fill=T.label))
        pc.bind("<Motion>", lambda e: self.folders_highlight(self.folders_row(e.y)))
        pc.bind("<Leave>", lambda e: self.folders_highlight(None))
        pc.bind("<ButtonRelease-1>", self.folders_click)
        x = self.root.winfo_rootx() + self.dots_x + px(18) - px(FOLDERS_W)
        y = self.root.winfo_rooty() + px(ROW) + px(4)
        pop.place(x, y, px(FOLDERS_W), px(ph))
        ui.to_foreground(self.root)  # клавиатура остаётся в строке…
        pop.raise_()  # …а список — поверх неё

    def folders_row(self, y):
        """Номер строки под мышью: 0..n-1 — пути, n — «Добавить папку…», None — мимо."""
        y = y / self.S - MENU_PAD
        n = len(self.folder_list)
        if 0 <= y < FOLDER_ROW * n:
            return int(y // FOLDER_ROW)
        if FOLDER_ROW * n + self.fold_sep <= y < FOLDER_ROW * (n + 1) + self.fold_sep:
            return n
        return None

    def folders_highlight(self, hl):
        if not self.folders_pop or hl == self.fold_hl_i:
            return
        self.fold_hl_i = hl
        pc, T, n = self.folders_pop.canvas, self.T, len(self.folder_list)
        if hl is None:
            pc.itemconfigure(self.fold_hl, state="hidden")
        else:
            top = MENU_PAD + FOLDER_ROW * hl + (self.fold_sep if hl == n else 0)
            pc.coords(self.fold_hl, self.px(MENU_PAD), self.px(top))
            pc.itemconfigure(self.fold_hl, state="normal")
        for i, (chk, txt, xm) in enumerate(self.fold_rows):
            on = i == hl
            pc.itemconfigure(txt, fill="white" if on else T.label)
            pc.itemconfigure(chk, image=self.img["check_on" if on else "check"])
            pc.itemconfigure(xm, state="normal" if on else "hidden")  # крестик — у строки под мышью
        pc.itemconfigure(self.fold_add[0], image=self.img["plus_on" if hl == n else "plus"])
        pc.itemconfigure(self.fold_add[1], fill="white" if hl == n else T.label)

    def folders_click(self, e):
        i = self.folders_row(e.y)
        if i is None:
            return
        if i == len(self.folder_list):
            self.open_add_dialog()
        elif e.x >= self.px(FOLDERS_W - 34):
            self.delete_folder(i)
        else:
            self.folder = self.folder_list[i]["path"]
            save_settings(folder=self.folder)
            self.close_folders()
            self.update_folder_icon()

    def delete_folder(self, i):
        removed = self.folder_list.pop(i)
        if same_path(removed["path"], self.folder):
            self.folder = self.folder_list[0]["path"] if self.folder_list else ytfast.DEFAULT_OUT
        save_settings(folders=self.folder_list, folder=self.folder)
        self.update_folder_icon()
        self.close_folders()
        self.open_folders()  # тот же список без удалённой строки

    def close_folders(self, refocus=True):
        if not self.folders_pop:
            return
        if refocus and not self.closed:
            ui.to_foreground(self.root)
            self.field.focus()
        self.folders_pop.destroy()
        self.folders_pop = None
        self.leave_since = None

    def open_add_dialog(self):
        """Окно-лист под строкой: путь (с кнопкой «Обзор…») и цвет метки."""
        self.close_folders(refocus=False)
        if self.dialog:
            return
        px, T, S = self.px, self.T, self.S
        self.dialog = dlg = ui.Panel(tk.Toplevel(self.root), T, 12, S)
        dc = self.dc = dlg.canvas
        dc.create_text(px(18), px(22), anchor="w", text="Папка для сохранения", font=self.f_semi, fill=T.label)
        fw = DLG_W - 36 - 92
        self.img["dlg_field"] = ui.raster(fw, 30, [(ui.rrect(0, 0, fw, 30, 6), T.input_edge),
                                                  (ui.rrect(1, 1, fw - 1, 29, 5), ui.rgba(T.input))], S)
        dc.create_image(px(18), px(40), anchor="nw", image=self.img["dlg_field"])
        self.dlg_field = ui.TextField(dc, T, self.f_text, px(fw - 18), px(24), "Например, D:\\Видео\\Шортсы")
        self.dlg_field.set_bg(ui.solid(px(fw - 18), px(24), ui.rgba(T.input)))
        dc.create_window(px(27), px(43), anchor="nw", window=self.dlg_field.c)
        self.dialog_button("browse", DLG_W - 18 - 84, 40, 84, "Обзор…", self.browse_folder)

        dc.create_text(px(18), px(90), anchor="w", text="Цвет", font=self.f_small, fill=T.secondary)
        self.dlg_color = None
        self.dlg_custom = None  # свой цвет #RRGGBB, если выбирали
        self.dlg_swatches = []
        for i, color in enumerate(TAG_COLORS):
            item = dc.create_image(px(18 + 11 + i * 30), px(116), tags=f"sw{i}")
            dc.tag_bind(f"sw{i}", "<ButtonRelease-1>", lambda e, c=color: self.pick_color(c))
            self.dlg_swatches.append(item)
        # «свой цвет» — после палитры, чуть отступив
        self.dlg_custom_item = dc.create_image(px(18 + 11 + len(TAG_COLORS) * 30 + 6), px(116), tags="swc")
        dc.tag_bind("swc", "<ButtonRelease-1>", lambda e: self.choose_custom_color())
        self.pick_color(None)

        self.dlg_err = dc.create_text(px(18), px(DLG_H - 33), anchor="w", text="", font=self.f_small,
                                      fill=T.red, width=px(DLG_W - 36 - 212))
        self.dialog_button("cancel", DLG_W - 18 - 96 - 10 - 96, DLG_H - 48, 96, "Отмена", self.close_dialog)
        self.dialog_button("add", DLG_W - 18 - 96, DLG_H - 48, 96, "Добавить", self.add_folder, primary=True)

        top = dlg.win
        top.bind("<Return>", lambda e: self.add_folder())
        top.bind("<KP_Enter>", lambda e: self.add_folder())
        top.bind("<Escape>", lambda e: self.close_dialog())
        x = self.root.winfo_rootx() + (px(W) - px(DLG_W)) // 2
        y = self.root.winfo_rooty() + self.panel.geom[3] + px(10)
        dlg.place(x, y, px(DLG_W), px(DLG_H))
        ui.to_foreground(top)
        self.dlg_field.focus()

    def dialog_button(self, tag, x, y, w, text, command, primary=False):
        """Кнопка в стиле Big Sur: основная — синяя, остальные — полупрозрачные."""
        T, S, px, dc = self.T, self.S, self.px, self.dc
        if primary:
            normal = hover = ui.plate(w, 30, 7, ui.rgba(T.accent), S)
        else:
            normal = ui.plate(w, 30, 7, T.circle, S)
            hover = ui.plate(w, 30, 7, T.circle[:3] + (T.circle[3] * 2,), S)
        self.img[f"dlg_{tag}"], self.img[f"dlg_{tag}_h"] = normal, hover
        bg = dc.create_image(px(x), px(y), anchor="nw", image=normal, tags=tag)
        dc.create_text(px(x + w / 2), px(y + 15), text=text, font=self.f_text,
                       fill="white" if primary else T.label, tags=tag)
        dc.tag_bind(tag, "<Enter>", lambda e: dc.itemconfigure(bg, image=hover))
        dc.tag_bind(tag, "<Leave>", lambda e: dc.itemconfigure(bg, image=normal))
        dc.tag_bind(tag, "<ButtonRelease-1>", lambda e: command())

    def pick_color(self, color):
        self.dlg_color = color
        for item, c in zip(self.dlg_swatches, TAG_COLORS):
            self.dc.itemconfigure(item, image=self.res.tag_swatch(c, c == color))
        custom = bool(color and color.startswith("#"))
        self.dc.itemconfigure(self.dlg_custom_item, image=self.res.custom_swatch(self.dlg_custom, custom))

    def choose_custom_color(self):
        """Системный выбор цвета Windows; выбранный цвет попадает в радужный кружок."""
        start = self.dlg_custom or self.res.color_hex(self.dlg_color) or self.T.accent
        self.modal = True  # системный диалог — не повод закрывать наши окна
        try:
            _, hexc = colorchooser.askcolor(color=start, parent=self.dialog.win, title="Свой цвет папки")
        finally:
            self.modal = False
        if not self.dialog:
            return
        if hexc:
            self.dlg_custom = hexc.lower()
            self.pick_color(self.dlg_custom)
        ui.to_foreground(self.dialog.win)
        self.dlg_field.focus()

    def browse_folder(self):
        start = self.dlg_field.get().strip() or self.folder
        while start and not os.path.isdir(start):
            parent = os.path.dirname(start)
            start = "" if parent == start else parent
        self.modal = True  # системный диалог — не повод закрывать наши окна
        try:
            path = filedialog.askdirectory(parent=self.dialog.win, title="Папка для сохранения",
                                           initialdir=start or os.path.expanduser("~"), mustexist=False)
        finally:
            self.modal = False
        if self.dialog:
            if path:
                self.dlg_field.set(os.path.normpath(path))
            ui.to_foreground(self.dialog.win)
            self.dlg_field.focus()

    def add_folder(self):
        raw = self.dlg_field.get().strip().strip('"')
        path = os.path.expandvars(os.path.expanduser(raw))
        if not raw or not os.path.splitdrive(path)[0] or not os.path.isabs(path):
            self.dc.itemconfigure(self.dlg_err, text="Нужен полный путь, например D:\\Видео\\Шортсы")
            return
        path = os.path.normpath(path)
        folders = [f for f in load_settings()["folders"] if not same_path(f["path"], path)]
        folders.append({"path": path, "color": self.dlg_color})
        self.folder = path  # новая папка сразу становится текущей
        save_settings(folders=folders, folder=path)
        self.close_dialog()
        self.update_folder_icon()

    def close_dialog(self, refocus=True):
        if not self.dialog:
            return
        if refocus and not self.closed:
            ui.to_foreground(self.root)
            self.field.focus()
        self.dialog.destroy()
        self.dialog = None

    # ---------- загрузка ----------
    def prefill(self):
        try:
            clip = self.root.clipboard_get().strip()
        except tk.TclError:
            return
        if clip.startswith("http") and "\n" not in clip and len(clip) < 500:
            self.field.set(clip)
            self.field.select_all()

    def start(self):
        url = self.field.get().strip()
        if not url.startswith("http"):
            self.show_error("Нужна ссылка", "Вставьте ссылку на видео, начинающуюся с http")
            return
        section = self.section
        if self.sect_open:
            try:
                section = ytfast.parse_section(self.sec_from.get(), self.sec_to.get())
            except ValueError as e:
                self.show_error("Неверный фрагмент", str(e))
                return
        self.close_menu()
        self.close_folders()
        self.set_dots(False)
        save_settings(folder=self.folder)  # «последняя папка сохранения» — для следующих окон
        self.job += 1
        self.cancel = threading.Event()
        self.mode, self.result, self.title, self.progress = "busy", None, "", None
        self.c.itemconfigure(self.d_title, text="Получаю ссылки…")
        self.status = f"Качество: {QUALITIES[self.q][0]}"
        if section:
            end = ytfast.format_time(section[1]) if section[1] else "конца"
            self.status += f"  ·  фрагмент {ytfast.format_time(section[0])} — {end}"
        self.layout()
        self.draw_progress()
        q = QUALITIES[self.q][1]
        kw = {"audio": True} if q == "audio" else {"quality": q}
        kw["output"] = self.folder  # если папки ещё нет — ytfast.download её создаст
        kw["section"] = section
        t = threading.Thread(target=self.work, args=(self.job, url, kw, self.cancel), daemon=True)
        self.threads.append(t)
        t.start()

    def work(self, job, url, kw, cancel):
        put = self.events.put
        try:
            out = ytfast.download(
                url, log=lambda s: None, cancel=cancel,
                on_title=lambda t: put(("title", job, t)),
                on_progress=lambda d, t, s, n: put(("progress", job, d, t, s, n)),
                **kw)
            put(("done", job, out))
        except ytfast.Cancelled:
            pass
        except Exception as e:  # noqa: BLE001 — любую ошибку показываем в окне
            put(("error", job, str(e)))

    def poll(self):
        if self.closed:
            return
        try:
            while True:
                ev = self.events.get_nowait()
                if ev[1] == self.job:
                    getattr(self, "on_" + ev[0])(*ev[2:])
        except queue.Empty:
            pass
        if self.closed:
            return
        self.check_blur()
        if self.closed:
            return
        if self.view == "full":
            self.update_folder_hover()
        if self.mode == "busy" and self.progress is None:
            self.spin = (self.spin + 1) % 12
            self.draw_progress()
        if self.view == "strip":
            self.update_strip_hover()
            if self.close_at and not self.strip_hover:
                self.close_at -= 1
                if self.close_at <= 0:
                    self.quit()
                    return
        self.root.after(60, self.poll)

    def on_title(self, title):
        self.title = title
        self.c.itemconfigure(self.d_title, text=elide(title, self.f_semi, self.px(W - 36)))
        self.refresh_strip()

    def on_progress(self, done, total, speed, n):
        self.progress = done / max(total, 1)
        eta = int((total - done) / speed) if speed > 0 else 0
        left = f"осталось {eta // 60}:{eta % 60:02d}"
        self.status = f"{done / 2**20:.1f} из {total / 2**20:.1f} МБ  ·  {speed / 2**20:.1f} МБ/с  ·  {left}"
        self.c.itemconfigure(self.d_cap, text=self.status)
        self.c.itemconfigure(self.s_cap, text=f"{speed / 2**20:.1f} МБ/с  ·  {left}")
        self.c.itemconfigure(self.s_pct, text=f"{int(self.progress * 100)}%")
        self.draw_progress()

    def on_done(self, out):
        self.mode, self.result, self.progress = "done", out, 1.0
        self.c.itemconfigure(self.d_title, text=elide(self.title or os.path.basename(out), self.f_semi, self.px(W - 36)))
        where = elide_path(os.path.dirname(out), self.f_small, self.px(W - 36 - 200))
        self.status = f"Сохранено в {where}  ·  Enter — новая ссылка"
        self.c.itemconfigure(self.s_cap, text="Готово")
        self.layout()
        self.draw_progress()
        if self.view == "strip":
            self.close_at = int(8000 / 60)  # полоска сама исчезнет через 8 с, если не навести

    def on_error(self, msg):
        self.show_error("Не получилось скачать", msg)
        self.c.itemconfigure(self.s_cap, text="Ошибка — разверните, чтобы посмотреть")
        self.refresh_strip()

    def show_error(self, title, msg):
        self.mode = "error"
        self.c.itemconfigure(self.d_title, text=title)
        self.status = msg.strip()[-400:]
        if self.view == "full":
            self.layout()

    def cancel_job(self):
        if self.cancel:
            self.cancel.set()
        self.job += 1  # события отменённой загрузки больше не интересны
        self.mode, self.status = "input", ""
        self.layout()
        self.field.focus()

    def reset(self):
        self.mode, self.result, self.status = "input", None, ""
        self.section = None
        self.sec_from.set("")
        self.sec_to.set("")
        self.field.set("")
        self.layout()
        self.prefill()
        self.field.focus()

    # ---------- полоска ----------
    def to_strip(self):
        self.view = "strip"
        self.layout()
        self.draw_progress()
        self.daemon.restack()  # своё место в стопке могли занять другие полоски

    def to_full(self):
        for w in list(self.daemon.windows):  # развёрнутым бывает одно окно: остальные — обратно в полоску
            if w is not self and w.view == "full" and not w.closed:
                w.dismiss()
        self.view = "full"
        self.close_at = None
        self.had_focus = False
        self.layout()
        self.draw_progress()
        self.daemon.restack()
        self.bring_up()

    def bring_up(self):
        ui.to_foreground(self.root)
        self.field.focus()

    def hotkey(self):
        """Повторное Ctrl+Alt+Y: если окно уже в фокусе — убрать, иначе — показать."""
        if ui.GetForegroundWindow() == self.panel.hwnd:
            self.dismiss()
        else:
            self.bring_up()

    def refresh_strip(self):
        c = self.c
        title = self.title or ("Получаю ссылки…" if self.mode == "busy" else "")
        c.itemconfigure(self.s_title, text=elide(title, self.f_semi, self.px(STRIP_W - 56 - 90)))
        if self.mode == "done":
            c.itemconfigure(self.s_icon, image=self.img["done_big"])
        elif self.mode == "error":
            c.itemconfigure(self.s_icon, image=self.img["error_big"])
        self.strip_hover = None
        if self.view == "strip":
            self.update_strip_hover()

    def update_strip_hover(self):
        x, y = self.root.winfo_pointerxy()
        rx, ry = self.root.winfo_rootx(), self.root.winfo_rooty()
        inside = rx <= x < rx + self.root.winfo_width() and ry <= y < ry + self.root.winfo_height()
        if inside == self.strip_hover:
            return
        self.strip_hover = inside
        c = self.c
        c.itemconfigure(self.s_btns[0], image=self.folder_img() if self.mode == "done" else self.img["expand"])
        c.itemconfigure(self.s_btns[1], image=self.img["xmark"])
        c.itemconfigure("s_hover", state="normal" if inside else "hidden")
        c.itemconfigure("s_idle", state="normal" if not inside and self.mode == "busy" else "hidden")

    def strip_action(self, i):
        if i == 0:
            if self.mode == "done":
                open_folder(self.result, self.folder)
            else:
                self.to_full()
        else:
            if self.mode == "busy" and self.cancel:
                self.cancel.set()
            self.quit()


class Daemon:
    """Фоновый процесс, как Spotlight: держит Tk и готовые ресурсы, по Ctrl+Alt+Y сразу
    показывает окно. Повторный запуск программы не стартует второй процесс, а просит этот."""

    def __init__(self, root):
        self.root = root
        root.withdraw()
        root.report_callback_exception = lambda *exc: log("".join(traceback.format_exception(*exc)))
        self.windows = []
        self.res = Resources(root, ui.dark_mode())
        self.links = queue.Queue()  # (ссылка, папка или None, фрагмент или None) от кнопки в браузере
        root.bind("<<Show>>", lambda e: self.show())
        root.bind("<<Download>>", lambda e: self.download())
        threading.Thread(target=self.hotkey_loop, daemon=True).start()
        threading.Thread(target=self.pipe_loop, daemon=True).start()

    def show(self):
        for w in self.windows:
            if w.view == "full":
                w.hotkey()
                return
        self.open_window()

    def download(self):
        while not self.links.empty():
            self.open_window(*self.links.get())

    def open_window(self, url=None, folder=None, section=None):
        dark = ui.dark_mode()
        if dark != self.res.T.dark and not self.windows:  # тему Windows переключили
            self.res = Resources(self.root, dark)
        self.windows.append(App(self, self.res, ui.active_monitor(), url, folder, section))

    def window_closed(self, w):
        if w in self.windows:
            self.windows.remove(w)
        self.restack()

    def strips(self):
        return [w for w in self.windows if w.view == "strip" and not w.closed]

    def strip_slot(self, w):
        strips = [s for s in self.strips() if s.work_area == w.work_area]  # стопка — своя на каждом мониторе
        return strips.index(w) if w in strips else len(strips)  # новая полоска — поверх стопки

    def restack(self):
        for w in self.strips():
            w.layout_strip()

    def hotkey_loop(self):
        u32 = ctypes.windll.user32
        if not u32.RegisterHotKey(None, 1, 0x0001 | 0x0002 | 0x4000, 0x59):  # Alt+Ctrl, без автоповтора, Y
            log("Ctrl+Alt+Y уже занято другим приложением")
            return
        msg = wt.MSG()
        while u32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == 0x0312:  # WM_HOTKEY
                self.root.event_generate("<<Show>>", when="tail")

    def pipe_loop(self):
        try:
            listener = Listener(PIPE, family="AF_PIPE")
        except OSError as e:
            log(f"не удалось открыть канал {PIPE}: {e}")
            return
        while True:
            try:
                with listener.accept() as conn:
                    msg = conn.recv_bytes()
                    if msg == b"show":
                        self.root.event_generate("<<Show>>", when="tail")
                    elif msg.startswith(b"download "):
                        request = json.loads(msg[len(b"download "):])
                        self.links.put((request["url"], request.get("folder"), request.get("section")))
                        self.root.event_generate("<<Download>>", when="tail")
            except (OSError, EOFError, ValueError, KeyError):
                pass


def log(msg):
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + msg.rstrip() + "\n")
    except OSError:
        pass


def load_bundled_fonts():
    """Inter из папки fonts — только для этого процесса, в систему не устанавливается."""
    folder = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")
    if os.path.isdir(folder):
        for name in os.listdir(folder):
            if name.lower().endswith((".ttf", ".otf")):
                ctypes.windll.gdi32.AddFontResourceExW(os.path.join(folder, name), 0x10, 0)  # FR_PRIVATE


def main():
    background = "--background" in sys.argv  # автозапуск: только поднять фоновый процесс
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except (AttributeError, OSError):
        pass
    load_bundled_fonts()
    root = tk.Tk()
    daemon = Daemon(root)
    if not background:
        daemon.show()
    root.mainloop()


if __name__ == "__main__":
    main()
