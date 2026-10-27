"""macui — интерфейс в духе macOS Big Sur поверх tkinter на Windows.

Tk умеет только плоские непрозрачные прямоугольники, поэтому всё «маковское»
собрано вручную:
  * Shot + material — снимок экрана под окном, размытый, насыщенный и
    подкрашенный: так выглядит «вибрантный» материал Big Sur;
  * Shadow — отдельное layered-окно с попиксельной альфой: большая мягкая тень;
  * Panel — окно без рамки со сглаженными скруглёнными углами на этом фоне;
  * raster + фигуры — сглаженная векторная отрисовка иконок в духе SF Symbols;
  * TextField — однострочное поле ввода прямо на фоне панели.
Все размеры — в «дизайн-пикселях» (как pt в macOS), умноженных на масштаб экрана S.
"""
import ctypes
import ctypes.wintypes as wt
import math
import struct
import tkinter as tk
import winreg
import zlib

KEY = "#010203"  # цвет-ключ: эти пиксели окна прозрачны (углы за скруглением)
BLUR = 16  # во сколько раз уменьшаем снимок перед размытием

VP, INT, UINT = ctypes.c_void_p, ctypes.c_int, ctypes.c_uint
_u32, _gdi, _dwm = ctypes.windll.user32, ctypes.windll.gdi32, ctypes.windll.dwmapi


def _fn(lib, name, res, *args):
    f = getattr(lib, name)
    f.restype, f.argtypes = res, list(args)
    return f


GetDC = _fn(_u32, "GetDC", VP, VP)
ReleaseDC = _fn(_u32, "ReleaseDC", INT, VP, VP)
CreateCompatibleDC = _fn(_gdi, "CreateCompatibleDC", VP, VP)
CreateCompatibleBitmap = _fn(_gdi, "CreateCompatibleBitmap", VP, VP, INT, INT)
SelectObject = _fn(_gdi, "SelectObject", VP, VP, VP)
DeleteObject = _fn(_gdi, "DeleteObject", INT, VP)
DeleteDC = _fn(_gdi, "DeleteDC", INT, VP)
BitBlt = _fn(_gdi, "BitBlt", INT, VP, INT, INT, INT, INT, VP, INT, INT, UINT)
StretchBlt = _fn(_gdi, "StretchBlt", INT, VP, INT, INT, INT, INT, VP, INT, INT, INT, INT, UINT)
SetStretchBltMode = _fn(_gdi, "SetStretchBltMode", INT, VP, INT)
SetBrushOrgEx = _fn(_gdi, "SetBrushOrgEx", INT, VP, INT, INT, VP)
GetDIBits = _fn(_gdi, "GetDIBits", INT, VP, VP, UINT, UINT, VP, VP, UINT)
CreateDIBSection = _fn(_gdi, "CreateDIBSection", VP, VP, VP, UINT, ctypes.POINTER(VP), VP, UINT)
CreateWindowExW = _fn(_u32, "CreateWindowExW", VP, UINT, ctypes.c_wchar_p, ctypes.c_wchar_p, UINT,
                      INT, INT, INT, INT, VP, VP, VP, VP)
DestroyWindow = _fn(_u32, "DestroyWindow", INT, VP)
ShowWindow = _fn(_u32, "ShowWindow", INT, VP, INT)
SetWindowPos = _fn(_u32, "SetWindowPos", INT, VP, VP, INT, INT, INT, INT, UINT)
UpdateLayeredWindow = _fn(_u32, "UpdateLayeredWindow", INT, VP, VP, VP, VP, VP, VP, UINT, VP, UINT)
SetWindowDisplayAffinity = _fn(_u32, "SetWindowDisplayAffinity", INT, VP, UINT)
GetForegroundWindow = _fn(_u32, "GetForegroundWindow", VP)
SetForegroundWindow = _fn(_u32, "SetForegroundWindow", INT, VP)
SystemParametersInfoW = _fn(_u32, "SystemParametersInfoW", INT, UINT, UINT, VP, UINT)
DwmSetWindowAttribute = _fn(_dwm, "DwmSetWindowAttribute", INT, VP, UINT, VP, UINT)

SRCCOPY, CAPTUREBLT = 0x00CC0020, 0x40000000
WDA_NONE, WDA_EXCLUDEFROMCAPTURE = 0, 0x11


# ---------- тема ----------

def dark_mode():
    try:
        key = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
            return winreg.QueryValueEx(k, "AppsUseLightTheme")[0] == 0
    except OSError:
        return False


