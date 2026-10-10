# -*- coding: utf-8 -*-
"""
IRIS 宣传图批量生成器 (promo-v2)
- 展示真实项目 Logo (logo.png, 透明底)
- 完整展示控制台界面 (assets/ui-dashboard.png 1600x1000)，等比缩放、绝不裁切
- 专业文案 + 优势/创新亮点 + 同批实测数据
- 一次产出多版方案供挑选
"""
import os
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageChops

BASE = r"D:/桌面资料/临时工作区/IRIS"
OUT = os.path.join(BASE, "assets", "promo-v2")
os.makedirs(OUT, exist_ok=True)

LOGO = os.path.join(BASE, "logo.png")
SHOT = os.path.join(BASE, "assets", "ui-dashboard.png")

F_CJK_B = r"C:/Windows/Fonts/msyhbd.ttc"
F_CJK_R = r"C:/Windows/Fonts/msyh.ttc"
F_LAT_B = r"C:/Windows/Fonts/arialbd.ttf"
F_LAT_R = r"C:/Windows/Fonts/arial.ttf"

_cache = {}
def F(path, size):
    k = (path, size)
    if k not in _cache:
        _cache[k] = ImageFont.truetype(path, size)
    return _cache[k]

def fb(s): return F(F_CJK_B, s)   # 粗 中英
def fr(s): return F(F_CJK_R, s)   # 常规 中英
def lb(s): return F(F_LAT_B, s)   # 拉丁粗
def lr(s): return F(F_LAT_R, s)   # 拉丁常规

# ---------------- 文案 ----------------
WORDMARK = "IRIS"
FULLNAME = "IoT Rehosting & Interconnection Simulator"
KICKER = "全自动化固件重托管 · 失败可归因"
HEAD1 = "让每一次失败"
HEAD2 = "都能被命名"
DESC = ("面向路由器 / IP 摄像头等网络设备的固件重托管平台：固件包输入，网络可达的仿真设备输出，"
        "全程无人值守。仿真失败时不再只返回一个“HTTP 000”，而是逐层实测、命名断点。")
INNOV = [
    ("01", "分层失败画像",
     "把「HTTP 000」拆成可命名的断点：link-no-route / link-no-arp / link-no-service …"),
    ("02", "四层链路主动探测",
     "route → ARP → ICMP → service 逐层实测，直接说明断在哪一层"),
    ("03", "可插拔规则引擎",
     "零 Python 的 YAML 修复规则，可上传 / 校验 / 卸载 / 回归，先校验后落盘"),
]
COMPARE = [("提取成功", "8/9", "5/9"), ("进入仿真", "8/9", "5/9"), ("Web 可达", "4/9", "2/9")]
CHIPS = ["9 台同批语料", "4 层链路探测", "6 条实证规则", "3 类架构"]
FOOT_L = "华为开发者大赛 · 珠海科技学院站（专业组）"
FOOT_TEAM = "浮点数绝队"
FOOT_R = "IRIS v0.3.25 · github.com/JiuZero/IRIS"
URLTXT = "iris-home  ·  固件仿真工作台"

# ---------------- 主题 ----------------
DARK = dict(
    text=(255, 255, 255, 255), dim=(255, 255, 255, 165), dim2=(255, 255, 255, 118),
    accent=(56, 189, 248, 255), accent2=(129, 140, 248, 255),
    card=(255, 255, 255, 17), cardStroke=(255, 255, 255, 50),
    frameFill=(13, 19, 34, 255), frameStroke=(56, 189, 248, 95),
    chromePill=(255, 255, 255, 22), chromeText=(150, 165, 186, 255),
    tableLine=(255, 255, 255, 46),
    chipFill=(255, 255, 255, 20), chipStroke=(56, 189, 248, 120),
    tagFill=(255, 255, 255, 28), tagStroke=(255, 255, 255, 70), tagText=(255, 255, 255, 240),
)
LIGHT = dict(
    text=(15, 23, 42, 255), dim=(71, 85, 105, 255), dim2=(100, 116, 139, 255),
    accent=(37, 99, 235, 255), accent2=(79, 70, 229, 255),
    card=(255, 255, 255, 240), cardStroke=(203, 213, 225, 255),
    frameFill=(255, 255, 255, 255), frameStroke=(203, 213, 225, 255),
    chromePill=(241, 245, 249, 255), chromeText=(100, 116, 139, 255),
    tableLine=(203, 213, 225, 255),
    chipFill=(255, 255, 255, 245), chipStroke=(191, 219, 254, 255),
    tagFill=(37, 99, 235, 22), tagStroke=(37, 99, 235, 120), tagText=(37, 99, 235, 255),
)

