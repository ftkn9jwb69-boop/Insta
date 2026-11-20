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


MASKS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "masks")


def _compose(im, mode, mask=None):
    """Returns the base canvas (and mask canvas) for a scene mode."""
    if mode == "cover":
        return im, mask
    if mode == "fit":
        cw, ch, yc = 1500, 2667, 0.42
    else:  # fitw[@yc]: 9:16 canvas exactly as wide as the photo
        cw, ch = im.width, int(im.width * 16 / 9)
        yc = float(mode.split("@")[1]) if "@" in mode else 0.56
    s = max(cw / im.width, ch / im.height)
    bg = im.resize((int(im.width * s), int(im.height * s)), Image.BICUBIC)
    bg = bg.crop(((bg.width - cw) // 2, (bg.height - ch) // 2, (bg.width - cw) // 2 + cw, (bg.height - ch) // 2 + ch))
    bg = bg.filter(ImageFilter.GaussianBlur(45))
    bg = Image.eval(bg, lambda v: int(v * 0.5))
    fh = int(im.height * cw / im.width)
    top = int(ch * yc - fh / 2)
    bg.paste(im.resize((cw, fh), Image.BICUBIC), (0, top))
    bg.info["fit"] = (top / ch, fh / ch)
    mk = None
    if mask is not None:
        mk = Image.new("L", (cw, ch), 0)
        mk.paste(255, (0, 0, cw, max(top, 0) + 2))
        mk.paste(mask.resize((cw, fh), Image.BILINEAR), (0, top))
    return bg, mk


def load_base(n, mode):
    key = (n, mode)
    if key in _cache:
        return _cache[key]
    im = ImageOps.exif_transpose(Image.open(f"{UP}/{PH[n]}-image.jpg")).convert("RGB")
    mp = f"{MASKS}/{n}.png"
    mask = Image.open(mp).convert("L") if os.path.exists(mp) else None
    _cache[key] = _compose(im, mode, mask)
    return _cache[key]


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
        self.fit = base.info.get("fit")

    def render(self, base):
        return base.transform((W, H), Image.EXTENT, self.box, Image.BICUBIC)

    def map(self, fx, fy):
        """Image fractions (of the original photo) -> output pixels."""
        if self.fit:
            fy = self.fit[0] + fy * self.fit[1]
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
    if kind in ("note", "sticker"):
        f = font(FB if kind == "sticker" else "/usr/share/fonts/opentype/inter/Inter-ExtraBold.otf",
                 66 if kind == "sticker" else 44)
        lines = text.split("\n")
        boxes = [f.getbbox(l) for l in lines]
        lh = int(f.size * 1.22)
        tw = max(b[2] - b[0] for b in boxes)
        px, py = (46, 34) if kind == "sticker" else (24, 14)
        bw, bh = tw + 2 * px, lh * len(lines) + 2 * py - int(f.size * 0.22)
        pad = 40
        lay = Image.new("RGBA", (bw + 2 * pad, bh + 2 * pad), (0, 0, 0, 0))
        sh = Image.new("L", lay.size, 0)
        ImageDraw.Draw(sh).rounded_rectangle([pad, pad + 6, pad + bw, pad + bh + 6], 28 if kind == "sticker" else 14, fill=120)
        shadow = Image.new("RGBA", lay.size, (0, 0, 0, 0))
        shadow.putalpha(sh.filter(ImageFilter.GaussianBlur(12)))
        lay.alpha_composite(shadow)
        d = ImageDraw.Draw(lay)
        d.rounded_rectangle([pad, pad, pad + bw, pad + bh], 28 if kind == "sticker" else 14, fill=(255, 255, 255, 250))
        for i, (l, b) in enumerate(zip(lines, boxes)):
            d.text((pad + px + (tw - (b[2] - b[0])) / 2 - b[0], pad + py + i * lh - f.getbbox("Ag")[1]), l, font=f,
                   fill=(18, 18, 18, 255))
        return lay
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
    a = lay.split()[3]
    sh = Image.new("RGBA", lay.size, (0, 0, 0, 0))
    sh.putalpha(a.filter(ImageFilter.GaussianBlur(9)).point(lambda v: int(v * 0.55)))
    out = Image.new("RGBA", lay.size, (0, 0, 0, 0))
    out.alpha_composite(sh, (0, 8))
    out.alpha_composite(lay)
    return out


def paste_text(img, lay, ycenter, age, xcenter=W / 2, rot=0.0, bounce=False):
    """Pop-in animation: scale 1.25 -> 1.0 (with overshoot for stickers), fade in."""
    if bounce:
        k = min(age / 0.35, 1)
        s = 1 + 0.35 * math.exp(-k * 5) * math.cos(k * 9) - 0.35 * (1 - k) ** 6 if k < 1 else 1
        s = max(s, 0.05) if age > 0.02 else 0.3
        k = min(age / 0.1, 1)
    else:
        k = ease_out(age / 0.16)
        s = 1.25 - 0.25 * k
    if rot:
        lay = lay.rotate(rot, Image.BICUBIC, expand=True)
    if abs(s - 1) > 1e-3:
        lay = lay.resize((max(1, int(lay.width * s)), max(1, int(lay.height * s))), Image.BICUBIC)
    if k < 1:
        a = lay.split()[3].point(lambda v: int(v * k))
        lay = lay.copy()
        lay.putalpha(a)
    img.alpha_composite(lay, (int(xcenter - lay.width / 2), int(ycenter - lay.height / 2)))


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
        _props[name] = {"duck": make_duck, "ufo": make_ufo, "banner": make_banner, "ticker": make_ticker, "mothership": make_mothership,
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
_gauze = None


def make_gauze(seed):
    rng = np.random.default_rng(seed)
    im = Image.new("RGBA", (160, 70), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    pts = [(80 + 70 * math.cos(t) * rng.uniform(0.7, 1), 35 + 26 * math.sin(t) * rng.uniform(0.7, 1))
           for t in np.linspace(0, 2 * math.pi, 14, endpoint=False)]
    d.polygon(pts, fill=(236, 232, 222, 235))
    for k in range(6):
        y = 14 + k * 8
        d.line([(20, y), (140, y + rng.uniform(-3, 3))], fill=(205, 200, 190, 160), width=1)
    return im.filter(ImageFilter.GaussianBlur(0.8))


def fx_flood(a, tl, S, cam):
    """Flooded operating room: horizontal water plane with real reflections, ripples, floating duck."""
    p = ease(tl / 0.9)
    level = H * (0.64 - 0.10 * p) + 3 * math.sin(tl * 1.7)
    Yi = np.arange(H, dtype=np.float32)[:, None]
    dist = np.clip(Yi - level, 0, None)
    persp = np.clip(dist / (H - level), 0, 1)
    amp = 1.5 + 16 * persp
    freq = 1.0 / (2.5 + 14 * persp)
    ph = dist * freq * 3.0 - tl * 4.5
    dx = amp * np.sin(ph + X / 140) + 0.6 * amp * np.sin(0.7 * ph - X / 57 + tl * 2)
    dy = 0.5 * amp * np.cos(ph * 1.3 + X / 90)
    src_y = np.clip(2 * level - Yi + dy, 0, H - 1).astype(np.int32)
    src_x = np.clip(X + dx, 0, W - 1).astype(np.int32)
    refl = a[src_y, src_x]
    fres = (0.78 - 0.40 * persp)[..., None]
    murky = np.array([86, 82, 62], np.float32)
    water = refl * fres * np.array([0.92, 0.95, 0.9], np.float32) + murky * (1 - fres) * (0.85 + 0.15 * np.sin(ph)[..., None])
    spec = np.clip(np.sin(ph + X / 140) * np.sin(0.5 * ph + X / 33 + tl), 0.82, 1) - 0.82
    water += (spec / 0.18)[..., None] * 70 * (0.4 + 0.6 * persp[..., None])
    under = (Yi > level)
    out = np.where(under[..., None], water, a * 0.96)
    edge = np.exp(-((Yi - level) ** 2) / 8)
    out += edge[..., None] * np.array([150, 150, 140], np.float32) * 0.35
    img = Image.fromarray(np.clip(out, 0, 255).astype(np.uint8)).convert("RGBA")
    global _gauze
    if _gauze is None:
        _gauze = [make_gauze(1), make_gauze(2)]

    def floater(obj, fx, depth, rot, bob):
        y = level + depth * (H - level)
        sc = 0.55 + 0.9 * depth
        o = obj.resize((int(obj.width * sc), int(obj.height * sc)), Image.LANCZOS).rotate(rot, Image.BICUBIC, expand=True)
        x = fx * W - o.width / 2
        yy = y - o.height * 0.78 + bob
        # ripple rings
        d = ImageDraw.Draw(img)
        for k in range(3):
            r = ((tl * 60 + k * 40) % 120) * sc
            al = int(110 * (1 - r / (120 * sc)))
            d.ellipse([fx * W - o.width * 0.5 - r, y - 6 * sc - r * 0.22, fx * W + o.width * 0.5 + r, y + 6 * sc + r * 0.22],
                      outline=(230, 230, 220, al), width=2)
        rf = o.transpose(Image.FLIP_TOP_BOTTOM)
        rf.putalpha(rf.split()[3].point(lambda v: int(v * 0.35)))
        rf = rf.filter(ImageFilter.GaussianBlur(2))
        img.alpha_composite(rf, (int(x + 3 * math.sin(tl * 5)), int(y - o.height * 0.2)))
        img.alpha_composite(o, (int(x), int(yy)))

    if p > 0.35:
        floater(_gauze[0], 0.22 + 0.02 * tl, 0.25, 12 + 4 * math.sin(tl), 2 * math.sin(tl * 3))
        floater(_gauze[1], 0.80 - 0.015 * tl, 0.55, -20 + 3 * math.sin(tl * 1.3), 3 * math.sin(tl * 2.6))
        floater(prop("duck"), 0.5 + 0.03 * math.sin(tl * 0.9), 0.38, 5 * math.sin(tl * 3.1), 5 * math.sin(tl * 3.6))
    return np.asarray(img.convert("RGB"), np.float32)


def fx_fire(a, tl, S, cam):
    """Fire burning all around the room (sides, back corners, floor), team and table stay visible."""
    tex = prop("fire")
    o1 = int(tl * 650) % H
    o2 = int(tl * 410 + 300) % H
    n = 0.6 * tex[o1:o1 + H] + 0.4 * np.roll(tex[o2:o2 + H], 400, axis=1)
    ramp = 0.7 + 0.3 * ease(tl / 1.0)
    side = np.clip(1 - np.minimum(X, W - X) / (W * 0.30), 0, 1) ** 1.4
    bottom = np.clip((Y / H - 0.78) / 0.22, 0, 1) ** 1.2
    topc = np.clip(1 - Y / (H * 0.5), 0, 1) * side
    grad = np.maximum(np.maximum(side * np.clip(Y / H + 0.25, 0, 1), bottom), topc * 0.6)
    f = np.clip((n * 0.8 + grad * 1.15 * ramp - 0.98) * 2.6, 0, 1)
    fire = np.stack([np.clip(f * 2.2, 0, 1) * 255, np.clip(f * 2.2 - 0.7, 0, 1) * 200,
                     np.clip(f * 2.2 - 1.5, 0, 1) * 170], -1)
    flick = 0.92 + 0.08 * math.sin(tl * 37) * math.sin(tl * 23)
    base = a * np.array([1.15, 0.84, 0.62], np.float32) * flick * 0.92
    glow = np.clip(grad * 1.3, 0, 1)[..., None]
    base += glow * np.array([170, 70, 12], np.float32) * ramp * flick
    sm = prop("smoke")
    so = int(tl * 60) % H
    smoke = np.clip((0.30 - Y / H) / 0.30, 0, 1) * (0.5 + 0.5 * np.roll(sm, so, axis=0))
    base *= (1 - 0.65 * smoke)[..., None]
    out = base * (1 - 0.6 * f[..., None]) + fire * 1.15
    rng = np.random.default_rng(3)
    N = 160
    x0, ph, v, r, sw = rng.random(N) * W, rng.random(N), rng.uniform(260, 700, N), rng.uniform(2, 6, N), rng.random(N) * 6
    yy = H * 1.05 - ((ph * H + v * tl) % H)
    xx = x0 + 40 * np.sin(tl * 3 + sw)
    pts = [(xx[i], yy[i], r[i], min(1, (yy[i] / H - 0.1) * 3)) for i in range(N) if yy[i] > 0.1 * H]
    em = blob_layer(pts, 2)
    out += em[..., None] * np.array([255, 170, 70], np.float32) * 1.6
    return out


def fx_feriado(a, tl, S, cam):
    return a * np.array([1.02, 1.0, 0.97], np.float32)


def fx_blackout(a, tl, S, cam):
    """Power cut: only the laparoscopy monitor keeps glowing."""
    flick = [1, 0.12, 0.9, 0.05, 0.6, 0.04, 0.04, 0.3, 0.04]
    if tl < 0.4:
        return a
    if tl < 0.4 + len(flick) / FPS * 1.5:
        lvl = flick[int((tl - 0.4) * FPS / 1.5)]
        if lvl > 0.5:
            return a * lvl
    else:
        lvl = 0.0
    x0, y0 = cam.map(0.615, 0.205)
    x1, y1 = cam.map(0.875, 0.375)
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    sx, sy = (x1 - x0) / 2, (y1 - y0) / 2
    scr = np.clip(1 - np.maximum(np.abs(X - cx) / sx, np.abs(Y - cy) / sy), 0, 0.04) / 0.04
    pulse = 0.93 + 0.07 * math.sin(tl * 21) * math.sin(tl * 13)
    r = 360
    glow = np.exp(-(((X - cx) / 1.3) ** 2 + (Y - cy) ** 2) / (2 * r * r)) * pulse
    light = (0.012 + lvl * 0.5 + 1.0 * glow ** 1.3)[..., None] * np.array([1.0, 0.68, 0.70], np.float32)
    light = light * (1 - scr[..., None]) + scr[..., None] * 1.05
    return a * light


_wave_tex = None


def fx_tsunami(a, tl, S, cam):
    global _wave_tex
    D = S["dur"]
    mk = S["mk"][..., None]
    if _wave_tex is None:
        _wave_tex = (noise_tex(2 * H, W, 31, ((30, 3), (70, 6), (150, 12), (300, 24))),
                     noise_tex(2 * H, W, 37, ((8, 14), (16, 28), (32, 56), (64, 112))))
    streak, foamn = _wave_tex
    # storm sky
    sky = (np.array([28, 38, 48], np.float32) * (1 - Y / H) + np.array([88, 104, 110], np.float32) * (Y / H))[..., None, :] \
        if False else None
    g = np.clip(Y / (H * 0.7), 0, 1)[..., None]
    sky = np.array([26, 34, 44], np.float32) * (1 - g) + np.array([92, 106, 112], np.float32) * g
    sm = prop("smoke")
    sky = sky * (0.75 + 0.35 * np.roll(sm, int(tl * 40), axis=1)[..., None])
    sky = np.broadcast_to(sky, (H, W, 3)).copy()
    # giant wave rising behind
    k = ease((tl + 0.1) / (D - 0.4))
    ytop = H * 0.66 - (H * 0.66 - H * 0.27) * k
    xs = X[0]
    rag = foamn[int(tl * 90) % H, :] - 0.5
    crest = (ytop + 55 * np.sin(xs / 170 + tl * 1.3) + 22 * np.sin(xs / 61 - tl * 2.4) - (0.5 - xs / W) * 150
             + 46 * rag)
    d = Y - crest[None, :]
    off = int(tl * 160) % H
    st = streak[(H - off):(2 * H - off)]
    fn = foamn[off:off + H]
    body_t = np.clip(d / 700, 0, 1)[..., None]
    lip = np.array([60, 140, 128], np.float32)
    deep = np.array([6, 26, 36], np.float32)
    wave = lip * (1 - body_t) ** 1.5 + deep * (1 - (1 - body_t) ** 1.5)
    wave = wave * (0.78 + 0.28 * st[..., None] + 0.3 * fn[..., None])
    dp = np.clip(d, 0, None)
    foam = np.clip(np.exp(-(dp / 55) ** 2) * (0.75 + 0.5 * fn), 0, 1)
    drip = np.clip((fn - 0.58) * 3.5, 0, 1) * np.exp(-dp / 320) * (st > 0.45)
    foam = np.clip(foam + drip, 0, 1)
    wave = wave * (1 - foam[..., None]) + foam[..., None] * np.array([228, 236, 234], np.float32)
    inside = d > 0
    bgimg = np.where(inside[..., None], wave, sky)
    up = np.clip(-d, 0, None)
    mist = np.exp(-up / 140) * (~inside) * np.clip(0.2 + 0.9 * fn, 0, 1)
    bgimg = bgimg * (1 - mist[..., None] * 0.8) + mist[..., None] * np.array([205, 215, 218], np.float32) * 0.8
    # foreground: cool storm grade, lightning
    lightning = 0.0
    for lt in (0.85, 1.55):
        if lt <= tl < lt + 0.18:
            lightning = max(lightning, (1 - (tl - lt) / 0.18) * (1 if int((tl - lt) * FPS) != 1 else 0.3))
    gr = gray(a)
    fg = (a * 0.7 + gr * 0.3) * np.array([0.74, 0.84, 0.92], np.float32) * (0.82 + 0.5 * lightning)
    bgimg = bgimg * (1 + 0.8 * lightning)
    rim = np.clip(np.asarray(Image.fromarray((S["mk"] * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(10)),
                             np.float32) / 255 - S["mk"], 0, 1)[..., None]
    fg = fg + rim * np.array([120, 150, 160], np.float32) * 0.8
    out = bgimg * mk + fg * (1 - mk)
    # rain
    rng = np.random.default_rng(5)
    N = 230
    x0, y0, ln = rng.random(N) * (W + 400), rng.random(N) * (H + 200), rng.uniform(50, 110, N)
    lay = Image.new("L", (W, H), 0)
    dr = ImageDraw.Draw(lay)
    for i in range(N):
        y = (y0[i] + 2600 * tl) % (H + 200) - 100
        x = (x0[i] - 700 * tl) % (W + 400) - 200
        dr.line([(x, y), (x - ln[i] * 0.27, y + ln[i])], fill=100, width=2)
    out += (np.asarray(lay, np.float32) / 255)[..., None] * 130
    # final: the wave crashes over the camera (top-down)
    if tl > D - 0.4:
        kk = (tl - (D - 0.4)) / 0.4
        front = -0.25 * H + kk * 1.45 * H + 90 * np.sin(X / 110 + tl * 7) + 35 * np.sin(X / 37)
        n2 = foamn[:H]
        ins = Y < front
        wcol = np.array([14, 50, 62], np.float32) + n2[..., None] * 70
        out = np.where(ins[..., None], wcol, out)
        fo = np.exp(-((Y - front) ** 2) / (2 * 70 ** 2)) * (0.45 + 0.55 * n2)
        out += fo[..., None] * 225
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


# (start time, entry x, entry y, angle (rad, below horizontal, moving left), speed px/s, size)
METEORS = [(0.05, 1.10, 0.02, 0.55, 2300, 1.0), (0.45, 0.95, -0.05, 0.75, 2000, 0.7), (0.80, 1.15, 0.12, 0.42, 2600, 1.25),
           (1.30, 0.70, -0.06, 0.95, 1700, 0.6), (1.65, 1.08, 0.05, 0.6, 2400, 0.9)]
HORIZON = 0.50  # fraction of output height where meteors hit (behind the people)


def meteor_path(m, tl):
    t0, x0, y0, ang, sp, sz = m
    dx, dy = -math.cos(ang), math.sin(ang)
    sx, sy = x0 * W, y0 * H
    t_imp = (HORIZON * H - sy) / (dy * sp)
    return sx, sy, dx, dy, sp, sz, t0 + t_imp


def meteor_impacts():
    return [meteor_path(m, 0)[6] for m in METEORS]


_sky_m = None


def fx_meteor(a, tl, S, cam):
    global _sky_m
    mk = S["mk"]
    if _sky_m is None:
        _sky_m = noise_tex(H, 2 * W, 41, ((6, 4), (12, 8), (24, 16), (48, 32)))
    hz = HORIZON * H
    g = np.clip(Y / hz, 0, 1)
    sky = (np.array([12, 6, 9], np.float32) * (1 - g[..., None]) + np.array([120, 36, 14], np.float32) * g[..., None] ** 2.2
           + np.array([255, 120, 40], np.float32) * (g[..., None] ** 14) * 0.8)
    sky = np.broadcast_to(sky, (H, W, 3)).copy()
    cl = _sky_m[:, int(tl * 30) % W:int(tl * 30) % W + W]
    sky = sky * (0.35 + 0.8 * cl[..., None]) + (cl[..., None] ** 3) * np.array([110, 40, 15], np.float32) * g[..., None] ** 2
    head = Image.new("RGB", (W, H), (0, 0, 0))
    dh = ImageDraw.Draw(head)
    smoke = Image.new("L", (W, H), 0)
    ds = ImageDraw.Draw(smoke)
    flash = 0.0
    fire = np.zeros((H, W), np.float32)
    for m in METEORS:
        sx, sy, dx, dy, sp, sz, timp = meteor_path(m, tl)
        t0 = m[0]
        if tl < t0:
            continue
        tt = min(tl, timp) - t0
        hx, hy = sx + dx * sp * tt, sy + dy * sp * tt
        # lingering smoke trail
        age = max(0.0, tl - timp)
        sa = int(150 * math.exp(-age / 0.9))
        ds.line([(sx - dx * 200, sy - dy * 200), (hx, hy)], fill=sa, width=int(26 * sz + 40 * min(tl - t0, 1.5)))
        if tl < timp:
            L = 950 * sz
            segs = 36
            for i in range(segs, 0, -1):
                f0, f1 = (i - 1) / segs, i / segs
                col = (int(255), int(255 - 150 * f1), int(235 - 225 * f1))
                fade = (1 - f1) ** 1.6
                w_ = max(1, int((15 * sz) * (1 - f1) ** 0.8 + 1))
                dh.line([(hx - dx * L * f0, hy - dy * L * f0), (hx - dx * L * f1, hy - dy * L * f1)],
                        fill=tuple(int(c * fade) for c in col), width=w_)
            r = 11 * sz
            dh.ellipse([hx - r, hy - r, hx + r, hy + r], fill=(255, 250, 235))
            # breakup fragments
            for j in range(3):
                fx_, fy_ = hx - dx * (60 + 40 * j) * sz + (j - 1) * 14 * dy, hy - dy * (60 + 40 * j) * sz - (j - 1) * 14 * dx
                dh.ellipse([fx_ - 2, fy_ - 2, fx_ + 2, fy_ + 2], fill=(255, 200, 120))
        else:
            # ground impact behind the people: fireball + glow
            age = tl - timp
            flash += math.exp(-age / 0.13) * sz
            rr = 40 + 230 * (1 - math.exp(-age / 0.3)) * sz
            ix = hx
            fb = np.exp(-(((X - ix) / 1.6) ** 2 + ((Y - hz) * 1.0) ** 2) / (2 * rr * rr)) * math.exp(-age / 0.9)
            fire += fb.astype(np.float32)
            colh = int(min(age, 1.5) * 500 * sz)
            ds.ellipse([ix - 120 * sz - colh * 0.2, hz - colh - 60, ix + 120 * sz + colh * 0.2, hz + 40], fill=int(170 * min(1, age * 3)))
    smoke_a = np.asarray(smoke.filter(ImageFilter.GaussianBlur(16)), np.float32)[..., None] / 255
    sky = sky * (1 - 0.55 * smoke_a) + smoke_a * np.array([70, 50, 45], np.float32) * 0.55
    hd = np.asarray(head, np.float32)
    glow = np.asarray(head.filter(ImageFilter.GaussianBlur(26)), np.float32)
    glow2 = np.asarray(head.filter(ImageFilter.GaussianBlur(6)), np.float32)
    sky = sky + hd + glow2 * 1.0 + glow * 2.6
    sky = sky + np.clip(fire, 0, 1.4)[..., None] * np.array([255, 120, 35], np.float32) * 1.2 + flash * np.array([255, 170, 100], np.float32) * 0.22
    # people: dusk grade + orange rim light from the sky behind them
    gr = gray(a)
    fg = (a * 0.8 + gr * 0.2) * np.array([0.82, 0.68, 0.62], np.float32) * (0.78 + 0.25 * min(flash, 1.5))
    rim = np.clip(np.asarray(Image.fromarray((mk * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(12)), np.float32) / 255
                  - mk, 0, 1)[..., None]
    fg += rim * np.array([255, 140, 60], np.float32) * (0.9 + 0.8 * min(flash, 1.5))
    fg += (np.clip(1 - Y / H, 0, 1) ** 2)[..., None] * np.array([90, 35, 10], np.float32) * (0.4 + 0.6 * min(flash, 1))
    out = sky * mk[..., None] + fg * (1 - mk[..., None])
    # falling ash & embers in front
    rng = np.random.default_rng(12)
    N = 90
    x0, y0, v, r, ph = rng.random(N) * W, rng.random(N) * H, rng.uniform(80, 220, N), rng.uniform(1.5, 4, N), rng.random(N) * 6
    pts = [((x0[i] - 90 * tl + 20 * math.sin(tl * 2 + ph[i])) % W, (y0[i] + v[i] * tl) % H, r[i], 0.8) for i in range(N)]
    em = blob_layer(pts, 1.5)
    out += em[..., None] * np.array([255, 150, 70], np.float32)
    return out


def make_mothership():
    w, h = int(W * 1.9), 620
    S2 = 1
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    cx, cy, ax, by = w / 2, 120, w / 2 - 4, 400
    rings = 9
    for i in range(rings):
        f = i / rings
        c = int(26 + 34 * f)
        d.ellipse([cx - ax * (1 - f * 0.8), cy - by * (1 - f * 0.8), cx + ax * (1 - f * 0.8), cy + by * (1 - f * 0.8)],
                  fill=(c, c + 4, c + 12, 255), outline=(c + 38, c + 46, c + 60, 255), width=3)
    for k in range(48):
        th = k / 48 * 2 * math.pi
        if math.sin(th) < 0.05:
            continue
        d.line([(cx + ax * 0.22 * math.cos(th), cy + by * 0.22 * math.sin(th)), (cx + ax * math.cos(th), cy + by * math.sin(th))],
               fill=(18, 20, 26, 255), width=3)
    # recessed panels
    rng = np.random.default_rng(3)
    for _ in range(140):
        th = rng.uniform(0.08, 0.92) * math.pi
        rr = rng.uniform(0.3, 0.95)
        px, py = cx + ax * rr * math.cos(th), cy + by * rr * math.sin(th)
        s = rng.uniform(10, 34)
        d.rectangle([px - s, py - s * 0.35, px + s, py + s * 0.35], fill=(16, 18, 24, 255))
    d.ellipse([cx - 230, cy + by * 0.62 - 70, cx + 230, cy + by * 0.62 + 70], fill=(12, 14, 20, 255), outline=(90, 110, 130, 255), width=4)
    return im


_stars = None


def fx_alien(a, tl, S, cam):
    global _stars
    D = S["dur"]
    mk = S["mk"]
    g = np.clip(Y / (H * 0.5), 0, 1)
    sky = np.array([4, 7, 16], np.float32) * (1 - g[..., None]) + np.array([18, 34, 58], np.float32) * g[..., None]
    sky = np.broadcast_to(sky, (H, W, 3)).copy()
    if _stars is None:
        rng = np.random.default_rng(77)
        _stars = (rng.random(260) * W, rng.random(260) * H * 0.6, rng.uniform(0.6, 2.0, 260), rng.random(260) * 6)
    sxs, sys_, srs, sph = _stars
    st = blob_layer([(sxs[i], sys_[i], srs[i], 0.5 + 0.5 * math.sin(tl * 5 + sph[i])) for i in range(len(sxs))], 0)
    sky += st[..., None] * 200
    img = Image.fromarray(np.clip(sky, 0, 255).astype(np.uint8)).convert("RGBA")
    ms = prop("mothership")
    oy = -160 + 70 * ease(tl / D) + 6 * math.sin(tl * 1.3)
    ox = W / 2 - ms.width / 2 + 10 * math.sin(tl * 0.8)
    img.alpha_composite(ms, (int(ox), int(oy)))
    sky = np.asarray(img.convert("RGB"), np.float32)
    # lights, core and searchlights (additive)
    lights = Image.new("RGB", (W, H), (0, 0, 0))
    dl = ImageDraw.Draw(lights)
    cx, cy, ax, by = ox + ms.width / 2, oy + 120, ms.width / 2 - 4, 400
    for k in range(34):
        th = 0.06 * math.pi + k / 33 * 0.88 * math.pi
        lx, ly = cx + ax * 0.97 * math.cos(th), cy + by * 0.97 * math.sin(th)
        on = 0.35 + 0.65 * (0.5 + 0.5 * math.sin(tl * 6 - k * 0.6))
        c = (int(150 * on), int(235 * on), int(255 * on))
        dl.ellipse([lx - 5, ly - 4, lx + 5, ly + 4], fill=c)
    core_y = cy + by * 0.62
    pulse = 0.75 + 0.25 * math.sin(tl * 9)
    dl.ellipse([cx - 170, core_y - 46, cx + 170, core_y + 46], fill=(int(90 * pulse), int(230 * pulse), int(255 * pulse)))
    beams = Image.new("L", (W, H), 0)
    db = ImageDraw.Draw(beams)
    on = 0.0 if tl < 0.55 else (1.0 if tl > 0.75 else [1, 0, 1, 0, 1, 1][int((tl - 0.55) * FPS) % 6])
    spots = []
    if on:
        db.polygon([(cx - 150, core_y), (cx + 150, core_y), (cx + 520, H), (cx - 520, H)], fill=70)
        for side, ph in ((-1, 0.0), (1, 1.7)):
            bx, byy = cx + side * ax * 0.55, cy + by * 0.82
            ang = math.pi / 2 + 0.38 * math.sin(tl * 1.6 + ph)
            tx, ty = bx + math.cos(ang) * H * 1.2, byy + math.sin(ang) * H * 1.2
            nx, ny = -math.sin(ang), math.cos(ang)
            db.polygon([(bx - nx * 14, byy - ny * 14), (bx + nx * 14, byy + ny * 14), (tx + nx * 170, ty + ny * 170),
                        (tx - nx * 170, ty - ny * 170)], fill=110)
            t_ = (H * 0.55 - byy) / max(math.sin(ang), 0.2)
            spots.append((bx + math.cos(ang) * t_, H * 0.55))
    lights_a = np.asarray(lights, np.float32)
    lg = np.asarray(lights.filter(ImageFilter.GaussianBlur(10)), np.float32)
    beam_a = np.asarray(beams.filter(ImageFilter.GaussianBlur(26)), np.float32)[..., None] / 255
    sky = sky + lights_a + lg * 2.0 + beam_a * np.array([140, 230, 255], np.float32) * 1.3
    # people: cold sci-fi grade + cyan rim + moving searchlight spots
    gr = gray(a)
    fg = (a * 0.65 + gr * 0.35) * np.array([0.62, 0.8, 0.95], np.float32) * 0.78
    rim = np.clip(np.asarray(Image.fromarray((mk * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(12)), np.float32) / 255
                  - mk, 0, 1)[..., None]
    fg += rim * np.array([120, 220, 255], np.float32) * 1.1
    for (sx_, sy_) in spots:
        sp = np.exp(-(((X - sx_) / 1.2) ** 2 + (Y - sy_) ** 2) / (2 * 230 ** 2))[..., None]
        fg = fg * (1 + 0.9 * sp) + sp * np.array([60, 110, 130], np.float32)
    out = sky * mk[..., None] + fg * (1 - mk[..., None])
    out += beam_a * np.array([140, 230, 255], np.float32) * 0.18  # volumetric haze in front
    # ---- HUD scan
    ys = (tl - 0.7) / 1.0 * H * 1.1
    if 0 < ys < H * 1.3:
        gy = gray(out)[..., 0]
        e = np.abs(np.diff(gy, axis=1, append=gy[:, -1:])) + np.abs(np.diff(gy, axis=0, append=gy[-1:, :]))
        e = np.clip((e - 12) / 40, 0, 1)
        win = np.clip(np.exp(-(ys - Y) / 260), 0, 1) * (Y < ys)
        out += (e * win)[..., None] * np.array([80, 230, 255], np.float32) * 0.9
        line = np.exp(-((Y - ys) ** 2) / (2 * 5 ** 2))
        out += line[..., None] * np.array([150, 240, 255], np.float32) * 1.4
        out += (np.exp(-((Y - ys) ** 2) / (2 * 60 ** 2)) * 0.25)[..., None] * np.array([60, 200, 255], np.float32)
    img = Image.fromarray(np.clip(out, 0, 255).astype(np.uint8)).convert("RGBA")
    d = ImageDraw.Draw(img)
    cyan = (120, 235, 255, 255)
    fm = font(FMONO, 30)
    fs = font(FMONO, 26)
    head = "SCAN BIOMÉTRICO  >>  2 ALVOS"
    n = int(max(0, tl - 0.7) * 40)
    d.text((110, 330), head[:n], font=fm, fill=cyan)
    targets = [((0.33, 0.30), "ALVO 01: HUMANO", "SONO: 4h EM 72h", 1.05),
               ((0.735, 0.405), "ALVO 02: HUMANO", "CAFEÍNA: 87%", 1.3)]
    for (fxy, l1, l2, tlock) in targets:
        if tl < tlock:
            continue
        px, py = cam.map(*fxy)
        k = ease_out((tl - tlock) / 0.3)
        sz = 190 - 80 * k
        rot = (1 - k) * 0.8
        col = cyan if k < 1 or int(tl * 6) % 2 else (255, 90, 90, 255)
        for (sx, sy) in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
            cxr, cyr = px + sx * sz, py + sy * sz
            p1 = (cxr - sx * 40, cyr)
            p2 = (cxr, cyr - sy * 40)
            rp = lambda p: (px + (p[0] - px) * math.cos(rot) - (p[1] - py) * math.sin(rot),
                            py + (p[0] - px) * math.sin(rot) + (p[1] - py) * math.cos(rot))
            d.line([rp(p1), rp((cxr, cyr)), rp(p2)], fill=col, width=5)
        if k >= 1:
            tx = px + 130 if px < W * 0.6 else px - 400
            d.line([(px + (110 if tx > px else -110), py - 110), (tx + (0 if tx > px else 270), py - 150)], fill=cyan, width=2)
            d.text((tx, py - 190), l1, font=fs, fill=cyan)
            d.text((tx, py - 160), l2, font=fs, fill=(255, 255, 255, 255))
    a2 = np.asarray(img.convert("RGB"), np.float32)
    # occasional signal glitch
    for gt in (0.58, 1.95):
        if gt <= tl < gt + 0.1:
            rng = np.random.default_rng(int(tl * 100))
            for _ in range(7):
                y0 = int(rng.random() * H)
                hgt = int(rng.uniform(10, 60))
                a2[y0:y0 + hgt] = np.roll(a2[y0:y0 + hgt], int(rng.uniform(-60, 60)), axis=1)
            a2[..., 0] = np.roll(a2[..., 0], 8, axis=1)
            a2[..., 2] = np.roll(a2[..., 2], -8, axis=1)
    return a2


_vortex = None


def make_debris(kind, seed):
    rng = np.random.default_rng(seed)
    if kind == "paper":
        im = Image.new("RGBA", (90, 120), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        d.polygon([(5, 8), (85, 2), (88, 115), (2, 110)], fill=(238, 238, 232, 240))
        for k in range(7):
            d.line([(14, 22 + k * 12), (74, 20 + k * 12)], fill=(150, 150, 160, 200), width=2)
    elif kind == "leaf":
        im = Image.new("RGBA", (70, 34), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        d.ellipse([2, 4, 68, 30], fill=(int(60 + rng.random() * 40), int(90 + rng.random() * 40), 40, 240))
        d.line([(4, 17), (66, 17)], fill=(40, 60, 25, 255), width=2)
    else:  # gauze
        im = make_gauze(seed).resize((100, 44))
    return im


def fx_typhoon(a, tl, S, cam):
    global _vortex
    D = S["dur"]
    mk = S["mk"]
    if _vortex is None:
        _vortex = noise_tex(2400, 2400, 51, ((6, 6), (12, 12), (24, 24), (48, 48), (96, 96)))
    cx, cy = W * 0.68, H * 0.06
    r = np.sqrt((X - cx) ** 2 + (Y - cy) ** 2) + 1
    th = np.arctan2(Y - cy, X - cx)
    ang = th - tl * 0.55 + 1.9 * np.log(r / 180)
    u = np.clip((r * 0.55 * np.cos(ang) + 1200).astype(np.int32), 0, 2399)
    v = np.clip((r * 0.55 * np.sin(ang) + 1200).astype(np.int32), 0, 2399)
    cl = _vortex[v, u]
    eye = np.exp(-(r / 130) ** 2)
    sky = (np.array([22, 28, 30], np.float32) + (cl ** 1.6)[..., None] * np.array([130, 142, 140], np.float32)
           + eye[..., None] * 60)
    flash = 0.0
    for lt in (0.7, 1.75):
        if lt <= tl < lt + 0.16:
            flash = max(flash, 1 - (tl - lt) / 0.16)
    sky = sky * (1 + 1.2 * flash)
    gr = gray(a)
    fg = (a * 0.6 + gr * 0.4) * np.array([0.78, 0.86, 0.86], np.float32) * (0.74 + 0.4 * flash)
    rim = np.clip(np.asarray(Image.fromarray((mk * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(10)), np.float32) / 255
                  - mk, 0, 1)[..., None]
    fg += rim * np.array([150, 170, 170], np.float32) * 0.7
    out = sky * mk[..., None] + fg * (1 - mk[..., None])
    # horizontal wind-driven rain
    rng = np.random.default_rng(15)
    N = 260
    x0, y0, ln = rng.random(N) * (W + 600), rng.random(N) * H, rng.uniform(80, 200, N)
    lay = Image.new("L", (W, H), 0)
    dr = ImageDraw.Draw(lay)
    for i in range(N):
        x = (x0[i] + 3200 * tl) % (W + 600) - 300
        y = (y0[i] + 500 * tl) % H
        dr.line([(x, y), (x - ln[i], y - ln[i] * 0.16)], fill=95, width=2)
    out += (np.asarray(lay, np.float32) / 255)[..., None] * 150
    img = Image.fromarray(np.clip(out, 0, 255).astype(np.uint8)).convert("RGBA")
    # flying debris
    kinds = ["paper", "leaf", "gauze", "leaf", "paper", "leaf", "gauze", "leaf", "paper", "leaf", "leaf", "paper"]
    rng = np.random.default_rng(16)
    for i, k in enumerate(kinds):
        key = ("debris", i)
        if key not in _props:
            _props[key] = make_debris(k, 100 + i)
        im = _props[key]
        sp = rng.uniform(1300, 2400)
        x = (rng.random() * (W + 600) + sp * tl) % (W + 600) - 300
        y = rng.uniform(0.05, 0.85) * H + 90 * math.sin(tl * 3 + i) + 160 * tl
        sc = rng.uniform(0.6, 1.5)
        o = im.resize((int(im.width * sc), int(im.height * sc))).rotate((tl * rng.uniform(300, 900) + i * 40) % 360, Image.BICUBIC,
                                                                         expand=True)
        if sc > 1.2:
            o = o.filter(ImageFilter.GaussianBlur(3))
        img.alpha_composite(o, (int(x - o.width / 2), int(y - o.height / 2)))
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
    return (0.42 - 0.06 * k, 0.42 - 0.04 * k, 1.06 + 0.16 * k)


def shake_none(tl, D):
    return (0, 0, 0)


def shake_quake(tl, D):
    amp = 26 * (0.55 + 0.45 * abs(math.sin(tl * 2.3))) * (1 if tl > 0.15 else tl / 0.15)
    return (amp * math.sin(tl * 47) + amp * 0.5 * math.sin(tl * 83), amp * math.cos(tl * 53), 1.3 * math.sin(tl * 31))


def shake_meteor(tl, D):
    s = 0
    for t, m in zip(meteor_impacts(), METEORS):
        if tl >= t:
            s += 24 * m[5] * math.exp(-(tl - t) / 0.18)
    return (s * math.sin(tl * 90), s * math.cos(tl * 70), 0.03 * s * math.sin(tl * 60))


def shake_soft(amp):
    return lambda tl, D: (amp * math.sin(tl * 13), amp * math.cos(tl * 11), 0)


def shake_tsunami(tl, D):
    amp = 4 + 10 * ease(tl / D) + (22 if tl > D - 0.4 else 0)
    return (amp * math.sin(tl * 41), amp * math.cos(tl * 37), 0.04 * amp * math.sin(tl * 29))


def shake_alien(tl, D):
    a = 9 * math.exp(-max(tl - 0.55, 0) / 0.3) if tl > 0.55 else 2
    return (a * math.sin(tl * 70), a * math.cos(tl * 60), 0)


def shake_typhoon(tl, D):
    gust = 0.6 + 0.4 * abs(math.sin(tl * 1.9))
    amp = 11 * gust
    return (amp * math.sin(tl * 37) + 6, amp * 0.6 * math.cos(tl * 43), 0.5 * gust * math.sin(tl * 23))


MY = 0.60
# captions: (t_in, t_out, text, kind, y, x, rotation)
SCENES = [
    dict(name="feriado", img=2, mode="cover", dur=3.1, cam=cam_static(0.5, 0.36, 1.0, 1.22), shake=shake_none, fx=fx_feriado,
         caps=[(0.25, None, "Você trabalha\nno feriado?", "sticker", 0.51, 0.5, 3), (1.15, None, "Sim...", "main", 0.625),
               (1.85, None, "e também...", "white", 0.685)]),
    dict(name="enchente", img=6, mode="fitw@0.40", dur=2.7, cam=cam_static(0.5, 0.40, 1.0, 1.04), shake=shake_soft(2), fx=fx_flood,
         caps=[(0.1, None, "Na enchente", "main", MY)]),
    dict(name="aliens", img=11, mode="fitw@0.60", dur=3.1, cam=cam_static(0.5, 0.5, 1.0, 1.04), shake=shake_alien, fx=fx_alien,
         caps=[(0.1, None, "Na invasão alienígena", "main", MY),
               (1.9, None, "abdução só depois\nda sutura", "note", 0.79, 0.5, -3)]),
    dict(name="apagao", img=4, mode="cover", dur=2.3, cam=cam_static(0.5, 0.40, 1.0, 1.08), shake=shake_none, fx=fx_blackout,
         caps=[(0.1, None, "No apagão", "main", MY)]),
    dict(name="incendio", img=12, mode="cover", dur=2.4, cam=cam_static(0.60, 0.30, 1.30, 1.40), shake=shake_soft(4), fx=fx_fire,
         caps=[(0.1, None, "No incêndio", "main", MY)]),
    dict(name="tufao", img=10, mode="cover", dur=2.7, cam=cam_static(0.47, 0.42, 1.03, 1.09), shake=shake_typhoon, fx=fx_typhoon,
         caps=[(0.1, None, "No tufão", "main", MY), (0.9, None, "e a cara de brava\nnem se mexeu", "note", 0.27, 0.70, 5)]),
    dict(name="meteoros", img=7, mode="fitw", dur=2.9, cam=cam_static(0.5, 0.5, 1.0, 1.05), shake=shake_meteor, fx=fx_meteor,
         caps=[(0.1, None, "Na chuva de meteoros", "main", MY), (1.0, None, "tudo sob controle,\nchefe", "note", 0.74, 0.30, 4)]),
    dict(name="nevasca", img=8, mode="cover", dur=2.1, cam=cam_static(0.40, 0.38, 1.06, 1.12), shake=shake_soft(2), fx=fx_snow,
         caps=[(0.1, None, "Na nevasca", "main", MY)]),
    dict(name="tsunami", img=5, mode="cover", dur=3.0, cam=cam_static(0.50, 0.45, 1.03, 1.10), shake=shake_tsunami,
         fx=fx_tsunami, caps=[(0.1, None, "No tsunami", "main", MY),
                              (0.9, None, "só sai quando o\nchefe liberar", "note", 0.755, 0.33, -4)]),
    dict(name="copa", img=3, mode="cover", dur=3.9, cam=cam_end, shake=shake_none, fx=fx_end,
         caps=[(0.1, 1.6, "E a única tragédia que", "white", 0.60), (0.1, 1.6, "ninguém sobrevive:", "main", 0.655),
               (1.65, None, "acabar o café da copa", "big", 0.66), (2.25, None, "socorro", "note", 0.74, 0.72, 8)]),
]


_vig = None
_grain = None


def render_frame(si, f, gframe):
    global _vig, _grain
    S = SCENES[si]
    D = S["dur"]
    tl = f / FPS
    base, mask = load_base(S["img"], S["mode"])
    fx_, fy_, z = S["cam"](tl, D)
    if si > 0:
        z *= 1 + 0.07 * math.exp(-tl / 0.09)  # punch-in on cut
    dx, dy, rot = S["shake"](tl, D)
    if rot:
        z *= 1.06
    cam = Cam(base, fx_, fy_, z, dx, dy)
    img = cam.render(base)
    mimg = cam.render(mask) if mask is not None else None
    if rot:
        img = img.rotate(rot, Image.BICUBIC)
        if mimg is not None:
            mimg = mimg.rotate(rot, Image.BICUBIC)
    a = np.asarray(img, np.float32)
    S["mk"] = np.asarray(mimg, np.float32) / 255 if mimg is not None else None
    if S["fx"]:
        a = S["fx"](a, tl, S, cam)
    if _vig is None:
        r2 = ((X - W / 2) / W) ** 2 + ((Y - H / 2) / H) ** 2
        _vig = (1 - 0.55 * r2)[..., None].astype(np.float32)
        rng = np.random.default_rng(0)
        _grain = [rng.normal(0, 6, (H, W, 1)).astype(np.float32) for _ in range(6)]
    a = a * _vig
    img = Image.fromarray(np.clip(a, 0, 255).astype(np.uint8)).convert("RGBA")
    hud(img, gframe)
    for cap in S["caps"]:
        t0, t1, text, kind, yc = cap[:5]
        xc = cap[5] if len(cap) > 5 else 0.5
        rr = cap[6] if len(cap) > 6 else 0
        if tl >= t0 and (t1 is None or tl < t1):
            key = ("cap", text, kind)
            if key not in _props:
                _props[key] = text_layer(text, kind)
            paste_text(img, _props[key], yc * H, tl - t0, xc * W, rr, bounce=kind in ("sticker", "note"))
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
    sc = dict(zip([S["name"] for S in SCENES], st))
    D = {S["name"]: S["dur"] for S in SCENES}
    # feriado: sticker pop + room tone
    s = sc["feriado"]
    add(s, nz(D["feriado"], 80, 400), 0.04)
    add(s + 0.25, tone(lambda t: 600 + 900 * np.exp(-t / 0.03), 0.12) * np.exp(-T(0.12) / 0.04), 0.4)
    add(s + 0.27, tone(1568, 0.25, (1, 2)) * np.exp(-T(0.25) / 0.08), 0.18)
    # enchente
    s = sc["enchente"]
    d = D["enchente"]
    add(s, nz(d, 150, 1400) * (0.6 + 0.4 * np.sin(T(d) * 5)), 0.28)
    for _ in range(16):
        add(s + rng.uniform(0.1, d - 0.1), tone(lambda t: 500 + 9000 * t, 0.05) * np.hanning(int(0.05 * SR)), 0.12)
    for q in (0.95, 1.25):
        dd = 0.17
        add(s + q, tone(lambda t: 1500 + 700 * np.sin(2 * np.pi * 28 * t) + 2000 * t, dd, (1, 2, 3)) * np.hanning(int(dd * SR)), 0.22)
    # apagão: hum, power-down, then the OR monitor keeps beeping in the dark
    s = sc["apagao"]
    add(s, tone(60, 0.4, (1, 2, 3)) * 0.5, 0.25)
    add(s + 0.4, tone(lambda t: 220 * np.exp(-t * 2.8), 0.75, (1, 2, 3, 4)) * np.linspace(1, 0, int(0.75 * SR)), 0.35)
    for q in np.arange(0.1, D["apagao"], 0.55):
        add(s + q, tone(985, 0.09) * np.minimum(1, (0.09 - T(0.09)) / 0.01), 0.1)
    # incêndio
    s = sc["incendio"]
    d = D["incendio"]
    add(s, nz(d, 30, 500) * np.minimum(1, T(d) / 0.3), 0.55)
    for _ in range(90):
        add(s + rng.uniform(0, d), nz(0.012, 1500, 9000) * np.exp(-T(0.012) / 0.003), rng.uniform(0.15, 0.45))
    # tsunami: growing roar, EAS tone, thunder, crash
    s = sc["tsunami"]
    d = D["tsunami"]
    add(s, nz(d, 25, 400) * (T(d) / d) ** 1.5, 0.9)
    eas = (np.sin(2 * np.pi * 853 * T(1.5)) + np.sin(2 * np.pi * 960 * T(1.5))) * np.minimum(1, np.minimum(T(1.5), 1.5 - T(1.5)) / 0.02)
    add(s + 0.08, eas, 0.09)
    add(s, nz(d, 2000, 9000), 0.08)
    for lt in (0.85, 1.55):
        add(s + lt + 0.05, nz(1.2, 30, 2500) * np.exp(-T(1.2) / 0.35) * np.minimum(1, T(1.2) / 0.01), 0.45)
    cr = nz(1.4, 40, 3500)
    tt = T(1.4)
    add(s + d - 0.4, cr * np.minimum(1, tt / 0.2) * np.exp(-np.maximum(tt - 0.2, 0) / 0.4), 0.8)
    # tufão: howling wind, gusts, debris knocks, thunder
    s = sc["tufao"]
    d = D["tufao"]
    t = T(d)
    gust = 0.6 + 0.4 * np.abs(np.sin(t * 1.9))
    add(s, nz(d, 60, 900) * gust, 0.7)
    add(s, nz(d, 1200, 2600) * (0.4 + 0.6 * np.sin(t * 2.7) ** 2), 0.12)
    add(s, tone(lambda t: 420 + 160 * np.sin(2 * np.pi * 0.6 * t) + 40 * np.sin(2 * np.pi * 5 * t), d) * gust, 0.035)
    for _ in range(10):
        add(s + rng.uniform(0.1, d - 0.1), nz(0.05, 300, 3000) * np.exp(-T(0.05) / 0.012), 0.35)
    for lt in (0.7, 1.75):
        add(s + lt + 0.06, nz(1.3, 25, 2200) * np.exp(-T(1.3) / 0.4) * np.minimum(1, T(1.3) / 0.01), 0.5)


    # meteoros: roaring entries + deep delayed booms
    s = sc["meteoros"]
    add(s, nz(D["meteoros"], 20, 200), 0.25)
    for m, ti in zip(METEORS, meteor_impacts()):
        dd = ti - m[0]
        add(s + m[0], nz(dd, 150, 2500) * (T(dd) / dd) ** 2, 0.25 * m[5])
        add(s + ti + 0.08, kick(0.9), 0.55 * m[5])
        add(s + ti + 0.08, nz(1.6, 20, 260) * np.exp(-T(1.6) / 0.45), 0.7 * m[5])
    # aliens: deep drone, scan sweep, data blips, glitch
    s = sc["aliens"]
    d = D["aliens"]
    t = T(d)
    drone = (tone(46, d, (1, 2, 3, 4, 5, 6)) * (0.6 + 0.4 * np.sin(2 * np.pi * 0.7 * t))) * np.minimum(1, t / 0.4)
    add(s, drone, 0.22)
    add(s, tone(92.5, d, (1, 3)) * np.minimum(1, t / 0.6), 0.06)
    add(s + 0.55, nz(0.35, 400, 6000) * np.exp(-T(0.35) / 0.1), 0.35)
    add(s + 0.7, tone(lambda t: 400 + 1400 * t, 1.0) * (0.5 + 0.5 * np.sign(np.sin(2 * np.pi * 14 * T(1.0)))), 0.07)
    for q in (1.05, 1.3):
        add(s + q, tone(1800, 0.06), 0.15)
        add(s + q + 0.08, tone(2400, 0.06), 0.15)
    for q in np.arange(0.8, 1.8, 0.07):
        add(s + q, tone(rng.uniform(2500, 4200), 0.025), 0.05)
    for gt in (0.58, 1.95):
        add(s + gt, nz(0.1, 200, 8000) * (rng.random(int(0.1 * SR)) > 0.5), 0.2)
    # nevasca
    s = sc["nevasca"]
    d = D["nevasca"]
    add(s, nz(d, 250, 1600) * (0.5 + 0.5 * np.sin(T(d) * 3.1) ** 2), 0.3)


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
