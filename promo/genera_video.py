#!/usr/bin/env python3
"""
GENERATORE DI VIDEO PROMOZIONALI DAL LISTINO
============================================

Crea video verticali 1080x1920 (Instagram Reels, TikTok, Storie, Shorts,
Stato WhatsApp) partendo da products.json: un video per categoria con
copertina animata, 5 articoli in evidenza (codice, descrizione, marchio,
prezzo di listino) e chiusura con i contatti del negozio.

Requisiti: Python 3 + Pillow (pip install pillow) + ffmpeg nel PATH.

Esempi:
    # tutte le categorie principali
    python promo/genera_video.py --negozio "Ferramenta Rossi" \
        --telefono "0123 456789" --indirizzo "Via Roma 1, Milano"

    # solo alcune categorie, senza prezzi
    python promo/genera_video.py --categorie "Maniglie" "Motori" --senza-prezzi

    # articoli scelti a mano (codici del listino)
    python promo/genera_video.py --codici AG500070 ASSDC175BLACK TINE-BAT\\ 20 \
        --titolo "Offerte del mese"

I video finiscono in promo/video/ (cartella esclusa da git).
"""
import argparse
import json
import math
import os
import random
import re
import shutil
import subprocess
import sys

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
W, H, FPS = 1080, 1920, 30

# Palette: antracite + arancio "da cantiere"
BG1 = (18, 20, 24)
BG2 = (34, 38, 46)
ACCENT = (255, 138, 0)
ACCENT_D = (204, 102, 0)
WHITE = (245, 245, 242)
MUTED = (160, 166, 176)
CARD = (40, 44, 53)

# ---------------------------------------------------------------- font --
FONT_DIRS = [
    "/usr/share/fonts/opentype/inter", "/usr/share/fonts/truetype/dejavu",
    "/usr/share/fonts", "/Library/Fonts", "/System/Library/Fonts",
    "C:/Windows/Fonts",
]
FONT_CANDIDATES = {
    "black": ["InterDisplay-Black.otf", "Inter-Black.otf", "arialbd.ttf",
              "DejaVuSans-Bold.ttf", "Arial Bold.ttf"],
    "bold": ["InterDisplay-Bold.otf", "Inter-Bold.otf", "arialbd.ttf",
             "DejaVuSans-Bold.ttf", "Arial Bold.ttf"],
    "medium": ["Inter-Medium.otf", "arial.ttf", "DejaVuSans.ttf", "Arial.ttf"],
    "mono": ["DejaVuSansMono-Bold.ttf", "consolab.ttf", "Menlo.ttc",
             "DejaVuSansMono.ttf"],
}
_font_cache = {}


def _find_font(kind):
    for name in FONT_CANDIDATES[kind]:
        for d in FONT_DIRS:
            for dirpath, _, files in os.walk(d) if os.path.isdir(d) else []:
                if name in files:
                    return os.path.join(dirpath, name)
    return None


def font(kind, size):
    key = (kind, size)
    if key not in _font_cache:
        path = _find_font(kind)
        _font_cache[key] = (ImageFont.truetype(path, size) if path
                            else ImageFont.load_default(size))
    return _font_cache[key]


# ------------------------------------------------------------- easing ---
def clamp(x, a=0.0, b=1.0):
    return max(a, min(b, x))


def ease_out(t):
    t = clamp(t)
    return 1 - (1 - t) ** 3


def ease_in_out(t):
    t = clamp(t)
    return 4 * t ** 3 if t < .5 else 1 - (-2 * t + 2) ** 3 / 2


def back_out(t):
    t = clamp(t)
    c1, c3 = 1.70158, 2.70158
    return 1 + c3 * (t - 1) ** 3 + c1 * (t - 1) ** 2


def prog(t, start, dur):
    return clamp((t - start) / dur)