def themed(base, accent=None, accent2=None):
    t = dict(base)
    if accent: t["accent"] = accent
    if accent2: t["accent2"] = accent2
    return t

# ---------------- 基础工具 ----------------
def lerp(a, b, t):
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))

def vgrad(size, stops):
    w, h = size
    col = Image.new("RGB", (1, h))
    px = col.load()
    for y in range(h):
        t = y / max(1, h - 1)
        c = stops[-1][1]
        for i in range(len(stops) - 1):
            p0, c0 = stops[i]
            p1, c1 = stops[i + 1]
            if p0 <= t <= p1:
                c = lerp(c0, c1, (t - p0) / max(1e-6, p1 - p0)); break
            if t < p0:
                c = c0; break
        px[0, y] = c
    return col.resize((w, h))

def add_glow(bg, cx, cy, rx, ry, color, blur, strength=1.0):
    g = Image.new("RGB", bg.size, (0, 0, 0))
    ImageDraw.Draw(g).ellipse([cx - rx, cy - ry, cx + rx, cy + ry], fill=color)
    g = g.filter(ImageFilter.GaussianBlur(blur))
    if strength != 1.0:
        g = g.point(lambda v: max(0, min(255, int(v * strength))))
    return ImageChops.screen(bg, g)

def add_grid(bg, step=54, color=(255, 255, 255, 12), sub=None):
    ov = Image.new("RGBA", bg.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    if sub:
        for x in range(0, bg.size[0], sub):
            d.line([(x, 0), (x, bg.size[1])], fill=color, width=1)
        for y in range(0, bg.size[1], sub):
            d.line([(0, y), (bg.size[0], y)], fill=color, width=1)
    for x in range(0, bg.size[0], step):
        d.line([(x, 0), (x, bg.size[1])], fill=color, width=1)
    for y in range(0, bg.size[1], step):
        d.line([(0, y), (bg.size[0], y)], fill=color, width=1)
    return Image.alpha_composite(bg.convert("RGBA"), ov).convert("RGB")

def load_logo(size):
    return Image.open(LOGO).convert("RGBA").resize((size, size), Image.LANCZOS)

def load_shot():
    return Image.open(SHOT).convert("RGBA")

def wrap(d, s, font, maxw):
    toks, buf = [], ""
    for ch in s:
        if ch == " ":
            if buf: toks.append(buf); buf = ""
            toks.append(" ")
        elif ord(ch) > 0x2E80:
            if buf: toks.append(buf); buf = ""
            toks.append(ch)
        else:
            buf += ch
    if buf: toks.append(buf)
    lines, cur = [], ""
    for t in toks:
        if d.textlength(cur + t, font=font) <= maxw or cur.strip() == "":
            cur += t
        else:
            lines.append(cur.rstrip()); cur = "" if t == " " else t
    if cur.strip(): lines.append(cur.rstrip())
    return lines

def truncate(d, s, font, maxw):
    if d.textlength(s, font=font) <= maxw: return s
    s2 = s
    while s2 and d.textlength(s2 + "…", font=font) > maxw:
        s2 = s2[:-1]
    return s2 + "…"

def draw_ls(d, xy, s, font, fill, ls=0):
    x, y = xy
    for ch in s:
        d.text((x, y), ch, font=font, fill=fill)
        x += d.textlength(ch, font=font) + ls
    return x

def ls_width(d, s, font, ls=0):
    if not s: return 0
    return sum(d.textlength(c, font=font) for c in s) + ls * (len(s) - 1)

def text_h(font):
    a, dsc = font.getmetrics()
    return a + dsc

def centered(d, cx, y, s, font, fill, ls=0):
    w = ls_width(d, s, font, ls)
    draw_ls(d, (cx - w / 2, y), s, font, fill, ls)

def rounded(img, radius):
    img = img.convert("RGBA")
    mask = Image.new("L", img.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, img.size[0] - 1, img.size[1] - 1], radius, fill=255)
    img.putalpha(mask)
    return img

