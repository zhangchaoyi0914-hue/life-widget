# -*- coding: utf-8 -*-
"""Life Widget for Windows —— 桌面时间进度小组件.

美术风格复刻 Life Widget(lifewidget.app)的 iOS 主屏幕小组件:
圆角卡片上竖向堆叠若干进度块, 同一进度有多种表现形式(点阵 / 日历 / 圆环 /
进度条 / 微笑弧线 / 100 格 / 圆环组合), 单击进度块即可循环切换.
所有圆点/圆环/弧线/进度条均用 Pillow 3 倍超采样渲染, 边缘平滑无锯齿.

操作:
  左键按住拖动   = 移动位置(自动记住)
  单击某个进度块 = 切换该块的表现形式
  右键           = 菜单(设置 / 置顶 / 开机自启 / 退出)
  Esc            = 退出
配置保存在同目录 config.json.
"""

import calendar
import ctypes
import json
import math
import os
import sys
import time
import tkinter as tk
import tkinter.font as tkfont
from datetime import date, datetime, timedelta
from tkinter import messagebox

from PIL import Image, ImageDraw, ImageTk

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SCRIPT_PATH = os.path.abspath(__file__)
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
APP_NAME = "LifeWidget"

DEFAULT_CONFIG = {
    "birth_date": "1990-01-01",   # 出生日期 YYYY-MM-DD
    "lifespan": 80,               # 预期寿命(年)
    "always_on_top": True,
    "autostart": False,
    "position": None,             # 窗口位置 [x, y]
    "configured": False,          # 是否完成过首次设置
    "views": {"today": True, "week": True, "month": True,
              "year": True, "life": True, "rings": False},  # 显示哪些进度块
    "gran": {},                   # 每个进度块当前的表现形式下标
    "theme": "light",             # light=浅色 / dark=深色
    "dot_shape": "circle",        # circle=圆形点 / square=方形点
}

# ---- 主题 ----
THEMES = {
    "light": dict(
        card="#ffffff", border="#e0e0e0",
        title="#3a3a3c", frac="#8e8e93",
        passed="#1c1c1e", current="#f5a01c", future="#e5e5ea",
        field="#f0f0f2", accent="#f5a01c",
    ),
    "dark": dict(
        card="#050505", border="#1f1f23",
        title="#f5f5f5", frac="#636366",
        passed="#f2f2f2", current="#f5a01c", future="#2e2e30",
        field="#232326", accent="#f5a01c",
    ),
}

KEY_COLOR = "#010101"   # 透明色键(此颜色会被抠掉)

# ---- 布局(逻辑像素, 运行时乘以 DPI 缩放) ----
WIN_W = 340
PAD = 22
TOP_PAD = 18
BOT_PAD = 16
CAPTION_H = 18          # 标题行高
CAPTION_GAP = 8         # 标题行与内容间距
BLOCK_GAP = 18          # 进度块之间的间距
MAX_CELL = 26           # 点阵格子边长上限
DOT_RATIO = 0.32        # 圆点直径 / 格子边长
SQUARE_RADIUS = 0.45    # 方形点的圆角半径(占边长的比例)
CARD_RADIUS = 14
WEEKDAY_H = 16          # 日历式星期表头行高
BIG_TEXT_H = 58         # 大文字(周六)高度
RING_D = 96             # 单圆环直径
RING_ROW_D = 62         # 圆环组合中单环直径
ARC_H = 54              # 微笑弧线高度
BAR_TITLE_H = 30
BAR_H = 14

SS = 3                  # 超采样倍数(抗锯齿)
CASCADE_LEVELS = 10     # 级联动画的离散化级数
BREATHE_STEP = 0.04     # 呼吸动画的尺寸量化步长

SCALE = 1.0


def S(value):
    return int(round(value * SCALE))


def init_dpi():
    """开启 Per-Monitor DPI 感知, 窗口在任何屏幕上都不被系统拉伸模糊."""
    global SCALE
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(-4)  # PER_MONITOR_AWARE_V2
    except Exception:
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            try:
                ctypes.windll.shcore.SetProcessDpiAwareness(1)
            except Exception:
                pass
    try:
        SCALE = ctypes.windll.user32.GetDpiForSystem() / 96.0
    except Exception:
        SCALE = 1.0


# ---- 视图与形式 ----
VIEW_DEFS = [
    ("today", "今天"),
    ("week", "本周"),
    ("month", "本月"),
    ("year", "今年"),
    ("life", "人生"),
    ("rings", "圆环组合"),
]
# 每个进度块的表现形式(单击循环切换)
FORMS = {
    "today": ["dots24", "dots60", "clock_ring", "bar"],
    "week": ["dots7", "dots168", "big_days", "bar"],
    "month": ["dots", "calendar", "bar"],
    "year": ["weeks", "days", "bar"],
    "life": ["years", "months", "hundred", "ring", "smile", "bar"],
}


# ---- 进度计算 ----

def day_fraction(now):
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return (now - start) / timedelta(days=1)


def life_fraction(now, birth, lifespan):
    start = datetime(birth.year, birth.month, birth.day)
    try:
        end = datetime(birth.year + lifespan, birth.month, birth.day)
    except ValueError:  # 2 月 29 日出生, 目标年不是闰年
        end = datetime(birth.year + lifespan, 3, 1)
    if end <= start:
        return 1.0
    return (now - start) / (end - start)


def life_units(now, birth):
    """返回 (周岁年龄, 上次生日以来满几个月)."""
    age = now.year - birth.year - ((now.month, now.day) < (birth.month, birth.day))
    months = (now.year - birth.year) * 12 + now.month - birth.month
    if now.day < birth.day:
        months -= 1
    return age, max(0, months - age * 12)


def parse_birth(text):
    try:
        return datetime.strptime(text.strip(), "%Y-%m-%d")
    except (ValueError, AttributeError):
        return None


def view_percent(key, now, birth, lifespan):
    """各进度的百分比(0-100), 用于圆环/进度条/弧线形式."""
    if key == "today":
        return day_fraction(now) * 100
    if key == "week":
        return (now.weekday() + 1) / 7.0 * 100
    if key == "month":
        days = calendar.monthrange(now.year, now.month)[1]
        return now.day / days * 100
    if key == "year":
        days = 366 if calendar.isleap(now.year) else 365
        return now.timetuple().tm_yday / days * 100
    if key == "life":
        return max(0.0, min(1.0, life_fraction(now, birth, lifespan))) * 100
    raise KeyError(key)