# --------------------------------------------------------- dati listino -
JUNK = re.compile(r"^\W*x?\W*$", re.I)
NOT_PRODUCT = re.compile(r"^\W*(maggiorazion|supplement|sovrapprezz|trasport|spes[ae]\b|"
                         r"riduzion|sconto|differenza)", re.I)


def load_catalog():
    with open(os.path.join(ROOT, "products.json"), encoding="utf-8") as f:
        return json.load(f)


def valid(item):
    d = (item.get("desc") or "").strip()
    p = item.get("price") or 0
    return (len(d) >= 6 and not JUNK.match(d) and not NOT_PRODUCT.match(d)
            and 0.5 <= p <= 3000)


def pretty_desc(desc):
    """'MANIGLIA PITAGORA CR.LUC.SAT.' -> 'Maniglia Pitagora Cr.Luc.Sat.'"""
    desc = re.sub(r"\*+", "", desc).strip()
    if desc.isupper() or sum(c.isupper() for c in desc) > len(desc) * .6:
        desc = " ".join(w if re.search(r"\d", w) else w.capitalize()
                        for w in desc.split())
    return desc


def brand_of(item):
    sub = (item.get("sub") or "").strip()
    cls = (item.get("classe") or "").strip()
    if sub and sub.lower() not in ("altro", "ferramenta", "viti"):
        return sub
    if cls and cls.upper() != "ALTRO":
        return cls.title()
    return ""


def pick_items(items, n=5, seed=7):
    """Sceglie n articoli vari: marchi diversi, descrizioni diverse."""
    rnd = random.Random(seed)
    pool = [i for i in items if valid(i)]
    rnd.shuffle(pool)
    chosen, seen_brand, seen_word = [], {}, set()
    for i in pool:
        b = brand_of(i)
        w = (i["desc"].split() or [""])[0].upper()
        if seen_brand.get(b, 0) >= 2 or w in seen_word:
            continue
        chosen.append(i)
        seen_brand[b] = seen_brand.get(b, 0) + 1
        seen_word.add(w)
        if len(chosen) == n:
            break
    for i in pool:  # se mancano, completa senza vincoli
        if len(chosen) >= n:
            break
        if i not in chosen:
            chosen.append(i)
    return chosen


def fmt_price(p):
    s = f"{p:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"€ {s}"


# ------------------------------------------------------------ icone ------
def icon_for(category):
    c = category.lower()
    for key, ic in [("manigl", "handle"), ("motor", "motor"),
                    ("cilindr", "key"), ("chiav", "key"),
                    ("sicurezza", "shield"), ("blindat", "shield"),
                    ("porte", "door"), ("porta", "door"),
                    ("finestr", "window"), ("serrament", "window"),
                    ("persian", "shutter"), ("scorrev", "slide"),
                    ("chiudiporta", "door"), ("posa", "gun"),
                    ("raso", "door")]:
        if key in c:
            return ic
    return "screw"