def drop_shadow(canvas, box, radius, blur=34, alpha=95, offset=(0, 20)):
    sh = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    ImageDraw.Draw(sh).rounded_rectangle(
        [box[0] + offset[0], box[1] + offset[1], box[2] + offset[0], box[3] + offset[1]],
        radius, fill=(2, 6, 23, alpha))
    sh = sh.filter(ImageFilter.GaussianBlur(blur))
    canvas.alpha_composite(sh)

def build_console(shot, Wc, chrome_ratio=0.030, pad_ratio=0.011, radius_ratio=0.013,
                  theme=DARK, light=False, url=URLTXT):
    scale = Wc / shot.width
    Hc = round(shot.height * scale)
    shot_r = shot.resize((Wc, Hc), Image.LANCZOS)
    pad = max(8, round(Wc * pad_ratio))
    chrome = max(26, round(Wc * chrome_ratio))
    W = Wc + 2 * pad
    H = chrome + Hc + 2 * pad
    out = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(out)
    r = max(10, round(W * radius_ratio))
    d.rounded_rectangle([0, 0, W - 1, H - 1], r, fill=theme["frameFill"])
    if not light:
        d.rounded_rectangle([0, 0, W - 1, r], r, fill=(255, 255, 255, 14))
    dot_r = max(4, round(chrome * 0.17))
    cy = chrome // 2
    for i, col in enumerate([(255, 95, 87), (254, 188, 46), (40, 200, 64)]):
        cx = pad + dot_r + i * (dot_r * 2 + max(6, dot_r))
        d.ellipse([cx - dot_r, cy - dot_r, cx + dot_r, cy + dot_r], fill=col + (255,))
    pill_f = fr(max(12, round(chrome * 0.42)))          # CJK 字体，避免豆腐块
    pw = d.textlength(url, font=pill_f)
    ph = max(16, round(chrome * 0.60))
    px0 = (W - pw) / 2 - 16
    d.rounded_rectangle([px0, cy - ph / 2, px0 + pw + 32, cy + ph / 2], ph / 2, fill=theme["chromePill"])
    d.text((px0 + 16, cy - text_h(pill_f) / 2 + 1), url, font=pill_f, fill=theme["chromeText"])
    shot_r = rounded(shot_r, max(6, round(r * 0.5)))
    out.alpha_composite(shot_r, (pad, chrome + pad))
    d.rounded_rectangle([0, 0, W - 1, H - 1], r, outline=theme["frameStroke"], width=max(1, round(W * 0.0011)))
    return out, (W, H)

def chip_w(d, text, font, padx=22):
    return d.textlength(text, font=font) + padx * 2

def chip(d, x, y, text, font, theme, padx=22, pady=13):
    w = chip_w(d, text, font, padx)
    h = text_h(font) + pady * 2 - 8
    d.rounded_rectangle([x, y, x + w, y + h], h / 2, fill=theme["chipFill"], outline=theme["chipStroke"], width=1)
    d.text((x + padx, y + (h - text_h(font)) / 2 + 1), text, font=font, fill=theme["text"])
    return w, h

