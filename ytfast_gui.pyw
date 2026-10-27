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


def load_quality():
    try:
        with open(SETTINGS, encoding="utf-8") as f:
            return json.load(f).get("quality", 3)
    except (OSError, ValueError):
        return 3  # 1080p


def save_quality(i):
    try:
        os.makedirs(os.path.dirname(SETTINGS), exist_ok=True)
        with open(SETTINGS, "w", encoding="utf-8") as f:
            json.dump({"quality": i}, f)
    except OSError:
        pass


def open_folder(path=None):
    os.makedirs(ytfast.DEFAULT_OUT, exist_ok=True)
    if path and os.path.exists(path):
        subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
    else:
        os.startfile(ytfast.DEFAULT_OUT)


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
        }
        # крутилки (неизвестный прогресс): 12 кадров для двух размеров
        self.spinner = {sz: [ui.ring(sz, w, T.track, acc, S, start=i / 12) for i in range(12)]
                        for sz, w in ((22, 2.4), (30, 3))}
        self.sel_plates = {}  # подложки селектора по ширине текста

    def sel_plate(self, w):
        if w not in self.sel_plates:
            self.sel_plates[w] = (ui.plate(w, 28, 7, self.T.hover, self.S),
                                  ui.plate(w, 28, 7, (0, 0, 0, 0), self.S))  # прозрачная, но ловит мышь
        return self.sel_plates[w]


class App:
    """Одно окно: строка Spotlight, которая при загрузке сворачивается в полоску."""

    def __init__(self, daemon, res):
        self.daemon, self.res = daemon, res
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
        self.q = min(max(load_quality(), 0), len(QUALITIES) - 1)

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
        self.panel.destroy()
        self.daemon.window_closed(self)

    # ---------- построение ----------
    def build_full(self):
        px, c, T = self.px, self.c, self.T
        cy = px(ROW / 2)
        self.lead = c.create_image(px(29), cy, image=self.img["search"], tags="full")

        # справа налево: папка/отмена, селектор качества
        bx = px(W - 14 - 30)
        self.btn = c.create_image(bx, cy - px(15), anchor="nw", image=self.img["clear30"], tags=("full", "btn"))
        self.btn_icon = c.create_image(bx + px(15), cy, image=self.img["folder"], tags=("full", "btn"))
        c.tag_bind("btn", "<Enter>", lambda e: c.itemconfigure(self.btn, image=self.img["hover30"]))
        c.tag_bind("btn", "<Leave>", lambda e: c.itemconfigure(self.btn, image=self.img["clear30"]))
        c.tag_bind("btn", "<ButtonRelease-1>", lambda e: self.on_button())

        # селектор качества: просто текст и неяркая стрелка, подложка — только при наведении
        self.sel_r = bx - px(4)
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
        c.itemconfigure(self.btn_icon, image=self.img["stop" if self.mode == "busy" else "folder"])

        h = ROW
        if self.mode in ("busy", "done") or (self.mode == "error" and self.status):
            c.itemconfigure("details", state="normal")
            y = ROW + 14
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
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        geom = ((sw - w) // 2, int(sh * 0.22), w, hh)
        if self.panel.geom != geom:
            self.panel.place(*geom)
            self.field.set_bg(self.panel.crop(self.field_x, self.field_y,
                                              self.field_x + self.field_w, self.field_y + self.field_h))

    def layout_strip(self):
        px, c = self.px, self.c
        c.itemconfigure("full", state="hidden")
        c.coords(self.field_item, -10000, -10000)  # окна-элементы не скрываются через state
        c.itemconfigure("strip", state="normal")
        self.refresh_strip()
        l, t, r, b = ui.work_area()
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
        """Закрываемся, когда активным становится чужое окно (после того как было наше)."""
        if self.view != "full":
            return
        ours = {self.panel.hwnd}
        if self.menu:
            ours.add(self.menu.hwnd)
        fg = ui.GetForegroundWindow()
        if fg in ours:
            self.had_focus = True
        elif self.had_focus and fg:
            self.dismiss()

    def dismiss(self):
        self.close_menu(refocus=False)
        if self.mode == "busy":
            self.to_strip()
        else:
            self.quit()

    def on_escape(self):
        if self.menu:
            self.close_menu()
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
        if self.menu and self.view == "full":
            x0, y0, x1, y1 = self.c.bbox("pop")
            if not (x0 <= e.x <= x1 and y0 <= e.y <= y1):
                self.close_menu()

    def on_button(self):
        if self.mode == "busy":
            self.cancel_job()
        else:
            open_folder(self.result if self.mode == "done" else None)

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
        sh = self.root.winfo_screenheight()
        y = int(max(px(4), min(y, sh - mh - px(4))))
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
        save_quality(i)
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
        self.close_menu()
        self.job += 1
        self.cancel = threading.Event()
        self.mode, self.result, self.title, self.progress = "busy", None, "", None
        self.c.itemconfigure(self.d_title, text="Получаю ссылки…")
        self.status = f"Качество: {QUALITIES[self.q][0]}"
        self.layout()
        self.draw_progress()
        q = QUALITIES[self.q][1]
        kw = {"audio": True} if q == "audio" else {"quality": q}
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
        self.status = "Сохранено в Загрузки › YouTube  ·  Enter — новая ссылка"
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
        self.field.set("")
        self.layout()
        self.prefill()
        self.field.focus()

    # ---------- полоска ----------
    def to_strip(self):
        self.view = "strip"
        self.layout()
        self.draw_progress()

    def to_full(self):
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
        first = "folder" if self.mode == "done" else "expand"
        c.itemconfigure(self.s_btns[0], image=self.img[first])
        c.itemconfigure(self.s_btns[1], image=self.img["xmark"])
        c.itemconfigure("s_hover", state="normal" if inside else "hidden")
        c.itemconfigure("s_idle", state="normal" if not inside and self.mode == "busy" else "hidden")

    def strip_action(self, i):
        if i == 0:
            if self.mode == "done":
                open_folder(self.result)
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
        root.bind("<<Show>>", lambda e: self.show())
        threading.Thread(target=self.hotkey_loop, daemon=True).start()
        threading.Thread(target=self.pipe_loop, daemon=True).start()

    def show(self):
        for w in self.windows:
            if w.view == "full":
                w.hotkey()
                return
        dark = ui.dark_mode()
        if dark != self.res.T.dark and not self.windows:  # тему Windows переключили
            self.res = Resources(self.root, dark)
        self.windows.append(App(self, self.res))

    def window_closed(self, w):
        if w in self.windows:
            self.windows.remove(w)
        self.restack()

    def strips(self):
        return [w for w in self.windows if w.view == "strip" and not w.closed]

    def strip_slot(self, w):
        strips = self.strips()
        return strips.index(w) if w in strips else 0

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
                    if conn.recv_bytes() == b"show":
                        self.root.event_generate("<<Show>>", when="tail")
            except (OSError, EOFError):
                pass


def log(msg):
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + msg.rstrip() + "\n")
    except OSError:
        pass


def main():
    background = "--background" in sys.argv  # автозапуск: только поднять фоновый процесс
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except (AttributeError, OSError):
        pass
    root = tk.Tk()
    daemon = Daemon(root)
    if not background:
        daemon.show()
    root.mainloop()


if __name__ == "__main__":
    main()
