#!/usr/bin/env python3
"""Render the homelab diagram (r/homelab style) from inventory.yaml.

Usage: python3 build.py [--unredacted] [--inventory FILE]
                                         → out/homelab.svg + out/homelab.png
                                            (--unredacted → out/homelab-unredacted.*)

Content comes from inventory.yaml; the layout (positions of every box and line) is
hand-placed in this file for one specific network, so expect to move things around.
The shape of this lab: one Docker host behind CGNAT, no VLANs, no hypervisor and no
port forwards — two outbound doors (Cloudflare Tunnel and Tailscale) and everything
running as containers on a laptop.
Written with Claude (Anthropic) in Claude Code, see README.md.
"""
import base64
import html
import pathlib
import subprocess
import sys

import yaml

ROOT = pathlib.Path(__file__).resolve().parent
if "--inventory" in sys.argv:
    _i = sys.argv.index("--inventory") + 1
    if _i >= len(sys.argv) or sys.argv[_i].startswith("--"):
        raise SystemExit("--inventory needs a file")
    INV_FILE = pathlib.Path(sys.argv[_i])
else:
    INV_FILE = ROOT / "inventory.yaml"
INV = yaml.safe_load(INV_FILE.read_text())
ICONS = ROOT / "icons"
OUT = ROOT / "out"

UNREDACTED = "--unredacted" in sys.argv
RED = INV["redact"]
DOMAIN = INV["meta"]["domain"]
SHOWN_DOMAIN = DOMAIN if (UNREDACTED or not RED["domain"]) else RED["domain_placeholder"]

W, H = 2400, 0      # H is set at the end from the content (see "canvas height")

# ---------------------------------------------------------------- palette
BG = "#0b0f15"
GRID = "#151c26"
PANEL = "#111821"
PANEL2 = "#161f2b"
CHIP = "#19222e"
DECK = "#0f151e"
BORDER = "#253042"
TEXT = "#e6edf3"
MUTED = "#8d9aab"
FAINT = "#5d6a7c"
LINK = "#79c0ff"        # a public hostname / the public door
HWC = "#c08a5c"         # physical hardware accent (also: a host service, not a container)
REDDIT = "#ff5722"
SANS = "Fira Sans"
MONO = "Fira Code"
NET = {n["key"]: n for n in INV["networks"]}
C = {k: v["color"] for k, v in NET.items()}
EXP = {"public": LINK, "private": C["ts"], "host": HWC}
EXP_TAG = {"public": "PUBLIC", "private": "TAILNET", "host": "HOST"}


def sub(s):
    """Fill {domain} placeholders and apply redaction."""
    s = str(s).replace("{domain}", SHOWN_DOMAIN)
    if not UNREDACTED and RED["domain"]:
        s = s.replace(DOMAIN, SHOWN_DOMAIN)
    return s


def esc(s):
    return html.escape(sub(s), quote=True)


def tw(s, size, mono=False):
    """Rough text width for layout decisions."""
    return len(sub(s)) * size * (0.6 if mono else 0.53)


def _font_file(pattern):
    """Path of an installed font via fontconfig (fc-match), or None."""
    try:
        out = subprocess.run(["fc-match", "-f", "%{file}", pattern], capture_output=True, text=True, timeout=5)
        return out.stdout.strip() or None
    except Exception:
        return None


try:
    from PIL import ImageFont
    _FONTS = {700: _font_file("Fira Sans:bold"), 600: _font_file("Fira Sans:semibold"),
              400: _font_file("Fira Sans:regular")}
    if not all(_FONTS.values()):
        raise RuntimeError("Fira Sans not found")
    _FCACHE = {}

    def measure(s, size, weight=700):
        key = (weight, size)
        if key not in _FCACHE:
            _FCACHE[key] = ImageFont.truetype(_FONTS[weight], size=size * 8)
        return _FCACHE[key].getlength(s) / 8
except Exception:          # no Pillow / font file: rough estimate
    def measure(s, size, weight=700):
        return tw_title(s, size)


def tw_title(s, size):
    """Width estimate for bold title text (capitals are wider than lowercase)."""
    return sum(0.68 if c.isupper() else 0.58 if c.isdigit() else 0.5 for c in s) * size


def fqdn(h):
    return f"{h}.{SHOWN_DOMAIN}" if h else SHOWN_DOMAIN


