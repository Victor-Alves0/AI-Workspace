"""Renderiza um gráfico (o dict da tool `chart.render.plot`) em PNG, no servidor.

Por que existe: nos CANAIS (WhatsApp/Telegram/Discord) não há front para desenhar o
gráfico — a IA chamava a tool, o artefato era descartado e ela ainda dizia "segue o
gráfico abaixo". Aqui ele vira uma imagem de verdade, entregue como mídia.

Decisões de desenho (o alvo é a tela de um CELULAR, imagem estática):
- Paleta categórica validada (CVD-safe; pior par adjacente ΔE 24.2). Três das cores
  ficam abaixo de 3:1 contra a superfície clara, o que EXIGE "relief": a identidade
  nunca é só a cor — sempre há legenda (2+ séries) e rótulo direto nas marcas.
- Sem hover/tooltip (é um PNG): tudo que importa está impresso.
- Um eixo só (jamais eixo duplo), grade recessiva, marcas finas, fundo claro (o
  WhatsApp mostra a imagem sobre qualquer tema).
"""

from __future__ import annotations

import io
import logging
import math
from typing import Any

from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

# --- tokens (paleta de referência, modo claro, validada) ----------------------
SURFACE = (252, 252, 251)      # #fcfcfb
INK = (11, 11, 11)             # #0b0b0b  texto primário
INK_SOFT = (82, 81, 78)        # #52514e  texto secundário
GRID = (231, 230, 227)         # grade recessiva
AXIS = (196, 195, 191)

# ordem FIXA (a ordenação é o mecanismo de segurança p/ daltonismo, não estética)
SERIES_COLORS = [
    (42, 120, 214),   # 1 azul     #2a78d6
    (27, 175, 122),   # 2 aqua     #1baf7a
    (237, 161, 0),    # 3 amarelo  #eda100
    (0, 131, 0),      # 4 verde    #008300
    (74, 58, 167),    # 5 violeta  #4a3aa7
    (227, 73, 72),    # 6 vermelho #e34948
    (232, 123, 164),  # 7 magenta  #e87ba4
    (235, 104, 52),   # 8 laranja  #eb6834
]

W, H = 1000, 640               # legível quando o app encolhe a imagem no balão
PAD = 56
_FONT_DIR = "/usr/share/fonts/truetype/dejavu"


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    try:
        return ImageFont.truetype(f"{_FONT_DIR}/{name}", size)
    except OSError:  # ambiente sem a fonte: não derruba o turno
        return ImageFont.load_default()


def _color(i: int) -> tuple[int, int, int]:
    # a 9ª série NÃO ganha uma cor inventada — reusa a ordem (e o rótulo direto
    # é que carrega a identidade). Gráficos com 9+ séries são ilegíveis de qualquer forma.
    return SERIES_COLORS[i % len(SERIES_COLORS)]


def _nice_ticks(lo: float, hi: float, n: int = 5) -> list[float]:
    """Escala 'redonda' (1/2/5 × 10^k) — evita eixos com 3,7142857."""
    if hi <= lo:
        hi = lo + 1
    raw = (hi - lo) / max(1, n)
    mag = 10 ** math.floor(math.log10(raw)) if raw > 0 else 1
    for m in (1, 2, 2.5, 5, 10):
        step = m * mag
        if raw <= step:
            break
    start = math.floor(lo / step) * step
    out, v = [], start
    while v <= hi + step * 0.5:
        out.append(round(v, 6))
        v += step
    return out


def _fmt(v: float) -> str:
    a = abs(v)
    if a >= 1_000_000:
        return f"{v / 1_000_000:.1f}M".replace(".0M", "M")
    if a >= 1_000:
        return f"{v / 1_000:.1f}k".replace(".0k", "k")
    if a == int(a):
        return str(int(v))
    return f"{v:.1f}"


def _text(d: ImageDraw.ImageDraw, xy, s, font, fill, anchor="la") -> None:
    d.text(xy, s, font=font, fill=fill, anchor=anchor)


def _legend(d: ImageDraw.ImageDraw, series: list[dict], y: int) -> None:
    """Legenda: obrigatória com 2+ séries (identidade nunca é só a cor)."""
    f = _font(20)
    x = PAD
    for i, s in enumerate(series):
        name = (s.get("name") or f"série {i + 1}")[:22]
        d.rounded_rectangle([x, y + 4, x + 14, y + 18], radius=3, fill=_color(i))
        _text(d, (x + 22, y + 2), name, f, INK_SOFT)
        x += 22 + int(d.textlength(name, font=f)) + 28


