"""Tiny formula layout for the cairo toy text API (no TeX required).

Markup supported inside `formula.text`:
  a_{i}      subscript
  k^{T}      superscript
  √{d_{k}}   square root with overline
  \\b{x}      bold run
Everything else is drawn literally (Unicode: · Σ √ → α β … are fine with the
fonts shipped on most Linux systems, e.g. Liberation Sans / Noto Sans).
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Node:
    kind: str  # "text" | "sub" | "sup" | "sqrt" | "bold"
    text: str = ""
    children: list["Node"] = field(default_factory=list)


def parse(markup: str) -> list[Node]:
    nodes, i = _parse(markup, 0, stop_at_brace=False)
    return nodes


def _parse(s: str, i: int, stop_at_brace: bool) -> tuple[list[Node], int]:
    nodes: list[Node] = []
    buf = ""

    def flush() -> None:
        nonlocal buf
        if buf:
            nodes.append(Node("text", buf))
            buf = ""

    while i < len(s):
        ch = s[i]
        if stop_at_brace and ch == "}":
            flush()
            return nodes, i + 1
        if ch in "_^" and i + 1 < len(s) and s[i + 1] == "{":
            flush()
            children, i = _parse(s, i + 2, True)
            nodes.append(Node("sub" if ch == "_" else "sup", children=children))
            continue
        if ch == "√" and i + 1 < len(s) and s[i + 1] == "{":
            flush()
            children, i = _parse(s, i + 2, True)
            nodes.append(Node("sqrt", children=children))
            continue
        if s.startswith("\\b{", i):
            flush()
            children, i = _parse(s, i + 3, True)
            nodes.append(Node("bold", children=children))
            continue
        buf += ch
        i += 1
    flush()
    return nodes, i


SCRIPT_SCALE = 0.62
SUB_SHIFT = 0.28   # fraction of size, downwards
SUP_SHIFT = -0.45  # fraction of size, upwards


def layout(ctx, nodes: list[Node], size: float, font: str, bold: bool = False) -> tuple[float, float, float]:
    """Measure: returns (width, ascent, descent) in user units."""
    width = 0.0
    ascent = 0.0
    descent = 0.0
    for n in nodes:
        if n.kind == "text":
            _select(ctx, font, size, bold)
            ext = ctx.text_extents(n.text)
            width += ext.x_advance
            ascent = max(ascent, -ext.y_bearing if n.text.strip() else size * 0.72)
            descent = max(descent, ext.height + ext.y_bearing if n.text.strip() else size * 0.2)
        elif n.kind in ("sub", "sup"):
            w, a, d = layout(ctx, n.children, size * SCRIPT_SCALE, font, bold)
            shift = size * (SUB_SHIFT if n.kind == "sub" else SUP_SHIFT)
            width += w
            ascent = max(ascent, a - shift)
            descent = max(descent, d + shift)
        elif n.kind == "sqrt":
            w, a, d = layout(ctx, n.children, size, font, bold)
            width += w + size * 0.7
            ascent = max(ascent, a + size * 0.18)
            descent = max(descent, d)
        elif n.kind == "bold":
            w, a, d = layout(ctx, n.children, size, font, True)
            width += w
            ascent = max(ascent, a)
            descent = max(descent, d)
    return width, ascent, descent


def draw(ctx, nodes: list[Node], x: float, baseline: float, size: float, font: str, bold: bool = False) -> float:
    """Draw at (x, baseline); returns the advance."""
    cursor = x
    for n in nodes:
        if n.kind == "text":
            _select(ctx, font, size, bold)
            ctx.move_to(cursor, baseline)
            ctx.show_text(n.text)
            cursor += ctx.text_extents(n.text).x_advance
        elif n.kind in ("sub", "sup"):
            shift = size * (SUB_SHIFT if n.kind == "sub" else SUP_SHIFT)
            cursor += draw(ctx, n.children, cursor, baseline + shift, size * SCRIPT_SCALE, font, bold)
        elif n.kind == "sqrt":
            w, a, d = layout(ctx, n.children, size, font, bold)
            top = baseline - a - size * 0.12
            lw = max(1.5, size * 0.05)
            ctx.save()
            ctx.set_line_width(lw)
            ctx.move_to(cursor, baseline - a * 0.45)
            ctx.line_to(cursor + size * 0.22, baseline + d * 0.6)
            ctx.line_to(cursor + size * 0.45, top)
            ctx.line_to(cursor + size * 0.6 + w, top)
            ctx.stroke()
            ctx.restore()
            draw(ctx, n.children, cursor + size * 0.6, baseline, size, font, bold)
            cursor += w + size * 0.7
        elif n.kind == "bold":
            cursor += draw(ctx, n.children, cursor, baseline, size, font, True)
    return cursor - x


def _select(ctx, font: str, size: float, bold: bool) -> None:
    import cairo

    ctx.select_font_face(font, cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD if bold else cairo.FONT_WEIGHT_NORMAL)
    ctx.set_font_size(size)