def wrap(text, width, size, weight=400):
    """Split text into lines that fit `width` px (measured with the real font)."""
    words, lines, cur = sub(text).split(), [], ""
    for w_ in words:
        t = f"{cur} {w_}".strip()
        if measure(t, size, weight) <= width or not cur:
            cur = t
        else:
            lines.append(cur)
            cur = w_
    if cur:
        lines.append(cur)
    return lines


class SVG:
    def __init__(self):
        self.p = []
        self._icon_cache = {}

    def add(self, s):
        self.p.append(s)

    def rect(self, x, y, w, h, rx=10, fill=PANEL, stroke=BORDER, sw=1.2, op=1, dash=None):
        d = f' stroke-dasharray="{dash}"' if dash else ""
        self.add(f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" rx="{rx}" '
                 f'fill="{fill}" stroke="{stroke}" stroke-width="{sw}" opacity="{op}"{d}/>')

    def text(self, x, y, s, size=14, fill=TEXT, weight=400, anchor="start", mono=False, ls=0, op=1):
        fam = MONO if mono else SANS
        lsa = f' letter-spacing="{ls}"' if ls else ""
        self.add(f'<text x="{x:.1f}" y="{y:.1f}" font-family="{fam}" font-size="{size}" fill="{fill}" '
                 f'font-weight="{weight}" text-anchor="{anchor}" opacity="{op}"{lsa}>{esc(s)}</text>')

    def icon(self, name, x, y, size):
        if name not in self._icon_cache:
            f = ICONS / f"{name}.svg"
            if not f.exists():
                raise SystemExit(f"missing icon '{name}' – run: python3 fetch_icons.py")
            self._icon_cache[name] = base64.b64encode(f.read_bytes()).decode()
        self.add(f'<image x="{x:.1f}" y="{y:.1f}" width="{size}" height="{size}" '
                 f'href="data:image/svg+xml;base64,{self._icon_cache[name]}"/>')

    def line(self, pts, color, sw=3, dash=None, r=10, op=1, cap="round"):
        self.add(f'<path d="{rounded(pts, r)}" fill="none" stroke="{color}" stroke-width="{sw}" '
                 f'stroke-linecap="{cap}" stroke-linejoin="round" opacity="{op}"'
                 + (f' stroke-dasharray="{dash}"' if dash else "") + "/>")

    def arrow(self, pts, color=MUTED, sw=1.8, dash=None, r=8):
        """Line with an arrowhead at the last point."""
        self.line(pts, color, sw, dash=dash, r=r)
        (x0, y0), (x1, y1) = pts[-2], pts[-1]
        dx, dy = x1 - x0, y1 - y0
        ln = max(abs(dx), abs(dy)) or 1
        ux, uy = dx / ln, dy / ln
        a, b = 8, 4.5
        p1 = (x1 - ux * a - uy * b, y1 - uy * a + ux * b)
        p2 = (x1 - ux * a + uy * b, y1 - uy * a - ux * b)
        self.add(f'<path d="M{x1},{y1} L{p1[0]:.1f},{p1[1]:.1f} L{p2[0]:.1f},{p2[1]:.1f} Z" fill="{color}"/>')

    def pill(self, x, y, s, color, size=12, fill=None, mono=False, anchor="start", h=None, pad=8, tcolor=None, weight=600):
        w = tw(s, size, mono) + pad * 2
        h = h or size + 10
        if anchor == "middle":
            x -= w / 2
        elif anchor == "end":
            x -= w
        self.rect(x, y, w, h, rx=h / 2, fill=fill or color + "22", stroke=color + "88", sw=1)
        self.text(x + w / 2, y + h / 2 + size * 0.36, s, size, tcolor or color, weight, "middle", mono)
        return w

    def label(self, x, y, s, color=MUTED, size=12, anchor="middle", mono=False, bg=BG):
        """Text on a line, with a background knock-out."""
        w = tw(s, size, mono) + 12
        bx = x - w / 2 if anchor == "middle" else (x if anchor == "start" else x - w)
        self.rect(bx, y - size - 2, w, size + 9, rx=5, fill=bg, stroke=bg, sw=0)
        self.text(bx + w / 2, y + 1, s, size, color, 500, "middle", mono)

    def render(self):
        return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">'
                + "".join(self.p) + "</svg>")