def _plot_frame(
    d: ImageDraw.ImageDraw, title: str, has_legend: bool,
    *, y_gutter: int = 62, right_gutter: int = 0,
) -> tuple[int, int, int, int]:
    """Desenha título/legenda e devolve a área do plot (x0, y0, x1, y1).

    `y_gutter` = espaço p/ os valores do eixo Y (a pizza não tem eixo → 0).
    `right_gutter` = espaço RESERVADO p/ o rótulo direto no fim da linha; sem ele o
    texto vazaria pela borda direita da imagem."""
    top = PAD
    if title:
        _text(d, (PAD, 24), title[:60], _font(28, bold=True), INK)
        top = 78
    if has_legend:
        top += 8
    return (PAD + y_gutter, top + (30 if has_legend else 0),
            W - PAD - right_gutter, H - PAD - 26)


def _axes(
    d: ImageDraw.ImageDraw, box, lo: float, hi: float, labels: list[str],
    *, centered: bool,
) -> list[float]:
    """Grade horizontal recessiva + eixo Y (valores) + eixo X (categorias).

    `centered`: a BARRA ocupa um slot (rótulo no centro do slot); a LINHA/ÁREA plota
    o ponto sobre o tick (rótulo embaixo do ponto). Usar a régua errada desalinha o
    rótulo do dado — o leitor associa o valor à categoria vizinha."""
    x0, y0, x1, y1 = box
    ticks = _nice_ticks(lo, hi)
    lo_t, hi_t = ticks[0], ticks[-1]
    f = _font(18)
    for t in ticks:
        y = y1 - (t - lo_t) / (hi_t - lo_t or 1) * (y1 - y0)
        d.line([x0, y, x1, y], fill=GRID, width=1)
        _text(d, (x0 - 12, y), _fmt(t), f, INK_SOFT, anchor="rm")
    d.line([x0, y0, x0, y1], fill=AXIS, width=1)
    d.line([x0, y1, x1, y1], fill=AXIS, width=1)
    # rótulos do eixo X: só os que cabem (colisão de texto é pior que omissão)
    if labels:
        n = len(labels)
        span = x1 - x0
        step_px = span / n if centered else span / max(n - 1, 1)
        every = max(1, math.ceil(90 / max(step_px, 1)))
        for i, lab in enumerate(labels):
            if i % every:
                continue
            cx = x0 + (step_px * (i + 0.5) if centered else step_px * i)
            _text(d, (cx, y1 + 10), str(lab)[:12], f, INK_SOFT, anchor="ma")
    return [lo_t, hi_t]


def _bounds(series: list[dict]) -> tuple[float, float]:
    vals = [v for s in series for v in s["data"]]
    lo, hi = min(vals + [0]), max(vals + [0])
    if lo == hi:
        hi = lo + 1
    return lo, hi


def _render_bars(d: ImageDraw.ImageDraw, box, series, labels) -> None:
    x0, y0, x1, y1 = box
    lo, hi = _bounds(series)
    lo_t, hi_t = _axes(d, box, lo, hi, labels, centered=True)
    n = max(len(s["data"]) for s in series)
    k = len(series)
    slot = (x1 - x0) / max(n, 1)
    bw = max(6, (slot * 0.72) / k - 2)          # 2px de respiro entre barras vizinhas
    base = y1 - (0 - lo_t) / (hi_t - lo_t) * (y1 - y0)
    f = _font(17, bold=True)
    show_values = n * k <= 12                    # rótulo direto quando cabe
    for si, s in enumerate(series):
        for i, v in enumerate(s["data"]):
            cx = x0 + slot * (i + 0.5) - (bw * k + 2 * (k - 1)) / 2 + si * (bw + 2)
            y = y1 - (v - lo_t) / (hi_t - lo_t) * (y1 - y0)
            top, bot = (y, base) if v >= 0 else (base, y)
            if bot - top < 1:
                bot = top + 1
            d.rounded_rectangle([cx, top, cx + bw, bot], radius=4, fill=_color(si))
            if show_values:
                _text(d, (cx + bw / 2, top - 6), _fmt(v), f, INK, anchor="mb")