def build_view_state(key, form, now, birth, lifespan):
    """计算一个进度块在某种形式下的展示状态."""
    if key == "today":
        if form == "dots24":
            return dict(kind="dots", caption="今日", cols=8, total=24,
                        passed=now.hour, current=now.hour,
                        frac="{}/24 小时".format(now.hour + 1))
        if form == "dots60":
            return dict(kind="dots", caption="今日", cols=12, total=60,
                        passed=now.minute, current=now.minute,
                        frac="本小时 {}/60 分钟".format(now.minute + 1))
        if form == "clock_ring":
            return dict(kind="ring", pct=view_percent(key, now, birth, lifespan),
                        center1=now.strftime("%H:%M"), center2="")
        return dict(kind="bar", title="今日",
                    pct=view_percent(key, now, birth, lifespan),
                    frac="{}/24 小时".format(now.hour + 1))

    if key == "week":
        wd = now.weekday()  # 周一 = 0
        caption = "周" + "一二三四五六日"[wd]
        if form == "dots7":
            return dict(kind="dots", caption=caption, cols=7, total=7,
                        passed=wd, current=wd,
                        frac="{}/7 天".format(wd + 1))
        if form == "dots168":
            h = wd * 24 + now.hour
            return dict(kind="dots", caption=caption, cols=21, total=168,
                        passed=h, current=h,
                        frac="{}/168 小时".format(h + 1))
        if form == "big_days":
            return dict(kind="big_days", text=caption, total=7,
                        passed=wd, current=wd)
        return dict(kind="bar", title=caption,
                    pct=view_percent(key, now, birth, lifespan),
                    frac="{}/7 天".format(wd + 1))

    if key == "month":
        days = calendar.monthrange(now.year, now.month)[1]
        if form == "dots":
            return dict(kind="dots", caption="{}月{}日".format(now.month, now.day),
                        cols=7, total=days,
                        passed=now.day - 1, current=now.day - 1,
                        frac="{}/{} 天".format(now.day, days))
        if form == "calendar":
            # 周日开头的日历: 计算 1 号前空几格
            offset = (date(now.year, now.month, 1).weekday() + 1) % 7
            return dict(kind="calendar", caption="{}月".format(now.month),
                        offset=offset, days=days, today=now.day,
                        frac="{}/{}".format(now.day, days))
        return dict(kind="bar", title="{}月{}日".format(now.month, now.day),
                    pct=view_percent(key, now, birth, lifespan),
                    frac="{}/{} 天".format(now.day, days))

    if key == "year":
        days = 366 if calendar.isleap(now.year) else 365
        doy = now.timetuple().tm_yday
        if form == "weeks":
            iso_weeks = date(now.year, 12, 28).isocalendar()[1]
            wk = now.isocalendar()[1]
            return dict(kind="dots", caption=str(now.year), cols=9, total=iso_weeks,
                        passed=wk - 1, current=wk - 1,
                        frac="{}/{} 周".format(wk, iso_weeks))
        if form == "days":
            return dict(kind="dots", caption=str(now.year), cols=19, total=days,
                        passed=doy - 1, current=doy - 1,
                        frac="{}/{} 天".format(doy, days))
        return dict(kind="bar", title=str(now.year),
                    pct=view_percent(key, now, birth, lifespan),
                    frac="{}/{} 天".format(doy, days))

    if key == "life":
        age, months = life_units(now, birth)
        pct = view_percent(key, now, birth, lifespan)
        if form == "years":
            return dict(kind="dots", caption="Life", cols=10, total=lifespan,
                        passed=age, current=min(age, lifespan - 1),
                        frac="{}/{} 年".format(age, lifespan))
        if form == "months":
            m = age * 12 + months
            return dict(kind="dots", caption="Life", cols=24, total=lifespan * 12,
                        passed=m, current=min(m, lifespan * 12 - 1),
                        frac="{}/{} 月".format(m, lifespan * 12))
        if form == "hundred":  # 每格 = 人生的 1%
            filled = min(int(pct), 99)
            return dict(kind="dots", caption="Life", cols=10, total=100,
                        passed=filled, current=filled,
                        frac="{}/{}".format(age, lifespan))
        if form == "ring":
            return dict(kind="ring", pct=pct,
                        center1="Life", center2="{:.1f}%".format(pct))
        if form == "smile":
            return dict(kind="smile", pct=pct, caption="Life")
        return dict(kind="bar", title="Life", pct=pct,
                    frac="{}/{} 年".format(age, lifespan))

    raise KeyError(key)


def build_rings_state(now, birth, lifespan):
    """圆环组合: 今日/本周/本月/今年四个环."""
    items = [
        ("今日", view_percent("today", now, birth, lifespan)),
        ("周" + "一二三四五六日"[now.weekday()], view_percent("week", now, birth, lifespan)),
        ("{}月".format(now.month), view_percent("month", now, birth, lifespan)),
        (str(now.year), view_percent("year", now, birth, lifespan)),
    ]
    return dict(kind="rings", items=items)


# ---- 配置读写 ----

def load_config():
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))  # 深拷贝
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        for k, v in data.items():
            if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                cfg[k].update(v)
            else:
                cfg[k] = v
    except (OSError, ValueError):
        pass
    return cfg


def save_config(cfg):
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


def set_autostart(enable):
    """通过注册表 Run 键设置/取消开机自启."""
    import winreg
    exe = sys.executable
    if os.path.basename(exe).lower() == "python.exe":
        candidate = os.path.join(os.path.dirname(exe), "pythonw.exe")
        if os.path.exists(candidate):
            exe = candidate  # 用 pythonw 避免弹出控制台窗口
    cmd = '"{}" "{}"'.format(exe, SCRIPT_PATH)
    try:
        with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run",
                0, winreg.KEY_SET_VALUE) as key:
            if enable:
                winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, cmd)
            else:
                try:
                    winreg.DeleteValue(key, APP_NAME)
                except FileNotFoundError:
                    pass
        return True
    except OSError:
        return False