def rounded(pts, r):
    """Orthogonal polyline with rounded corners."""
    if len(pts) == 2:
        return f"M{pts[0][0]},{pts[0][1]} L{pts[1][0]},{pts[1][1]}"
    d = f"M{pts[0][0]},{pts[0][1]}"
    for i in range(1, len(pts) - 1):
        (x0, y0), (x1, y1), (x2, y2) = pts[i - 1], pts[i], pts[i + 1]
        l1 = max(abs(x1 - x0), abs(y1 - y0))
        l2 = max(abs(x2 - x1), abs(y2 - y1))
        rr = min(r, l1 / 2, l2 / 2)
        ax = x1 - (x1 - x0) / l1 * rr if l1 else x1
        ay = y1 - (y1 - y0) / l1 * rr if l1 else y1
        bx = x1 + (x2 - x1) / l2 * rr if l2 else x1
        by = y1 + (y2 - y1) / l2 * rr if l2 else y1
        d += f" L{ax:.1f},{ay:.1f} Q{x1},{y1} {bx:.1f},{by:.1f}"
    d += f" L{pts[-1][0]},{pts[-1][1]}"
    return d


S = SVG()
TAB = 34                     # height of the folder tab on the host card


def hw_bar(x, y, h):
    """Left accent bar = this card is a physical box / hardware part."""
    S.add(f'<rect x="{x+1}" y="{y+12}" width="4" height="{h-24}" rx="2" fill="{HWC}"/>')


def hw_tile(x, y, item, w=270, h=58):
    S.rect(x, y, w, h, rx=11, fill="#151d29", stroke="#2e3a4c", sw=1.2)
    hw_bar(x, y, h)
    S.icon(item["icon"], x + 12, y + 15, 28)
    S.text(x + 50, y + 17, item["label"], 10, HWC, 700, ls=1.3)
    vs = min(15.5, 15.5 * (w - 62) / max(measure(item["value"], 15.5, 700), 1))
    S.text(x + 50, y + 35, item["value"], round(vs, 2), TEXT, 700)
    ss = min(10.5, 10.5 * (w - 60) / max(tw(item["sub"], 10.5), 1))
    S.text(x + 50, y + 50, item["sub"], round(ss, 2), MUTED, 400)


def folder(x, y, w, h, tab_w, fill, stroke, sw):
    """Card body at (x, y, w, h) with a folder tab on its top-left edge, tab_w wide and TAB high."""
    t = y - TAB
    r, rt, sl = 14, 9, 16
    d = (f"M{x},{y+h-r} L{x},{t+rt} Q{x},{t} {x+rt},{t} L{x+tab_w-sl:.1f},{t} "
         f"C{x+tab_w-sl/3:.1f},{t} {x+tab_w-sl/3:.1f},{y} {x+tab_w+sl/2:.1f},{y} "
         f"L{x+w-r},{y} Q{x+w},{y} {x+w},{y+r} L{x+w},{y+h-r} Q{x+w},{y+h} {x+w-r},{y+h} "
         f"L{x+r},{y+h} Q{x},{y+h} {x},{y+h-r} Z")
    S.add(f'<path d="{d}" fill="{fill}" stroke="{stroke}" stroke-width="{sw}"/>')


def tab_text(x, y, name, type_):
    S.add(f'<text x="{x+16}" y="{y-10}" font-family="{SANS}" fill="{TEXT}">'
          f'<tspan font-size="20" font-weight="700">{esc(name)}</tspan>'
          f'<tspan dx="7" font-size="20" font-weight="700" fill-opacity="0.6">·</tspan>'
          f'<tspan dx="7" font-size="14" font-weight="600">{esc(type_)}</tspan></text>')


def tab_width(name, type_):
    return 16 + measure(name, 20, 700) + 7 + measure("·", 20, 700) + 7 + measure(type_, 14, 600) + 18


# ---------------------------------------------------------------- background + header
S.add(f'<defs><pattern id="dots" width="24" height="24" patternUnits="userSpaceOnUse">'
      f'<circle cx="1.5" cy="1.5" r="1.3" fill="{GRID}"/></pattern></defs>')
BG_IDX = len(S.p)
S.add("")   # background + dot grid: drawn at the end, once the canvas height is known
S.add("")

meta = INV["meta"]
title = meta["title"] if (UNREDACTED or not RED["domain"]) else meta["title_redacted"]
S.text(40, 86, title, 46, TEXT, 700, ls=-0.5)
S.text(40, 116, "one laptop behind CGNAT · two outbound doors · twenty containers, no forwarded port",
       15.5, MUTED, 400)
