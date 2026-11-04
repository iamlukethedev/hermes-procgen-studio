# technique: generative raster pattern with numpy (topographic contour lines)
# use for: seamless-ish patterns, textures, generative backgrounds, noise fields, data-art
# notes: fully vectorised numpy (no per-pixel Python loops); returns a uint8 array; the seeded
#        generator makes every seed a different map.
import numpy as np


def draw(t=0.0):
    nrng = np.random.default_rng(SEED)

    def vnoise(res):
        g = nrng.random((res + 1, res + 1))
        xs = np.linspace(0, res, W, endpoint=False)
        ys = np.linspace(0, res, H, endpoint=False)
        ix, iy = xs.astype(int), ys.astype(int)
        fx, fy = xs - ix, ys - iy
        fx, fy = fx * fx * (3 - 2 * fx), fy * fy * (3 - 2 * fy)
        a = g[np.ix_(iy, ix)]
        b = g[np.ix_(iy, ix + 1)]
        c = g[np.ix_(iy + 1, ix)]
        d = g[np.ix_(iy + 1, ix + 1)]
        fx, fy = fx[None, :], fy[:, None]
        return (a * (1 - fx) + b * fx) * (1 - fy) + (c * (1 - fx) + d * fx) * fy

    h = sum(vnoise(r) / (2.2 ** k) for k, r in enumerate([2, 4, 8, 16]))
    h = (h - h.min()) / (h.max() - h.min() + 1e-9)
    levels = 28
    v = h * levels
    fr = v - np.floor(v)
    gy, gx = np.gradient(v)
    gm = np.hypot(gx, gy) + 1e-6
    dist = np.minimum(fr, 1 - fr) / gm
    line = np.clip(1.6 - dist, 0, 1)
    major = (np.round(v) % 5 == 0)
    line = np.where(major, np.clip(2.6 - dist, 0, 1), line * .65)
    c0 = np.array(hex_to_rgb(PAL["bg2"]), float)
    c1 = np.array(hex_to_rgb(PAL["bg"]), float)
    c2 = np.array(hex_to_rgb(PAL["base2"]), float)
    tt = h[..., None]
    base = np.where(tt < .5, c0 + (c1 - c0) * (tt / .5), c1 + (c2 - c1) * ((tt - .5) / .5))
    band = (np.floor(v) % 2)[..., None] * 6
    ink = np.array(hex_to_rgb(PAL["glow"]), float)
    img = base + band
    img = img * (1 - line[..., None]) + ink * line[..., None]
    yy, xx = np.mgrid[0:H, 0:W]
    vig = 1 - 0.35 * (((xx - W / 2) ** 2 + (yy - H / 2) ** 2) / (max(W, H) / 2) ** 2)
    return np.clip(img * vig[..., None], 0, 255).astype(np.uint8)