class Theme:
    """Цвета Big Sur. Кортежи (r, g, b, a) — полупрозрачные слои поверх материала."""

    def __init__(self, dark):
        self.dark = dark
        if dark:
            self.tint, self.tint_amount, self.saturation = (30, 30, 32), 0.74, 1.5
            self.border, self.inner = (0, 0, 0, 0.75), (255, 255, 255, 0.14)
            self.shadow = [(18, 0.55, 12), (1.5, 0.35, 0.5)]
            self.label, self.secondary, self.placeholder = "#ECECEE", "#9C9CA1", "#77777C"
            self.accent, self.green, self.red = "#0A84FF", "#32D74B", "#FF453A"
            self.separator, self.hover, self.track = (255, 255, 255, 0.10), (255, 255, 255, 0.10), (255, 255, 255, 0.14)
            self.circle = (255, 255, 255, 0.13)
            self.selection = "#3F638B"
            self.tags = {"red": "#FF453A", "orange": "#FF9F0A", "yellow": "#FFD60A", "green": "#32D74B",
                         "blue": "#0A84FF", "purple": "#BF5AF2", "gray": "#98989D"}
            self.input, self.input_edge = "#3A3A3C", (255, 255, 255, 0.10)
        else:
            self.tint, self.tint_amount, self.saturation = (246, 246, 248), 0.72, 1.6
            self.border, self.inner = (0, 0, 0, 0.17), None
            self.shadow = [(18, 0.26, 12), (1.5, 0.12, 0.5)]
            self.label, self.secondary, self.placeholder = "#1D1D1F", "#6E6E73", "#A0A0A5"
            self.accent, self.green, self.red = "#007AFF", "#28CD41", "#FF3B30"
            self.separator, self.hover, self.track = (0, 0, 0, 0.09), (0, 0, 0, 0.07), (0, 0, 0, 0.09)
            self.circle = (0, 0, 0, 0.06)
            self.selection = "#B3D7FF"
            self.tags = {"red": "#FF3B30", "orange": "#FF9500", "yellow": "#FFCC00", "green": "#28CD41",
                         "blue": "#007AFF", "purple": "#AF52DE", "gray": "#8E8E93"}
            self.input, self.input_edge = "#FFFFFF", (0, 0, 0, 0.16)


def rgba(hex_color, a=1.0):
    return (int(hex_color[1:3], 16), int(hex_color[3:5], 16), int(hex_color[5:7], 16), a)


# ---------- PNG ----------

def png(w, h, data, alpha=True):
    """Минимальный PNG-кодер: RGBA или RGB, без внешних библиотек."""
    stride = w * (4 if alpha else 3)
    raw = b"".join(b"\x00" + bytes(data[y * stride:(y + 1) * stride]) for y in range(h))

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d))

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 6 if alpha else 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw, 1)) + chunk(b"IEND", b"")


def photo(w, h, data, alpha=True):
    return tk.PhotoImage(data=png(w, h, data, alpha))


# ---------- снимок экрана и материал ----------

def _bmi(w, h):
    return ctypes.create_string_buffer(struct.pack("<IiiHHIIiiII", 40, w, -h, 1, 32, 0, 0, 0, 0, 0, 0), 44)