S.text(W - 40, 74, meta["watermark"], 21, REDDIT, 700, "end")
S.text(W - 40, 100, meta["date"], 16, MUTED, 400, "end")

# ================================================================ left column: networks + why
nx, ny, nw = 40, 150, 500
nh = 70 + len(INV["networks"]) * 60
S.rect(nx, ny, nw, nh, rx=14, fill=PANEL, stroke=BORDER, sw=1.4)
S.text(nx + 20, ny + 36, "Networks", 19, TEXT, 600)
for i, n in enumerate(INV["networks"]):
    y = ny + 56 + i * 60
    S.rect(nx + 14, y, nw - 28, 52, rx=9, fill=CHIP, stroke=n["color"] + "55", sw=1)
    S.add(f'<rect x="{nx+14}" y="{y}" width="6" height="52" rx="3" fill="{n["color"]}"/>')
    S.text(nx + 32, y + 22, n["tag"], 13, n["color"], 600, mono=True)
    S.text(nx + 128, y + 22, n["name"], 15.5, TEXT, 600)
    S.text(nx + nw - 28, y + 22, n["subnet"], 13.5, TEXT, 500, "end", mono=True)
    S.text(nx + 32, y + 42, n["note"], 12, MUTED, 400)

wy = ny + nh + 20
why = INV["why"]
S.rect(nx, wy, nw, 210, rx=14, fill=PANEL, stroke=BORDER, sw=1.4)
S.text(nx + 20, wy + 34, why["title"], 17, TEXT, 600)
yy = wy + 58
for pt in why["points"]:
    S.add(f'<circle cx="{nx+26}" cy="{yy-4}" r="3" fill="{C["wan"]}"/>')
    for chunk in wrap(pt, nw - 60, 12.5):
        S.text(nx + 40, yy, chunk, 12.5, MUTED, 400)
        yy += 18
    yy += 8



# ================================================================ internet, ISP router, the two doors
CX, CY = 900, 205
blobs = [(-95, 18, 42), (-45, -8, 56), (25, -18, 62), (90, 6, 48), (130, 26, 30), (-130, 30, 28)]
for dx, dy, r in blobs:
    S.add(f'<circle cx="{CX+dx}" cy="{CY+dy}" r="{r+2.5}" fill="#3a4a60"/>')
S.add(f'<rect x="{CX-150}" y="{CY+12}" width="300" height="46" rx="23" fill="#3a4a60"/>')
for dx, dy, r in blobs:
    S.add(f'<circle cx="{CX+dx}" cy="{CY+dy}" r="{r}" fill="#1a2433"/>')
S.add(f'<rect x="{CX-147.5}" y="{CY+14.5}" width="295" height="41" rx="20.5" fill="#1a2433"/>')
S.text(CX, CY + 14, "Internet", 26, TEXT, 600, "middle")
S.text(CX, CY + 40, "CGNAT · one public IPv4, shared", 13, MUTED, 400, "middle")

# ISP router: an ISP-managed box, nothing in it is yours to configure
isp = INV["isp"]
ix0, iy0, iw0, ih0 = CX - 170, 300, 340, 76
S.rect(ix0, iy0, iw0, ih0, rx=14, fill=PANEL, stroke=C["wan"] + "aa", sw=1.6)
S.text(ix0 + 16, iy0 + 26, f"{isp['name']} · {isp['type']}", 16, TEXT, 600)
S.text(ix0 + 16, iy0 + 46, isp["ip"], 12.5, C["wan"], 500, mono=True)
S.text(ix0 + 16, iy0 + 64, isp["os"], 11.5, MUTED, 400)
S.pill(ix0 + iw0 - 14, iy0 + 14, "CGNAT", C["wan"], 10, mono=True, anchor="end", pad=6, h=18)
S.icon("glyph-wifi", ix0 + iw0 - 44, iy0 + 40, 26)
S.line([(CX, CY + 58), (CX, iy0)], MUTED, 1.8, dash="2 5", r=0)

