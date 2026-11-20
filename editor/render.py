#!/usr/bin/env python3
"""Reels "A equipe da cirurgia em qualquer situação" — procedural VFX editor.

Usage:
  python3 render.py stills OUTDIR     -> preview frames of every scene
  python3 render.py video  OUTDIR     -> full render (with SFX + silent version)
"""
import math, os, sys, subprocess, wave
from multiprocessing import Pool
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageOps

W, H, FPS = 1080, 1920, 30
UP = os.environ.get("PHOTOS", "/root/.claude/uploads/af75da45-f17d-563b-bcb8-1c71ac64484a")
PH = {1: "7658a59e", 2: "138715e8", 3: "f7293a66", 4: "27635337", 5: "317657d7", 6: "6b1e98f8",
      7: "7eb547ba", 8: "daa82206", 9: "0158ea6c", 10: "57995877", 11: "a7fddd95", 12: "87919d37"}
FB = "/usr/share/fonts/opentype/inter/InterDisplay-Black.otf"
FBOLD = "/usr/share/fonts/opentype/inter/Inter-Bold.otf"
FMONO = "/usr/share/fonts/truetype/jetbrains-mono/JetBrainsMono-Bold.ttf"
YEL = (255, 221, 64)

Y = np.arange(H, dtype=np.float32)[:, None]
X = np.arange(W, dtype=np.float32)[None, :]


def ease(x):
    x = min(max(x, 0.0), 1.0)
    return x * x * (3 - 2 * x)


def ease_out(x):
    x = min(max(x, 0.0), 1.0)
    return 1 - (1 - x) ** 3


# ---------------------------------------------------------------- images
_cache = {}


