"""SVG preview of a sweep, for checking the layout before burning it."""

from __future__ import annotations

from .layout import Layout

MARGIN = 2.0


def render(layout: Layout, show_travel: bool = True) -> str:
    min_x, min_y, max_x, max_y = layout.burn_bounds
    min_x -= MARGIN
    min_y -= MARGIN
    max_x += MARGIN
    max_y += MARGIN
    width = max_x - min_x
    height = max_y - min_y

    parts: list[str] = [
        '<?xml version="1.0" encoding="UTF-8" standalone="no"?>',
        "<!-- laser-sweep preview -->",
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width:.3f}mm"'
        f' height="{height:.3f}mm" viewBox="0 0 {width:.3f} {height:.3f}"'
        ' version="1.1">',
        f'<rect x="0" y="0" width="{width:.3f}" height="{height:.3f}" fill="#ffffff"/>',
        # Machine Y points up, SVG Y points down.
        f'<g transform="translate({-min_x:.3f},{max_y:.3f}) scale(1,-1)"'
        ' fill="none" stroke-linecap="round" stroke-linejoin="round">',
    ]

    if show_travel:
        parts.append(
            '<g stroke="#b0b0b0" stroke-width="0.05" stroke-dasharray="0.4,0.4">'
        )
        parts.extend(f'<path d="{d}"/>' for d in _travel_paths(layout))
        parts.append("</g>")

    for cell in layout.travel_order():
        parts.append(
            f'<g stroke="#c02020" stroke-width="{cell.spot:.4f}" opacity="0.85">'
        )
        parts.extend(f'<path d="{_path_data(path)}"/>' for path in cell.paths)
        parts.append("</g>")

    if layout.label_paths:
        spot = min(layout.config.spots)
        parts.append(f'<g stroke="#204090" stroke-width="{spot:.4f}">')
        parts.extend(f'<path d="{_path_data(path)}"/>' for path in layout.label_paths)
        parts.append("</g>")

    parts.append("</g>")
    parts.append("</svg>")
    return "\n".join(parts) + "\n"


def _path_data(path) -> str:
    head = f"M {path[0][0]:.4f} {path[0][1]:.4f}"
    rest = " ".join(f"L {x:.4f} {y:.4f}" for x, y in path[1:])
    return f"{head} {rest}".strip()


def _travel_paths(layout: Layout) -> list[str]:
    segments: list[str] = []
    position = None
    ordered = [path for cell in layout.travel_order() for path in cell.paths]
    ordered += layout.label_paths
    for path in ordered:
        if position is not None:
            segments.append(
                f"M {position[0]:.4f} {position[1]:.4f}"
                f" L {path[0][0]:.4f} {path[0][1]:.4f}"
            )
        position = path[-1]
    return segments


def render_iso(plan, show_travel: bool = True, show_copper: bool = True) -> str:
    """Isolation preview: the copper under the loops that free it."""
    min_x, min_y, max_x, max_y = plan.bounds
    min_x -= MARGIN
    min_y -= MARGIN
    max_x += MARGIN
    max_y += MARGIN
    width = max_x - min_x
    height = max_y - min_y
    spot = plan.config.spot

    parts: list[str] = [
        '<?xml version="1.0" encoding="UTF-8" standalone="no"?>',
        "<!-- laser-iso preview -->",
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width:.3f}mm"'
        f' height="{height:.3f}mm" viewBox="0 0 {width:.3f} {height:.3f}"'
        ' version="1.1">',
        f'<rect x="0" y="0" width="{width:.3f}" height="{height:.3f}" fill="#ffffff"/>',
        f'<g transform="translate({-min_x:.3f},{max_y:.3f}) scale(1,-1)"'
        ' fill="none" stroke-linecap="round" stroke-linejoin="round">',
    ]

    if show_copper and plan.copper:
        # One path with the even-odd rule draws the holes as holes.
        data = " ".join(_closed(contour) for contour in plan.copper)
        parts.append(
            f'<path d="{data}" fill="#c8a165" fill-rule="evenodd" stroke="none"/>'
        )

    if show_travel:
        parts.append(
            f'<g stroke="#b0b0b0" stroke-width="{spot / 4.0:.4f}"'
            f' stroke-dasharray="{spot * 4:.3f},{spot * 4:.3f}">'
        )
        parts.extend(f'<path d="{d}"/>' for d in _iso_travel(plan))
        parts.append("</g>")

    parts.append(f'<g stroke="#c02020" stroke-width="{spot:.4f}" opacity="0.8">')
    parts.extend(f'<path d="{_path_data(loop.points)}"/>' for loop in plan.loops)
    parts.append("</g>")

    if plan.outline:
        parts.append(f'<g stroke="#204090" stroke-width="{spot:.4f}" opacity="0.8">')
        parts.extend(f'<path d="{_path_data(path)}"/>' for path in plan.outline)
        parts.append("</g>")

    if plan.drills:
        parts.append(f'<g stroke="#207040" stroke-width="{spot:.4f}" opacity="0.9">')
        parts.extend(f'<path d="{_path_data(path)}"/>' for path in plan.drills)
        parts.append("</g>")

    for bridge in plan.bridges:
        x, y = bridge.at
        radius = max(0.5, spot * 4)
        parts.append(
            f'<circle cx="{x:.3f}" cy="{y:.3f}" r="{radius:.3f}"'
            f' stroke="#e07000" stroke-width="{spot / 2.0:.4f}" fill="none"/>'
        )

    parts.append("</g>")
    parts.append("</svg>")
    return "\n".join(parts) + "\n"


def _closed(contour) -> str:
    head = f"M {contour[0][0]:.4f} {contour[0][1]:.4f}"
    rest = " ".join(f"L {x:.4f} {y:.4f}" for x, y in contour[1:])
    return f"{head} {rest} Z"


def _iso_travel(plan) -> list[str]:
    segments: list[str] = []
    position = None
    ordered = [loop.points for loop in plan.loops] + plan.outline + plan.drills
    for path in ordered:
        if position is not None:
            segments.append(
                f"M {position[0]:.4f} {position[1]:.4f}"
                f" L {path[0][0]:.4f} {path[0][1]:.4f}"
            )
        position = path[-1]
    return segments