# tailnet: the private door's clients
tn = INV["tailnet"]
tx0, ty0, tw0 = 1250, 396, 590
th0 = 56 + len(tn["devices"]) * 52 + 12
S.rect(tx0, ty0, tw0, th0, rx=14, fill=PANEL, stroke=C["ts"] + "88", sw=1.5)
S.icon("tailscale", tx0 + 16, ty0 + 14, 26)
S.text(tx0 + 50, ty0 + 33, tn["name"], 18, TEXT, 600)
S.text(tx0 + 16, ty0 + 52, tn["note"].replace("{domain}", SHOWN_DOMAIN), 11.5, MUTED, 400)
for i, d in enumerate(tn["devices"]):
    y = ty0 + 62 + i * 52
    S.rect(tx0 + 14, y, tw0 - 28, 46, rx=9, fill=CHIP, stroke=BORDER, sw=1)
    S.icon(d["icon"], tx0 + 26, y + 8, 28)
    S.text(tx0 + 62, y + 20, d["name"], 14, TEXT, 600)
    S.text(tx0 + 62, y + 36, d["os"], 11.5, MUTED, 400)
    S.text(tx0 + 240, y + 20, "LAN " + d["lan"], 11, FAINT, 500, mono=True)
    S.text(tx0 + 240, y + 36, "ts  " + d["ts"], 11, C["ts"], 500, mono=True)

# the two doors, drawn as the paths they are (dotted = not a cable)
S.arrow([(CX - 80, iy0 + ih0), (CX - 80, 820), (420, 820), (420, 886)], MUTED, 1.8, dash="6 5")
S.label(440, 812, "Wi-Fi 5 · 802.11ac uplink", MUTED, 11.5, anchor="start")
S.arrow([(CX, iy0 + ih0), (CX, 886)], LINK, 2, dash="2 6")
S.label(CX + 16, 452, "Cloudflare Tunnel", LINK, 12, anchor="start")
S.label(CX + 16, 474, "outbound · TLS ends at the edge", MUTED, 11.5, anchor="start")
S.arrow([(tx0 + tw0 / 2 - 50, ty0 + th0), (tx0 + tw0 / 2 - 50, 700), (1260, 700), (1260, 886)],
        C["ts"], 2, dash="2 6")
S.label(1400, 692, "Tailscale · WireGuard · no size cap", C["ts"], 12)

# ================================================================ right column: cloud, public routes, storage
csx, csy, csw = 1860, 150, 500
for i, cs in enumerate(INV["internet"]["cloud_services"]):
    y = csy + i * 54
    S.rect(csx, y, csw, 44, rx=10, fill=PANEL2, stroke=BORDER)
    S.icon(cs["icon"], csx + 10, y + 9, 26)
    S.text(csx + 46, y + 20, cs["name"], 14, TEXT, 600)
    S.text(csx + 46, y + 36, cs["note"], 12, MUTED, 400)
S.line([(CX + 145, 168), (csx, 168)], FAINT, 1.6, dash="2 5", r=0)
S.line([(CX + 160, 214), (csx, 214)], FAINT, 1.6, dash="2 5", r=0)

prx, pry, prw = 1860, 342, 500
pr = INV["public_routes"]
prh = 84 + len(pr) * 44
S.rect(prx, pry, prw, prh, rx=14, fill=PANEL, stroke=BORDER, sw=1.4)
S.text(prx + 20, pry + 34, "Open to the internet", 19, TEXT, 600)
S.text(prx + 20, pry + 56, "no port forwards exist — four hostnames, all through the tunnel",
       12, MUTED, 400)
for i, r in enumerate(pr):
    y = pry + 72 + i * 44
    S.rect(prx + 14, y, prw - 28, 38, rx=8, fill=CHIP, stroke=BORDER, sw=1)
    S.text(prx + 26, y + 17, fqdn(r["host"]) + (" + www" if not r["host"] else ""), 12.5, LINK, 600, mono=True)
    S.text(prx + 26, y + 32, r["what"], 11.5, MUTED, 400)
    S.text(prx + prw - 96, y + 24, r["service"], 13, TEXT, 600, "end")
    S.pill(prx + prw - 24, y + 9, r["pill"], LINK, 10, mono=True, anchor="end", pad=6, h=20)

stx, sty, stw = 1860, 622, 500
st = INV["storage"]
sth = 84 + len(st["disks"]) * 66 + 34
S.rect(stx, sty, stw, sth, rx=14, fill=PANEL, stroke=BORDER, sw=1.4)
S.text(stx + 20, sty + 34, st["title"], 19, TEXT, 600)
for i, d in enumerate(st["disks"]):
    y = sty + 62 + i * 66
    S.icon("glyph-ssd", stx + 18, y + 4, 26)
    S.text(stx + 52, y + 18, d["name"], 14.5, TEXT, 600)
    S.text(stx + stw - 20, y + 18, d["used"], 11, MUTED, 400, "end")
    S.text(stx + 52, y + 34, d["device"], 11.5, MUTED, 400)
    S.text(stx + stw - 20, y + 34, f"{d['pct']} %", 11.5, C["lan"], 600, "end")
    bw_ = stw - 72
    S.rect(stx + 52, y + 44, bw_, 8, rx=4, fill="#253042", stroke="none", sw=0)
    S.rect(stx + 52, y + 44, max(bw_ * d["pct"] / 100, 8), 8, rx=4, fill=C["lan"], stroke="none", sw=0)