def innov_card(d, box, num, title, desc, theme, fs_title=22, fs_desc=15):
    x, y, w, h = box
    d.rounded_rectangle([x, y, x + w, y + h], 14, fill=theme["card"], outline=theme["cardStroke"], width=1)
    d.rounded_rectangle([x, y + 12, x + 4, y + h - 12], 2, fill=theme["accent"])
    ix = x + 22
    d.text((ix, y + 16), num, font=lb(fs_title - 2), fill=theme["accent"])
    tx = ix + 44
    yy = y + 16
    for ln in wrap(d, title, fb(fs_title), w - 44 - 22)[:2]:
        d.text((tx, yy), ln, font=fb(fs_title), fill=theme["text"]); yy += fs_title + 6
    yy += 8
    maxlines = max(0, int((y + h - 12 - yy) // (fs_desc + 7)))
    for ln in wrap(d, desc, fr(fs_desc), w - 46)[:maxlines]:
        d.text((ix, yy), ln, font=fr(fs_desc), fill=theme["dim"]); yy += fs_desc + 7

def compare_table(d, x, y, w, theme, rows, fs=22, title="与 FirmAE 同批实测 · 有效语料 9 台"):
    d.text((x, y), title, font=fr(fs - 3), fill=theme["dim2"])
    ty = y + fs + 18
    colw = w / 3
    d.text((x + colw, ty), "IRIS", font=fb(fs), fill=theme["accent"])
    d.text((x + colw * 2, ty), "FirmAE", font=fb(fs), fill=theme["dim"])
    ty += fs + 14
    d.line([(x, ty), (x + w, ty)], fill=theme["tableLine"], width=1)
    ty += 12
    for i, (k, a, b) in enumerate(rows):
        d.text((x, ty), k, font=fr(fs), fill=theme["text"])
        d.text((x + colw, ty), a, font=fb(fs), fill=theme["accent"])
        d.text((x + colw * 2, ty), b, font=fr(fs), fill=theme["dim"])
        ty += fs + 26
        if i < len(rows) - 1:
            tl = theme["tableLine"]
            d.line([(x, ty - 10), (x + w, ty - 10)], fill=(tl[0], tl[1], tl[2], 30), width=1)

def footer(canvas, d, theme, y, W):
    tl = theme["tableLine"]
    d.line([(100, y), (W - 100, y)], fill=(tl[0], tl[1], tl[2], 80), width=1)
    d.text((100, y + 22), FOOT_L, font=fr(23), fill=theme["dim"])
    d.text((100 + d.textlength(FOOT_L, font=fr(23)) + 26, y + 22), FOOT_TEAM, font=fb(23), fill=theme["accent"])
    d.text((W - 100 - d.textlength(FOOT_R, font=lr(22)), y + 23), FOOT_R, font=lr(22), fill=theme["dim2"])

def header(canvas, d, theme, x, y, logo_s, wm_s, sub_s=None, tag=True):
    canvas.alpha_composite(load_logo(logo_s), (x, y))
    cy = y + logo_s / 2
    wx = x + logo_s + int(logo_s * 0.26)
    wtxt = lb(wm_s)
    d.text((wx, cy - text_h(wtxt) / 2), WORDMARK, font=wtxt, fill=theme["text"])
    if tag:
        pf = lr(int(wm_s * 0.30))
        px = wx + d.textlength(WORDMARK, font=wtxt) + int(wm_s * 0.30)
        pw = d.textlength("v0.3.25", font=pf) + 26
        ph = int(wm_s * 0.60)
        d.rounded_rectangle([px, cy - ph / 2, px + pw, cy + ph / 2], ph / 2, fill=theme["tagFill"],
                            outline=theme["tagStroke"], width=1)
        d.text((px + 13, cy - text_h(pf) / 2 + 1), "v0.3.25", font=pf, fill=theme["tagText"])
    if sub_s:
        d.text((x + 2, y + logo_s + int(logo_s * 0.16)), FULLNAME, font=lr(sub_s), fill=theme["dim2"])
    return cy

def kicker_line(d, x, y, theme, s=KICKER, fs=25):
    d.rectangle([x, y + 6, x + 5, y + 6 + fs], fill=theme["accent"])
    d.text((x + 20, y), s, font=fb(fs), fill=theme["accent"])

def headline(d, x, y, theme, fs=80, lh=None):
    lh = lh or int(fs * 1.24)
    d.text((x, y), HEAD1, font=fb(fs), fill=theme["text"])
    d.text((x, y + lh), HEAD2, font=fb(fs), fill=theme["accent"])

def desc_block(d, x, y, maxw, theme, fs=23, lh=38, maxlines=3):
    yy = y
    for ln in wrap(d, DESC, fr(fs), maxw)[:maxlines]:
        d.text((x, yy), ln, font=fr(fs), fill=theme["dim"]); yy += lh
    return yy

def start(size, bg):
    canvas = bg.convert("RGBA")
    ov = Image.new("RGBA", size, (0, 0, 0, 0))
    return canvas, ov, ImageDraw.Draw(ov)

def save(canvas, ov, name):
    canvas.alpha_composite(ov)
    p = os.path.join(OUT, name)
    canvas.convert("RGB").save(p, "PNG")
    print("saved", p, canvas.size)

# ================= 方案 1：归因之眼（左文 + 右控制台，深空蓝） =================
def v1():
    W, H = 1920, 1080
    bg = add_grid(vgrad((W, H), [(0, (6, 12, 30)), (0.5, (10, 22, 48)), (1, (4, 8, 20))]), 54, (255, 255, 255, 10))
    bg = add_glow(bg, 1520, 170, 640, 430, (28, 110, 220), 190, 0.95)
    bg = add_glow(bg, 250, 920, 540, 390, (62, 62, 195), 200, 0.6)
    canvas, ov, d = start((W, H), bg)
    th = DARK
    header(canvas, d, th, 100, 76, 100, 62, sub_s=20)
    d.line([(102, 246), (560, 246)], fill=th["accent"], width=2)
    kicker_line(d, 100, 300, th)
    headline(d, 100, 356, th, fs=80)
    desc_block(d, 100, 600, 780, th)
    compare_table(d, 100, 736, 780, th, COMPARE)
    shot = load_shot()
    frame, (fw, fh) = build_console(shot, 856, theme=th)
    fx, fy = 966, 200
    drop_shadow(canvas, (fx, fy, fx + fw, fy + fh), 12, blur=40, alpha=115)
    canvas.alpha_composite(frame, (fx, fy))
    cy0 = fy + fh + 38
    cw = (fw - 40) / 3
    for i, (n, t, ds) in enumerate(INNOV):
        innov_card(d, (fx + i * (cw + 20), cy0, cw, 182), n, t, ds, th, fs_title=20, fs_desc=15)
    footer(canvas, d, th, 1024, W)
    save(canvas, ov, "01-attribution-split.png")

# ================= 方案 2：控制台全景 + 左右数据栏（紫青） =================
def v2():
    W, H = 1920, 1080
    bg = add_grid(vgrad((W, H), [(0, (12, 8, 32)), (0.5, (20, 12, 48)), (1, (7, 6, 22))]), 60, (255, 255, 255, 9))
    bg = add_glow(bg, 960, 110, 920, 380, (120, 60, 220), 210, 0.85)
    bg = add_glow(bg, 960, 990, 780, 320, (20, 170, 210), 210, 0.6)
    canvas, ov, d = start((W, H), bg)
    th = themed(DARK, accent=(168, 85, 247, 255), accent2=(34, 211, 238, 255))
    header(canvas, d, th, 100, 54, 88, 54, sub_s=19)
    xs = W - 100
    for t in reversed(CHIPS[:3]):
        w = chip_w(d, t, fr(19), 20)
        xs -= w
        chip(d, xs, 72, t, fr(19), th, padx=20, pady=11)
        xs -= 14
    shot = load_shot()
    frame, (fw, fh) = build_console(shot, 1104, theme=th)
    fx, fy = (W - fw) // 2, 250
    drop_shadow(canvas, (fx, fy, fx + fw, fy + fh), 12, blur=46, alpha=125)
    canvas.alpha_composite(frame, (fx, fy))
    compare_table(d, 70, 300, 270, th, COMPARE, fs=20, title="同批实测 · 9 台语料")
    rx, ry = 1628, 300
    d.text((rx, ry - 46), "核心创新", font=fb(24), fill=th["accent"])
    for n, t, ds in INNOV:
        yy = ry
        d.text((rx, yy), n, font=lb(18), fill=th["accent"])
        yv = yy
        for ln in wrap(d, t, fb(21), 232 - 40):
            d.text((rx + 34, yv), ln, font=fb(21), fill=th["text"]); yv += 29
        yy = max(yv, yy + 30)
        for ln in wrap(d, ds, fr(16), 232)[:5]:
            d.text((rx, yy), ln, font=fr(16), fill=th["dim"]); yy += 24
        ry = yy + 26
    footer(canvas, d, th, 1032, W)
    save(canvas, ov, "02-console-rails.png")

# ================= 方案 3：控制台居左 · 信息居右（青绿） =================
def v3():
    W, H = 1920, 1080
    bg = add_grid(vgrad((W, H), [(0, (4, 20, 22)), (0.5, (6, 32, 34)), (1, (3, 12, 16))]), 54, (255, 255, 255, 9))
    bg = add_glow(bg, 700, 180, 720, 430, (20, 180, 170), 200, 0.8)
    bg = add_glow(bg, 1720, 920, 560, 380, (30, 120, 200), 200, 0.5)
    canvas, ov, d = start((W, H), bg)
    th = themed(DARK, accent=(45, 212, 191, 255))
    shot = load_shot()
    frame, (fw, fh) = build_console(shot, 1096, theme=th)
    fx, fy = 84, 196
    drop_shadow(canvas, (fx, fy, fx + fw, fy + fh), 12, blur=44, alpha=118)
    canvas.alpha_composite(frame, (fx, fy))
    cxs = fx
    for t in CHIPS:
        w, _ = chip(d, cxs, fy + fh + 34, t, fr(19), th, padx=20, pady=11)
        cxs += w + 14
    rx = 1290
    header(canvas, d, th, rx, 70, 92, 56, sub_s=18)
    d.line([(rx + 2, 232), (rx + 470, 232)], fill=th["accent"], width=2)
    kicker_line(d, rx, 272, th, fs=22)
    d.text((rx, 322), HEAD1, font=fb(56), fill=th["text"])
    d.text((rx, 322 + 72), HEAD2, font=fb(56), fill=th["accent"])
    yy = desc_block(d, rx, 502, 552, th, fs=20, lh=32, maxlines=4)
    yy += 18
    for n, t, ds in INNOV:
        innov_card(d, (rx, yy, 552, 112), n, t, ds, th, fs_title=21, fs_desc=15)
        yy += 122
    footer(canvas, d, th, 1032, W)
    save(canvas, ov, "03-console-left.png")

# ================= 方案 4：专业浅色（B 端质感） =================
def v4():
    W, H = 1920, 1080
    bg = add_grid(vgrad((W, H), [(0, (249, 251, 255)), (0.55, (236, 242, 251)), (1, (223, 232, 246))]), 54, (37, 99, 235, 13))
    bg = add_glow(bg, 1580, 110, 640, 380, (203, 224, 255), 200, 0.9)
    bg = add_glow(bg, 180, 1010, 540, 320, (224, 240, 255), 200, 0.9)
    canvas, ov, d = start((W, H), bg)
    th = LIGHT
    header(canvas, d, th, 100, 76, 100, 62, sub_s=20)
    d.line([(102, 246), (560, 246)], fill=th["accent"], width=3)
    kicker_line(d, 100, 300, th)
    headline(d, 100, 348, th, fs=74, lh=92)
    desc_block(d, 100, 550, 780, th, fs=22, lh=36, maxlines=3)
    cy0 = 680
    for i, (k, a, b) in enumerate(COMPARE):
        cw, cx = 250, 100 + i * 266
        d.rounded_rectangle([cx, cy0, cx + cw, cy0 + 84], 14, fill=th["card"], outline=th["cardStroke"], width=1)
        d.text((cx + 18, cy0 + 12), k, font=fr(19), fill=th["dim"])
        d.text((cx + 18, cy0 + 36), a, font=fb(34), fill=th["accent"])
        d.text((cx + 18 + d.textlength(a, font=fb(34)) + 14, cy0 + 50), "FirmAE " + b, font=fr(17), fill=th["dim2"])
    yy2 = 784
    for n, t, ds in INNOV:
        d.rounded_rectangle([100, yy2, 880, yy2 + 68], 12, fill=th["card"], outline=th["cardStroke"], width=1)
        d.rounded_rectangle([100, yy2 + 12, 104, yy2 + 56], 2, fill=th["accent"])
        d.text((124, yy2 + 12), n, font=lb(21), fill=th["accent"])
        d.text((166, yy2 + 11), t, font=fb(23), fill=th["text"])
        for ln in wrap(d, ds, fr(15), 740)[:1]:
            d.text((124, yy2 + 40), ln, font=fr(15), fill=th["dim"])
        yy2 += 76
    shot = load_shot()
    frame, (fw, fh) = build_console(shot, 856, theme=th, light=True)
    fx, fy = 962, 266
    drop_shadow(canvas, (fx, fy, fx + fw, fy + fh), 12, blur=42, alpha=70, offset=(0, 22))
    canvas.alpha_composite(frame, (fx, fy))
    bx, by = fx, fy + fh + 32
    d.rounded_rectangle([bx, by, bx + fw, by + 68], 14, fill=(255, 255, 255, 242), outline=th["cardStroke"], width=1)
    txt = "实时失败画像 · 四层链路探测 · 可插拔规则引擎"
    d.text((bx + 22, by + 18), txt, font=fb(20), fill=th["text"])
    d.text((bx + 22 + d.textlength(txt, font=fb(20)) + 18, by + 22), "M1 评测集 · Web 可达 66.7%", font=fr(17), fill=th["dim"])
    footer(canvas, d, th, 1032, W)
    save(canvas, ov, "04-light-pro.png")

# ================= 方案 5：蓝图巨幕（控制台为主体） =================
def v5():
    W, H = 1920, 1080
    bg = vgrad((W, H), [(0, (7, 18, 37)), (0.5, (8, 24, 48)), (1, (4, 10, 24))])
    bg = add_glow(bg, 960, 560, 920, 470, (20, 90, 190), 220, 0.75)
    bg = add_grid(bg, 120, (120, 190, 255, 16), sub=24)
    canvas, ov, d = start((W, H), bg)
    th = themed(DARK, accent=(34, 211, 238, 255))
    header(canvas, d, th, 96, 46, 78, 50, sub_s=None, tag=True)
    tn = lr(24)
    s = "Rehosting  ·  Linkprobe  ·  Rule Engine"
    d.text((W - 96 - d.textlength(s, font=tn), 72), s, font=tn, fill=th["accent"])
    d.text((W - 96 - d.textlength(KICKER, font=fb(20)), 108), KICKER, font=fb(20), fill=th["dim"])
    shot = load_shot()
    frame, (fw, fh) = build_console(shot, 1272, theme=th, chrome_ratio=0.031)
    fx, fy = (W - fw) // 2, 158
    drop_shadow(canvas, (fx, fy, fx + fw, fy + fh), 12, blur=50, alpha=130)
    canvas.alpha_composite(frame, (fx, fy))
    def vtext(x, cy, s, font, fill, ls=6):
        w = ls_width(d, s, font, ls)
        layer = Image.new("RGBA", (int(w) + 10, int(text_h(font)) + 10), (0, 0, 0, 0))
        draw_ls(ImageDraw.Draw(layer), (5, 5), s, font, fill, ls)
        layer = layer.rotate(90, expand=True)
        canvas.alpha_composite(layer, (int(x), int(cy - layer.size[1] / 2)))
    vtext(52, 560, HEAD1 + HEAD2, fr(26), th["dim"])
    vtext(W - 78, 560, FULLNAME, lr(18), (255, 255, 255, 95), ls=5)
    by = 1026
    d.line([(96, by), (W - 96, by)], fill=(255, 255, 255, 70), width=1)
    d.text((96, by + 20), "分层失败画像    ·    四层链路主动探测    ·    可插拔规则引擎", font=fb(22), fill=th["text"])
    d.text((W - 96 - d.textlength(FOOT_L + "  " + FOOT_TEAM, font=fr(21)), by + 21),
           FOOT_L + "  " + FOOT_TEAM, font=fr(21), fill=th["dim"])
    save(canvas, ov, "05-blueprint-hero.png")

# ================= 方案 6：方形 1:1 =================
def v6():
    W, H = 1080, 1080
    bg = add_grid(vgrad((W, H), [(0, (6, 12, 30)), (0.5, (10, 22, 48)), (1, (4, 8, 20))]), 54, (255, 255, 255, 10))
    bg = add_glow(bg, 540, 120, 540, 330, (28, 110, 220), 170, 0.95)
    bg = add_glow(bg, 540, 1040, 540, 270, (62, 62, 195), 180, 0.7)
    canvas, ov, d = start((W, H), bg)
    th = DARK
    canvas.alpha_composite(load_logo(94), (int(W / 2 - 47), 30))
    centered(d, W / 2, 130, WORDMARK, lb(58), th["text"], ls=8)
    centered(d, W / 2, 220, FULLNAME, lr(20), th["dim2"], ls=1)
    centered(d, W / 2, 258, KICKER, fb(22), th["accent"])
    centered(d, W / 2, 300, HEAD1, fb(52), th["text"])
    centered(d, W / 2, 362, HEAD2, fb(52), th["accent"])
    centered(d, W / 2, 442, "分层失败画像 · 四层链路探测 · 可插拔规则引擎", fb(17), th["dim"])
    shot = load_shot()
    frame, (fw, fh) = build_console(shot, 800, theme=th)
    fx, fy = int((W - fw) / 2), 474
    drop_shadow(canvas, (fx, fy, fx + fw, fy + fh), 12, blur=40, alpha=120)
    canvas.alpha_composite(frame, (fx, fy))
    save(canvas, ov, "06-square-1x1.png")

if __name__ == "__main__":
    v1(); v2(); v3(); v4(); v5(); v6()
    print("ALL DONE ->", OUT)