def _render_line(d: ImageDraw.ImageDraw, box, series, labels, area: bool) -> None:
    x0, y0, x1, y1 = box
    lo, hi = _bounds(series)
    lo_t, hi_t = _axes(d, box, lo, hi, labels, centered=False)
    n = max(len(s["data"]) for s in series)
    step = (x1 - x0) / max(n - 1, 1) if n > 1 else 0
    for si, s in enumerate(series):
        col = _color(si)
        pts = [
            (x0 + (step * i if n > 1 else (x1 - x0) / 2),
             y1 - (v - lo_t) / (hi_t - lo_t) * (y1 - y0))
            for i, v in enumerate(s["data"])
        ]
        if area and len(pts) > 1:
            # preenchimento translúcido: séries sobrepostas continuam legíveis
            layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            ImageDraw.Draw(layer).polygon(
                pts + [(pts[-1][0], y1), (pts[0][0], y1)], fill=col + (56,)
            )
            d._image.alpha_composite(layer)  # noqa: SLF001 - composição na mesma tela
        if len(pts) > 1:
            d.line(pts, fill=col, width=3, joint="curve")
        for px, py in pts:
            d.ellipse([px - 5, py - 5, px + 5, py + 5], fill=col,
                      outline=SURFACE, width=2)  # anel de 2px: marcas sobrepostas se separam
    # rótulo direto no fim da linha (a cor sozinha não pode carregar a identidade).
    # Cabe na calha reservada à direita pelo _plot_frame — nada de vazar pela borda.
    if len(series) <= 4:
        f = _font(17, bold=True)
        for si, s in enumerate(series):
            if not s["data"]:
                continue
            v = s["data"][-1]
            px = x0 + (step * (len(s["data"]) - 1) if n > 1 else (x1 - x0) / 2)
            py = y1 - (v - lo_t) / (hi_t - lo_t) * (y1 - y0)
            _text(d, (px + 12, py), _fmt(v), f, _color(si), anchor="lm")


def _render_pie(d: ImageDraw.ImageDraw, box, series, labels) -> None:
    x0, y0, x1, y1 = box
    data = series[0]["data"]
    total = sum(abs(v) for v in data) or 1
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    # o raio precisa deixar espaço p/ os rótulos DE FORA (eles não podem invadir as
    # fatias — o texto sobre a cor some, e é justamente o rótulo que garante a
    # identidade das cores de baixo contraste)
    r = min((x1 - x0) / 2 - 190, (y1 - y0) / 2 - 34)
    f = _font(19, bold=True)
    ang = -90.0
    for i, v in enumerate(data):
        sweep = abs(v) / total * 360
        # 2px de respiro entre fatias (o mesmo espaçador das barras empilhadas)
        d.pieslice([cx - r, cy - r, cx + r, cy + r], ang + 0.6, ang + sweep - 0.6,
                   fill=_color(i), outline=SURFACE, width=2)
        if sweep >= 10:  # fatia fina: o rótulo colidiria — só a cor + a fatia
            mid = math.radians(ang + sweep / 2)
            dx, dy = math.cos(mid), math.sin(mid)
            lx, ly = cx + dx * (r + 16), cy + dy * (r + 16)
            lab = str(labels[i])[:14] if i < len(labels) else ""
            txt = f"{lab} {abs(v) / total * 100:.0f}%".strip()
            # ancora pelo LADO: à direita do círculo o texto cresce p/ a direita, à
            # esquerda cresce p/ a esquerda — assim nunca cobre a fatia nem vaza
            anchor = "lm" if dx >= 0 else "rm"
            lx = min(max(lx, PAD), W - PAD)
            _text(d, (lx, ly), txt, f, INK, anchor=anchor)
        ang += sweep


def render_chart(chart: dict[str, Any]) -> bytes | None:
    """Recebe o dict da tool (kind=chart) e devolve o PNG. None se não der p/ desenhar."""
    try:
        series = [s for s in (chart.get("series") or []) if s.get("data")]
        if not series:
            return None
        labels = [str(x) for x in (chart.get("labels") or [])]
        title = str(chart.get("title") or "")
        kind = str(chart.get("type") or "line").lower()

        img = Image.new("RGBA", (W, H), SURFACE + (255,))
        d = ImageDraw.Draw(img)
        d._image = img  # noqa: SLF001 - usado pelo alpha_composite da área

        multi = len(series) > 1
        if kind == "pie":
            # sem eixos: a pizza usa a largura inteira (a calha do Y a descentralizava).
            # O rótulo de cada fatia já traz categoria + %, então não há legenda.
            box = _plot_frame(d, title, has_legend=False, y_gutter=0)
            _render_pie(d, box, series, labels)
        else:
            # reserva a calha da direita p/ o rótulo do fim da linha (senão vaza)
            right = 62 if (kind in ("line", "area") and len(series) <= 4) else 0
            box = _plot_frame(d, title, has_legend=multi, right_gutter=right)
            if multi:
                _legend(d, series, y=(78 if title else PAD) - 4)
            if kind == "bar":
                _render_bars(d, box, series, labels)
            else:
                _render_line(d, box, series, labels, area=(kind == "area"))

        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="PNG", optimize=True)
        return buf.getvalue()
    except Exception as exc:  # noqa: BLE001 - um gráfico ruim não derruba a resposta
        logger.warning("falha ao renderizar gráfico: %s", exc)
        return None