S.text(stx + 20, sty + sth - 14, st["footer"], 11.5, FAINT, 400)

# ================================================================ the host
hst = INV["host"]
hx, hy, hw_ = 40, 890, W - 80
tw_ = tab_width(hst["name"], hst["type"])
assert tw_ < 330, "the tab is so wide it collides with the Wi-Fi uplink"
# body height is set after the content below is placed (see "host body")
BODY_BOTTOM = 1630
folder(hx, hy, hw_, BODY_BOTTOM - hy, tw_, DECK, "#3a475a", 2.2)
tab_text(hx, hy, hst["name"], hst["type"])

mx = hx + 20
S.text(mx, hy + 30, hst["model"], 14, TEXT, 500)
S.text(mx + measure(hst["model"], 14, 500) + 12, hy + 30, hst["os"], 12, MUTED, 400)
ip_s = hst["ip"]
S.text(mx, hy + 50, ip_s, 12.5, C["lan"], 500, mono=True)
cw_ = 12.5 * 0.6
S.text(mx + len(ip_s) * cw_ + cw_ * 0.6, hy + 50, "·", 12.5, MUTED, 400, mono=True)
S.text(mx + len(ip_s) * cw_ + cw_ * 2.2, hy + 50, hst["name"], 12.5, C["lan"], 400, mono=True, op=0.85)
tx_ = mx + len(ip_s) * cw_ + cw_ * 2.2 + len(hst["name"]) * cw_ + 26
S.text(tx_, hy + 50, hst["tailnet"], 12.5, C["ts"], 500, mono=True)
S.pill(tx_ + len(hst["tailnet"]) * cw_ + 8, hy + 38, "tailnet", C["ts"], 10, mono=True, pad=6, h=18)

# ---- hardware column
HWX, HWW, TH_, TG_ = hx + 330, 270, 58, 6
S.text(HWX + 2, hy + 82, "HARDWARE", 11.5, HWC, 700, ls=1.4)
for i, item in enumerate(hst["hw"]):
    hw_tile(HWX, hy + 92 + i * (TH_ + TG_), item, w=HWW, h=TH_)
if hst.get("note"):
    ny_ = hy + 92 + len(hst["hw"]) * (TH_ + TG_) + 14
    for line in wrap(hst["note"], HWW - 8, 11.5):
        S.text(HWX + 2, ny_, line, 11.5, FAINT, 400)
        ny_ += 16

# ---- the doors band
DX0, DBY, DH = hx + 640, hy + 92, 150
DGAP = 40
DW = 360
DOOR_COL = {"tunnel": LINK, "caddy": "#8d9aab", "adguard": C["ts"]}
for i, d in enumerate(INV["doors"]):
    x = DX0 + i * (DW + DGAP)
    dc = DOOR_COL.get(d["key"], C["proxy"])
    S.rect(x, DBY, DW, DH, rx=12, fill=PANEL, stroke=dc + "77", sw=1.4)
    S.icon(d["icon"], x + 14, DBY + 16, 34)
    S.text(x + 56, DBY + 32, d["name"], 17, TEXT, 600)
    S.pill(x + DW - 12, DBY + 12, d["tag"], dc, 9.5, mono=True, anchor="end", pad=6, h=18)
    S.text(x + 16, DBY + 76, d["note"], 12, MUTED, 400)
    S.line([(x + 14, DBY + 92), (x + DW - 14, DBY + 92)], BORDER, 1, r=0)
    S.text(x + 16, DBY + 118, d["ports"], 11.5, TEXT, 500, mono=True, op=0.9)
DOORS_R = DX0 + len(INV["doors"]) * (DW + DGAP) - DGAP