def draw_icon(d, kind, cx, cy, s, col, lw):
    """Icone vettoriali semplici, s = mezza dimensione."""
    if kind == "handle":
        d.rounded_rectangle([cx - s * .35, cy - s, cx + s * .35, cy + s * .2],
                            radius=s * .3, outline=col, width=lw)
        d.ellipse([cx - s * .12, cy - s * .7, cx + s * .12, cy - s * .46],
                  outline=col, width=lw)
        d.rounded_rectangle([cx - s * .15, cy - s * .2, cx + s * 1.1,
                             cy + s * .05], radius=s * .12, fill=col)
        d.ellipse([cx - s * .12, cy + s * .45, cx + s * .12, cy + s * .7],
                  outline=col, width=lw)
    elif kind == "key":
        d.ellipse([cx - s, cy - s * .45, cx - s * .1, cy + s * .45],
                  outline=col, width=lw)
        d.ellipse([cx - s * .7, cy - s * .15, cx - s * .4, cy + s * .15],
                  fill=col)
        d.rectangle([cx - s * .12, cy - s * .08, cx + s, cy + s * .08],
                    fill=col)
        for x in (s * .45, s * .75):
            d.rectangle([cx + x, cy, cx + x + s * .14, cy + s * .35],
                        fill=col)
    elif kind == "shield":
        pts = [(cx, cy - s), (cx + s * .85, cy - s * .6),
               (cx + s * .7, cy + s * .35), (cx, cy + s),
               (cx - s * .7, cy + s * .35), (cx - s * .85, cy - s * .6)]
        d.polygon(pts, outline=col, width=lw)
        d.rounded_rectangle([cx - s * .3, cy - s * .05, cx + s * .3,
                             cy + s * .45], radius=s * .06, fill=col)
        d.arc([cx - s * .22, cy - s * .4, cx + s * .22, cy + s * .1],
              180, 360, fill=col, width=lw)
    elif kind == "door":
        d.rectangle([cx - s * .6, cy - s, cx + s * .6, cy + s],
                    outline=col, width=lw)
        d.rectangle([cx - s * .4, cy - s * .8, cx + s * .4, cy - s * .1],
                    outline=col, width=max(2, lw // 2))
        d.rectangle([cx - s * .4, cy + s * .1, cx + s * .4, cy + s * .8],
                    outline=col, width=max(2, lw // 2))
        d.ellipse([cx + s * .3, cy - s * .08, cx + s * .48, cy + s * .1],
                  fill=col)
    elif kind == "window":
        d.rectangle([cx - s * .8, cy - s, cx + s * .8, cy + s],
                    outline=col, width=lw)
        d.line([cx, cy - s, cx, cy + s], fill=col, width=lw)
        d.line([cx - s * .8, cy - s * .1, cx + s * .8, cy - s * .1],
               fill=col, width=lw)
        d.rounded_rectangle([cx - s * .2, cy + s * .05, cx - s * .1,
                             cy + s * .45], radius=4, fill=col)
    elif kind == "shutter":
        d.rectangle([cx - s * .8, cy - s, cx + s * .8, cy + s],
                    outline=col, width=lw)
        for k in range(1, 8):
            y = cy - s + k * s * 2 / 8
            d.line([cx - s * .8, y, cx + s * .8, y], fill=col,
                   width=max(2, lw // 2))
    elif kind == "slide":
        d.line([cx - s, cy - s * .9, cx + s, cy - s * .9], fill=col, width=lw)
        d.rectangle([cx - s * .7, cy - s * .75, cx + s * .3, cy + s],
                    outline=col, width=lw)
        d.polygon([(cx + s * .5, cy - s * .1), (cx + s * .9, cy + s * .1),
                   (cx + s * .5, cy + s * .3)], fill=col)
    elif kind == "motor":
        d.rounded_rectangle([cx - s, cy - s * .35, cx + s * .7, cy + s * .35],
                            radius=s * .2, outline=col, width=lw)
        d.rectangle([cx + s * .7, cy - s * .12, cx + s, cy + s * .12],
                    fill=col)
        for k in range(4):
            x = cx - s * .7 + k * s * .35
            d.line([x, cy - s * .2, x, cy + s * .2], fill=col,
                   width=max(2, lw // 2))
        d.arc([cx - s * .5, cy - s, cx + s * .5, cy - s * .4], 200, 340,
              fill=col, width=max(2, lw // 2))
    elif kind == "gun":
        d.rounded_rectangle([cx - s, cy - s * .55, cx + s * .5, cy - s * .05],
                            radius=s * .1, outline=col, width=lw)
        d.polygon([(cx + s * .5, cy - s * .4), (cx + s, cy - s * .3),
                   (cx + s * .5, cy - s * .2)], fill=col)
        d.rounded_rectangle([cx - s * .6, cy - s * .05, cx - s * .3, cy + s],
                            radius=s * .1, fill=col)
    else:  # screw
        d.polygon([(cx - s * .55, cy - s), (cx + s * .55, cy - s),
                   (cx + s * .55, cy - s * .7), (cx - s * .55, cy - s * .7)],
                  fill=col)
        d.polygon([(cx - s * .22, cy - s * .7), (cx + s * .22, cy - s * .7),
                   (cx + s * .22, cy + s * .6), (cx, cy + s),
                   (cx - s * .22, cy + s * .6)], outline=col, width=lw)
        for k in range(6):
            y = cy - s * .55 + k * s * .2
            d.line([cx - s * .32, y + s * .08, cx + s * .32, y], fill=col,
                   width=max(2, lw // 2))


# ------------------------------------------------------------ disegno ----
def background():
    """Gradiente verticale + bande diagonali tenui (riutilizzato)."""
    img = Image.new("RGB", (W, H), BG1)
    px = ImageDraw.Draw(img)
    for y in range(H):
        k = y / H
        c = tuple(int(BG1[i] + (BG2[i] - BG1[i]) * (1 - abs(k - .45) * 1.6))
                  for i in range(3))
        px.line([(0, y), (W, y)], fill=c)
    over = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    od = ImageDraw.Draw(over)
    for k in range(-6, 14):
        x = k * 180
        od.polygon([(x, 0), (x + 60, 0), (x + 60 - H * .35, H),
                    (x - H * .35, H)], fill=(255, 255, 255, 6))
    img.paste(over, (0, 0), over)
    return img


def text_w(d, txt, f):
    return d.textlength(txt, font=f)


def wrap(d, txt, f, maxw, max_lines=3):
    words, lines, cur = txt.split(), [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if text_w(d, t, f) <= maxw:
            cur = t
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        while text_w(d, lines[-1] + "…", f) > maxw and " " in lines[-1]:
            lines[-1] = lines[-1].rsplit(" ", 1)[0]
        lines[-1] += "…"
    return lines


def fit_font(d, txt, kind, size, maxw, min_size=40):
    while size > min_size and text_w(d, txt, font(kind, size)) > maxw:
        size -= 4
    return font(kind, size)


def a(col, alpha):
    return col + (int(255 * clamp(alpha)),)


def header(frame, cfg, t):
    d = ImageDraw.Draw(frame, "RGBA")
    al = ease_out(prog(t, 0, .6))
    d.rectangle([0, 0, W, 10], fill=a(ACCENT, al))
    if cfg.negozio:
        f = fit_font(d, cfg.negozio.upper(), "bold", 40, W - 160)
        d.text((80, 70), cfg.negozio.upper(), font=f, fill=a(WHITE, al))


def scene_intro(frame, cfg, scene, t, dur):
    d = ImageDraw.Draw(frame, "RGBA")
    out = 1 - ease_in_out(prog(t, dur - .4, .4))
    # icona in un cerchio
    s = ease_out(prog(t, .1, .7))
    r = 190 * back_out(prog(t, .1, .7))
    cx, cy = W // 2, 700
    if r > 2:
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=a(ACCENT, out))
        draw_icon(d, scene["icon"], cx, cy, r * .55, a(BG1, out),
                  max(3, int(r / 16)))
    # etichetta
    al = ease_out(prog(t, .5, .5)) * out
    lab = scene["kicker"].upper()
    f = font("bold", 42)
    tw = text_w(d, lab, f)
    d.text(((W - tw) / 2, 980), lab, font=f, fill=a(ACCENT, al))
    # titolo che sale
    yoff = 60 * (1 - ease_out(prog(t, .6, .6)))
    al = ease_out(prog(t, .6, .6)) * out
    f = fit_font(d, scene["title"], "black", 150, W - 140, 64)
    lines = wrap(d, scene["title"], f, W - 140, 3)
    y = 1050 + yoff
    for ln in lines:
        tw = text_w(d, ln, f)
        d.text(((W - tw) / 2, y), ln, font=f, fill=a(WHITE, al))
        y += f.size * 1.08
    # sottotitolo
    al = ease_out(prog(t, 1.1, .6)) * out
    f = font("medium", 44)
    for ln in wrap(d, scene["subtitle"], f, W - 200, 3):
        tw = text_w(d, ln, f)
        d.text(((W - tw) / 2, y + 30), ln, font=f, fill=a(MUTED, al))
        y += 60


def scene_product(frame, cfg, scene, t, dur):
    item, idx, tot = scene["item"], scene["idx"], scene["tot"]
    d = ImageDraw.Draw(frame, "RGBA")
    enter = ease_out(prog(t, 0, .55))
    leave = ease_in_out(prog(t, dur - .35, .35))
    dx = (1 - enter) * W * .9 - leave * W * .9
    al = clamp(enter * 1.5) * (1 - leave)

    # contatore
    f = font("bold", 40)
    d.text((80, 170), f"{idx:02d}", font=font("black", 110),
           fill=a(ACCENT, al))
    d.text((80, 290), f"/ {tot:02d}  ·  {scene['category'].upper()}",
           font=f, fill=a(MUTED, al))

    # card
    x0, y0, x1, y1 = 70 + dx, 420, W - 70 + dx, 1640
    d.rounded_rectangle([x0, y0, x1, y1], radius=48, fill=a(CARD, al))
    d.rounded_rectangle([x0, y0, x1, y0 + 560], radius=48,
                        fill=a((52, 57, 68), al))
    d.rectangle([x0, y0 + 500, x1, y0 + 560], fill=a((52, 57, 68), al))
    # icona grande "tratteggiata" nel riquadro
    pulse = 1 + .04 * math.sin(t * 3)
    cx, cy = (x0 + x1) / 2, y0 + 280
    d.ellipse([cx - 200 * pulse, cy - 200 * pulse, cx + 200 * pulse,
               cy + 200 * pulse], outline=a(ACCENT, al * .35), width=6)
    draw_icon(d, scene["icon"], cx, cy, 130, a(ACCENT, al), 12)
    # marchio
    brand = brand_of(item)
    if brand:
        f = font("bold", 38)
        bw = text_w(d, brand.upper(), f) + 56
        d.rounded_rectangle([x0 + 50, y0 + 50, x0 + 50 + bw, y0 + 118],
                            radius=34, fill=a(ACCENT, al))
        d.text((x0 + 78, y0 + 62), brand.upper(), font=f, fill=a(BG1, al))

    # testi
    tx = x0 + 60
    ty = y0 + 610
    f = font("mono", 36)
    d.text((tx, ty), f"COD. {item['code']}", font=f, fill=a(MUTED, al))
    desc = pretty_desc(item["desc"])
    f = font("bold", 64)
    lines = wrap(d, desc, f, x1 - x0 - 120, 4)
    if len(lines) >= 4:
        f = font("bold", 54)
        lines = wrap(d, desc, f, x1 - x0 - 120, 4)
    ty += 70
    for ln in lines:
        d.text((tx, ty), ln, font=f, fill=a(WHITE, al))
        ty += f.size * 1.18

    # prezzo con pop
    if cfg.prezzi:
        pp = back_out(prog(t, .45, .5))
        ps = int(120 * max(.2, pp))
        f = font("black", ps)
        price = fmt_price(item["price"])
        py = y1 - 230
        d.text((tx, py), price, font=f, fill=a(ACCENT, al * clamp(pp)))
        f = font("medium", 34)
        d.text((tx, py + 140),
               f"Prezzo di listino · IVA esclusa · al {item.get('unit') or 'pz'}",
               font=f, fill=a(MUTED, al * clamp(pp)))
    else:
        f = font("bold", 52)
        d.text((tx, y1 - 170), "Disponibile in negozio", font=f,
               fill=a(ACCENT, al))

    # barra avanzamento
    bw = (W - 160) * clamp(t / dur)
    d.rounded_rectangle([80, 1720, W - 80, 1730], radius=5,
                        fill=a((70, 75, 86), al))
    d.rounded_rectangle([80, 1720, 80 + bw, 1730], radius=5,
                        fill=a(ACCENT, al))


def scene_outro(frame, cfg, scene, t, dur):
    d = ImageDraw.Draw(frame, "RGBA")
    # fascia arancio che entra
    k = ease_in_out(prog(t, 0, .6))
    d.rectangle([0, H * (1 - k), W, H], fill=(*ACCENT, 255))
    al = ease_out(prog(t, .5, .5))
    y = 520 + 40 * (1 - al)
    for ln, kind, size in [("VIENI A", "black", 120),
                           ("TROVARCI!", "black", 150)]:
        f = font(kind, size)
        tw = text_w(d, ln, f)
        d.text(((W - tw) / 2, y), ln, font=f, fill=a(BG1, al))
        y += size * 1.05
    y += 40
    f = font("medium", 46)
    for ln in wrap(d, scene["claim"], f, W - 200, 3):
        tw = text_w(d, ln, f)
        d.text(((W - tw) / 2, y), ln, font=f, fill=a(BG1, al))
        y += 62
    y += 70
    rows = [cfg.negozio, cfg.indirizzo, cfg.telefono and f"Tel. {cfg.telefono}",
            cfg.sito]
    for i, ln in enumerate([r for r in rows if r]):
        al2 = ease_out(prog(t, .9 + i * .2, .5))
        f = fit_font(d, ln, "black" if i == 0 else "bold",
                     76 if i == 0 else 52, W - 160, 32)
        tw = text_w(d, ln, f)
        if i == 0:
            d.rounded_rectangle([(W - tw) / 2 - 40, y - 18, (W + tw) / 2 + 40,
                                 y + f.size + 28], radius=24,
                                fill=a(BG1, al2))
            d.text(((W - tw) / 2, y), ln, font=f, fill=a(ACCENT, al2))
            y += f.size + 80
        else:
            d.text(((W - tw) / 2, y), ln, font=f, fill=a(BG1, al2))
            y += f.size + 30


SCENES = {"intro": scene_intro, "product": scene_product,
          "outro": scene_outro}


# ----------------------------------------------------------- rendering ---
def render(video_scenes, cfg, out_path):
    bg = background()
    total = sum(s["dur"] for s in video_scenes)
    n = int(total * FPS)
    cmd = [cfg.ffmpeg, "-y", "-loglevel", "error", "-f", "rawvideo",
           "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-"]
    if cfg.musica:
        cmd += ["-stream_loop", "-1", "-i", cfg.musica, "-shortest",
                "-af", f"afade=t=out:st={total - 1.5}:d=1.5", "-c:a", "aac",
                "-b:a", "160k"]
    cmd += ["-c:v", "libx264", "-preset", "medium", "-crf", "20",
            "-pix_fmt", "yuv420p", "-movflags", "+faststart", out_path]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    t0 = 0.0
    bounds = []
    for s in video_scenes:
        bounds.append((t0, t0 + s["dur"], s))
        t0 += s["dur"]
    for fi in range(n):
        t = fi / FPS
        frame = bg.copy()
        for start, end, s in bounds:
            if start <= t < end:
                if s["type"] != "outro":
                    header(frame, cfg, t)
                SCENES[s["type"]](frame, cfg, s, t - start, s["dur"])
                break
        proc.stdin.write(frame.tobytes())
    proc.stdin.close()
    if proc.wait() != 0:
        raise RuntimeError("ffmpeg ha restituito un errore")


def build_scenes(title, kicker, subtitle, items, category, icon, cfg):
    sc = [{"type": "intro", "dur": 3.2, "title": title, "kicker": kicker,
           "subtitle": subtitle, "icon": icon}]
    for i, it in enumerate(items, 1):
        sc.append({"type": "product", "dur": cfg.durata_articolo, "item": it,
                   "idx": i, "tot": len(items), "category": category,
                   "icon": icon})
    sc.append({"type": "outro", "dur": 4.0,
               "claim": "Consulenza, ricambi e forniture complete per "
                        "professionisti e privati."})
    return sc


def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--negozio", default="", help="nome del negozio")
    ap.add_argument("--telefono", default="")
    ap.add_argument("--indirizzo", default="")
    ap.add_argument("--sito", default="", help="sito o profilo social")
    ap.add_argument("--categorie", nargs="*",
                    help="categorie da generare (default: tutte con >= 5 "
                         "articoli validi)")
    ap.add_argument("--codici", nargs="*",
                    help="video unico con questi codici articolo")
    ap.add_argument("--titolo", default="Offerte del mese",
                    help="titolo del video con --codici")
    ap.add_argument("--articoli", type=int, default=5,
                    help="articoli per video (default 5)")
    ap.add_argument("--durata-articolo", type=float, default=2.8)
    ap.add_argument("--senza-prezzi", dest="prezzi", action="store_false")
    ap.add_argument("--musica", help="file audio (mp3/wav) di sottofondo "
                                     "con licenza d'uso")
    ap.add_argument("--seme", type=int, default=7,
                    help="cambia il numero per scegliere altri articoli")
    ap.add_argument("--uscita", default=os.path.join(ROOT, "promo", "video"))
    cfg = ap.parse_args()

    cfg.ffmpeg = shutil.which("ffmpeg")
    if not cfg.ffmpeg:
        sys.exit("ffmpeg non trovato: installalo (https://ffmpeg.org) e riprova.")
    os.makedirs(cfg.uscita, exist_ok=True)
    catalog = load_catalog()
    jobs = []

    if cfg.codici:
        by_code = {i["code"]: (i, c["name"]) for c in catalog
                   for i in c["items"]}
        found = [by_code[c] for c in cfg.codici if c in by_code]
        missing = [c for c in cfg.codici if c not in by_code]
        if missing:
            print("Codici non trovati:", ", ".join(missing))
        if not found:
            sys.exit("Nessun articolo valido.")
        items = [f[0] for f in found]
        jobs.append((slug(cfg.titolo), build_scenes(
            cfg.titolo, "Selezione speciale",
            f"{len(items)} articoli scelti per te", items, cfg.titolo,
            icon_for(found[0][1]), cfg)))
    else:
        wanted = {c.lower() for c in cfg.categorie} if cfg.categorie else None
        for cat in catalog:
            name = cat["name"]
            if wanted and name.lower() not in wanted:
                continue
            good = [i for i in cat["items"] if valid(i)]
            if len(good) < cfg.articoli and not wanted:
                continue
            cnt = {}
            for i in good:
                b = brand_of(i)
                if b:
                    cnt[b] = cnt.get(b, 0) + 1
            brands = sorted(cnt, key=cnt.get, reverse=True)
            n_art = f"{len(good):,}".replace(",", ".")
            sub = f"{n_art} articoli a listino"
            if brands:
                sub += " · " + ", ".join(brands[:3])
            items = pick_items(good, cfg.articoli, cfg.seme)
            jobs.append((slug(name), build_scenes(
                name, "Scopri la nostra gamma", sub, items, name,
                icon_for(name), cfg)))

    if not jobs:
        sys.exit("Nessuna categoria trovata. Controlla i nomi con "
                 "--categorie (es. \"Maniglie\").")
    for name, scenes in jobs:
        out = os.path.join(cfg.uscita, f"promo-{name}.mp4")
        print(f"→ {out}  ({sum(s['dur'] for s in scenes):.0f}s)", flush=True)
        render(scenes, cfg, out)
    print(f"Fatto: {len(jobs)} video in {cfg.uscita}")


if __name__ == "__main__":
    main()
