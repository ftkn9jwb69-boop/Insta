#!/usr/bin/env python3
"""Cut people/objects out of the wall background with GrabCut + hand-placed hints.

Writes editor/masks/<photo>.png  (white = background that gets replaced by sky / wave).
"""
import os
import cv2
import numpy as np
from PIL import Image, ImageOps

UP = os.environ.get("PHOTOS", "/root/.claude/uploads/af75da45-f17d-563b-bcb8-1c71ac64484a")
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "masks")

# rects are (x0, y0, x1, y1) in image fractions
HINTS = {
    # 5: woman looking at monitor (tsunami)
    5: dict(file="317657d7",
            bg=[(0.01, 0.22, 0.16, 0.50), (0.41, 0.33, 0.60, 0.52), (0.62, 0.00, 0.98, 0.07), (0.40, 0.30, 0.62, 0.36),
                (0.0, 0.0, 0.2, 0.2)],
            prbg=[(0.0, 0.0, 1.0, 0.70)],
            fg=[(0.24, 0.42, 0.33, 0.50), (0.21, 0.57, 0.46, 1.0), (0.68, 0.34, 0.88, 0.55), (0.64, 0.60, 0.88, 0.80),
                (0.0, 0.78, 1.0, 1.0), (0.0, 0.58, 0.18, 1.0), (0.955, 0.33, 0.975, 0.60),
                (0.33, 0.135, 0.62, 0.20), (0.28, 0.10, 0.70, 0.13), (0.62, 0.20, 0.87, 0.215), (0.45, 0.21, 0.49, 0.30), (0.66, 0.215, 0.80, 0.235)],
            ell=[(0.300, 0.458, 0.118, 0.092)],
            floor=0.72),
    # 7: thumbs up (meteors)
    7: dict(file="7eb547ba",
            bg=[(0.47, 0.01, 0.70, 0.40), (0.42, 0.13, 0.72, 0.30), (0.93, 0.02, 1.0, 0.22), (0.0, 0.0, 0.15, 0.2)],
            prbg=[(0.0, 0.0, 1.0, 0.47)],
            fg=[(0.25, 0.04, 0.37, 0.22), (0.06, 0.28, 0.40, 1.0), (0.77, 0.13, 0.88, 0.27), (0.68, 0.34, 0.95, 1.0),
                (0.0, 0.66, 1.0, 1.0), (0.46, 0.49, 0.64, 0.70), (0.23, 0.02, 0.38, 0.09), (0.74, 0.09, 0.89, 0.16)],
            refine=[(0.16, 0.0, 0.45, 0.12, 2.4), (0.70, 0.06, 0.93, 0.19, 3.2)],
            floor=0.48),
    # 11: two looking up (aliens)
    11: dict(file="a7fddd95",
             bg=[(0.02, 0.03, 0.22, 0.33), (0.45, 0.03, 0.95, 0.28), (0.43, 0.05, 0.62, 0.34), (0.86, 0.30, 1.0, 0.46),
                 (0.0, 0.0, 1.0, 0.05)],
             prbg=[(0.0, 0.0, 1.0, 0.56)],
             fg=[(0.29, 0.25, 0.37, 0.35), (0.22, 0.42, 0.50, 1.0), (0.68, 0.36, 0.79, 0.46), (0.64, 0.52, 0.90, 1.0),
                 (0.0, 0.78, 1.0, 1.0)],
             ell=[(0.330, 0.298, 0.075, 0.084), (0.742, 0.410, 0.095, 0.090), (0.35, 0.395, 0.13, 0.035),
                  (0.76, 0.48, 0.12, 0.03)],
             floor=0.58),
    # 10: smiling + arms-crossed (typhoon)
    10: dict(file="57995877",
             bg=[(0.02, 0.02, 0.98, 0.21), (0.0, 0.22, 0.14, 0.40), (0.0, 0.38, 0.05, 0.44), (0.43, 0.22, 0.53, 0.30), (0.77, 0.45, 1.0, 0.54),
                 (0.68, 0.22, 1.0, 0.32), (0.67, 0.335, 0.89, 0.415), (0.385, 0.31, 0.45, 0.42)],
             prbg=[(0.0, 0.0, 1.0, 0.72)],
             fg=[(0.08, 0.48, 0.50, 1.0), (0.55, 0.47, 0.71, 0.72), (0.0, 0.76, 1.0, 1.0), (0.84, 0.57, 1.0, 0.70)],
             ell=[(0.285, 0.345, 0.085, 0.095), (0.605, 0.385, 0.058, 0.07), (0.29, 0.44, 0.24, 0.04), (0.62, 0.465, 0.1, 0.03)],
             floor=0.74),
}


def rect(m, r, v, w, h):
    x0, y0, x1, y1 = r
    m[int(y0 * h):int(y1 * h), int(x0 * w):int(x1 * w)] = v


