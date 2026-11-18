# -*- coding: utf-8 -*-
"""
Desk Shooter - 作業しながら遊べる、画面オーバーレイ型の暇つぶしシューティング
 * 画面は透明。的・銃・照準だけが表示され、下のアプリはそのまま操作できます
 * Windows 用 / 必要: Python 3.9+  と  pip install pynput

操作
 - 照準 = マウスカーソルの位置
 - 発射 = マウスの「中ボタン」 or 「右Ctrlキー」（下の設定で変更可）
 - 終了 = F9 キー or 操作パネルの「ストップ」
"""
import sys
import time
import math
import json
import random
import ctypes
import tkinter as tk
from tkinter import messagebox
from collections import deque
from pathlib import Path

if sys.platform != "win32":
    print("このバージョンは Windows 専用です。")
    sys.exit(1)

# 高DPI環境でも pynput / tkinter の座標を一致させる
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

from pynput import mouse, keyboard  # noqa: E402

# ===================== 設定 =====================
LEFT_CLICK_SHOOTS = False      # True にすると左クリックでも発射（下のアプリにもクリックが届く点に注意）
SHOOT_KEYS = {keyboard.Key.ctrl_r}
SHOOT_BUTTONS = {mouse.Button.middle}
for _n in ("x1", "x2"):        # マウスのサイドボタンでも撃てる
    if hasattr(mouse.Button, _n):
        SHOOT_BUTTONS.add(getattr(mouse.Button, _n))
STOP_KEY = keyboard.Key.f9
SHOW_HUD = True                # 画面右上の小さなスコア表示
FPS_MS = 16
SCORE_FILE = Path.home() / ".desk_shooter_scores.json"
# ================================================

KEY = "#010203"  # この色は透明になる（的などには使わない）

MODES = [
    dict(name="タイムアタック 60秒",      duration=60,  life=(1.8, 2.8), delay=(0.3, 1.2), conc=1, r=(34, 48)),
    dict(name="のんびり 3分",            duration=180, life=(3.0, 4.5), delay=(0.8, 3.0), conc=1, r=(38, 52)),
    dict(name="ラピッド 60秒（小さい的）", duration=60,  life=(1.0, 1.6), delay=(0.1, 0.6), conc=2, r=(26, 38)),
    dict(name="エンドレス（ふつう）",     duration=None, life=(2.2, 3.5), delay=(0.5, 2.0), conc=1, r=(34, 48)),
    dict(name="エンドレス（まったり）",   duration=None, life=(4.0, 6.0), delay=(2.0, 6.0), conc=1, r=(40, 56)),
]

FONT = "Yu Gothic UI"


def make_click_through(win):
    """ウィンドウをクリック透過・タスクバー非表示・フォーカス非取得にする"""
    win.update_idletasks()
    user32 = ctypes.windll.user32
    hwnd = user32.GetParent(win.winfo_id()) or win.winfo_id()
    GWL_EXSTYLE = -20
    style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    # LAYERED | TRANSPARENT | TOOLWINDOW | NOACTIVATE
    style |= 0x00080000 | 0x00000020 | 0x00000080 | 0x08000000
    user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)