# ---- what listens on the host
lx0 = DOORS_R + DGAP
lw_ = hx + hw_ - 20 - lx0
li = INV["listening"]
S.rect(lx0, DBY, lw_, DH, rx=12, fill=PANEL, stroke=BORDER, sw=1.4)
S.text(lx0 + 18, DBY + 28, li["title"], 16, TEXT, 600)
S.text(lx0 + lw_ - 16, DBY + 28, "reachable from: LAN + tailnet", 11, MUTED, 400, "end")
colw = (lw_ - 36) / 2
for i, p in enumerate(li["ports"]):
    col, row = i % 2, i // 2
    px, py = lx0 + 18 + col * colw, DBY + 46 + row * 24
    S.text(px, py, p["port"], 11.5, LINK, 600, mono=True)
    S.text(px + 62, py, p["what"], 11.5, MUTED, 400)
S.text(lx0 + 18, DBY + DH - 12, li["note"], 11, FAINT, 400)

# ---- the stacks
SZ0, SY0 = hx + 640, DBY + DH + 40
colw_ = (hx + hw_ - 20 - SZ0 - 2 * 24) / 3
CH_, CG_ = 104, 8


def chip(g, x, y, w):
    col = EXP[g["exposure"]]
    apps = g.get("apps") or []
    h = CH_ + (len(apps) * 22 + 6 if apps else 0)
    S.rect(x, y, w, h, rx=11, fill=CHIP, stroke=col + "66", sw=1.3)
    S.icon(g["icons"][0], x + 14, y + 12, 30)
    S.text(x + 56, y + 28, g["name"], 16.5, TEXT, 600)
    S.pill(x + w - 12, y + 12, EXP_TAG[g["exposure"]], col, 9.5, mono=True, anchor="end", pad=6, h=18)
    S.text(x + 56, y + 46, g["note"], 12, MUTED, 400)
    ep = sub(g["endpoint"])
    S.text(x + 16, y + 70, ep, 11.5, col if g["exposure"] != "host" else TEXT, 500, mono=True)
    S.text(x + 16, y + 88, g["meta"], 11, FAINT, 400)
    if apps:
        S.line([(x + 14, y + 98), (x + w - 14, y + 98)], BORDER, 1, r=0)
        for k, a in enumerate(apps):
            ay = y + 112 + k * 22
            S.icon(a["icon"], x + 16, ay - 12, 16)
            S.text(x + 38, ay, a["name"], 12.5, TEXT, 600, mono=True)
            S.text(x + 138, ay, a["note"], 11.5, MUTED, 400)
            S.text(x + w - 16, ay, a["port"], 11, FAINT, 500, "end", mono=True)
    return h


for gi, grp in enumerate(INV["stacks"]):
    gx = SZ0 + gi * (colw_ + 24)
    gy = SY0
    S.text(gx + 2, gy + 12, grp["name"], 11.5, HWC, 700, ls=1.4)
    S.text(gx + 12 + measure(grp["name"], 11.5, 700), gy + 12, f"· {grp['note']}", 11.5, FAINT, 400)
    gy += 22
    for g in grp["guests"]:
        gy += chip(g, gx, gy, colw_) + CG_
    assert gy <= BODY_BOTTOM - 90, f"stack column {gi} runs into the backups band ({gy})"

# ---- the backup chain along the bottom of the card
bk = INV["backups"]
bkx, bky, bkh = SZ0, BODY_BOTTOM - 70, 50
S.text(bkx - 2, bky - 10, bk["title"], 11.5, HWC, 700, ls=1.4)
bx_ = bkx
for i, stp in enumerate(bk["steps"]):
    w_ = max(measure(stp["name"], 13, 600), measure(stp["note"], 11, 400)) + 28
    S.rect(bx_, bky, w_, bkh, rx=9, fill=CHIP, stroke=BORDER, sw=1)
    S.text(bx_ + 14, bky + 21, stp["name"], 13, TEXT, 600)
    S.text(bx_ + 14, bky + 38, stp["note"], 11, MUTED, 400)
    bx_ += w_
    if i < len(bk["steps"]) - 1:
        S.arrow([(bx_ + 6, bky + bkh / 2), (bx_ + 30, bky + bkh / 2)], FAINT, 1.8, r=0)
        bx_ += 38
assert bx_ <= hx + hw_ - 20, "the backup chain runs past the card"

