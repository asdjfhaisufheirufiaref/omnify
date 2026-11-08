"""Light/dark theming for Omnify.

The theme is a set of CSS custom properties (variables) applied to the
top-level window, plus a handful of small component styles. Every widget in
the app should reference the variables (``--omnify-bg``, ``--omnify-accent``,
…) rather than hard-coded colours, so switching theme or accent re-skins the
whole app instantly.

:func:`apply_css` installs the provider on the display; :func:`css_for`
returns the CSS text for a given (mode, accent) pair so it can be unit-tested
without a display.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Tuple

# Named palette entries. Each mode provides a full set of variables.
_MODES = {
    "dark": {
        "--omnify-bg": "#1e1f24",
        "--omnify-bg-alt": "#24262c",
        "--omnify-bg-raised": "#2a2d34",
        "--omnify-bg-hover": "#33363e",
        "--omnify-fg": "#e7e9ee",
        "--omnify-fg-dim": "#9aa0ab",
        "--omnify-border": "#3a3d46",
        "--omnify-danger": "#e0636e",
        "--omnify-success": "#6ecf8f",
        "--omnify-warning": "#e6b455",
        # Calendar day selection: a light "chip" with dark, readable text
        # (the user asked for a white highlight that is NOT white-on-white).
        "--omnify-sel-bg": "#f5f6f8",
        "--omnify-sel-fg": "#1f2329",
    },
    "light": {
        "--omnify-bg": "#f6f7f9",
        "--omnify-bg-alt": "#eef0f3",
        "--omnify-bg-raised": "#ffffff",
        "--omnify-bg-hover": "#e4e7ec",
        "--omnify-fg": "#1f2329",
        "--omnify-fg-dim": "#5b616c",
        "--omnify-border": "#d3d8de",
        "--omnify-danger": "#d64550",
        "--omnify-success": "#2e9e5b",
        "--omnify-warning": "#c07d10",
        "--omnify-sel-bg": "#ffffff",
        "--omnify-sel-fg": "#1f2329",
    },
}

ACCENTS = {
    "blue": "#4f8cff",
    "purple": "#8b6ef0",
    "green": "#3fb56f",
    "orange": "#f0883e",
    "pink": "#e0629a",
    "teal": "#2fb6c4",
}

#: Colour tokens exposed by the customization screen.  The second item is the
#: English label used by the UI; the actual translation is done by i18n.
COLOR_TOKENS: Tuple[Tuple[str, str], ...] = (
    ("bg", "Background"),
    ("bg-alt", "Sidebar / alternate background"),
    ("bg-raised", "Cards and raised surfaces"),
    ("bg-hover", "Hover background"),
    ("fg", "Text"),
    ("fg-dim", "Secondary text"),
    ("border", "Borders"),
    ("accent", "Accent"),
    ("danger", "Danger"),
    ("success", "Success"),
    ("warning", "Warning"),
    ("sel-bg", "Selected day background"),
    ("sel-fg", "Selected day text"),
)

_HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


def valid_hex(value: Any) -> Optional[str]:
    """Normalize a ``#rrggbb`` colour, returning ``None`` for invalid input."""
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if _HEX_RE.match(candidate):
        return candidate.lower()
    return None


def normalize_custom(custom: Optional[Mapping[str, Any]]) -> Dict[str, str]:
    """Validate a user colour map; unknown or malformed entries are dropped.

    Keys may be short aliases (``"accent"``) or CSS variables
    (``"--omnify-accent"``).  The result always uses CSS variable names.
    """
    result: Dict[str, str] = {}
    if not isinstance(custom, Mapping):
        return result
    for key, value in custom.items():
        var = _COLOR_ALIASES.get(str(key), str(key))
        if not var.startswith("--omnify-"):
            continue
        colour = valid_hex(value)
        if colour is not None:
            result[var] = colour
    return result