def run(n, cfg):
    im = ImageOps.exif_transpose(Image.open(f"{UP}/{cfg['file']}-image.jpg")).convert("RGB")
    W0, H0 = im.size
    s = 700 / W0
    small = np.asarray(im.resize((700, int(H0 * s)), Image.LANCZOS))[:, :, ::-1].copy()
    h, w = small.shape[:2]
    m = np.full((h, w), cv2.GC_PR_FGD, np.uint8)
    for r in cfg["prbg"]:
        rect(m, r, cv2.GC_PR_BGD, w, h)
    for r in cfg["bg"]:
        rect(m, r, cv2.GC_BGD, w, h)
    for r in cfg["fg"]:
        rect(m, r, cv2.GC_FGD, w, h)
    m[int(cfg["floor"] * h):, :] = cv2.GC_FGD
    yy, xx = np.mgrid[0:h, 0:w]
    ellm = np.zeros((h, w), bool)
    for (ex, ey, rx, ry) in cfg.get("ell", []):
        ellm |= ((xx / w - ex) / rx) ** 2 + ((yy / h - ey) / ry) ** 2 <= 1
    m[ellm] = cv2.GC_FGD
    bgd, fgd = np.zeros((1, 65), np.float64), np.zeros((1, 65), np.float64)
    cv2.grabCut(small, m, None, bgd, fgd, 8, cv2.GC_INIT_WITH_MASK)
    bgmask = np.where((m == cv2.GC_BGD) | (m == cv2.GC_PR_BGD), 255, 0).astype(np.uint8)
    if cfg.get("refine"):
        lab = cv2.cvtColor(small, cv2.COLOR_BGR2LAB).astype(np.float32)
        wall = np.zeros((h, w), bool)
        for r in cfg["bg"]:
            x0, y0, x1, y1 = r
            wall[int(y0 * h):int(y1 * h), int(x0 * w):int(x1 * w)] = True
        mu = lab[wall].mean(0)
        sd = lab[wall].std(0) + 2
        dist = np.sqrt((((lab - mu) / sd) ** 2).sum(-1))
        for r in cfg["refine"]:
            x0, y0, x1, y1 = [int(v) for v in (r[0] * w, r[1] * h, r[2] * w, r[3] * h)]
            sub = (dist[y0:y1, x0:x1] < r[4]).astype(np.uint8) * 255
            sub = cv2.morphologyEx(sub, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
            # keep only wall-coloured blobs that touch the existing background (no holes inside caps)
            seed = np.maximum(sub, bgmask[y0:y1, x0:x1])
            n_, lab_ = cv2.connectedComponents(seed)
            border = set(np.unique(np.concatenate([lab_[0], lab_[-1], lab_[:, 0], lab_[:, -1]]))) - {0}
            touch = np.isin(lab_, list(border)) & (bgmask[y0:y1, x0:x1] == 0)
            near = cv2.dilate(bgmask[y0:y1, x0:x1], np.ones((15, 15), np.uint8)) > 0
            sub = ((sub > 0) & touch & near).astype(np.uint8) * 255
            bgmask[y0:y1, x0:x1] = np.maximum(bgmask[y0:y1, x0:x1], sub)
    # heads: protect the core, but let wall-coloured pixels in the outer ring go back to background
    lab = cv2.cvtColor(small, cv2.COLOR_BGR2LAB).astype(np.float32)
    wallr = np.zeros((h, w), bool)
    for r in cfg["bg"]:
        wallr[int(r[1] * h):int(r[3] * h), int(r[0] * w):int(r[2] * w)] = True
    mu, sd = lab[wallr].mean(0), lab[wallr].std(0) + 2
    dist = np.sqrt((((lab - mu) / sd) ** 2).sum(-1))
    core = np.zeros((h, w), bool)
    for (ex, ey, rx, ry) in cfg.get("ell", []):
        core |= ((xx / w - ex) / (rx * 0.84)) ** 2 + ((yy / h - ey) / (ry * 0.84)) ** 2 <= 1
    ring = ellm & ~core & (dist < cfg.get("ring_thr", 2.2))
    cand = ((bgmask > 0) | ring).astype(np.uint8)
    n_, lab_ = cv2.connectedComponents(cand)
    good = set(np.unique(lab_[bgmask > 0])) - {0}
    bgmask = np.where(np.isin(lab_, list(good)) & ((bgmask > 0) | ring), 255, 0).astype(np.uint8)
    bgmask[core] = 0
    # clean: keep only background blobs connected to large areas, close small holes
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    bgmask = cv2.morphologyEx(bgmask, cv2.MORPH_OPEN, k)
    bgmask = cv2.morphologyEx(bgmask, cv2.MORPH_CLOSE, k)
    nlab, lab, stats, _ = cv2.connectedComponentsWithStats(bgmask)
    keep = np.zeros_like(bgmask)
    for i in range(1, nlab):
        if stats[i, cv2.CC_STAT_AREA] > 0.004 * w * h:
            keep[lab == i] = 255
    big = cv2.resize(keep, (W0, H0), interpolation=cv2.INTER_LINEAR)
    big = cv2.GaussianBlur(big, (0, 0), 2.2)
    os.makedirs(OUT, exist_ok=True)
    Image.fromarray(big).save(f"{OUT}/{n}.png")
    return im, big


if __name__ == "__main__":
    import sys
    prev = []
    for n, cfg in HINTS.items():
        im, mk = run(n, cfg)
        a = np.asarray(im, np.float32)
        tint = a * (1 - mk[..., None] / 255 * 0.7) + np.array([255, 0, 255]) * (mk[..., None] / 255 * 0.7)
        prev.append(Image.fromarray(tint.astype(np.uint8)).resize((500, int(500 * im.height / im.width))))
    if len(sys.argv) > 1:
        sheet = Image.new("RGB", (500 * len(prev), max(p.height for p in prev)))
        for i, p in enumerate(prev):
            sheet.paste(p, (i * 500, 0))
        sheet.save(sys.argv[1])