def load_base(n, mode):
    key = (n, mode)
    if key in _cache:
        return _cache[key]
    im = ImageOps.exif_transpose(Image.open(f"{UP}/{PH[n]}-image.jpg")).convert("RGB")
    if mode == "fit":
        cw, ch = 1500, 2667
        s = max(cw / im.width, ch / im.height)
        bg = im.resize((int(im.width * s), int(im.height * s)), Image.BICUBIC)
        bg = bg.crop(((bg.width - cw) // 2, (bg.height - ch) // 2, (bg.width - cw) // 2 + cw, (bg.height - ch) // 2 + ch))
        bg = bg.filter(ImageFilter.GaussianBlur(45))
        bg = Image.eval(bg, lambda v: int(v * 0.5))
        fw = cw
        fh = int(im.height * fw / im.width)
        fg = im.resize((fw, fh), Image.BICUBIC)
        bg.paste(fg, (0, int(ch * 0.42 - fh / 2)))
        im = bg
    _cache[key] = im
    return im


class Cam:
    """Maps an image-space window to the 1080x1920 output."""

    def __init__(self, base, fx, fy, z, dx=0, dy=0):
        bw, bh = base.size
        c = max(W / bw, H / bh)
        ww, wh = W / (c * z), H / (c * z)
        cx = fx * bw + dx / (c * z)
        cy = fy * bh + dy / (c * z)
        cx = min(max(cx, ww / 2), bw - ww / 2)
        cy = min(max(cy, wh / 2), bh - wh / 2)
        self.box = (cx - ww / 2, cy - wh / 2, cx + ww / 2, cy + wh / 2)
        self.bw, self.bh = bw, bh

    def render(self, base):
        return base.transform((W, H), Image.EXTENT, self.box, Image.BICUBIC)

    def map(self, fx, fy):
        x0, y0, x1, y1 = self.box
        return ((fx * self.bw - x0) / (x1 - x0) * W, (fy * self.bh - y0) / (y1 - y0) * H)


# ---------------------------------------------------------------- text
_font = {}


def font(path, size):
    k = (path, size)
    if k not in _font:
        _font[k] = ImageFont.truetype(path, size)
    return _font[k]


def text_layer(text, kind):
    if kind == "main":
        path, size, fill, sw = FB, 92, YEL, 8
    elif kind == "big":
        path, size, fill, sw = FB, 104, YEL, 9
    elif kind == "white":
        path, size, fill, sw = FB, 70, (255, 255, 255), 7
    else:  # sub
        path, size, fill, sw = FBOLD, 46, (255, 255, 255), 5
    maxw = W * 0.86
    while True:
        f = font(path, size)
        bb = f.getbbox(text, stroke_width=sw)
        if bb[2] - bb[0] <= maxw or size < 30:
            break
        size -= 4
    tw, th = bb[2] - bb[0], bb[3] - bb[1]
    pad = 30
    lay = Image.new("RGBA", (tw + 2 * pad, th + 2 * pad), (0, 0, 0, 0))
    d = ImageDraw.Draw(lay)
    d.text((pad - bb[0], pad - bb[1]), text, font=f, fill=fill + (255,), stroke_width=sw, stroke_fill=(0, 0, 0, 255))
    # soft drop shadow
    a = lay.split()[3]
    sh = Image.new("RGBA", lay.size, (0, 0, 0, 0))
    sh.putalpha(a.filter(ImageFilter.GaussianBlur(9)).point(lambda v: int(v * 0.55)))
    out = Image.new("RGBA", lay.size, (0, 0, 0, 0))
    out.alpha_composite(sh, (0, 8))
    out.alpha_composite(lay)
    return out


def paste_text(img, lay, ycenter, age):
    """Pop-in animation: scale 1.25 -> 1.0, fade in."""
    k = ease_out(age / 0.16)
    s = 1.25 - 0.25 * k
    if abs(s - 1) > 1e-3:
        lay = lay.resize((max(1, int(lay.width * s)), max(1, int(lay.height * s))), Image.BICUBIC)
    if k < 1:
        a = lay.split()[3].point(lambda v: int(v * k))
        lay = lay.copy()
        lay.putalpha(a)
    img.alpha_composite(lay, (int(W / 2 - lay.width / 2), int(ycenter - lay.height / 2)))


# ---------------------------------------------------------------- HUD (camcorder)
def hud(img, gframe):
    d = ImageDraw.Draw(img)
    col = (255, 255, 255, 225)
    m, top, bot, L, t = 70, 230, H - 380, 74, 7
    for (x, y, sx, sy) in [(m, top, 1, 1), (W - m, top, -1, 1), (m, bot, 1, -1), (W - m, bot, -1, -1)]:
        d.rectangle([min(x, x + sx * L), min(y, y + sy * t), max(x, x + sx * L), max(y, y + sy * t)], fill=col)
        d.rectangle([min(x, x + sx * t), min(y, y + sy * L), max(x, x + sx * t), max(y, y + sy * L)], fill=col)
    # battery
    bx, by = m + 40, top + 40
    d.rounded_rectangle([bx, by, bx + 70, by + 34], 5, outline=col, width=4)
    d.rectangle([bx + 70, by + 10, bx + 77, by + 24], fill=col)
    for i in range(3):
        d.rectangle([bx + 8 + i * 19, by + 8, bx + 22 + i * 19, by + 26], fill=col)
    # REC
    sec = gframe / FPS
    f = font(FMONO, 40)
    if int(sec * 2) % 2 == 0:
        d.ellipse([W - m - 205, top + 46, W - m - 179, top + 72], fill=(255, 40, 40, 255))
    d.text((W - m - 165, top + 36), "REC", font=f, fill=col)
    # timecode + date
    tc = f"00:00:{int(sec):02d}:{gframe % FPS:02d}"
    f2 = font(FMONO, 34)
    d.text((m + 40, bot - 80), tc, font=f2, fill=col)
    d.text((W - m - 280, bot - 80), "20 NOV 2026", font=f2, fill=col)


# ---------------------------------------------------------------- prerendered props
def make_duck():
    S = 2
    im = Image.new("RGBA", (260 * S, 220 * S), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    s = lambda *a: [v * S for v in a]
    d.polygon(s(20, 120, 60, 95, 60, 150), fill=(240, 190, 0, 255))
    d.ellipse(s(25, 100, 215, 200), fill=(255, 212, 0, 255))
    d.ellipse(s(60, 112, 170, 160), fill=(255, 232, 80, 255))
    d.ellipse(s(110, 20, 210, 120), fill=(255, 212, 0, 255))
    d.ellipse(s(130, 35, 165, 60), fill=(255, 238, 120, 255))
    d.ellipse(s(190, 70, 250, 100), fill=(255, 120, 20, 255))
    d.ellipse(s(170, 48, 186, 66), fill=(20, 20, 20, 255))
    d.ellipse(s(175, 51, 180, 56), fill=(255, 255, 255, 255))
    return im.resize((185, 156), Image.LANCZOS)


def make_ufo():
    S = 2
    im = Image.new("RGBA", (600 * S, 300 * S), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    s = lambda *a: [v * S for v in a]
    d.ellipse(s(200, 45, 400, 165), fill=(110, 240, 210, 150))
    d.ellipse(s(240, 60, 300, 95), fill=(230, 255, 250, 150))
    d.ellipse(s(20, 115, 580, 215), fill=(105, 110, 122, 255))
    d.ellipse(s(30, 112, 570, 190), fill=(165, 170, 182, 255))
    d.ellipse(s(80, 118, 520, 160), fill=(214, 218, 228, 255))
    d.ellipse(s(150, 175, 450, 232), fill=(60, 64, 74, 255))
    d.ellipse(s(230, 192, 370, 226), fill=(160, 255, 170, 255))
    im = im.resize((480, 240), Image.LANCZOS)
    glow = Image.new("RGBA", im.size, (0, 0, 0, 0))
    glow.putalpha(im.split()[3].filter(ImageFilter.GaussianBlur(14)).point(lambda v: int(v * 0.5)))
    g2 = Image.new("RGBA", im.size, (120, 255, 160, 0))
    g2.putalpha(glow.split()[3])
    out = Image.new("RGBA", im.size, (0, 0, 0, 0))
    out.alpha_composite(g2)
    out.alpha_composite(im)
    return out


def make_banner():
    im = Image.new("RGBA", (W, 190), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rectangle([0, 0, W, 116], fill=(200, 16, 30, 240))
    d.rectangle([0, 116, W, 190], fill=(10, 10, 10, 235))
    d.polygon([(60, 92), (100, 22), (140, 92)], fill=(255, 221, 64, 255))
    d.text((100, 76), "!", font=font(FB, 52), fill=(0, 0, 0, 255), anchor="ms")
    d.text((168, 58), "ALERTA DE TSUNAMI", font=font(FB, 64), fill=(255, 255, 255, 255), anchor="lm")
    return im


TICKER = ("URGENTE: ONDA DE 30 METROS A CAMINHO DO HOSPITAL  •  EQUIPE DA CIRURGIA SE RECUSA A SAIR: "
          "\"FALTA SÓ FECHAR A PELE\"  •  ")


def make_ticker():
    f = font(FMONO, 36)
    tw = int(math.ceil(f.getlength(TICKER)))
    one = Image.new("RGBA", (tw, 56), (0, 0, 0, 0))
    ImageDraw.Draw(one).text((0, 4), TICKER, font=f, fill=(255, 221, 64, 255))
    n = W // tw + 2
    strip = Image.new("RGBA", (tw * n, 56), (0, 0, 0, 0))
    for k in range(n):
        strip.alpha_composite(one, (k * tw, 0))
    strip.info["period"] = tw
    return strip


def noise_tex(h, w, seed, octs=((6, 18), (12, 36), (24, 72), (48, 144))):
    rng = np.random.default_rng(seed)
    acc = np.zeros((h, w), np.float32)
    amp, tot = 1.0, 0.0
    for (gw, gh) in octs:
        r = (rng.random((gh, gw)) * 255).astype(np.uint8)
        up = np.asarray(Image.fromarray(r).resize((w, h), Image.BICUBIC), np.float32) / 255
        acc += up * amp
        tot += amp
        amp *= 0.55
    acc /= tot
    acc = (acc - acc.min()) / (acc.max() - acc.min())
    return acc


_props = {}


def prop(name):
    if name not in _props:
        _props[name] = {"duck": make_duck, "ufo": make_ufo, "banner": make_banner, "ticker": make_ticker,
                        "fire": lambda: noise_tex(2 * H, W, 7),
                        "smoke": lambda: noise_tex(H, W, 11, ((3, 6), (6, 12), (12, 24)))}[name]()
    return _props[name]


def blob_layer(points, blur):
    """points: (x, y, r, a) -> float32 HxW layer 0..1"""
    lay = Image.new("L", (W, H), 0)
    d = ImageDraw.Draw(lay)
    for x, y, r, a in points:
        d.ellipse([x - r, y - r, x + r, y + r], fill=int(255 * a))
    if blur:
        lay = lay.filter(ImageFilter.GaussianBlur(blur))
    return np.asarray(lay, np.float32) / 255


def gray(a):
    return a.mean(axis=2, keepdims=True)


# ---------------------------------------------------------------- effects
def fx_flood(a, tl, S, cam):
    p = ease(tl / 0.7)
    level = H - p * H * 0.48
    xs = X[0]
    surf = level + 18 * np.sin(xs / 85 + tl * 3.2) + 7 * np.sin(xs / 31 - tl * 5.1)
    under = Y > surf[None, :]
    depth = np.clip((Y - surf[None, :]) / 450, 0, 1)
    shift = (9 * np.sin(Y[:, 0] / 23 + tl * 6)).astype(np.int32)
    idx = (np.arange(W)[None, :] + shift[:, None]) % W
    refr = np.take_along_axis(a, idx[..., None].repeat(3, 2), axis=1)
    alpha = (0.5 + 0.42 * depth)[..., None]
    water = refr * (1 - alpha) + np.array([92, 80, 52], np.float32) * alpha
    caus = np.sin(X / 38 + Y / 50 + tl * 3) * np.sin(X / 27 - Y / 31 + tl * 2.3)
    water += (np.clip(caus, 0.55, 1) - 0.55)[..., None] * 90 * (1 - depth[..., None])
    out = np.where(under[..., None], water, a * np.array([0.95, 0.95, 0.9], np.float32))
    band = np.exp(-((Y - surf[None, :]) ** 2) / 18)
    out += band[..., None] * np.array([210, 205, 180], np.float32) * 0.55
    img = Image.fromarray(np.clip(out, 0, 255).astype(np.uint8)).convert("RGBA")
    # floating rubber duck
    dx = W * (0.86 - 0.06 * tl / 2.2)
    i = int(min(max(dx, 0), W - 1))
    duck = prop("duck").rotate(6 * math.sin(tl * 3.4), Image.BICUBIC, expand=True)
    dy = surf[i] - duck.height * 0.72 + 8 * math.sin(tl * 4)
    if p > 0.5:
        img.alpha_composite(duck, (int(dx - duck.width / 2), int(dy)))
    return np.asarray(img.convert("RGB"), np.float32)


def fx_blackout(a, tl, S, cam):
    flick = [1, 0.12, 0.9, 0.05, 0.6, 0.04, 0.04, 0.3, 0.04]
    if tl < 0.4:
        lvl = 1.0
    elif tl < 0.4 + len(flick) / FPS * 1.5:
        lvl = flick[int((tl - 0.4) * FPS / 1.5)]
    else:
        lvl = 0.04
    dark = a * np.array([0.35, 0.42, 0.6], np.float32)
    if tl < 1.0:
        return a * lvl + dark * 0 if lvl > 0.5 else a * lvl * np.array([0.6, 0.7, 1.0], np.float32)
    k = ease_out((tl - 1.0) / 0.45)
    cx = W * (0.25 + 0.27 * k) + 12 * math.sin(tl * 7)
    cy = H * (0.62 - 0.22 * k) + 10 * math.cos(tl * 5)
    r = 330
    m = np.exp(-(((X - cx) ** 2 + (Y - cy) ** 2) / (2 * r * r)) ** 1.6)
    halo = np.exp(-((X - cx) ** 2 + (Y - cy) ** 2) / (2 * (r * 2.2) ** 2)) * 0.12
    light = (0.035 + 1.1 * m + halo)[..., None]
    return a * light * np.array([1.05, 1.0, 0.92], np.float32)


def fx_fire(a, tl, S, cam):
    tex = prop("fire")
    o1 = int(tl * 700) % H
    o2 = int(tl * 430 + 300) % H
    n = 0.6 * tex[o1:o1 + H] + 0.4 * np.roll(tex[o2:o2 + H], 400, axis=1)
    ramp = 0.65 + 0.35 * ease(tl / 1.2)
    grad = np.clip((Y / H - 0.40) / 0.6, 0, 1) ** 1.2
    f = np.clip((n * 0.75 + grad * 1.05 * ramp - 0.95) * 2.6, 0, 1)
    fire = np.stack([np.clip(f * 2.2, 0, 1) * 255, np.clip(f * 2.2 - 0.7, 0, 1) * 200,
                     np.clip(f * 2.2 - 1.5, 0, 1) * 170], -1)
    flick = 0.93 + 0.07 * math.sin(tl * 37) * math.sin(tl * 23)
    base = a * np.array([1.18, 0.86, 0.62], np.float32) * flick
    base += (np.clip(Y / H - 0.3, 0, 1) ** 2)[..., None] * np.array([150, 60, 10], np.float32) * ramp
    sm = prop("smoke")
    so = int(tl * 60) % H
    smoke = np.clip((0.42 - Y / H) / 0.42, 0, 1) * (0.5 + 0.5 * np.roll(sm, so, axis=0))
    base *= (1 - 0.6 * smoke)[..., None]
    out = base * (1 - 0.6 * f[..., None]) + fire * 1.15
    rng = np.random.default_rng(3)
    N = 150
    x0, ph, v, r, sw = rng.random(N) * W, rng.random(N), rng.uniform(260, 700, N), rng.uniform(2, 6, N), rng.random(N) * 6
    yy = H * 1.05 - ((ph * H + v * tl) % H)
    xx = x0 + 40 * np.sin(tl * 3 + sw)
    pts = [(xx[i], yy[i], r[i], min(1, (yy[i] / H - 0.15) * 3)) for i in range(N) if yy[i] > 0.15 * H]
    em = blob_layer(pts, 2)
    out += em[..., None] * np.array([255, 170, 70], np.float32) * 1.6
    return out


def fx_tsunami(a, tl, S, cam):
    g = gray(a)
    out = (a * 0.65 + g * 0.35) * np.array([0.78, 0.9, 1.05], np.float32) * 0.86
    # rain
    rng = np.random.default_rng(5)
    N = 230
    x0, y0, ln = rng.random(N) * (W + 400), rng.random(N) * (H + 200), rng.uniform(50, 110, N)
    lay = Image.new("L", (W, H), 0)
    d = ImageDraw.Draw(lay)
    for i in range(N):
        y = (y0[i] + 2600 * tl) % (H + 200) - 100
        x = (x0[i] - 700 * tl) % (W + 400) - 200
        d.line([(x, y), (x - ln[i] * 0.27, y + ln[i])], fill=110, width=2)
    out += (np.asarray(lay, np.float32) / 255)[..., None] * 140
    # incoming wave wipe (last 0.5s)
    D = S["dur"]
    if tl > D - 0.55:
        k = (tl - (D - 0.55)) / 0.55
        front = -0.3 * W + k * 1.7 * W + 110 * np.sin(Y / 95 + tl * 9) + 40 * np.sin(Y / 31)
        tex = prop("fire")
        n = tex[:H] if True else None
        inside = (X < front)
        wcol = np.array([18, 58, 72], np.float32)[None, None] + (n[..., None] * 60)
        out = np.where(inside[..., None], wcol, out)
        foam = np.exp(-((X - front) ** 2) / (2 * 75 ** 2)) * (0.45 + 0.55 * n)
        out += foam[..., None] * 230
    img = Image.fromarray(np.clip(out, 0, 255).astype(np.uint8)).convert("RGBA")
    b = prop("banner")
    by = 300 - int(200 * (1 - ease_out((tl - 0.05) / 0.25)))
    img.alpha_composite(b, (0, by))
    strip = prop("ticker")
    tx = int(tl * 520) % strip.info["period"]
    img.alpha_composite(strip.crop((tx, 0, tx + W, strip.height)), (0, by + 128))
    return np.asarray(img.convert("RGB"), np.float32)


def crack_segments(seed, ox, oy, t0, t1, branches=7):
    rng = np.random.default_rng(seed)
    segs = []
    for b in range(branches):
        ang = b / branches * 2 * math.pi + rng.uniform(-0.3, 0.3)
        x, y, dist = ox, oy, 0
        steps = rng.integers(7, 15)
        for s in range(steps):
            ang += rng.uniform(-0.55, 0.55)
            L = rng.uniform(35, 95)
            nx, ny = x + L * math.cos(ang), y + L * math.sin(ang)
            segs.append((x, y, nx, ny, t0 + (t1 - t0) * (dist / 900)))
            if rng.random() < 0.18:
                a2 = ang + rng.choice([-1, 1]) * rng.uniform(0.6, 1.1)
                bx, by = nx, ny
                for _ in range(3):
                    L2 = rng.uniform(25, 60)
                    cx, cy = bx + L2 * math.cos(a2), by + L2 * math.sin(a2)
                    segs.append((bx, by, cx, cy, t0 + (t1 - t0) * (dist / 900) + 0.05))
                    bx, by = cx, cy
            x, y, dist = nx, ny, dist + L
    return segs


CRACKS = crack_segments(1, W * 0.78, H * 0.33, 0.3, 0.9) + crack_segments(2, W * 0.18, H * 0.72, 1.15, 1.7, 6)


def fx_quake(a, tl, S, cam):
    out = a * 0.9 + np.array([180, 160, 130], np.float32) * 0.1
    rng = np.random.default_rng(9)
    N = 120
    x0, y0, v, r = rng.random(N) * W, rng.random(N) * H, rng.uniform(300, 800, N), rng.uniform(1.5, 4.5, N)
    pts = [(x0[i] + 15 * math.sin(tl * 4 + i), (y0[i] + v[i] * tl) % H, r[i], 0.7) for i in range(N)]
    out += blob_layer(pts, 1)[..., None] * np.array([200, 185, 160], np.float32) * 0.8
    img = Image.fromarray(np.clip(out, 0, 255).astype(np.uint8)).convert("RGBA")
    lay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(lay)
    for (x, y, nx, ny, ts) in CRACKS:
        if tl >= ts:
            d.line([(x + 2, y + 2), (nx + 2, ny + 2)], fill=(255, 255, 255, 200), width=2)
            d.line([(x, y), (nx, ny)], fill=(15, 15, 15, 235), width=4)
    img.alpha_composite(lay)
    return np.asarray(img.convert("RGB"), np.float32)


METEORS = [(0.10, 1.05, -0.1, 0.62), (0.55, 0.85, -0.15, 0.55), (0.95, 1.2, 0.05, 0.6), (1.35, 0.6, -0.12, 0.66)]


def meteor_impacts():
    out = []
    for (t0, x0, y0, ang) in METEORS:
        out.append(t0 + 0.42)
    return out


def fx_meteor(a, tl, S, cam):
    g = np.clip(1 - Y / (H * 0.55), 0, 1)[..., None]
    out = a * np.array([1.0, 0.82, 0.9], np.float32) * 0.78 + g * np.array([90, 30, 70], np.float32) * 0.6
    lay = Image.new("RGB", (W, H), (0, 0, 0))
    d = ImageDraw.Draw(lay)
    flash = 0.0
    for (t0, x0, y0, ang) in METEORS:
        if tl < t0:
            continue
        dt = tl - t0
        sp = 3600
        dx, dy = -math.cos(ang), math.sin(ang)
        hx, hy = x0 * W + dx * sp * dt, y0 * H + dy * sp * dt
        if dt < 0.42:
            for wdt, colr, Lm in [(26, (120, 40, 10), 700), (12, (255, 150, 40), 520), (4, (255, 245, 210), 360)]:
                d.line([(hx, hy), (hx - dx * Lm, hy - dy * Lm)], fill=colr, width=wdt)
            d.ellipse([hx - 14, hy - 14, hx + 14, hy + 14], fill=(255, 250, 230))
        imp = t0 + 0.42
        if tl >= imp:
            flash += math.exp(-(tl - imp) / 0.12)
    glow = np.asarray(lay.filter(ImageFilter.GaussianBlur(14)), np.float32)
    out += np.asarray(lay, np.float32) * 0.9 + glow * 1.6
    out += flash * np.array([255, 190, 120], np.float32) * 0.55
    return out


def fx_alien(a, tl, S, cam):
    out = a * np.array([0.72, 1.0, 0.82], np.float32) * 0.78
    ux = W * 0.5 + 40 * math.sin(tl * 2.1)
    uy = -260 + (H * 0.16 + 260) * ease_out(tl / 0.55) + 12 * math.sin(tl * 3.3)
    on = 0.0
    if tl > 0.6:
        on = 1.0 if tl > 0.8 else [1, 0, 1, 0, 1, 1][int((tl - 0.6) * FPS) % 6]
    if on:
        m = Image.new("L", (W, H), 0)
        ImageDraw.Draw(m).polygon([(ux - 80, uy + 90), (ux + 80, uy + 90), (ux + 470, H * 0.86), (ux - 470, H * 0.86)], fill=255)
        mm = np.asarray(m.filter(ImageFilter.GaussianBlur(28)), np.float32) / 255
        scan = 0.85 + 0.15 * np.sin(Y / 7 - tl * 30)
        fade = np.clip(1 - (Y - uy) / (H * 0.9), 0.25, 1)
        beam = mm * scan * fade * on
        out = out * (1 + 0.45 * beam[..., None]) + beam[..., None] * np.array([90, 255, 140], np.float32) * 0.33
        rng = np.random.default_rng(4)
        N = 70
        px, ph, v = rng.uniform(-1, 1, N), rng.random(N), rng.uniform(200, 500, N)
        pts = []
        for i in range(N):
            yy = H * 0.86 - ((ph[i] * H * 0.7 + v[i] * tl) % (H * 0.7))
            frac = (yy - uy) / (H * 0.86 - uy)
            xx = ux + px[i] * (80 + 390 * frac)
            pts.append((xx, yy, 3, 0.9))
        out += blob_layer(pts, 2)[..., None] * np.array([170, 255, 190], np.float32)
    img = Image.fromarray(np.clip(out, 0, 255).astype(np.uint8)).convert("RGBA")
    ufo = prop("ufo").rotate(4 * math.sin(tl * 2.6), Image.BICUBIC, expand=True)
    img.alpha_composite(ufo, (int(ux - ufo.width / 2), int(uy - ufo.height / 2)))
    d = ImageDraw.Draw(img)
    cols = [(255, 60, 60), (255, 220, 60), (60, 220, 255), (120, 255, 120)]
    for i in range(7):
        lx = ux - 170 + i * 57
        c = cols[(i + int(tl * 8)) % 4]
        d.ellipse([lx - 9, uy + 40 - 9, lx + 9, uy + 40 + 9], fill=c + (255,))
    return np.asarray(img.convert("RGB"), np.float32)


_frost = None


def fx_snow(a, tl, S, cam):
    global _frost
    g = gray(a)
    out = (a * 0.7 + g * 0.3) * np.array([0.88, 0.98, 1.14], np.float32) + 18
    if _frost is None:
        n = noise_tex(H, W, 21, ((10, 18), (20, 36), (40, 72), (80, 144)))
        e = np.minimum(np.minimum(X, W - X), np.minimum(Y, H - Y)) / 260
        _frost = np.clip((1 - e) * 1.2 + (n - 0.5) * 1.1, 0, 1) ** 1.5
    out = out * (1 - 0.75 * _frost[..., None]) + _frost[..., None] * np.array([235, 245, 255], np.float32) * 0.75
    rng = np.random.default_rng(8)
    for (N, rr, v, blur, al) in [(160, (2, 4), 140, 0, 0.75), (90, (4, 7), 260, 1, 0.9), (26, (9, 15), 480, 5, 0.9)]:
        x0, y0, r, ph = rng.random(N) * W, rng.random(N) * H, rng.uniform(*rr, N), rng.random(N) * 6
        pts = [((x0[i] + 160 * tl + 30 * math.sin(tl * 2 + ph[i])) % W, (y0[i] + v * tl) % (H + 40) - 20, r[i], al) for i in range(N)]
        out += blob_layer(pts, blur)[..., None] * 255 * 0.85
    return out


def fx_danger(a, tl, S, cam):
    if tl < 0.95:
        return a
    k = ease_out((tl - 0.95) / 0.2)
    g = gray(a)
    out = a * (1 - 0.55 * k) + g * 0.55 * k
    r2 = ((X - W / 2) ** 2 + (Y - H * 0.45) ** 2) / (W * W)
    vig = np.clip(r2 * 1.3, 0, 1)[..., None] * k
    out = out * (1 - vig * 0.65) + vig * np.array([120, 0, 0], np.float32) * 0.5
    img = Image.fromarray(np.clip(out, 0, 255).astype(np.uint8)).convert("RGBA")
    if tl > 1.15:
        cx, cy = cam.map(0.598, 0.385)
        r = 250
        prog = ease_out((tl - 1.15) / 0.25)
        d = ImageDraw.Draw(img)
        d.arc([cx - r, cy - r * 1.1, cx + r, cy + r * 1.1], -90, -90 + 360 * prog, fill=(230, 20, 20, 255), width=14)
    return np.asarray(img.convert("RGB"), np.float32)


def fx_end(a, tl, S, cam):
    out = a * np.array([1.05, 1.0, 0.92], np.float32)
    if tl < 1.7:
        return out
    img = Image.fromarray(np.clip(out, 0, 255).astype(np.uint8)).convert("RGBA")
    cx, cy = cam.map(0.06, 0.295)
    prog = ease_out((tl - 1.75) / 0.25)
    d = ImageDraw.Draw(img)
    r = 150
    d.arc([cx - r, cy - r * 0.85, cx + r, cy + r * 0.85], -90, -90 + 360 * prog, fill=(255, 221, 64, 255), width=12)
    if prog > 0.9:
        bob = 12 * math.sin(tl * 9)
        ax, ay = cx + r * 0.8 + bob, cy - r * 0.75 - bob
        d.line([(ax + 150, ay - 150), (ax + 20, ay - 20)], fill=(255, 221, 64, 255), width=16)
        d.polygon([(ax, ay), (ax + 70, ay - 12), (ax + 12, ay - 70)], fill=(255, 221, 64, 255))
    return np.asarray(img.convert("RGB"), np.float32)


# ---------------------------------------------------------------- scenes
def cam_static(fx, fy, z0, z1):
    return lambda tl, D: (fx, fy, z0 + (z1 - z0) * ease(tl / D))


def cam_danger(tl, D):
    if tl < 0.95:
        return (0.45, 0.42, 1.0 + 0.05 * tl)
    k = ease_out((tl - 0.95) / 0.22)
    return (0.45 + (0.598 - 0.45) * k, 0.42 + (0.415 - 0.42) * k, 1.05 + (2.25 - 1.05) * k + 0.08 * (tl - 0.95))


def cam_end(tl, D):
    if tl < 1.6:
        return (0.42, 0.42, 1.0 + 0.04 * tl)
    k = ease_out((tl - 1.6) / 0.25)
    return (0.42 - 0.17 * k, 0.42 - 0.08 * k, 1.06 + 0.5 * k)


def shake_none(tl, D):
    return (0, 0, 0)


def shake_quake(tl, D):
    amp = 26 * (0.55 + 0.45 * abs(math.sin(tl * 2.3))) * (1 if tl > 0.15 else tl / 0.15)
    return (amp * math.sin(tl * 47) + amp * 0.5 * math.sin(tl * 83), amp * math.cos(tl * 53), 1.3 * math.sin(tl * 31))


def shake_meteor(tl, D):
    s = 0
    for t in meteor_impacts():
        if tl >= t:
            s += 30 * math.exp(-(tl - t) / 0.15)
    return (s * math.sin(tl * 90), s * math.cos(tl * 70), 0.03 * s * math.sin(tl * 60))


def shake_soft(amp):
    return lambda tl, D: (amp * math.sin(tl * 13), amp * math.cos(tl * 11), 0)


def shake_tsunami(tl, D):
    amp = 6 + (22 if tl > D - 0.55 else 0)
    return (amp * math.sin(tl * 41), amp * math.cos(tl * 37), 0.04 * amp * math.sin(tl * 29))


def shake_alien(tl, D):
    a = 10 * math.exp(-max(tl - 0.6, 0) / 0.3) if tl > 0.6 else 0
    return (a * math.sin(tl * 70), a * math.cos(tl * 60), 0)


MY, SY = 0.60, 0.665
SCENES = [
    dict(name="intro", img=9, mode="fit", dur=2.4, cam=cam_static(0.5, 0.5, 1.02, 1.10), shake=shake_none, fx=None,
         caps=[(0.05, None, "A equipe da cirurgia geral", "white", 0.665), (0.75, None, "em QUALQUER situação:", "main", 0.72)]),
    dict(name="enchente", img=5, mode="cover", dur=2.2, cam=cam_static(0.56, 0.42, 1.04, 1.12), shake=shake_soft(3), fx=fx_flood,
         caps=[(0.1, None, "Na enchente", "main", MY), (0.75, None, "(o patinho é o instrumentador)", "sub", SY)]),
    dict(name="apagao", img=2, mode="cover", dur=2.3, cam=cam_static(0.5, 0.4, 1.0, 1.08), shake=shake_none, fx=fx_blackout,
         caps=[(0.1, None, "No apagão", "main", MY), (1.15, None, "(gerador? que gerador?)", "sub", SY)]),
    dict(name="incendio", img=12, mode="cover", dur=2.2, cam=cam_static(0.5, 0.45, 1.04, 1.12), shake=shake_soft(4), fx=fx_fire,
         caps=[(0.1, None, "No incêndio", "main", MY), (0.7, None, "(selfie primeiro, extintor depois)", "sub", SY)]),
    dict(name="tsunami", img=6, mode="cover", dur=2.4, cam=cam_static(0.5, 0.45, 1.06, 1.14), shake=shake_tsunami, fx=fx_tsunami,
         caps=[(0.1, None, "No tsunami", "main", MY), (0.7, None, "(ninguém larga a pinça)", "sub", SY)]),
    dict(name="terremoto", img=1, mode="cover", dur=2.2, cam=cam_static(0.45, 0.33, 1.12, 1.18), shake=shake_quake, fx=fx_quake,
         caps=[(0.1, None, "No terremoto", "main", MY), (0.7, None, "(magnitude 8.9 e o biquinho nem tremeu)", "sub", SY)]),
    dict(name="meteoros", img=7, mode="cover", dur=2.2, cam=cam_static(0.45, 0.42, 1.06, 1.12), shake=shake_meteor, fx=fx_meteor,
         caps=[(0.1, None, "Na chuva de meteoros", "main", MY), (0.7, None, "(tudo sob controle, chefe)", "sub", SY)]),
    dict(name="aliens", img=11, mode="cover", dur=2.6, cam=cam_static(0.5, 0.38, 1.02, 1.10), shake=shake_alien, fx=fx_alien,
         caps=[(0.1, None, "Na invasão alienígena", "main", MY), (0.9, None, "(abdução só depois da sutura)", "sub", SY)]),
    dict(name="nevasca", img=8, mode="cover", dur=2.1, cam=cam_static(0.45, 0.38, 1.03, 1.10), shake=shake_soft(2), fx=fx_snow,
         caps=[(0.1, None, "Na nevasca", "main", MY), (0.7, None, "(vulgo ar-condicionado do centro cirúrgico)", "sub", SY)]),
    dict(name="perigo", img=10, mode="cover", dur=3.3, cam=cam_danger, shake=shake_none, fx=fx_danger,
         caps=[(0.1, 0.95, "Mas o maior perigo de todos...", "main", MY), (1.2, None, "...é esse olhar aqui", "big", 0.70)]),
    dict(name="copa", img=3, mode="cover", dur=3.9, cam=cam_end, shake=shake_none, fx=fx_end,
         caps=[(0.1, 1.6, "E a única tragédia que", "white", 0.60), (0.1, 1.6, "ninguém sobrevive:", "main", 0.655),
               (1.65, None, "acabar o café da copa", "big", 0.66), (2.25, None, "(socorro)", "sub", 0.715)]),
]

_vig = None
_grain = None


def render_frame(si, f, gframe):
    global _vig, _grain
    S = SCENES[si]
    D = S["dur"]
    tl = f / FPS
    base = load_base(S["img"], S["mode"])
    fx_, fy_, z = S["cam"](tl, D)
    if si > 0:
        z *= 1 + 0.07 * math.exp(-tl / 0.09)  # punch-in on cut
    dx, dy, rot = S["shake"](tl, D)
    if rot:
        z *= 1.06
    cam = Cam(base, fx_, fy_, z, dx, dy)
    img = cam.render(base)
    if rot:
        img = img.rotate(rot, Image.BICUBIC)
    a = np.asarray(img, np.float32)
    if S["fx"]:
        S["dur"] = D
        a = S["fx"](a, tl, S, cam)
    if _vig is None:
        r2 = ((X - W / 2) / W) ** 2 + ((Y - H / 2) / H) ** 2
        _vig = (1 - 0.55 * r2)[..., None].astype(np.float32)
        rng = np.random.default_rng(0)
        _grain = [rng.normal(0, 6, (H, W, 1)).astype(np.float32) for _ in range(6)]
    a = a * _vig
    img = Image.fromarray(np.clip(a, 0, 255).astype(np.uint8)).convert("RGBA")
    hud(img, gframe)
    for (t0, t1, text, kind, yc) in S["caps"]:
        if tl >= t0 and (t1 is None or tl < t1):
            key = ("cap", text, kind)
            if key not in _props:
                _props[key] = text_layer(text, kind)
            paste_text(img, _props[key], yc * H, tl - t0)
    a = np.asarray(img.convert("RGB"), np.float32)
    if si > 0 and tl < 0.17:
        k = 1 - tl / 0.17
        s = int(14 * k)
        a[..., 0] = np.roll(a[..., 0], s, axis=1)
        a[..., 2] = np.roll(a[..., 2], -s, axis=1)
        a = a + 255 * 0.55 * k ** 2
    a = a + _grain[gframe % 6]
    return np.clip(a, 0, 255).astype(np.uint8)


def scene_starts():
    st, t = [], 0.0
    for S in SCENES:
        st.append(t)
        t += S["dur"]
    return st, t


def render_scene(args):
    si, out = args
    st, _ = scene_starts()
    S = SCENES[si]
    n = int(round(S["dur"] * FPS))
    g0 = int(round(st[si] * FPS))
    p = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS),
                          "-i", "-", "-c:v", "libx264", "-preset", "slow", "-crf", "15", "-pix_fmt", "yuv420p",
                          "-profile:v", "high", out], stdin=subprocess.PIPE)
    for f in range(n):
        p.stdin.write(render_frame(si, f, g0 + f).tobytes())
    p.stdin.close()
    p.wait()
    return out


# ---------------------------------------------------------------- audio
SR = 44100


def band(x, lo, hi):
    F = np.fft.rfft(x)
    fr = np.fft.rfftfreq(len(x), 1 / SR)
    F[(fr < lo) | (fr > hi)] = 0
    return np.fft.irfft(F, len(x))


def make_audio(path):
    st, total = scene_starts()
    n = int((total + 1.0) * SR)
    mix = np.zeros(n)
    rng = np.random.default_rng(42)

    def add(t, sig, gain=1.0):
        i = int(t * SR)
        j = min(n, i + len(sig))
        if j > i:
            mix[i:j] += sig[: j - i] * gain

    def T(d):
        return np.arange(int(d * SR)) / SR

    def tone(freq_fn, d, harm=(1,)):
        t = T(d)
        f = freq_fn(t) if callable(freq_fn) else np.full_like(t, freq_fn)
        ph = 2 * np.pi * np.cumsum(f) / SR
        return sum(np.sin(ph * h) / (i + 1) for i, h in enumerate(harm))

    def nz(d, lo, hi):
        s = band(rng.normal(0, 1, int(d * SR)), lo, hi)
        return s / (np.abs(s).max() + 1e-9)

    def kick(d=0.35):
        t = T(d)
        return tone(lambda t: 45 + 110 * np.exp(-t / 0.04), d) * np.exp(-t / 0.11)

    # transitions: whoosh into every cut + kick
    for k, s in enumerate(st):
        if k == 0:
            continue
        d = 0.32
        w = nz(d, 500, 7000) * (T(d) / d) ** 2
        add(s - d, w, 0.35)
        add(s, kick(), 0.75)
    # intro camcorder beeps
    add(0.05, tone(1250, 0.08) * 0.5)
    add(0.2, tone(1250, 0.08) * 0.5)
    sc = dict(zip([S["name"] for S in SCENES], st))
    D = {S["name"]: S["dur"] for S in SCENES}
    # enchente
    s = sc["enchente"]
    d = D["enchente"]
    add(s, nz(d, 150, 1400) * (0.6 + 0.4 * np.sin(T(d) * 5)), 0.28)
    for _ in range(16):
        add(s + rng.uniform(0.1, d - 0.1), tone(lambda t: 500 + 9000 * t, 0.05) * np.hanning(int(0.05 * SR)), 0.12)
    for q in (0.95, 1.25):
        dd = 0.17
        add(s + q, tone(lambda t: 1500 + 700 * np.sin(2 * np.pi * 28 * t) + 2000 * t, dd, (1, 2, 3)) * np.hanning(int(dd * SR)), 0.22)
    # apagão
    s = sc["apagao"]
    add(s, tone(60, 0.4, (1, 2, 3)) * 0.5, 0.25)
    add(s + 0.4, tone(lambda t: 220 * np.exp(-t * 2.8), 0.75, (1, 2, 3, 4)) * np.linspace(1, 0, int(0.75 * SR)), 0.35)
    add(s + 1.0, nz(0.012, 2000, 12000), 0.6)
    add(s + 1.03, nz(0.01, 2000, 12000), 0.4)
    # incêndio
    s = sc["incendio"]
    d = D["incendio"]
    add(s, nz(d, 30, 500) * np.minimum(1, T(d) / 0.3), 0.55)
    for _ in range(90):
        add(s + rng.uniform(0, d), nz(0.012, 1500, 9000) * np.exp(-T(0.012) / 0.003), rng.uniform(0.15, 0.45))
    # tsunami (EAS tone + rain + crash)
    s = sc["tsunami"]
    d = D["tsunami"]
    eas = (np.sin(2 * np.pi * 853 * T(1.5)) + np.sin(2 * np.pi * 960 * T(1.5))) * np.minimum(1, np.minimum(T(1.5), 1.5 - T(1.5)) / 0.02)
    add(s + 0.08, eas, 0.11)
    add(s, nz(d, 2000, 9000), 0.09)
    cr = nz(1.4, 40, 3500)
    tt = T(1.4)
    add(s + d - 0.55, cr * np.minimum(1, tt / 0.25) * np.exp(-np.maximum(tt - 0.25, 0) / 0.4), 0.75)
    # terremoto
    s = sc["terremoto"]
    d = D["terremoto"]
    add(s, nz(d, 20, 110) * (0.6 + 0.4 * np.abs(np.sin(T(d) * 2.3))), 1.0)
    add(s, nz(d, 110, 700) * 0.3, 0.3)
    for q in (0.3, 0.55, 1.15, 1.4):
        add(s + q, nz(0.04, 900, 6000) * np.exp(-T(0.04) / 0.01), 0.5)
    # meteoros
    s = sc["meteoros"]
    for (t0, *_r) in METEORS:
        dd = 0.42
        add(s + t0, nz(dd, 400, 4000) * (T(dd) / dd) ** 1.5, 0.3)
        add(s + t0 + dd, kick(0.6) * 1.0 + nz(0.6, 30, 300) * np.exp(-T(0.6) / 0.18), 0.6)
    # aliens
    s = sc["aliens"]
    d = D["aliens"]
    add(s, tone(lambda t: np.where(t < 0.5, 1600 - 1800 * t, 700) + 140 * np.sin(2 * np.pi * 6 * t), d) * np.minimum(1, (d - T(d)) / 0.1), 0.16)
    add(s, tone(110, d, (1, 3, 5)) * 0.6, 0.08)
    add(s + 0.6, tone(lambda t: 300 + 3000 * t, 0.3) * np.linspace(1, 0.2, int(0.3 * SR)), 0.2)
    # nevasca
    s = sc["nevasca"]
    d = D["nevasca"]
    add(s, nz(d, 250, 1600) * (0.5 + 0.5 * np.sin(T(d) * 3.1) ** 2), 0.3)
    # perigo: dun dun dunnn
    s = sc["perigo"] + 0.95
    brass = (1, 2, 3, 4, 5, 6)
    for (q, f0, dd) in [(0.0, 196, 0.22), (0.3, 196, 0.22), (0.6, 155.6, 1.4)]:
        t = T(dd)
        env = np.minimum(1, t / 0.02) * np.where(t > dd - 0.15, (dd - t) / 0.15, 1)
        sig = tone(f0, dd, brass) + 0.7 * tone(f0 * 1.5, dd, brass) + 0.6 * tone(f0 / 2, dd, brass)
        add(s + q, sig * env, 0.16)
    add(s, kick(0.5), 0.7)
    # copa: sad trombone
    s = sc["copa"] + 1.7
    q = 0.0
    for (f0, dd) in [(233.1, 0.36), (220.0, 0.36), (207.7, 0.36), (196.0, 1.1)]:
        t = T(dd)
        vib = 1 + (0.025 * np.sin(2 * np.pi * 6 * t) * np.minimum(1, t / 0.4) if dd > 1 else 0)
        sig = tone(lambda t: f0 * (1 - 0.03 * np.exp(-t / 0.05)) * vib, dd, (1, 2, 3, 4, 5))
        env = np.minimum(1, t / 0.03) * np.where(t > dd - 0.08, (dd - t) / 0.08, 1)
        add(s + q, sig * env, 0.22)
        q += dd
    mix = np.tanh(mix * 1.1)
    mix = mix / np.abs(mix).max() * 0.89
    pcm = (np.repeat(mix[:, None], 2, 1) * 32767).astype(np.int16)
    with wave.open(path, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())


def main():
    mode, outdir = sys.argv[1], sys.argv[2]
    os.makedirs(outdir, exist_ok=True)
    st, total = scene_starts()
    if mode == "stills":
        which = sys.argv[3:] or [S["name"] for S in SCENES]
        for si, S in enumerate(SCENES):
            if S["name"] not in which:
                continue
            for frac in (0.2, 0.55, 0.92):
                f = int(S["dur"] * FPS * frac)
                Image.fromarray(render_frame(si, f, int(st[si] * FPS) + f)).save(f"{outdir}/{si:02d}_{S['name']}_{int(frac*100)}.jpg", quality=88)
        return
    segs = [(i, f"{outdir}/seg_{i:02d}.mp4") for i in range(len(SCENES))]
    with Pool(4) as pool:
        order = sorted(segs, key=lambda s: -SCENES[s[0]]["dur"])
        pool.map(render_scene, order, chunksize=1)
    with open(f"{outdir}/list.txt", "w") as fh:
        for _, p in segs:
            fh.write(f"file '{os.path.abspath(p)}'\n")
    make_audio(f"{outdir}/sfx.wav")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", f"{outdir}/list.txt", "-c", "copy",
                    f"{outdir}/video_only.mp4"], check=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", f"{outdir}/video_only.mp4", "-i", f"{outdir}/sfx.wav", "-map", "0:v",
                    "-map", "1:a", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-t", f"{total:.3f}", "-movflags", "+faststart",
                    f"{outdir}/reels_equipe_cirurgia.mp4"], check=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", f"{outdir}/video_only.mp4", "-c:v", "copy", "-an", "-movflags", "+faststart",
                    f"{outdir}/reels_equipe_cirurgia_SEM_SOM.mp4"], check=True)
    print("done", total)


if __name__ == "__main__":
    main()