def normalize_options(options: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """Validate theme layout options used by :func:`css_for`."""
    if not isinstance(options, Mapping):
        return {}
    result: Dict[str, Any] = {}
    font = options.get("font_family")
    if isinstance(font, str):
        cleaned = "".join(ch for ch in font if ch.isalnum() or ch in " -_,").strip()
        if cleaned:
            result["font_family"] = cleaned[:120]
    try:
        radius = int(options.get("radius", 8))
    except (TypeError, ValueError):
        radius = 8
    result["radius"] = max(0, min(24, radius))
    image = options.get("background_image")
    if isinstance(image, str):
        path = Path(image).expanduser()
        try:
            if path.is_file():
                result["background_image"] = str(path.resolve())
        except OSError:
            pass
    return result


def _variables_for(
    mode: str,
    accent: str,
    custom: Optional[Mapping[str, Any]] = None,
) -> Dict[str, str]:
    """Resolve the full variable table (name → hex) for a mode + accent."""
    if mode not in _MODES:
        mode = "dark"
    if accent not in ACCENTS:
        accent = "blue"
    overrides = normalize_custom(custom)
    variables = dict(_MODES[mode])
    variables.update(overrides)
    accent_colour = variables.get("--omnify-accent", ACCENTS[accent])
    # If the user supplied a custom accent, derive its tints from that colour,
    # not from the named accent swatch.
    if "--omnify-accent" in overrides:
        accent_colour = overrides["--omnify-accent"]
    variables["--omnify-accent"] = accent_colour
    variables.setdefault("--omnify-accent-soft", _soften(accent_colour))
    if "--omnify-accent-soft" not in overrides:
        variables["--omnify-accent-soft"] = _soften(accent_colour)
    # A tint that stays *dark* in dark mode: used as a background behind
    # regular foreground text (checked pills, today's header). The "soft"
    # variant is almost white and would give white-on-white text.
    if "--omnify-accent-tint" not in overrides:
        variables["--omnify-accent-tint"] = _blend(
            accent_colour, variables.get("--omnify-bg", _MODES[mode]["--omnify-bg"]), 0.74
        )
    return variables


def theme_palette(
    mode: str = "dark",
    accent: str = "blue",
    custom: Optional[Mapping[str, Any]] = None,
) -> Dict[str, str]:
    """Return the resolved colour variables for *mode* + *accent*.

    This is a public helper for canvases (for example the calculator graph)
    that need the same colours as the CSS theme without hard-coding them.
    """
    return _variables_for(mode, accent, custom)


def css_for(
    mode: str = "dark",
    accent: str = "blue",
    custom: Optional[Mapping[str, Any]] = None,
    options: Optional[Mapping[str, Any]] = None,
) -> str:
    """Return the CSS text for a given mode, accent and customisations."""
    variables = _variables_for(mode, accent, custom)
    opts = normalize_options(options)
    variables["--omnify-font"] = opts.get(
        "font_family", '"Fira Sans", "Cantarell", "Adwaita Sans", sans-serif'
    )
    variables["--omnify-radius"] = f"{int(opts.get('radius', 8))}px"

    lines = [":root {"]
    for name, value in variables.items():
        lines.append(f"    {name}: {value};")
    lines.append("}")
    lines.append(_COMPONENT_CSS)
    lines.append(_decorative_css())
    lines.append(_calendar_css())
    lines.append(_custom_css(opts))
    return "\n".join(lines)


def _decorative_css() -> str:
    """Subtle accent circles behind the app, matching the preferred mock-up.

    The circles are part of the default theme rather than a required image:
    they use the active accent colour and therefore follow theme switches,
    custom accents and both light/dark modes.  A user-selected background
    image is applied later and replaces this layer.
    """
    return "\n".join(
        (
            "window, .omnify-dialog {",
            "    background-color: var(--omnify-bg);",
            "    background-image:",
            "        radial-gradient(circle at 86% 12%, alpha(var(--omnify-accent), 0.20), transparent 34%),",
            "        radial-gradient(circle at 4% 92%, alpha(var(--omnify-accent), 0.10), transparent 38%);",
            "}",
        )
    )


def _custom_css(options: Mapping[str, Any]) -> str:
    """Extra CSS for a background image and rounded-corner preference."""
    image = options.get("background_image")
    if not image:
        return ""
    uri = Path(str(image)).as_uri().replace('"', "%22")
    return "\n".join(
        (
            "/* User background image (Settings → Appearance). */",
            "window, .omnify-dialog {",
            f'    background-image: url("{uri}");',
            "    background-size: cover;",
            "    background-position: center;",
            "    background-repeat: no-repeat;",
            "}",
            ".omnify-sidebar {",
            "    background-color: alpha(var(--omnify-bg-alt), 0.88);",
            "}",
        )
    )


#: Calendar layer palette: stable colour names stored with categories; the
#: CSS classes and the Cairo drawing helpers both read this table.
CATEGORY_COLORS: Dict[str, str] = {
    "blue": "#4f8cff",
    "purple": "#a06cf0",
    "green": "#3fb56f",
    "orange": "#f0883e",
    "pink": "#e0629a",
    "teal": "#2fb6c4",
    "red": "#e0636e",
    "yellow": "#e6b455",
    "grey": "#9aa0ab",
}


def category_color(name: Optional[str]) -> str:
    """Hex colour for a palette name or a custom ``#rrggbb`` value."""
    if name in CATEGORY_COLORS:
        return CATEGORY_COLORS[name or "grey"]
    if isinstance(name, str):
        candidate = name.strip()
        if len(candidate) == 7 and candidate.startswith("#"):
            try:
                int(candidate[1:], 16)
            except ValueError:
                pass
            else:
                return candidate.lower()
    return CATEGORY_COLORS["grey"]


def _calendar_css() -> str:
    """Event-chip styles generated from :data:`CATEGORY_COLORS`."""
    lines = [
        "/* Calendar event chips and layer swatches */",
        ".omnify-event {",
        "    padding: 2px 5px;",
        "    min-height: 0;",
        "    border-radius: 6px;",
        "    border: 1px solid transparent;",
        "    box-shadow: none;",
        "}",
        ".omnify-event label { font-size: 0.85em; }",
        ".omnify-event:hover { opacity: 0.92; }",
        ".omnify-date-field {",
        "    padding: 6px 10px;",
        "    min-height: 0;",
        "    border: 1px solid var(--omnify-border);",
        "    border-radius: 6px;",
        "    background: var(--omnify-bg-alt);",
        "    color: var(--omnify-fg);",
        "}",
        ".omnify-date-field:hover { background: var(--omnify-bg-hover); }",
        ".omnify-today {",
        "    background: var(--omnify-accent-tint);",
        "    border: 1px solid var(--omnify-accent);",
        "    border-radius: 8px;",
        "    color: var(--omnify-fg);",
        "}",
        ".omnify-weekend { color: var(--omnify-fg-dim); }",
        ".omnify-swatch { min-width: 10px; min-height: 10px; border-radius: 50%; }",
        "/* Layer toggles: readable in both checked and unchecked states */",
        ".omnify-layer {",
        "    padding: 4px 10px;",
        "    min-height: 0;",
        "    background: var(--omnify-bg-alt);",
        "    border: 1px solid var(--omnify-border);",
        "    color: var(--omnify-fg);",
        "}",
        ".omnify-layer:hover { background: var(--omnify-bg-hover); }",
        ".omnify-layer:checked {",
        "    background: var(--omnify-accent-tint);",
        "    border-color: var(--omnify-accent);",
        "    color: var(--omnify-fg);",
        "}",
        ".omnify-layer:checked:hover {",
        "    background: var(--omnify-accent-tint);",
        "    opacity: 0.92;",
        "}",
    ]
    for name, value in CATEGORY_COLORS.items():
        r, g, b = parse_hex_color(value)
        tint = (
            f"rgba({round(r * 255)}, {round(g * 255)}, "
            f"{round(b * 255)}, 0.30)"
        )
        lines.append(
            f".omnify-event.omnify-cat-{name} {{ background: {tint}; "
            f"border-left: 3px solid {value}; color: var(--omnify-fg); }}"
        )
        lines.append(
            f".omnify-event.omnify-cat-{name}:hover {{ background: {tint}; "
            "opacity: 0.9; }"
        )
        lines.append(
            f".omnify-swatch-{name} {{ background: {value}; }}"
        )
    return "\n".join(lines)


#: Short colour names accepted by :func:`resolve_color` → CSS variable.
_COLOR_ALIASES = {
    "bg": "--omnify-bg",
    "bg-alt": "--omnify-bg-alt",
    "bg-raised": "--omnify-bg-raised",
    "bg-hover": "--omnify-bg-hover",
    "fg": "--omnify-fg",
    "fg-dim": "--omnify-fg-dim",
    "border": "--omnify-border",
    "danger": "--omnify-danger",
    "success": "--omnify-success",
    "warning": "--omnify-warning",
    "accent": "--omnify-accent",
    "accent-soft": "--omnify-accent-soft",
    "accent-tint": "--omnify-accent-tint",
}


def parse_hex_color(hex_color: str) -> Tuple[float, float, float]:
    """Parse ``"#rrggbb"`` into an ``(r, g, b)`` tuple with values in ``0..1``.

    Pure and testable (no GTK). Malformed input degrades to black
    ``(0.0, 0.0, 0.0)`` instead of raising.
    """
    h = hex_color.lstrip("#")
    if len(h) != 6:
        return (0.0, 0.0, 0.0)
    try:
        r, g, b = (int(h[i : i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return (0.0, 0.0, 0.0)
    return (r / 255.0, g / 255.0, b / 255.0)


def resolve_color(
    mode: str,
    accent: str,
    name: str,
    custom: Optional[Mapping[str, Any]] = None,
) -> Tuple[float, float, float]:
    """Resolve a theme colour to an ``(r, g, b)`` tuple with values in ``0..1``.

    Pure and testable: no GTK involved. *name* may be a CSS variable
    (``"--omnify-accent"``) or a short alias (``"accent"``, ``"bg"``,
    ``"fg-dim"``, ``"danger"``, …). Unknown names or unparseable values
    degrade to black ``(0.0, 0.0, 0.0)`` instead of raising, so drawing
    code can call this unguarded.
    """
    var = _COLOR_ALIASES.get(name, name)
    if not var.startswith("--omnify-"):
        return (0.0, 0.0, 0.0)
    hex_value = _variables_for(mode, accent, custom).get(var)
    if hex_value is None:
        return (0.0, 0.0, 0.0)
    return parse_hex_color(hex_value)


def _hex_components(value: str):
    """``"#rrggbb"`` → ``[r, g, b]`` (or ``None`` when malformed)."""
    value = (value or "").lstrip("#")
    if len(value) != 6:
        return None
    try:
        return [int(value[index : index + 2], 16) for index in (0, 2, 4)]
    except ValueError:
        return None


def _blend(hex_color: str, target: str, amount: float) -> str:
    """Blend *hex_color* toward *target* by *amount* (0 = source, 1 = target)."""
    source = _hex_components(hex_color)
    destination = _hex_components(target)
    if source is None or destination is None:
        return hex_color
    amount = max(0.0, min(1.0, amount))
    channels = (
        round(src + (dst - src) * amount)
        for src, dst in zip(source, destination)
    )
    return "#" + "".join(f"{channel:02x}" for channel in channels)


def _soften(hex_color: str, amount: float = 0.82) -> str:
    """Blend *hex_color* with white to produce a soft background tint."""
    return _blend(hex_color, "#ffffff", amount)


_COMPONENT_CSS = """
/* Base: typography and window background (Windows 11 look) */
window {
    font-family: var(--omnify-font, "Fira Sans"), "Cantarell", "Adwaita Sans", sans-serif;
    /* The fallback keeps secondary windows opaque even if variables are
       not resolved in their style context yet. */
    background-color: var(--omnify-bg, #1e1f24);
}
.omnify-dialog {
    background-color: var(--omnify-bg, #1e1f24);
}
/* Scrolled pages should reveal the themed window and its accent circles,
   not the toolkit's opaque black viewport. */
scrolledwindow, scrolledwindow > viewport {
    background: transparent;
    background-color: transparent;
}
.omnify-scroll,
.omnify-scroll > viewport,
.omnify-scroll list,
.omnify-laps {
    background: transparent;
    background-color: transparent;
    background-image: none;
}

/* Buttons: flat, rounded, soft hover */
button {
    border-radius: var(--omnify-radius, 8px);
    background-color: var(--omnify-bg-raised);
    background-image: none;
    border: 1px solid var(--omnify-border);
    box-shadow: none;
    padding: 6px 14px;
    min-height: 0;
    color: var(--omnify-fg);
}
button:hover {
    background-color: var(--omnify-bg-hover);
}
button:active {
    background-color: var(--omnify-bg-hover);
    border-color: var(--omnify-accent);
}
button:checked {
    background-color: var(--omnify-accent-tint);
    border-color: var(--omnify-accent);
    color: var(--omnify-fg);
}
button:disabled { opacity: 0.55; }

.notes-icon-button {
    background: transparent;
    background-image:none;
    border: none;
    box-shadow: none;
    border-radius: 6px;
    min-width: 28px;
    min-height: 28px;
    padding: 0;
    color: var(--omnify-fg-dim);
    font-size: 1.15em;
    font-weight:600;}

.notes-icon-button:hover {
    background: var(--omnify-bg-hover);
    color: var(--omnify-fg);}

.notes-icon-button:active,
.notes-icon-button:checked {
    background: var(--omnify-accent-soft);
    color: var(--omnify-accent);}

/* Картинка, яку Notes показує всередині тексту нотатки. */
.notes-inline-image {
    border: 1px solid var(--omnify-border);
    border-radius: 8px;
    background: var(--omnify-bg-alt);
    padding: 4px;
}

/* Sidebar */
.omnify-sidebar {
    background: var(--omnify-bg-alt);
    border-right: 1px solid var(--omnify-border);
}
.omnify-nav-button {
    border-radius: 8px;
    margin: 1px 4px;
    padding: 8px 12px;
    background: transparent;
    background-image: none;
    border: none;
    box-shadow: none;
    color: var(--omnify-fg-dim);
    font-weight: 500;
    min-height: 0;
}
.omnify-nav-button:hover {
    background: var(--omnify-bg-hover);
    color: var(--omnify-fg);
}
.omnify-nav-button:checked {
    background: var(--omnify-accent-soft);
    color: var(--omnify-accent);
    font-weight: 600;
}

/* Cards / panels */
.omnify-card {
    background: var(--omnify-bg-raised);
    border: 1px solid var(--omnify-border);
    border-radius: var(--omnify-radius, 10px);
    padding: 14px 16px;
    margin: 6px;
}
.omnify-card-title {
    font-size: 1.05em;
    font-weight: 600;
    color: var(--omnify-fg);
    margin-bottom: 4px;
}

/* A row being dragged to a new position (timer cards). */
.omnify-dragging {
    border-color: var(--omnify-accent);
    box-shadow: 0 4px 14px rgba(0, 0, 0, 0.35);
}

/* The drag handle at the start of a reorderable card. */
.omnify-drag-handle {
    padding: 2px 6px;
    margin: 0;
    min-height: 0;
    opacity: 0.55;
}
.omnify-drag-handle:hover { opacity: 1; }

/* Numeric displays (mono for tables and big readouts) */
.omnify-mono {
    font-family: "JetBrains Mono", "FantasqueSansM Nerd Font Mono",
                 "Adwaita Mono", monospace;
    font-variant-numeric: tabular-nums;
}

/* Headline values on the Performance cards (CPU% / RAM% / GPU%) */
.omnify-big-value {
    font-size: 1.55em;
    font-weight: 650;
}

/* Accent button (primary action) */
.omnify-accent {
    background: var(--omnify-accent);
    background-image: none;
    color: #ffffff;
    border: none;
    border-radius: 8px;
    padding: 8px 16px;
    font-weight: 600;
    box-shadow: none;
}
.omnify-accent:hover { opacity: 0.9; }

.omnify-dim { color: var(--omnify-fg-dim); }

/* Browser status card */
.browser-status {
    padding: 7px 10px;
    border-radius: 8px;
    background: var(--omnify-bg-alt);
    border: 1px solid var(--omnify-border);
    font-weight: 600;
}
.browser-status-ok { color: var(--omnify-success); }
.browser-status-warn { color: var(--omnify-warning); }
.browser-status-error { color: var(--omnify-danger); }
.browser-score {
    color: var(--omnify-success);
    font-weight: 650;
    padding: 2px 8px;
}

/* Alarm clock */
.alarm-clock {
    font-size: 2.7em;
    font-weight: 700;
    letter-spacing: 0.02em;
}
.alarm-time {
    font-size: 1.8em;
    font-weight: 650;
}
.alarm-name { font-weight: 600; }
.alarm-next { color: var(--omnify-fg-dim); font-size: 0.92em; }
.alarm-ring {
    background: var(--omnify-accent-tint);
    border: 1px solid var(--omnify-accent);
    border-radius: 10px;
    padding: 12px 14px;
}

/* Home dashboard */
.dashboard-hero {
    background: linear-gradient(135deg, var(--omnify-bg-raised), var(--omnify-accent-tint));
    border: 1px solid var(--omnify-border);
    border-radius: 14px;
    padding: 22px 24px;
    margin: 0 0 2px;
}
.dashboard-greeting {
    font-size: 1.9em;
    font-weight: 700;
    color: var(--omnify-fg);
}
.dashboard-intro {
    color: var(--omnify-fg-dim);
    font-size: 1.02em;
    margin-top: 4px;
}
.dashboard-clock {
    font-size: 2.5em;
    font-weight: 700;
    letter-spacing: 0.03em;
}
.dashboard-value { font-weight: 550; }
.dashboard-quick { font-weight: 550; }
button.dashboard-tile {
    background: var(--omnify-bg-raised);
    border: 1px solid var(--omnify-border);
    border-radius: 12px;
    padding: 0;
}
button.dashboard-tile:hover {
    background: var(--omnify-bg-hover);
    border-color: var(--omnify-accent);
}
.dashboard-tile-icon { font-size: 1.65em; }
.dashboard-tile-title { font-size: 1.08em; font-weight: 650; }

/* Calculator: display, keypad, history and graph */
.calc-root { padding: 2px; }
.calc-display {
    background: var(--omnify-bg-alt);
    border: 1px solid var(--omnify-border);
    border-radius: 12px;
    padding: 14px 18px;
}
.calc-expression {
    font-size: 0.92em;
    color: var(--omnify-fg-dim);
}
.calc-result {
    font-size: 2.15em;
    font-weight: 650;
    color: var(--omnify-fg);
}
.calc-secondary {
    font-size: 1.0em;
    color: var(--omnify-fg-dim);
}
.calc-error { color: var(--omnify-danger); }
.calc-key {
    padding: 7px 8px;
    min-height: 36px;
    font-size: 0.95em;
}
.calc-key-fn {
    color: var(--omnify-fg-dim);
    font-size: 0.88em;
}
.calc-key-accent {
    background: var(--omnify-accent-tint);
    border-color: var(--omnify-accent);
}
.calc-key-equals {
    background: var(--omnify-accent);
    color: #ffffff;
    border: none;
    font-weight: 700;
    font-size: 1.1em;
}
.calc-key-equals:hover { opacity: 0.9; }
.calc-key-danger { color: var(--omnify-danger); }
.calc-chip {
    padding: 3px 9px;
    min-height: 0;
    font-size: 0.84em;
    color: var(--omnify-fg-dim);
}
.calc-chip:hover { color: var(--omnify-fg); }
.calc-input {
    background: var(--omnify-bg-raised);
    border: 1px solid var(--omnify-border);
    border-radius: 9px;
    padding: 8px 10px;
    color: var(--omnify-fg);
}
.calc-input:focus-within { border-color: var(--omnify-accent); }
.calc-input textview,
.calc-input text {
    background: transparent;
    color: var(--omnify-fg);
    caret-color: var(--omnify-accent);
}
.calc-input text selection {
    background: var(--omnify-accent);
    color: #ffffff;
}
.calc-steps { font-size: 0.9em; }
.calc-history-row {
    padding: 7px 9px;
    border-radius: 8px;
    background: transparent;
    border: 1px solid transparent;
}
.calc-history-row:hover {
    background: var(--omnify-bg-hover);
    border-color: var(--omnify-border);
}
.calc-graph {
    background: var(--omnify-bg-alt);
    border-radius: 10px;
}
.calc-graph-row {
    background: var(--omnify-bg-raised);
    border: 1px solid var(--omnify-border);
    border-radius: 8px;
    padding: 5px 7px;
}
.calc-invalid { border-color: var(--omnify-danger); }
.calc-param {
    background: var(--omnify-bg-alt);
    border: 1px solid var(--omnify-border);
    border-radius: 8px;
    padding: 6px 8px;
}
.calc-graph-scroll { background: transparent; }

/* Destructive button */
.omnify-danger {
    background: var(--omnify-danger);
    background-image: none;
    color: #ffffff;
    border: none;
    border-radius: 8px;
    box-shadow: none;
}
.omnify-danger:hover { opacity: 0.9; }

/* Sidebar group titles (stand-ins for listbox section headers) */
.omnify-group-title {
    font-size: 0.8em;
    font-weight: 700;
    color: var(--omnify-fg-dim);
}

/* Calendar (Gtk.Calendar). The system theme (Breeze) renders the
   selected day as a white highlight with white text — unreadable.
   Pin the calendar to the app palette: the selection is a light chip
   with dark, bold, readable text in both modes. */
calendar {
    background-color: var(--omnify-bg-alt);
    color: var(--omnify-fg);
}
calendar header {
    background-color: transparent;
}
calendar grid {
    background-color: transparent;
}
calendar label.day-number {
    background-color: transparent;
    color: var(--omnify-fg);
}
calendar label.day-number:selected {
    background-color: var(--omnify-sel-bg);
    color: var(--omnify-sel-fg);
    font-weight: 700;
    border-radius: 6px;
}
calendar label.day-number.other-month {
    color: var(--omnify-fg-dim);
}
calendar label.day-number.today {
    font-weight: 700;
}

/* Inputs, rows and progress bars */
entry {
    border-radius: 8px;
    background-color: var(--omnify-bg-raised);
    background-image: none;
    border: 1px solid var(--omnify-border);
    box-shadow: none;
}
entry:focus {
    border-color: var(--omnify-accent);
}
list row:selected {
    background-color: var(--omnify-accent-soft);
    color: var(--omnify-fg);
}
progressbar trough {
    background-color: var(--omnify-bg-hover);
    border: none;
    border-radius: 4px;
    min-height: 8px;
}
progressbar progress {
    background-color: var(--omnify-accent);
    border-radius: 4px;
}
"""


class Theme:
    """Applies and re-applies the theme to the running GTK application."""

    def __init__(
        self,
        mode: str = "dark",
        accent: str = "blue",
        custom: Optional[Mapping[str, Any]] = None,
        options: Optional[Mapping[str, Any]] = None,
    ) -> None:
        self.mode = mode if mode in _MODES else "dark"
        self.accent = accent if accent in ACCENTS else "blue"
        self.custom: Dict[str, str] = normalize_custom(custom)
        self.options: Dict[str, Any] = normalize_options(options)
        self._provider = None

    def css(self) -> str:
        return css_for(self.mode, self.accent, self.custom, self.options)

    def set_mode(self, mode: str) -> None:
        if mode in _MODES:
            self.mode = mode
        self.refresh()

    def set_accent(self, accent: str) -> None:
        if accent in ACCENTS:
            self.accent = accent
        self.refresh()

    def set_custom(
        self,
        custom: Optional[Mapping[str, Any]],
        options: Optional[Mapping[str, Any]] = None,
    ) -> None:
        """Replace the custom palette/options and refresh the display."""
        self.custom = normalize_custom(custom)
        if options is not None:
            self.options = normalize_options(options)
        self.refresh()

    def set_options(self, options: Optional[Mapping[str, Any]]) -> None:
        self.options = normalize_options(options)
        self.refresh()

    def resolve(self, name: str) -> Tuple[float, float, float]:
        """Resolve one palette colour for canvas code."""
        return resolve_color(self.mode, self.accent, name, self.custom)

    def set(self, mode: str, accent: str) -> None:
        self.mode = mode if mode in _MODES else self.mode
        self.accent = accent if accent in ACCENTS else self.accent
        self.refresh()

    def apply(self, display) -> None:
        """Install the current CSS on *display* and keep it for refreshes."""
        from gi.repository import Gtk

        if self._provider is None:
            self._provider = Gtk.CssProvider()
            Gtk.StyleContext.add_provider_for_display(
                display, self._provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )
        self._push()

    def refresh(self) -> None:
        if self._provider is not None:
            self._push()

    def _push(self) -> None:
        if self._provider is not None:
            self._provider.load_from_string(self.css())