# ---- 绘图小工具 ----

def rounded_points(x1, y1, x2, y2, r):
    r = max(0, min(r, (x2 - x1) / 2.0, (y2 - y1) / 2.0))
    return [
        x1 + r, y1, x2 - r, y1, x2, y1,
        x2, y1 + r, x2, y2 - r, x2, y2,
        x2 - r, y2, x1 + r, y2, x1, y2,
        x1, y2 - r, x1, y1 + r, x1, y1,
    ]


def _rgb(hex_color):
    hex_color = hex_color.lstrip("#")
    return tuple(int(hex_color[i:i + 2], 16) for i in (0, 2, 4))


def blend(c1, c2, t):
    a, b = _rgb(c1), _rgb(c2)
    return "#{:02x}{:02x}{:02x}".format(
        *(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3)))


def smoothstep(t):
    return t * t * (3 - 2 * t)


# ---- 抗锯齿精灵图(Pillow 超采样渲染 + 缓存) ----
_SPRITES = {}


def _make_sprite(key, w, h, draw_fn):
    """以 SS 倍尺寸绘制后缩小, 得到抗锯齿的 PhotoImage 并缓存."""
    key = (key, int(round(w)), int(round(h)))
    photo = _SPRITES.get(key)
    if photo is not None:
        return photo
    w, h = max(1, int(round(w))), max(1, int(round(h)))
    big = Image.new("RGBA", (w * SS, h * SS), (0, 0, 0, 0))
    draw_fn(ImageDraw.Draw(big), SS)
    photo = ImageTk.PhotoImage(big.resize((w, h), Image.LANCZOS))
    _SPRITES[key] = photo
    return photo


def dot_sprite(shape, color, d):
    """圆点: shape=circle/square, d=直径(像素)."""
    pad = 2
    size = int(round(d)) + pad * 2

    def draw(dr, k):
        x0, y0 = pad * k, pad * k
        x1, y1 = x0 + d * k, y0 + d * k
        if shape == "square":
            dr.rounded_rectangle((x0, y0, x1, y1),
                                 radius=d * k * SQUARE_RADIUS, fill=color)
        else:
            dr.ellipse((x0, y0, x1, y1), fill=color)

    return _make_sprite(("dot", shape, color, int(round(d))), size, size, draw)


def ring_sprite(d, width, track, color, frac):
    """圆环: 灰色底环 + 从顶部顺时针的彩色弧. frac 按 1° 量化."""
    pad = 2
    size = int(round(d)) + pad * 2
    deg = int(round(frac * 360))

    def draw(dr, k):
        c = size * k / 2
        r = d * k / 2
        bb = (c - r, c - r, c + r, c + r)
        dr.ellipse(bb, outline=track, width=int(width * k))
        if deg > 0:
            dr.arc(bb, -90, -90 + deg, fill=color, width=int(width * k))

    return _make_sprite(("ring", track, color, deg, int(round(d)), width),
                        size, size, draw)


def smile_sprite(w, ry, lw, track, color, frac):
    """微笑弧线: 椭圆底部的一段弧. frac 按 1° 量化."""
    pad = 2
    w, hh = int(round(w)) + pad * 2, int(round(ry * 2)) + pad * 2
    deg = int(round(frac * 150))

    def draw(dr, k):
        bb = (pad * k, pad * k, (w - pad) * k, (hh - pad) * k)
        dr.arc(bb, 15, 165, fill=track, width=int(lw * k))
        if deg > 0:
            dr.arc(bb, 165 - deg, 165, fill=color, width=int(lw * k))

    return _make_sprite(("smile", track, color, deg, w, hh, lw), w, hh, draw)


def bar_sprite(w, h, track, fill, frac):
    """进度条: 圆角底槽 + 圆角填充. frac 按 0.5% 量化."""
    w, h = int(round(w)), int(round(h))
    q = int(round(frac * 200))

    def draw(dr, k):
        dr.rounded_rectangle((0, 0, w * k - 1, h * k - 1),
                             radius=h * k / 2, fill=track)
        fw = max(h, int(w * q / 200.0))
        dr.rounded_rectangle((0, 0, fw * k - 1, h * k - 1),
                             radius=h * k / 2, fill=fill)

    return _make_sprite(("bar", track, fill, q, w, h), w, h, draw)