class Shot:
    """Снимок области экрана: полный BGRA и уменьшенный в BLUR раз.
    Окна из hide на время снимка исключаются из захвата — видно то, что под ними."""

    def __init__(self, x, y, w, h, hide=()):
        self.x, self.y, self.w, self.h = x, y, w, h
        self.sw, self.sh = max(2, -(-w // BLUR)), max(2, -(-h // BLUR))
        sdc = GetDC(None)
        mdc, bmp = CreateCompatibleDC(sdc), CreateCompatibleBitmap(sdc, w, h)
        old = SelectObject(mdc, bmp)
        for hw in hide:
            SetWindowDisplayAffinity(hw, WDA_EXCLUDEFROMCAPTURE)
        try:
            BitBlt(mdc, 0, 0, w, h, sdc, x, y, SRCCOPY | CAPTUREBLT)
        finally:
            for hw in hide:
                SetWindowDisplayAffinity(hw, WDA_NONE)
        sdc2, sbmp = CreateCompatibleDC(sdc), CreateCompatibleBitmap(sdc, self.sw, self.sh)
        old2 = SelectObject(sdc2, sbmp)
        SetStretchBltMode(sdc2, 4)  # HALFTONE: усреднение при уменьшении
        SetBrushOrgEx(sdc2, 0, 0, None)
        StretchBlt(sdc2, 0, 0, self.sw, self.sh, mdc, 0, 0, w, h, SRCCOPY)
        SelectObject(mdc, old)
        SelectObject(sdc2, old2)
        self.full = self._bits(mdc, bmp, w, h)
        self.small = self._bits(sdc2, sbmp, self.sw, self.sh)
        for o in (bmp, sbmp):
            DeleteObject(o)
        DeleteDC(mdc)
        DeleteDC(sdc2)
        ReleaseDC(None, sdc)

    @staticmethod
    def _bits(dc, bmp, w, h):
        buf = ctypes.create_string_buffer(w * h * 4)
        GetDIBits(dc, bmp, 0, h, buf, _bmi(w, h), 0)
        return buf.raw


def material(shot, theme):
    """Размытый, насыщенный и подкрашенный фон размером со снимок: bytearray RGB."""
    sw, sh, s = shot.sw, shot.sh, shot.small
    px = [[float(s[i * 4 + 2]), float(s[i * 4 + 1]), float(s[i * 4])] for i in range(sw * sh)]
    for _ in range(2):  # два прохода box-blur 3×3 на маленькой картинке
        nxt = []
        for y in range(sh):
            for x in range(sw):
                acc, n = [0.0, 0.0, 0.0], 0
                for yy in (y - 1, y, y + 1):
                    for xx in (x - 1, x, x + 1):
                        if 0 <= yy < sh and 0 <= xx < sw:
                            p = px[yy * sw + xx]
                            acc[0] += p[0]
                            acc[1] += p[1]
                            acc[2] += p[2]
                            n += 1
                nxt.append([v / n for v in acc])
        px = nxt
    tr, tg, tb = theme.tint
    a, sat = theme.tint_amount, theme.saturation
    for p in px:
        lum = 0.299 * p[0] + 0.587 * p[1] + 0.114 * p[2]
        p[0] = min(255.0, max(0.0, lum + (p[0] - lum) * sat)) * (1 - a) + tr * a
        p[1] = min(255.0, max(0.0, lum + (p[1] - lum) * sat)) * (1 - a) + tg * a
        p[2] = min(255.0, max(0.0, lum + (p[2] - lum) * sat)) * (1 - a) + tb * a
    w, h = shot.w, shot.h
    # билинейное увеличение: сначала каждую маленькую строку по горизонтали, потом смешиваем строки
    xs = []
    for X in range(w):
        f = min(max((X + 0.5) * sw / w - 0.5, 0), sw - 1)
        x0 = int(f)
        xs.append((x0, min(x0 + 1, sw - 1), f - x0))
    rows = []
    for y in range(sh):
        base = px[y * sw:(y + 1) * sw]
        row = []
        for x0, x1, t in xs:
            p0, p1 = base[x0], base[x1]
            u = 1 - t
            row += (p0[0] * u + p1[0] * t, p0[1] * u + p1[1] * t, p0[2] * u + p1[2] * t)
        rows.append(row)
    out = bytearray(w * h * 3)
    for Y in range(h):
        f = min(max((Y + 0.5) * sh / h - 0.5, 0), sh - 1)
        y0 = int(f)
        r0, r1, t = rows[y0], rows[min(y0 + 1, sh - 1)], f - y0
        u = 1 - t
        out[Y * w * 3:(Y + 1) * w * 3] = bytes(int(a0 * u + a1 * t) for a0, a1 in zip(r0, r1))
    return out


def _rbox(x, y, w, h, r):
    """SDF скруглённого прямоугольника [0,w]×[0,h]; < 0 внутри."""
    qx = abs(x - w / 2) - w / 2 + r
    qy = abs(y - h / 2) - h / 2 + r
    return math.hypot(max(qx, 0), max(qy, 0)) + min(max(qx, qy), 0) - r


def panel_pixels(shot, mat, r, theme):
    """Фон панели: материал, сглаженные углы (за ними KEY), тонкая обводка."""
    w, h, full = shot.w, shot.h, shot.full
    out = bytearray(mat)
    br, bg_, bb, ba = theme.border
    inner = theme.inner
    ri = int(math.ceil(r)) + 2
    for y in range(h):
        if y < ri or y >= h - ri:
            xs = range(w) if (y < 2 or y >= h - 2) else list(range(ri)) + list(range(w - ri, w))
        else:
            xs = (0, 1, 2, w - 3, w - 2, w - 1)
        for x in xs:
            d = _rbox(x + 0.5, y + 0.5, w, h, r)
            cov = 0.5 - d
            i = (y * w + x) * 3
            if cov <= 0:
                out[i:i + 3] = b"\x01\x02\x03"
                continue
            c = [out[i], out[i + 1], out[i + 2]]
            sc = max(0.0, 1 - abs(d + 0.5)) * ba  # обводка 1px по краю
            if sc:
                c = [c[0] + (br - c[0]) * sc, c[1] + (bg_ - c[1]) * sc, c[2] + (bb - c[2]) * sc]
            if inner:  # в тёмной теме — светлый внутренний кант
                ic = max(0.0, 1 - abs(d + 1.5)) * inner[3]
                c = [c[k] + (inner[k] - c[k]) * ic for k in range(3)]
            if cov < 1:  # сглаживание: смешиваем с тем, что за окном
                j = (y * w + x) * 4
                behind = (full[j + 2], full[j + 1], full[j])
                c = [behind[k] + (c[k] - behind[k]) * cov for k in range(3)]
            px = bytes(min(255, max(0, int(v + 0.5))) for v in c)
            out[i:i + 3] = b"\x01\x02\x04" if px == b"\x01\x02\x03" else px
    return out


# ---------- тень ----------

def _box_profile(n, m, size, sigma, shift):
    k = 1 / (sigma * math.sqrt(2))
    return [0.5 * (math.erf((i + 0.5 - m - shift) * k) - math.erf((i + 0.5 - m - shift - size) * k)) for i in range(n)]


class Shadow:
    """Мягкая тень: layered-окно с попиксельной альфой, сквозь него проходят клики."""

    def __init__(self, layers):
        ex = 0x80000 | 0x20 | 0x80 | 0x08000000 | 0x8  # LAYERED|TRANSPARENT|TOOLWINDOW|NOACTIVATE|TOPMOST
        self.hwnd = CreateWindowExW(ex, "Static", None, 0x80000000, 0, 0, 1, 1, None, None, None, None)
        self.layers = layers  # [(sigma, alpha, dy)] уже в физических пикселях
        self.cache = None

    def place(self, x, y, w, h, below):
        m = int(max(3 * s + abs(dy) for s, _, dy in self.layers)) + 2
        W, H = w + 2 * m, h + 2 * m
        if not self.cache or self.cache[0] != (w, h):
            buf = bytearray(W * H * 4)
            profs = [(_box_profile(W, m, w, s, 0), _box_profile(H, m, h, s, dy), a) for s, a, dy in self.layers]
            for Y in range(H):
                ks = [(fx, a * fy[Y] * 255) for fx, fy, a in profs]
                if len(ks) == 2:
                    (f1, k1), (f2, k2) = ks
                    row = bytes(min(255, int(k1 * a + k2 * b)) for a, b in zip(f1, f2))
                else:
                    row = bytes(min(255, int(sum(k * f[i] for f, k in ks))) for i in range(W))
                buf[Y * W * 4 + 3:(Y + 1) * W * 4:4] = row
            self.cache = ((w, h), bytes(buf))
        data = self.cache[1]
        sdc = GetDC(None)
        mdc = CreateCompatibleDC(sdc)
        bits = VP()
        bmp = CreateDIBSection(sdc, _bmi(W, H), 0, ctypes.byref(bits), None, 0)
        ctypes.memmove(bits, data, len(data))
        old = SelectObject(mdc, bmp)
        dst, size, src = wt.POINT(x - m, y - m), wt.SIZE(W, H), wt.POINT(0, 0)
        blend = (ctypes.c_ubyte * 4)(0, 0, 255, 1)  # AC_SRC_OVER, 255, AC_SRC_ALPHA
        UpdateLayeredWindow(self.hwnd, sdc, ctypes.addressof(dst), ctypes.addressof(size), mdc,
                            ctypes.addressof(src), 0, ctypes.addressof(blend), 2)
        SelectObject(mdc, old)
        DeleteObject(bmp)
        DeleteDC(mdc)
        ReleaseDC(None, sdc)
        # сразу под панелью, без активации
        SetWindowPos(self.hwnd, below, 0, 0, 0, 0, 0x1 | 0x2 | 0x10 | 0x40)

    def hide(self):
        ShowWindow(self.hwnd, 0)

    def destroy(self):
        DestroyWindow(self.hwnd)


# ---------- окно-панель ----------

def hwnd_of(win):
    return int(win.wm_frame(), 16)


def work_area():
    r = wt.RECT()
    SystemParametersInfoW(0x30, 0, ctypes.addressof(r), 0)
    return r.left, r.top, r.right, r.bottom


GetWindowThreadProcessId = _fn(_u32, "GetWindowThreadProcessId", UINT, VP, ctypes.POINTER(wt.DWORD))


def foreground_owner():
    """PID владельца активного окна (0 — активного окна нет, например в момент переключения)."""
    hwnd = GetForegroundWindow()
    if not hwnd:
        return 0
    pid = wt.DWORD()
    GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def to_foreground(win):
    win.deiconify()
    win.lift()
    SetForegroundWindow(hwnd_of(win))
    win.focus_force()


class Panel:
    """Окно без рамки: размытый фон под ним, скруглённые углы, мягкая тень."""

    def __init__(self, win, theme, radius, S):
        self.win, self.T, self.r, self.S = win, theme, radius * S, S
        win.withdraw()
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.attributes("-transparentcolor", KEY)
        win.configure(bg=KEY)
        self.canvas = tk.Canvas(win, bg=KEY, highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)
        self.bg_item = self.canvas.create_image(0, 0, anchor="nw")
        self.shadow = Shadow([(s * S, a, dy * S) for s, a, dy in theme.shadow])
        win.update_idletasks()
        self.hwnd = hwnd_of(win)
        pref = ctypes.c_int(1)  # DWMWCP_DONOTROUND — углы рисуем сами
        DwmSetWindowAttribute(self.hwnd, 33, ctypes.addressof(pref), 4)
        self.bg = None
        self.geom = None

    def place(self, x, y, w, h):
        shot = Shot(x, y, w, h, hide=(self.hwnd, self.shadow.hwnd))
        self.bg = photo(w, h, panel_pixels(shot, material(shot, self.T), self.r, self.T), alpha=False)
        self.canvas.itemconfigure(self.bg_item, image=self.bg)
        self.canvas.configure(width=w, height=h)
        self.win.geometry(f"{w}x{h}+{x}+{y}")
        self.geom = (x, y, w, h)
        if self.win.state() == "withdrawn":
            self.win.deiconify()
        self.win.update_idletasks()
        self.shadow.place(x, y, w, h, self.hwnd)

    def raise_(self):
        """Поднять над остальными окнами без активации (тень — сразу под панелью)."""
        self.win.lift()
        SetWindowPos(self.shadow.hwnd, self.hwnd, 0, 0, 0, 0, 0x1 | 0x2 | 0x10)

    def crop(self, x0, y0, x1, y1):
        img = tk.PhotoImage(width=x1 - x0, height=y1 - y0)
        img.tk.call(img, "copy", self.bg, "-from", x0, y0, x1, y1)
        return img

    def destroy(self):
        self.shadow.destroy()
        self.win.destroy()


# ---------- векторные фигуры (SDF: расстояние до края, < 0 внутри) ----------

def circle(cx, cy, r):
    return lambda x, y: math.hypot(x - cx, y - cy) - r


def rrect(x0, y0, x1, y1, r):
    cx, cy, hw, hh = (x0 + x1) / 2, (y0 + y1) / 2, (x1 - x0) / 2, (y1 - y0) / 2

    def f(x, y):
        qx, qy = abs(x - cx) - hw + r, abs(y - cy) - hh + r
        return math.hypot(max(qx, 0), max(qy, 0)) + min(max(qx, qy), 0) - r
    return f


def line(points, w):
    """Ломаная толщины w со скруглёнными концами."""
    segs = list(zip(points, points[1:]))

    def f(x, y):
        best = 1e9
        for (ax, ay), (bx, by) in segs:
            dx, dy = bx - ax, by - ay
            t = max(0.0, min(1.0, ((x - ax) * dx + (y - ay) * dy) / (dx * dx + dy * dy or 1)))
            best = min(best, math.hypot(x - ax - t * dx, y - ay - t * dy))
        return best - w / 2
    return f


def stroke(shape, w):
    return lambda x, y: abs(shape(x, y)) - w / 2


def union(*shapes):
    return lambda x, y: min(s(x, y) for s in shapes)


def cut(shape, hole):
    return lambda x, y: max(shape(x, y), -hole(x, y))


def arc(cx, cy, r, w, start, length):
    """Дуга кольца от угла start (доли оборота, 0 — сверху, по часовой) длиной length."""
    a0, a1 = start * 2 * math.pi, (start + length) * 2 * math.pi
    caps = [(cx + r * math.sin(a), cy - r * math.cos(a)) for a in (a0, a1)]

    def f(x, y):
        ang = math.atan2(x - cx, -(y - cy)) % (2 * math.pi)
        rel = (ang - a0) % (2 * math.pi)
        if rel <= a1 - a0:
            return abs(math.hypot(x - cx, y - cy) - r) - w / 2
        return min(math.hypot(x - px, y - py) for px, py in caps) - w / 2
    return f


def raster(w, h, layers, S):
    """Сглаженная отрисовка слоёв [(фигура, (r,g,b,a))] в PhotoImage с альфой."""
    W, H = max(1, round(w * S)), max(1, round(h * S))
    buf = bytearray(W * H * 4)
    i = 0
    for Y in range(H):
        y = (Y + 0.5) / S
        for X in range(W):
            x = (X + 0.5) / S
            R = G = B = A = 0.0
            for f, (lr, lg, lb, la) in layers:
                cov = 0.5 - f(x, y) * S
                if cov <= 0:
                    continue
                a = la * (cov if cov < 1 else 1)
                if a <= 0:
                    continue
                na = a + A * (1 - a)
                k = A * (1 - a)
                R, G, B = (lr * a + R * k) / na, (lg * a + G * k) / na, (lb * a + B * k) / na
                A = na
            buf[i:i + 4] = bytes((int(R), int(G), int(B), int(A * 255 + 0.5)))
            i += 4
    return photo(W, H, buf)


# ---------- иконки в духе SF Symbols ----------

def sym_magnifier(color, S, size=20):
    k = size / 20
    return raster(size, size, [(union(stroke(circle(8.4 * k, 8.4 * k, 6.1 * k), 1.8 * k),
                                      line([(12.9 * k, 12.9 * k), (17.6 * k, 17.6 * k)], 2.3 * k)), color)], S)


def sym_folder(color, S, size=20, fill=None):
    """Папка; fill — полупрозрачная заливка того же цвета (для цветных папок)."""
    k = size / 20
    body = union(rrect(2 * k, 5.2 * k, 18 * k, 16.4 * k, 2.4 * k), rrect(2 * k, 3.4 * k, 9 * k, 8 * k, 1.8 * k))
    layers = [(body, color[:3] + (fill,))] if fill else []
    layers.append((union(stroke(body, 1.5 * k), line([(2.6 * k, 7.6 * k), (17.4 * k, 7.6 * k)], 1.3 * k)), color))
    return raster(size, size, layers, S)


def sym_dots(color, S, size=16):
    k = size / 16
    return raster(size, size, [(union(*(circle(x * k, 8 * k, 1.6 * k) for x in (3.5, 8, 12.5))), color)], S)


def sym_plus(color, S, size=12):
    k = size / 12
    return raster(size, size, [(union(line([(6 * k, 2 * k), (6 * k, 10 * k)], 1.6 * k),
                                      line([(2 * k, 6 * k), (10 * k, 6 * k)], 1.6 * k)), color)], S)


def dot(color, S, size=10):
    """Цветная метка как в Finder; без цвета — пустой кружок."""
    c = size / 2
    if color is None:
        return raster(size, size, [(stroke(circle(c, c, c - 1), 1.2), (142, 142, 147, 1))], S)
    return raster(size, size, [(circle(c, c, c - 0.5), color)], S)


def swatch(color, S, selected, size=22):
    """Кружок выбора цвета; выбранный — с кольцом вокруг. color=None — «без цвета»."""
    c = size / 2
    gray = (142, 142, 147, 1)
    if color is None:
        layers = [(union(stroke(circle(c, c, 6.5), 1.3), line([(c - 4.4, c + 4.4), (c + 4.4, c - 4.4)], 1.3)), gray)]
    else:
        layers = [(circle(c, c, 7.5 if selected else 8.5), color)]
    if selected:
        layers.append((stroke(circle(c, c, c - 1.2), 2), color or gray))
    return raster(size, size, layers, S)


def sym_xmark_circle(color, S, size=18):
    c, k = size / 2, size / 18
    x = union(line([(c - 3 * k, c - 3 * k), (c + 3 * k, c + 3 * k)], 1.8 * k),
              line([(c + 3 * k, c - 3 * k), (c - 3 * k, c + 3 * k)], 1.8 * k))
    return raster(size, size, [(cut(circle(c, c, size / 2 - 0.5), x), color)], S)


def sym_check_circle(color, S, size=22):
    c, k = size / 2, size / 22
    tick = line([(c - 4.6 * k, c + 0.3 * k), (c - 1.4 * k, c + 3.4 * k), (c + 4.6 * k, c - 3.4 * k)], 2.2 * k)
    return raster(size, size, [(cut(circle(c, c, size / 2 - 0.5), tick), color)], S)


def sym_exclamation_circle(color, S, size=22):
    c, k = size / 2, size / 22
    mark = union(line([(c, c - 5.4 * k), (c, c + 1.4 * k)], 2.4 * k), circle(c, c + 5 * k, 1.4 * k))
    return raster(size, size, [(cut(circle(c, c, size / 2 - 0.5), mark), color)], S)


def sym_expand(color, S, size=14):
    k = size / 14
    return raster(size, size, [(union(line([(3 * k, 3 * k), (11 * k, 11 * k)], 1.5 * k),
                                      line([(3 * k, 7.2 * k), (3 * k, 3 * k), (7.2 * k, 3 * k)], 1.5 * k),
                                      line([(6.8 * k, 11 * k), (11 * k, 11 * k), (11 * k, 6.8 * k)], 1.5 * k)),
                                color)], S)


def sym_xmark(color, S, size=12):
    k = size / 12
    return raster(size, size, [(union(line([(3 * k, 3 * k), (9 * k, 9 * k)], 1.6 * k),
                                      line([(9 * k, 3 * k), (3 * k, 9 * k)], 1.6 * k)), color)], S)


def sym_check(color, S, size=12):
    k = size / 12
    return raster(size, size, [(line([(2.2 * k, 6.4 * k), (4.8 * k, 9 * k), (9.8 * k, 3 * k)], 1.7 * k), color)], S)


def ring(size, width, track, color, S, value=None, start=0.0, length=0.28):
    """Кольцо прогресса (value 0..1) или крутилка (start/length)."""
    c, r = size / 2, size / 2 - width / 2 - 0.5
    layers = [(stroke(circle(c, c, r), width), track)]
    if value is None:
        layers.append((arc(c, c, r, width, start, length), color))
    elif value > 0.005:
        layers.append((arc(c, c, r, width, 0, min(value, 0.999)), color))
    return raster(size, size, layers, S)


def sym_chevron_down(color, S, size=10):
    k = size / 10
    return raster(size, size, [(line([(2.2 * k, 3.7 * k), (5 * k, 6.5 * k), (7.8 * k, 3.7 * k)], 1.4 * k), color)], S)


def plate(w, h, r, color, S):
    """Скруглённая полупрозрачная подложка (наведение, подсветка пункта меню)."""
    return raster(w, h, [(rrect(0, 0, w, h, r), color)], S)


def solid(w, h, color):
    """Прямоугольник сплошного цвета с альфой — быстро, без SDF."""
    r, g, b, a = color
    return photo(w, h, bytes((r, g, b, int(a * 255))) * (w * h))


# ---------- поле ввода ----------

class TextField:
    """Однострочное поле на фоне панели: курсор, выделение, вставка, горизонтальная прокрутка."""

    VK_A, VK_C, VK_V, VK_X = 65, 67, 86, 88  # клавиши по коду, чтобы Ctrl+V работал и в русской раскладке

    def __init__(self, parent, theme, font, w, h, placeholder):
        self.T, self.font, self.w, self.h = theme, font, w, h
        self.c = tk.Canvas(parent, width=w, height=h, bg=KEY, highlightthickness=0, bd=0,
                           insertbackground=theme.accent, insertwidth=2, insertontime=600,
                           insertofftime=500, selectbackground=theme.selection, selectborderwidth=0,
                           selectforeground=theme.label, cursor="xterm")
        self.bg_item = self.c.create_image(0, 0, anchor="nw")
        self.item = self.c.create_text(0, h / 2, anchor="w", text="", font=font, fill=theme.label)
        self.ph = self.c.create_text(0, h / 2, anchor="w", text=placeholder, font=font, fill=theme.placeholder)
        self.offset, self.anchor, self.readonly = 0, 0, False
        self.bg_img = ""
        self.on_change = None
        self.c.bind("<Key>", self.key)
        self.c.bind("<Button-1>", self.click)
        self.c.bind("<B1-Motion>", self.drag)
        self.c.bind("<Double-Button-1>", lambda e: self.select_all())
        self.c.bind("<FocusIn>", lambda e: self.c.focus(self.item))

    # --- состояние ---
    def get(self):
        return self.c.itemcget(self.item, "text")

    def set(self, text):
        self.c.itemconfigure(self.item, text=text)
        self.c.icursor(self.item, "end")
        self.c.select_clear()
        self.changed()

    def set_bg(self, img):
        self.bg_img = img
        self.c.itemconfigure(self.bg_item, image=img)

    def set_readonly(self, ro):
        self.readonly = ro
        self.c.itemconfigure(self.item, fill=self.T.secondary if ro else self.T.label)
        self.c.configure(cursor="arrow" if ro else "xterm")
        if ro:
            self.c.select_clear()
            self.c.focus("")
            self.show_start()
        else:
            self.c.focus(self.item)
        self.repaint()

    def show_start(self):
        """Показать начало текста (ссылку удобнее читать с https://)."""
        self.offset = 0
        self.c.coords(self.item, 0, self.h / 2)

    def focus(self):
        self.c.focus_set()
        if not self.readonly:
            self.c.focus(self.item)

    def select_all(self):
        if self.get():
            self.c.select_from(self.item, 0)
            self.c.select_to(self.item, "end")
            self.anchor = 0
        self.c.icursor(self.item, 0)  # ввод всё равно заменит выделенное
        self.show_start()
        self.repaint()

    # --- правка ---
    def sel(self):
        try:
            return self.c.index(self.item, "sel.first"), self.c.index(self.item, "sel.last") + 1
        except tk.TclError:
            return None

    def delete_sel(self):
        s = self.sel()
        if s:
            self.c.dchars(self.item, s[0], s[1] - 1)
            self.c.icursor(self.item, s[0])
            self.c.select_clear()
        return bool(s)

    def insert(self, text):
        self.delete_sel()
        self.c.insert(self.item, "insert", text)

    def key(self, e):
        ctrl = e.state & 0x4
        ks = e.keysym
        if ks in ("Return", "KP_Enter", "Escape", "Tab", "Up", "Down"):
            return None  # обрабатывает приложение
        if ctrl and e.keycode in (self.VK_A, self.VK_C):
            if e.keycode == self.VK_A:
                self.select_all()
            else:
                self.copy()
            return "break"
        if self.readonly:
            return "break"
        idx = self.c.index(self.item, "insert")
        shift = e.state & 0x1
        if ctrl and e.keycode == self.VK_V:
            try:
                clip = self.c.clipboard_get().replace("\r", "").replace("\n", "")
            except tk.TclError:
                clip = ""
            self.insert(clip)
        elif ctrl and e.keycode == self.VK_X:
            self.copy()
            self.delete_sel()
        elif ks == "BackSpace":
            if not self.delete_sel() and idx > 0:
                self.c.dchars(self.item, idx - 1)
        elif ks == "Delete":
            if not self.delete_sel():
                self.c.dchars(self.item, idx)
        elif ks in ("Left", "Right", "Home", "End"):
            n = len(self.get())
            new = {"Left": idx - 1, "Right": idx + 1, "Home": 0, "End": n}[ks]
            s = self.sel()
            if not shift and s and ks in ("Left", "Right"):
                new = s[0] if ks == "Left" else s[1]
            new = max(0, min(n, new))
            if shift:
                if not s:
                    self.anchor = idx
                self.select_range(self.anchor, new)
            else:
                self.c.select_clear()
            self.c.icursor(self.item, new)
        elif e.char and e.char.isprintable() and not ctrl:
            self.insert(e.char)
        else:
            return "break"
        self.changed()
        return "break"

    def copy(self):
        s = self.sel()
        text = self.get()[s[0]:s[1]] if s else ""
        if text:
            self.c.clipboard_clear()
            self.c.clipboard_append(text)

    def select_range(self, a, b):
        if a == b:
            self.c.select_clear()
            return
        lo, hi = min(a, b), max(a, b)
        self.c.select_from(self.item, lo)
        self.c.select_to(self.item, hi - 1)

    def click(self, e):
        self.c.focus_set()
        if self.readonly:
            return
        self.c.focus(self.item)
        i = self.index_at(e.x)
        self.c.icursor(self.item, i)
        self.c.select_clear()
        self.anchor = i
        self.repaint()

    def drag(self, e):
        if self.readonly:
            return
        i = self.index_at(e.x)
        self.select_range(self.anchor, i)
        self.c.icursor(self.item, i)
        self.scroll()
        self.repaint()

    def index_at(self, x):
        text = self.get()
        x += self.offset
        best, prev = len(text), 0
        for i in range(1, len(text) + 1):
            cur = self.font.measure(text[:i])
            if cur >= x:
                best = i if cur - x < x - prev else i - 1
                break
            prev = cur
        return best

    def changed(self):
        empty = not self.get()
        self.c.itemconfigure(self.ph, state="normal" if empty else "hidden")
        self.scroll()
        self.repaint()
        if self.on_change:
            self.on_change()

    def repaint(self):
        """Перерисовать поле целиком: Tk обновляет только рамку текста и оставляет
        полоски от подсветки выделения сверху и снизу."""
        self.c.itemconfigure(self.bg_item, image=self.bg_img)

    def scroll(self):
        text = self.get()
        idx = self.c.index(self.item, "insert")
        cur = self.font.measure(text[:idx])
        total = self.font.measure(text)
        room = self.w - 4
        if cur - self.offset > room:
            self.offset = cur - room
        elif cur - self.offset < 0:
            self.offset = cur
        if total - self.offset < room:
            self.offset = max(0, total - room)
        self.c.coords(self.item, -self.offset, self.h / 2)