# ================================================================ bottom band: legend + the two doors
BBY = BODY_BOTTOM + 40
LG_ROWS = ["tunnel", "mesh", "net", "wifi", "pubname", "privname", "pill", "host"]
lgw = 500
lgh = 52 + len(LG_ROWS) * 26 + 8
S.rect(40, BBY, lgw, lgh, rx=14, fill=PANEL, stroke=BORDER, sw=1.4)
S.text(60, BBY + 36, "Legend", 19, TEXT, 600)
SX, TX = 62, 230
for i, k in enumerate(LG_ROWS):
    y = BBY + 62 + i * 26
    t = None
    if k == "tunnel":
        S.line([(SX, y), (SX + 70, y)], LINK, 2, dash="2 6", r=0); t = "public door · over the internet"
    elif k == "mesh":
        S.line([(SX, y), (SX + 70, y)], C["ts"], 2, dash="2 6", r=0); t = "private door · WireGuard mesh"
    elif k == "net":
        S.line([(SX, y), (SX + 70, y)], MUTED, 1.8, dash="2 5", r=0); t = "over the internet, not a cable"
    elif k == "wifi":
        S.line([(SX, y), (SX + 70, y)], MUTED, 1.8, dash="6 5", r=0); t = "Wi-Fi uplink (no cable at all)"
    elif k == "pubname":
        S.text(SX, y + 4.5, "cloud." + SHOWN_DOMAIN, 11.5, LINK, 500, mono=True)
        t = "public hostname · TLS at the edge"
    elif k == "privname":
        S.text(SX, y + 4.5, "uptime.local." + SHOWN_DOMAIN, 10.5, C["ts"], 500, mono=True)
        t = "private hostname · real TLS from Caddy"
    elif k == "pill":
        S.pill(SX, y - 9, "PUBLIC", LINK, 9.5, mono=True, pad=6, h=18)
        S.pill(SX + 78, y - 9, "HOST", HWC, 9.5, mono=True, pad=6, h=18)
        t = "who can reach it"
    elif k == "host":
        S.rect(SX, y - 11, 58, 22, rx=6, fill="#151d29", stroke="#2e3a4c", sw=1)
        S.add(f'<rect x="{SX+1}" y="{y-9}" width="4" height="18" rx="2" fill="{HWC}"/>')
        S.text(SX + 12, y + 4.5, "CPU", 10, HWC, 700, ls=1.2)
        t = "hardware tile · part of the host"
    if t:
        S.text(TX, y + 4.5, t, 12.5, MUTED, 400)

td = INV["two_doors"]
tdx, tdy = 560, BBY
tdw = W - 40 - tdx
tdh = lgh
S.rect(tdx, tdy, tdw, tdh, rx=14, fill=PANEL, stroke=BORDER, sw=1.4)
S.text(tdx + 20, tdy + 34, "Two doors, and why both", 19, TEXT, 600)
S.text(tdx + 20, tdy + 56, "the router cannot be reached from outside, so everything here starts on the inside",
       12, MUTED, 400)
halfw = (tdw - 56) / 2
for hi, (key, col) in enumerate((("public", LINK), ("private", C["ts"]))):
    h = td[key]
    hx0 = tdx + 20 + hi * (halfw + 16)
    S.rect(hx0, tdy + 68, halfw, tdh - 84, rx=11, fill=CHIP, stroke=col + "55", sw=1.2)
    S.text(hx0 + 14, tdy + 90, h["title"], 11.5, col, 700, ls=1.4)
    S.text(hx0 + 22 + measure(h["title"], 11.5, 700) + 1.4 * len(h["title"]), tdy + 90, h["sub"], 13, TEXT, 600)
    for ri, r in enumerate(h["rows"]):
        ry = tdy + 112 + ri * 28
        S.text(hx0 + 14, ry, r["k"], 10.5, FAINT, 600, mono=True)
        S.text(hx0 + 96, ry, r["v"], 11.5, MUTED, 400)

# ---------------------------------------------------------------- canvas height
H = int(BBY + lgh + 40)
S.rect(0, 0, W, H, rx=0, fill=BG, stroke=BG, sw=0)
S.rect(0, 0, W, H, rx=0, fill="url(#dots)", stroke="none", sw=0)
dots = S.p.pop()
S.p[BG_IDX], S.p[BG_IDX + 1] = S.p.pop(), dots

OUT.mkdir(exist_ok=True)
name = "homelab-unredacted" if UNREDACTED else "homelab"
svg_path = OUT / f"{name}.svg"
svg_path.write_text(S.render())
png_path = OUT / f"{name}.png"
subprocess.run(["rsvg-convert", "-z", "1.6", "-o", str(png_path), str(svg_path)], check=True)
print(svg_path, png_path)