class SettingsDialog(tk.Toplevel):
    """设置窗口(自绘界面, 跟随主题): 出生日期 / 预期寿命 / 显示进度 / 背景 / 圆点形状."""

    DLG_W = 320
    DLG_H = 540

    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.theme = app._theme()
        cfg = app.config_data

        self.overrideredirect(True)                      # 无边框
        self.configure(bg=KEY_COLOR)
        self.attributes("-transparentcolor", KEY_COLOR)
        self.attributes("-topmost", True)

        self.f_title = tkfont.Font(family="Microsoft YaHei UI", size=-S(14), weight="bold")
        self.f_sec = tkfont.Font(family="Microsoft YaHei UI", size=-S(9))
        self.f_label = tkfont.Font(family="Microsoft YaHei UI", size=-S(11))
        self.f_entry = tkfont.Font(family="Microsoft YaHei UI", size=-S(11))
        self.f_btn = tkfont.Font(family="Microsoft YaHei UI", size=-S(11), weight="bold")

        # 界面状态(保存时才写回配置)
        self._views = {k: bool(cfg["views"].get(
            k, DEFAULT_CONFIG["views"].get(k, True))) for k, _ in VIEW_DEFS}
        self._seg = {"theme": cfg.get("theme", "light"),
                     "dot_shape": cfg.get("dot_shape", "circle")}
        self._seg_opts = {}
        self._tog = {}
        self.birth_var = tk.StringVar(value=str(cfg["birth_date"]))
        self.lifespan_var = tk.StringVar(value=str(cfg["lifespan"]))

        w, h = S(self.DLG_W), S(self.DLG_H)
        self.canvas = tk.Canvas(self, width=w, height=h, bg=KEY_COLOR,
                                highlightthickness=0, bd=0)
        self.canvas.pack()
        self._build_ui()

        # 位置: 显示在小组件旁边
        x = app.winfo_x() - w - S(12)
        y = app.winfo_y()
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        if x < 0:
            x = min(app.winfo_x() + app.winfo_width() + S(12), sw - w)
        self.geometry("+{}+{}".format(max(0, x), max(0, min(y, sh - h))))

        self.canvas.bind("<ButtonPress-1>", self._drag_start)
        self.canvas.bind("<B1-Motion>", self._drag_move)
        self.bind("<Escape>", lambda e: self.destroy())
        self.grab_set()
        self.after(150, self.focus_force)

    # ---- 界面绘制 ----

    def _build_ui(self):
        cv = self.canvas
        th = self.theme
        pad = S(22)
        w = S(self.DLG_W)

        cv.create_polygon(rounded_points(1, 1, w - 2, S(self.DLG_H) - 2,
                                         S(CARD_RADIUS)),
                          smooth=True, fill=th["card"], outline=th["border"])
        cv.create_text(pad, S(18), anchor="nw", text="设置",
                       font=self.f_title, fill=th["title"])
        cv.create_text(w - pad, S(18), anchor="ne", text="×",
                       font=self.f_title, fill=th["frac"], tags=("close",))
        cv.tag_bind("close", "<Button-1>", lambda e: self.destroy())

        y = S(58)
        y = self._section(y, "基本信息")
        y = self._entry_row(y, "出生日期 (YYYY-MM-DD)", self.birth_var)
        y = self._entry_row(y, "预期寿命 (年)", self.lifespan_var)
        y = self._section(y + S(6), "显示进度")
        y = self._toggles_grid(y)
        y = self._section(y + S(6), "外观")
        y = self._seg_row(y, "背景模式", "theme",
                          [("light", "浅色"), ("dark", "深色")])
        y = self._seg_row(y, "圆点形状", "dot_shape",
                          [("circle", "圆形"), ("square", "方形")])

        bw, bh = S(88), S(32)
        bx, by = w - pad - bw, S(self.DLG_H) - S(18) - bh
        cv.create_polygon(rounded_points(bx, by, bx + bw, by + bh, bh / 2),
                          smooth=True, fill=th["accent"], outline="", tags=("save",))
        cv.create_text(bx + bw / 2, by + bh / 2, text="保存",
                       font=self.f_btn, fill="#ffffff", tags=("save",))
        cv.tag_bind("save", "<Button-1>", lambda e: self.save())

    def _section(self, y, text):
        self.canvas.create_text(S(22), y, anchor="nw", text=text,
                                font=self.f_sec, fill=self.theme["frac"])
        return y + S(20)

    def _entry_row(self, y, label, var):
        th = self.theme
        pad = S(22)
        ew = S(self.DLG_W) - 2 * pad
        self.canvas.create_text(pad, y, anchor="nw", text=label,
                                font=self.f_label, fill=th["title"])
        y += S(22)
        eh = S(28)
        self.canvas.create_polygon(rounded_points(pad, y, pad + ew, y + eh, S(8)),
                                   smooth=True, fill=th["field"], outline="")
        entry = tk.Entry(self.canvas, textvariable=var, font=self.f_entry,
                         relief="flat", bd=0, bg=th["field"], fg=th["title"],
                         insertbackground=th["title"], highlightthickness=0)
        self.canvas.create_window(pad + ew / 2, y + eh / 2, window=entry,
                                  width=ew - S(16), height=eh - S(8))
        return y + eh + S(12)

    def _toggles_grid(self, y):
        th = self.theme
        pad = S(22)
        col_w = (S(self.DLG_W) - 2 * pad) // 2
        for i, (key, name) in enumerate(VIEW_DEFS):
            row, col = i // 2, i % 2
            x = pad + col * col_w
            yy = y + row * S(30)
            self.canvas.create_text(x, yy + S(10), anchor="w", text=name,
                                    font=self.f_label, fill=th["title"])
            self._toggle_draw(key, x + col_w - S(38), yy)
        return y + 3 * S(30) + S(6)

    def _toggle_draw(self, key, x, y):
        th = self.theme
        on = self._views[key]
        w, h = S(34), S(20)
        pill = self.canvas.create_polygon(
            rounded_points(x, y, x + w, y + h, h / 2), smooth=True,
            fill=th["accent"] if on else th["future"], outline="",
            tags=("tg:" + key,))
        kx = x + w - h / 2 - 1 if on else x + h / 2 + 1
        knob = self.canvas.create_image(
            kx, y + h / 2, image=dot_sprite("circle", "#ffffff", h - S(6)),
            anchor="center", tags=("tg:" + key,))
        self._tog[key] = (pill, knob, x, y, w, h)
        self.canvas.tag_bind("tg:" + key, "<Button-1>",
                             lambda e, k=key: self._toggle_flip(k))

    def _toggle_flip(self, key):
        self._views[key] = not self._views[key]
        pill, knob, x, y, w, h = self._tog[key]
        on = self._views[key]
        self.canvas.itemconfigure(
            pill, fill=self.theme["accent"] if on else self.theme["future"])
        kx = x + w - h / 2 - 1 if on else x + h / 2 + 1
        self.canvas.coords(knob, kx, y + h / 2)

    def _seg_row(self, y, label, key, options):
        pad = S(22)
        self.canvas.create_text(pad, y, anchor="nw", text=label,
                                font=self.f_label, fill=self.theme["title"])
        y += S(22)
        self._seg_opts[key] = (options, pad, y, S(76), S(26))
        self._seg_draw(key)
        return y + S(26) + S(12)

    def _seg_draw(self, key):
        th = self.theme
        options, x, y, seg_w, h = self._seg_opts[key]
        grp = "seg:" + key
        self.canvas.delete(grp)
        cur = self._seg[key]
        self.canvas.create_polygon(
            rounded_points(x, y, x + seg_w * len(options), y + h, h / 2),
            smooth=True, fill=th["future"], outline="", tags=(grp,))
        for i, (val, text) in enumerate(options):
            sx = x + i * seg_w
            if val == cur:
                self.canvas.create_polygon(
                    rounded_points(sx + 2, y + 2, sx + seg_w - 2,
                                   y + h - 2, (h - 4) / 2),
                    smooth=True, fill=th["card"], outline="", tags=(grp,))
            self.canvas.create_text(sx + seg_w / 2, y + h / 2, text=text,
                                    font=self.f_label,
                                    fill=th["title"] if val == cur else th["frac"],
                                    tags=(grp,))
        self.canvas.tag_bind(grp, "<Button-1>",
                             lambda e, k=key: self._seg_click(k, e.x))

    def _seg_click(self, key, x):
        options, x0, _y, seg_w, _h = self._seg_opts[key]
        idx = int((x - x0) // seg_w)
        if 0 <= idx < len(options) and options[idx][0] != self._seg[key]:
            self._seg[key] = options[idx][0]
            self._seg_draw(key)

    # ---- 标题栏拖动 ----

    def _drag_start(self, event):
        self._dragging = event.y <= S(46)
        if self._dragging:
            self._dx = event.x_root - self.winfo_x()
            self._dy = event.y_root - self.winfo_y()

    def _drag_move(self, event):
        if getattr(self, "_dragging", False):
            self.geometry("+{}+{}".format(event.x_root - self._dx,
                                          event.y_root - self._dy))

    # ---- 保存 ----

    def save(self):
        birth = parse_birth(self.birth_var.get())
        if birth is None:
            messagebox.showerror("格式错误", "出生日期格式应为 YYYY-MM-DD, 例如 1995-06-15", parent=self)
            return
        if birth > datetime.now():
            messagebox.showerror("格式错误", "出生日期不能是将来的日期", parent=self)
            return
        try:
            lifespan = int(self.lifespan_var.get())
            if not 1 <= lifespan <= 150:
                raise ValueError
        except ValueError:
            messagebox.showerror("格式错误", "预期寿命请输入 1-150 之间的整数", parent=self)
            return
        if not any(self._views.values()):
            messagebox.showerror("提示", "至少打开一种进度", parent=self)
            return

        cfg = self.app.config_data
        cfg["birth_date"] = birth.strftime("%Y-%m-%d")
        cfg["lifespan"] = lifespan
        cfg["views"] = dict(self._views)
        cfg["theme"] = self._seg["theme"]
        cfg["dot_shape"] = self._seg["dot_shape"]
        cfg["configured"] = True
        save_config(cfg)
        self.app.apply_config()
        self.destroy()


class LifeWidget(tk.Tk):
    def __init__(self):
        super().__init__()
        self.config_data = load_config()
        self.birth = parse_birth(self.config_data["birth_date"]) \
            or parse_birth(DEFAULT_CONFIG["birth_date"])
        self._settings = None
        self._anim = None
        self._cur_items = []
        self._sig = None

        self.overrideredirect(True)                      # 无边框
        self.configure(bg=KEY_COLOR)
        self.attributes("-transparentcolor", KEY_COLOR)  # 透明背景
        self.attributes("-topmost", bool(self.config_data["always_on_top"]))

        pos = self.config_data.get("position")
        self.width = S(WIN_W)
        self._x = int(pos[0]) if pos else self.winfo_screenwidth() - self.width - S(40)
        self._y = int(pos[1]) if pos else S(60)

        self._make_fonts()
        self._apply_window_dpi()     # 用窗口所在屏幕的真实 DPI 修正缩放
        self.rebuild(animate=False)
        self._build_menu()

        # 拖动 / 单击 / 右键 / Esc
        self.canvas.bind("<ButtonPress-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._motion)
        self.canvas.bind("<ButtonRelease-1>", self._release)
        self.canvas.bind("<Button-3>", self._show_menu)
        self.bind("<Escape>", lambda e: self.destroy())

        self.after(50, self._breathe)
        self.after(1000, self._tick)
        self.after(2000, self._dpi_poll)

    def _make_fonts(self):
        self.cap_font = tkfont.Font(family="Microsoft YaHei UI", size=-S(11))
        self.frac_font = tkfont.Font(family="Microsoft YaHei UI", size=-S(11))
        self.big_font = tkfont.Font(family="Segoe UI Light", size=-S(40))
        self.weekday_font = tkfont.Font(family="Microsoft YaHei UI", size=-S(9))
        self.ring_center_font = tkfont.Font(family="Microsoft YaHei UI", size=-S(13))
        self.ring_pct_font = tkfont.Font(family="Microsoft YaHei UI", size=-S(10))
        self.ring_label_font = tkfont.Font(family="Microsoft YaHei UI", size=-S(11))
        self.ring_small_font = tkfont.Font(family="Microsoft YaHei UI", size=-S(8))
        self.bar_title_font = tkfont.Font(family="Microsoft YaHei UI", size=-S(20), weight="bold")
        self.bar_pct_font = tkfont.Font(family="Microsoft YaHei UI", size=-S(14), weight="bold")
        self.icon_font = tkfont.Font(family="Segoe UI Symbol", size=-S(10))

    def _apply_window_dpi(self):
        """按窗口当前所在屏幕的 DPI 修正缩放(跨屏拖动时跟随)."""
        global SCALE
        try:
            dpi = ctypes.windll.user32.GetDpiForWindow(self.winfo_id())
        except Exception:
            return
        s = dpi / 96.0
        if abs(s - SCALE) > 0.01:
            SCALE = s
            self._make_fonts()
            if hasattr(self, "canvas"):
                self.rebuild(animate=False)

    def _dpi_poll(self):
        self._apply_window_dpi()
        self.after(2000, self._dpi_poll)

    # ---- 视图开关 ----

    def _enabled_keys(self):
        keys = [k for k, _ in VIEW_DEFS
                if self.config_data["views"].get(
                    k, DEFAULT_CONFIG["views"].get(k, True))]
        return keys or ["today"]

    def _form(self, key):
        forms = FORMS.get(key)
        if not forms:
            return None
        idx = int(self.config_data["gran"].get(key, 0)) % len(forms)
        return forms[idx]

    def _theme(self):
        return THEMES.get(self.config_data.get("theme", "light"), THEMES["light"])

    def _build_specs(self):
        now = datetime.now()
        lifespan = self.config_data["lifespan"]
        specs = []
        for key in self._enabled_keys():
            if key == "rings":
                spec = build_rings_state(now, self.birth, lifespan)
            else:
                spec = build_view_state(key, self._form(key), now,
                                        self.birth, lifespan)
            spec["_key"] = key
            specs.append(spec)
        return specs

    # ---- 整体绘制 ----

    def rebuild(self, animate=True):
        """按当前配置重绘整张卡片."""
        self._anim = None
        self._cur_items = []
        theme = self._theme()
        specs = self._build_specs()

        # 先量出每个块的高度, 得出卡片总高度
        self.width = S(WIN_W)
        measures = [self._measure_block(sp) for sp in specs]
        h = S(TOP_PAD) + S(BOT_PAD) + sum(measures) \
            + S(BLOCK_GAP) * (len(specs) - 1)
        self.height = int(h)
        self.geometry("{}x{}+{}+{}".format(self.width, self.height, self._x, self._y))

        if hasattr(self, "canvas"):
            self.canvas.config(width=self.width, height=self.height)
            self.canvas.delete("all")
        else:
            self.canvas = tk.Canvas(self, width=self.width, height=self.height,
                                    bg=KEY_COLOR, highlightthickness=0, bd=0)
            self.canvas.pack()

        cv = self.canvas
        # 圆角卡片
        cv.create_polygon(
            rounded_points(1, 1, self.width - 2, self.height - 2, S(CARD_RADIUS)),
            smooth=True, fill=theme["card"], outline=theme["border"])

        self._dot_targets = []   # 级联动画的目标点 (item, shape, d, color)
        self._animate = animate
        self._block_ranges = []    # 每个可点击块的垂直范围 (y0, y1, key)
        y = S(TOP_PAD)
        for spec, bh in zip(specs, measures):
            self._render_block(spec, y, theme)
            if spec["_key"] in FORMS:
                self._block_ranges.append((y, y + bh, spec["_key"]))
            y += bh + S(BLOCK_GAP)

        self._specs = specs
        self._sig = tuple(self._block_sig(sp) for sp in specs)

        if animate and self._dot_targets:
            self._start_cascade(self._dot_targets, theme["card"])

    def _measure_block(self, sp):
        kind = sp["kind"]
        if kind == "dots":
            rows = math.ceil(sp["total"] / sp["cols"])
            cell = min((self.width - 2 * S(PAD)) / sp["cols"], S(MAX_CELL))
            return S(CAPTION_H) + S(CAPTION_GAP) + rows * cell
        if kind == "calendar":
            rows = math.ceil((sp["offset"] + sp["days"]) / 7)
            cell = min((self.width - 2 * S(PAD)) / 7, S(MAX_CELL))
            return S(CAPTION_H) + S(CAPTION_GAP) + S(WEEKDAY_H) + rows * cell
        if kind == "big_days":
            cell = (self.width - 2 * S(PAD)) / 7
            return S(BIG_TEXT_H) + cell
        if kind == "ring":
            return S(RING_D)
        if kind == "smile":
            return S(ARC_H) + S(CAPTION_GAP) + S(CAPTION_H)
        if kind == "bar":
            return S(BAR_TITLE_H) + S(6) + S(BAR_H) + S(8) + S(20) + S(16)
        if kind == "rings":
            return S(RING_ROW_D) + S(4) + S(14)
        raise KeyError(kind)

    def _block_sig(self, sp):
        """用于每秒刷新比对的签名(只含会变化的字段)."""
        kind = sp["kind"]
        if kind == "dots":
            return (kind, sp["passed"], sp["current"], sp["total"], sp["frac"])
        if kind == "calendar":
            return (kind, sp["offset"], sp["today"], sp["days"])
        if kind == "big_days":
            return (kind, sp["passed"], sp["current"])
        if kind == "ring":
            return (kind, round(sp["pct"], 1), sp.get("center1"))
        if kind == "smile":
            return (kind, round(sp["pct"], 1))
        if kind == "bar":
            return (kind, round(sp["pct"], 1), sp["frac"])
        if kind == "rings":
            return (kind, tuple(round(p, 1) for _, p in sp["items"]))
        raise KeyError(kind)

    # ---- 各形式的渲染 ----

    def _dot_color(self, i, passed, current, theme):
        if i < passed:
            return theme["passed"]
        if i == current:
            return theme["current"]
        return theme["future"]

    def _cycle_icon(self, y, key, theme):
        """块右上角的循环切换图标(整块都可点, 图标只是提示)."""
        self.canvas.create_text(self.width - S(PAD), y + S(1), anchor="ne",
                                text="⇄", font=self.icon_font,
                                fill=theme["frac"], tags=("blk:" + key,))

    def _create_dot(self, cx, cy, r, key, color, animate, card_color):
        """创建圆点(抗锯齿精灵图); animate 时先以卡片色隐藏, 级联淡入."""
        shape = self.config_data.get("dot_shape", "circle")
        d = r * 2
        start_color = card_color if animate else color
        item = self.canvas.create_image(
            cx, cy, image=dot_sprite(shape, start_color, d),
            anchor="center", tags=("blk:" + key,))
        self._dot_targets.append((item, shape, d, color))
        return item

    def _render_block(self, sp, y, theme):
        kind = sp["kind"]
        key = sp["_key"]
        pad = S(PAD)
        cv = self.canvas

        if kind == "dots":
            cv.create_text(pad, y, anchor="nw", text=sp["caption"],
                           font=self.cap_font, fill=theme["title"],
                           tags=("blk:" + key,))
            cv.create_text(self.width - pad - S(20), y, anchor="ne",
                           text=sp["frac"], font=self.frac_font,
                           fill=theme["frac"], tags=("blk:" + key,))
            self._cycle_icon(y, key, theme)
            y += S(CAPTION_H) + S(CAPTION_GAP)
            rows = math.ceil(sp["total"] / sp["cols"])
            cell = min((self.width - 2 * pad) / sp["cols"], S(MAX_CELL))
            self._draw_dot_grid(key, sp["total"], sp["passed"], sp["current"],
                                sp["cols"], rows, cell, y, theme)
            return

        if kind == "calendar":
            cv.create_text(pad, y, anchor="nw", text=sp["caption"],
                           font=self.cap_font, fill=theme["title"],
                           tags=("blk:" + key,))
            cv.create_text(self.width - pad - S(20), y, anchor="ne",
                           text=sp["frac"], font=self.frac_font,
                           fill=theme["frac"], tags=("blk:" + key,))
            self._cycle_icon(y, key, theme)
            y += S(CAPTION_H) + S(CAPTION_GAP)
            cell = min((self.width - 2 * pad) / 7, S(MAX_CELL))
            gw = cell * 7
            ox = (self.width - gw) / 2
            # 星期表头(周日起)
            for i, name in enumerate("日一二三四五六"):
                cv.create_text(ox + i * cell + cell / 2, y, anchor="n",
                               text=name, font=self.weekday_font,
                               fill=theme["frac"], tags=("blk:" + key,))
            y += S(WEEKDAY_H)
            d = max(S(2), cell * DOT_RATIO)
            r = d / 2.0
            oy = y + cell / 2
            for day in range(1, sp["days"] + 1):
                pos = sp["offset"] + day - 1
                col, row = pos % 7, pos // 7
                cx, cy = ox + col * cell + cell / 2, oy + row * cell
                color = self._dot_color(day - 1, sp["today"] - 1,
                                        sp["today"] - 1, theme)
                item = self._create_dot(cx, cy, r, key, color,
                                        self._animate, theme["card"])
                if day == sp["today"]:
                    shape = self.config_data.get("dot_shape", "circle")
                    self._cur_items.append((item, shape, color, d))
            return

        if kind == "big_days":
            cv.create_text(pad - S(4), y, anchor="nw", text=sp["text"],
                           font=self.big_font, fill=theme["title"],
                           tags=("blk:" + key,))
            self._cycle_icon(y + S(6), key, theme)
            y += S(BIG_TEXT_H)
            cell = (self.width - 2 * pad) / 7
            self._draw_dot_grid(key, sp["total"], sp["passed"], sp["current"],
                                7, 1, cell, y, theme)
            return

        if kind == "ring":
            self._cycle_icon(y, key, theme)
            d = S(RING_D)
            cx, cy = self.width / 2, y + d / 2
            cv.create_image(cx, cy, anchor="center",
                            image=ring_sprite(d, S(7), theme["future"],
                                              theme["current"],
                                              max(0.0, min(1.0, sp["pct"] / 100.0))),
                            tags=("blk:" + key,))
            if sp.get("center2"):
                cv.create_text(cx, cy - S(10), text=sp["center1"],
                               font=self.ring_center_font, fill=theme["title"],
                               tags=("blk:" + key,))
                cv.create_text(cx, cy + S(12), text=sp["center2"],
                               font=self.ring_pct_font, fill=theme["frac"],
                               tags=("blk:" + key,))
            else:
                cv.create_text(cx, cy, text=sp["center1"],
                               font=self.ring_center_font, fill=theme["title"],
                               tags=("blk:" + key,))
            return

        if kind == "smile":
            x_mid = self.width / 2
            cy = y - S(8)           # 椭圆中心(弧线向下弯进块内)
            cv.create_image(x_mid, cy, anchor="center",
                            image=smile_sprite(self.width - 2 * pad - S(12),
                                               S(54), S(6), theme["future"],
                                               theme["current"],
                                               max(0.0, min(1.0, sp["pct"] / 100.0))),
                            tags=("blk:" + key,))
            y += S(ARC_H) + S(CAPTION_GAP)
            cv.create_text(pad, y, anchor="nw", text=sp["caption"],
                           font=self.cap_font, fill=theme["title"],
                           tags=("blk:" + key,))
            cv.create_text(self.width - pad - S(20), y, anchor="ne",
                           text="{:.1f}%".format(sp["pct"]),
                           font=self.frac_font, fill=theme["title"],
                           tags=("blk:" + key,))
            self._cycle_icon(y, key, theme)
            return

        if kind == "bar":
            cv.create_text(pad, y, anchor="nw", text=sp["title"],
                           font=self.bar_title_font, fill=theme["title"],
                           tags=("blk:" + key,))
            self._cycle_icon(y + S(4), key, theme)
            y += S(BAR_TITLE_H) + S(6)
            cv.create_image(pad, y, anchor="nw",
                            image=bar_sprite(self.width - 2 * pad, S(BAR_H),
                                             theme["future"], theme["passed"],
                                             max(0.0, min(1.0, sp["pct"] / 100.0))),
                            tags=("blk:" + key,))
            y += S(BAR_H) + S(8)
            cv.create_text(self.width - pad, y, anchor="ne",
                           text="{:.1f}%".format(sp["pct"]),
                           font=self.bar_pct_font, fill=theme["title"],
                           tags=("blk:" + key,))
            cv.create_text(self.width - pad, y + S(20), anchor="ne",
                           text=sp["frac"], font=self.frac_font,
                           fill=theme["frac"], tags=("blk:" + key,))
            return

        if kind == "rings":
            items = sp["items"]
            n = len(items)
            d = S(RING_ROW_D)
            gap = (self.width - 2 * pad - n * d) / max(n - 1, 1) if n > 1 else 0
            total_w = n * d + gap * (n - 1)
            x = (self.width - total_w) / 2
            for label, pct in items:
                cx = x + d / 2
                cy = y + d / 2
                cv.create_image(cx, cy, anchor="center",
                                image=ring_sprite(d, S(5), theme["future"],
                                                  theme["passed"],
                                                  max(0.0, min(1.0, pct / 100.0))),
                                tags=("blk:" + key,))
                cv.create_text(cx, cy, text=label, font=self.ring_label_font,
                               fill=theme["title"], tags=("blk:" + key,))
                cv.create_text(cx, y + d + S(4), anchor="n",
                               text="{:.0f}%".format(pct),
                               font=self.ring_small_font, fill=theme["frac"],
                               tags=("blk:" + key,))
                x += d + gap
            return

        raise KeyError(kind)

    def _draw_dot_grid(self, key, total, passed, current, cols, rows, cell,
                       y, theme):
        """通用点阵(水平居中), 供 dots / big_days 形式复用."""
        d = max(S(2), cell * DOT_RATIO)
        r = d / 2.0
        gw = cell * cols
        ox = (self.width - gw) / 2 + cell / 2
        oy = y + cell / 2
        for i in range(total):
            col, row = i % cols, i // cols
            cx, cy = ox + col * cell, oy + row * cell
            color = self._dot_color(i, passed, current, theme)
            item = self._create_dot(cx, cy, r, key, color,
                                    self._animate, theme["card"])
            if i == current:
                shape = self.config_data.get("dot_shape", "circle")
                self._cur_items.append((item, shape, color, d))

    # ---- 动画 ----

    def _start_cascade(self, targets, from_color):
        """圆点从左到右、从上到下级联淡入."""
        n = len(targets)
        if n == 0:
            return
        self._anim = {
            "dots": targets,
            "from": from_color,
            "t0": time.monotonic(),
            "per": min(0.6 / n, 0.018),   # 总交错时长不超过 0.6s
            "dur": 0.28,
            "head": 0,
        }
        self.after(20, self._cascade_step)

    def _cascade_step(self):
        a = self._anim
        if a is None:
            return
        dots = a["dots"]
        n = len(dots)
        now = time.monotonic()
        i = a["head"]
        while i < n:
            item, shape, d, target = dots[i]
            t = (now - a["t0"] - i * a["per"]) / a["dur"]
            if t >= 1:
                self.canvas.itemconfigure(
                    item, image=dot_sprite(shape, target, d))
                i += 1
            elif t > 0:
                # 量化到固定级数, 避免生成过多精灵图
                lvl = int(t * CASCADE_LEVELS)
                tq = lvl / float(CASCADE_LEVELS)
                color = blend(a["from"], target, smoothstep(tq))
                self.canvas.itemconfigure(
                    item, image=dot_sprite(shape, color, d))
                break
            else:
                break
        a["head"] = i
        if i < n:
            self.after(20, self._cascade_step)
        else:
            self._anim = None

    def _breathe(self):
        """当前(橙色)圆点缓慢呼吸(尺寸量化, 复用缓存精灵图)."""
        if self._cur_items:
            k = 1.0 + 0.22 * math.sin(time.monotonic() * 2 * math.pi / 1.8)
            k = round(k / BREATHE_STEP) * BREATHE_STEP
            for item, shape, color, d in self._cur_items:
                self.canvas.itemconfigure(
                    item, image=dot_sprite(shape, color, d * k))
        self.after(50, self._breathe)

    # ---- 每秒刷新 ----

    def _tick(self):
        specs = self._build_specs()
        sig = tuple(self._block_sig(sp) for sp in specs)
        if sig != self._sig:   # 有内容变化, 无动画重建
            self.rebuild(animate=False)
        self.after(1000, self._tick)

    # ---- 交互 ----

    def _press(self, event):
        self._press_x, self._press_y = event.x_root, event.y_root
        self._win_dx = event.x_root - self.winfo_x()
        self._win_dy = event.y_root - self.winfo_y()
        self._moved = False

    def _motion(self, event):
        if (abs(event.x_root - self._press_x) > 5
                or abs(event.y_root - self._press_y) > 5):
            self._moved = True
        if self._moved:
            self._x = event.x_root - self._win_dx
            self._y = event.y_root - self._win_dy
            self.geometry("+{}+{}".format(self._x, self._y))

    def _release(self, event):
        if self._moved:
            self.config_data["position"] = [self.winfo_x(), self.winfo_y()]
            save_config(self.config_data)
            return
        # 单击: 命中哪个进度块的区域, 就循环切换哪个块的表现形式
        for y0, y1, key in self._block_ranges:
            if y0 <= event.y <= y1:
                forms = FORMS[key]
                idx = (int(self.config_data["gran"].get(key, 0)) + 1) % len(forms)
                self.config_data["gran"][key] = idx
                save_config(self.config_data)
                self.rebuild()
                return

    def _show_menu(self, event):
        self.menu.tk_popup(event.x_root, event.y_root)

    def _build_menu(self):
        theme = self._theme()
        self.var_top = tk.BooleanVar(value=bool(self.config_data["always_on_top"]))
        self.var_auto = tk.BooleanVar(value=bool(self.config_data["autostart"]))
        menu = tk.Menu(self, tearoff=0,
                       bg=theme["card"], fg=theme["title"],
                       activebackground=theme["future"],
                       activeforeground=theme["title"],
                       disabledforeground=theme["frac"],
                       bd=1, relief="flat",
                       font=tkfont.Font(family="Microsoft YaHei UI", size=-S(10)))
        menu.add_command(label="设置…", command=self.open_settings)
        menu.add_checkbutton(label="窗口置顶", variable=self.var_top,
                             selectcolor=theme["current"],
                             command=self._toggle_topmost)
        menu.add_checkbutton(label="开机自启", variable=self.var_auto,
                             selectcolor=theme["current"],
                             command=self._toggle_autostart)
        menu.add_separator()
        menu.add_command(label="退出", command=self.destroy)
        self.menu = menu

    def destroy(self):
        """退出前兜底保存一次, 保证重开时恢复最后的样子."""
        try:
            self.config_data["position"] = [self.winfo_x(), self.winfo_y()]
            save_config(self.config_data)
        except Exception:
            pass
        super().destroy()

    def _toggle_topmost(self):
        on = bool(self.var_top.get())
        self.config_data["always_on_top"] = on
        self.attributes("-topmost", on)
        save_config(self.config_data)

    def _toggle_autostart(self):
        on = bool(self.var_auto.get())
        if set_autostart(on):
            self.config_data["autostart"] = on
            save_config(self.config_data)
        else:
            self.var_auto.set(not on)
            messagebox.showerror("错误", "无法写入注册表, 开机自启设置失败", parent=self)

    def apply_config(self):
        """设置保存后重新生效."""
        birth = parse_birth(self.config_data["birth_date"])
        if birth:
            self.birth = birth
        self._build_menu()   # 菜单跟随主题重新上色
        self.rebuild(animate=False)

    def open_settings(self):
        if self._settings is not None and self._settings.winfo_exists():
            self._settings.lift()
            return
        self._settings = SettingsDialog(self)


def main():
    init_dpi()
    app = LifeWidget()
    if not app.config_data.get("configured"):
        app.after(400, app.open_settings)  # 首次运行先弹出设置
    app.mainloop()


if __name__ == "__main__":
    main()