def load_scores():
    try:
        return json.loads(SCORE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_scores(d):
    try:
        SCORE_FILE.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


class DeskShooter:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Desk Shooter")
        self.root.attributes("-topmost", True)
        self.root.resizable(False, False)
        self.W = self.root.winfo_screenwidth()
        self.H = self.root.winfo_screenheight()

        self.running = False
        self.stop_req = False
        self.after_id = None
        self.ov = None
        self.cv = None
        self.shots = deque()
        self.down = set()
        self.scores = load_scores()

        self.build_panel()

        self.kl = keyboard.Listener(on_press=self.on_key_press, on_release=self.on_key_release)
        self.ml = mouse.Listener(on_click=self.on_click)
        self.kl.daemon = self.ml.daemon = True
        self.kl.start()
        self.ml.start()
        self.root.protocol("WM_DELETE_WINDOW", self.quit)

    # ---------------- 操作パネル ----------------
    def build_panel(self):
        r = self.root
        pad = dict(padx=10)
        tk.Label(r, text="Desk Shooter", font=(FONT, 13, "bold")).pack(pady=(10, 2), **pad)
        tk.Label(r, text="モードを選んでスタート", font=(FONT, 9), fg="#555").pack(**pad)

        self.mode_var = tk.IntVar(value=0)
        for i, m in enumerate(MODES):
            tk.Radiobutton(r, text=m["name"], variable=self.mode_var, value=i,
                           font=(FONT, 10), anchor="w").pack(fill="x", **pad)

        bf = tk.Frame(r)
        bf.pack(pady=8)
        self.btn_start = tk.Button(bf, text="スタート", width=10, command=self.start, font=(FONT, 10, "bold"))
        self.btn_stop = tk.Button(bf, text="ストップ (F9)", width=12, command=self.request_stop,
                                  state="disabled", font=(FONT, 10))
        self.btn_start.pack(side="left", padx=4)
        self.btn_stop.pack(side="left", padx=4)

        self.lbl_stats = tk.Label(r, text="-", font=(FONT, 10), justify="left")
        self.lbl_stats.pack(**pad)
        self.lbl_best = tk.Label(r, text="", font=(FONT, 9), fg="#555")
        self.lbl_best.pack(**pad)

        keys = "発射: マウス中ボタン / 右Ctrl" + ("/ 左クリック" if LEFT_CLICK_SHOOTS else "")
        tk.Label(r, text=keys + "\n終了: F9", font=(FONT, 9), fg="#555", justify="left").pack(pady=(4, 10), **pad)
        self.update_best_label()
        r.geometry("+30+30")

    def update_best_label(self):
        m = MODES[self.mode_var.get()]
        best = self.scores.get(m["name"], 0)
        self.lbl_best.config(text=f"ハイスコア: {best}")

    # ---------------- 入力 ----------------
    def fire(self):
        if self.running:
            self.shots.append(time.time())

    def on_key_press(self, key):
        if key == STOP_KEY:
            if self.running:
                self.stop_req = True
            return
        if key in SHOOT_KEYS and key not in self.down:
            self.down.add(key)
            self.fire()

    def on_key_release(self, key):
        self.down.discard(key)

    def on_click(self, x, y, button, pressed):
        if not pressed:
            return
        if button in SHOOT_BUTTONS or (LEFT_CLICK_SHOOTS and button == mouse.Button.left):
            self.fire()

    # ---------------- ゲーム制御 ----------------
    def start(self):
        if self.running:
            return
        self.mode = MODES[self.mode_var.get()]
        self.ov = tk.Toplevel(self.root)
        self.ov.overrideredirect(True)
        self.ov.geometry(f"{self.W}x{self.H}+0+0")
        self.ov.attributes("-topmost", True)
        self.ov.configure(bg=KEY)
        self.ov.attributes("-transparentcolor", KEY)
        self.cv = tk.Canvas(self.ov, width=self.W, height=self.H, bg=KEY, highlightthickness=0)
        self.cv.pack()
        make_click_through(self.ov)
        self.root.lift()

        now = time.time()
        self.score = 0
        self.hits = 0
        self.misses = 0
        self.escaped = 0
        self.targets = []
        self.effects = []
        self.shots.clear()
        self.recoil = 0.0
        self.flash_until = 0.0
        self.tracer = None
        self.next_spawn = now + 0.8
        self.start_time = now
        self.end_time = now + self.mode["duration"] if self.mode["duration"] else None
        self.stop_req = False
        self.running = True
        self.btn_start.config(state="disabled")
        self.btn_stop.config(state="normal")
        self.last_lift = now
        self.tick()

    def request_stop(self):
        if self.running:
            self.stop_req = True

    def stop(self, timed_out=False):
        self.running = False
        if self.after_id:
            try:
                self.root.after_cancel(self.after_id)
            except Exception:
                pass
            self.after_id = None
        if self.ov is not None:
            self.ov.destroy()
            self.ov = None
        self.btn_start.config(state="normal")
        self.btn_stop.config(state="disabled")

        name = self.mode["name"]
        new_best = self.score > self.scores.get(name, 0) and self.score > 0
        if new_best:
            self.scores[name] = self.score
            save_scores(self.scores)
        self.update_best_label()
        total = self.hits + self.misses
        acc = (self.hits / total * 100) if total else 0
        title = "タイムアップ！" if timed_out else "お疲れさまでした"
        msg = (f"モード: {name}\n"
               f"スコア: {self.score}" + ("  ★ハイスコア更新！" if new_best else "") + "\n"
               f"命中: {self.hits}  / ミス: {self.misses}  / 逃した的: {self.escaped}\n"
               f"命中率: {acc:.0f}%")
        self.lbl_stats.config(text=msg)
        self.root.lift()
        messagebox.showinfo(title, msg, parent=self.root)

    def quit(self):
        self.running = False
        try:
            self.kl.stop()
            self.ml.stop()
        except Exception:
            pass
        self.root.destroy()

    # ---------------- メインループ ----------------
    def spawn(self, now):
        m = self.mode
        r = random.randint(*m["r"])
        margin = r + 12
        x = random.randint(margin, self.W - margin)
        y = random.randint(margin, self.H - margin)
        self.targets.append(dict(x=x, y=y, r=r, born=now, life=random.uniform(*m["life"])))

    def tick(self):
        if not self.running:
            return
        now = time.time()
        if self.stop_req:
            self.stop(False)
            return
        if self.end_time and now >= self.end_time:
            self.stop(True)
            return

        # たまに最前面を再主張
        if now - self.last_lift > 1.5:
            self.ov.attributes("-topmost", True)
            self.root.attributes("-topmost", True)
            self.last_lift = now

        m = self.mode
        # 出現
        if len(self.targets) < m["conc"] and now >= self.next_spawn:
            self.spawn(now)
            self.next_spawn = now + random.uniform(*m["delay"])

        # 時間切れで引っ込む
        alive = []
        for t in self.targets:
            if now - t["born"] >= t["life"]:
                self.escaped += 1
                self.next_spawn = min(self.next_spawn, now + random.uniform(*m["delay"]))
            else:
                alive.append(t)
        self.targets = alive

        px, py = self.ov.winfo_pointerxy()

        # 発射処理
        while self.shots:
            self.shots.popleft()
            self.handle_shot(px, py, now)

        self.draw(now, px, py)
        self.update_stats(now)
        self.after_id = self.root.after(FPS_MS, self.tick)

    def scale_of(self, t, now):
        age = now - t["born"]
        remain = t["life"] - age
        if age < 0.22:
            k = age / 0.22
            return 1 - (1 - k) ** 3
        if remain < 0.25:
            return max(0.0, remain / 0.25)
        return 1.0

    def handle_shot(self, px, py, now):
        self.recoil = 1.0
        self.flash_until = now + 0.07
        self.tracer = (px, py, now + 0.06)
        for t in sorted(self.targets, key=lambda t: t["born"], reverse=True):
            s = self.scale_of(t, now)
            rr = t["r"] * s
            d = math.hypot(px - t["x"], py - t["y"])
            if rr > 4 and d <= rr:
                ratio = d / rr
                if ratio <= 0.33:
                    pts, label, col = 30, "BULL +30", "#ffd600"
                elif ratio <= 0.66:
                    pts, label, col = 20, "+20", "#ffffff"
                else:
                    pts, label, col = 10, "+10", "#ffffff"
                self.score += pts
                self.hits += 1
                self.targets.remove(t)
                self.next_spawn = min(self.next_spawn, now + random.uniform(*self.mode["delay"]))
                self.effects.append(dict(kind="ring", x=t["x"], y=t["y"], r=t["r"], t0=now, dur=0.35))
                self.effects.append(dict(kind="text", x=t["x"], y=t["y"], text=label, col=col, t0=now, dur=0.8))
                return
        self.misses += 1
        self.effects.append(dict(kind="spark", x=px, y=py, t0=now, dur=0.2))

    def update_stats(self, now):
        if int(now * 5) % 2:  # 更新頻度を抑える
            return
        total = self.hits + self.misses
        acc = (self.hits / total * 100) if total else 0
        if self.end_time:
            left = max(0, int(self.end_time - now + 0.999))
            head = f"残り {left} 秒   "
        else:
            head = f"経過 {int(now - self.start_time)} 秒   "
        self.lbl_stats.config(text=f"{head}スコア {self.score}\n命中 {self.hits} / ミス {self.misses} / 命中率 {acc:.0f}%")

    # ---------------- 描画 ----------------
    def draw(self, now, px, py):
        c = self.cv
        c.delete("all")

        # 的
        for t in self.targets:
            s = self.scale_of(t, now)
            if s <= 0.02:
                continue
            r = t["r"] * s
            x, y = t["x"], t["y"]
            for k, col in ((1.0, "#e53935"), (0.78, "#fafafa"), (0.56, "#e53935"), (0.33, "#ffd600")):
                rr = r * k
                c.create_oval(x - rr, y - rr, x + rr, y + rr, fill=col, outline="#202020", width=2 if k == 1.0 else 1)

        # エフェクト
        keep = []
        for e in self.effects:
            p = (now - e["t0"]) / e["dur"]
            if p >= 1:
                continue
            keep.append(e)
            if e["kind"] == "ring":
                rr = e["r"] * (1 + p * 0.9)
                c.create_oval(e["x"] - rr, e["y"] - rr, e["x"] + rr, e["y"] + rr,
                              outline="#ffd600", width=max(1, int(5 * (1 - p))))
            elif e["kind"] == "text":
                yy = e["y"] - 20 - p * 40
                c.create_text(e["x"] + 1, yy + 1, text=e["text"], fill="#202020", font=(FONT, 16, "bold"))
                c.create_text(e["x"], yy, text=e["text"], fill=e["col"], font=(FONT, 16, "bold"))
            elif e["kind"] == "spark":
                rr = 6 + p * 14
                for a in range(0, 360, 45):
                    ca, sa = math.cos(math.radians(a)), math.sin(math.radians(a))
                    c.create_line(e["x"] + ca * rr * 0.5, e["y"] + sa * rr * 0.5,
                                  e["x"] + ca * rr, e["y"] + sa * rr, fill="#b0bec5", width=2)
        self.effects = keep

        self.draw_gun(c, now, px, py)
        self.draw_reticle(c, px, py)
        if SHOW_HUD:
            self.draw_hud(c, now)

    def draw_gun(self, c, now, px, py):
        bx, by = self.W // 2, self.H - 24
        ang = math.atan2(py - by, px - bx)
        ang = min(max(ang, -math.pi + 0.2), -0.2)  # 上向きにクランプ
        self.recoil = max(0.0, self.recoil - 0.12)
        length = 120 - 14 * self.recoil
        dx, dy = math.cos(ang), math.sin(ang)
        nx, ny = -dy, dx
        mx, my = bx + dx * length, by + dy * length
        w0, w1 = 18, 10
        poly = [bx + nx * w0, by + ny * w0, mx + nx * w1, my + ny * w1,
                mx - nx * w1, my - ny * w1, bx - nx * w0, by - ny * w0]
        c.create_polygon(poly, fill="#607d8b", outline="#1c1c1c", width=2)
        # 銃身のハイライト
        c.create_line(bx + dx * 20 + nx * 6, by + dy * 20 + ny * 6, mx + nx * 4, my + ny * 4, fill="#90a4ae", width=2)
        # 本体
        c.create_oval(bx - 38, by - 38, bx + 38, by + 38, fill="#37474f", outline="#1c1c1c", width=2)
        c.create_oval(bx - 14, by - 14, bx + 14, by + 14, fill="#ff7043", outline="#1c1c1c", width=2)
        # 照準
        c.create_oval(mx - 4, my - 4, mx + 4, my + 4, fill="#ffeb3b", outline="#1c1c1c")

        if now < self.flash_until:
            pts = []
            for i in range(12):
                a = ang + math.radians(i * 30)
                rr = 26 if i % 2 == 0 else 11
                pts += [mx + math.cos(a) * rr, my + math.sin(a) * rr]
            c.create_polygon(pts, fill="#ffeb3b", outline="#ff9800")
        if self.tracer and now < self.tracer[2]:
            c.create_line(mx, my, self.tracer[0], self.tracer[1], fill="#fff59d", width=2)

    def draw_reticle(self, c, px, py):
        R, gap, ln = 20, 7, 12
        for col, wd in (("#101010", 5), ("#00ff6a", 2)):
            c.create_oval(px - R, py - R, px + R, py + R, outline=col, width=wd)
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                c.create_line(px + dx * gap, py + dy * gap, px + dx * (R + ln), py + dy * (R + ln), fill=col, width=wd)
        c.create_oval(px - 1.5, py - 1.5, px + 1.5, py + 1.5, fill="#ff1744", outline="#ff1744")

    def draw_hud(self, c, now):
        if self.end_time:
            left = max(0, int(self.end_time - now + 0.999))
            txt = f"SCORE {self.score}   TIME {left}"
        else:
            txt = f"SCORE {self.score}   HIT {self.hits} / MISS {self.misses}"
        x, y = self.W - 20, 16
        c.create_text(x + 1, y + 1, text=txt, anchor="ne", fill="#202020", font=(FONT, 12, "bold"))
        c.create_text(x, y, text=txt, anchor="ne", fill="#ffffff", font=(FONT, 12, "bold"))

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    DeskShooter().run()