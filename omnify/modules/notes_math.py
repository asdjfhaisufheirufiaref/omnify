"""A dependency-free LaTeX subset renderer for Notes.

Omnify deliberately avoids WebKit, Node and KaTeX: math is converted into
ordinary Unicode text with Pango or HTML markup.  The same conversion feeds
the GTK preview, the PDF exporter and the standalone HTML exporter, so a
formula looks consistent everywhere and works completely offline.

Supported syntax covers everyday notes:

* inline ``$x^2$`` / ``\\(x^2\\)`` and display ``$$...$$`` / ``\\[...\\]``;
* Greek letters, arrows, set operators and other common symbols;
* fractions, roots, superscripts, subscripts and accents;
* ``\\text{...}``, ``\\mathbf{...}``, ``\\mathbb{...}`` and friends;
* simple ``matrix``, ``pmatrix``, ``bmatrix``, ``vmatrix`` and ``cases``.

It is not a full TeX engine.  Unknown commands are shown literally instead of
being dropped, which keeps the source understandable and debuggable.
"""

from __future__ import annotations

import html
import re
from typing import Dict, Optional, Tuple

# ---------------------------------------------------------------------------
# Symbol tables
# ---------------------------------------------------------------------------
_GREEK: Dict[str, str] = {
    "alpha": "α",
    "beta": "β",
    "gamma": "γ",
    "delta": "δ",
    "epsilon": "ε",
    "varepsilon": "ε",
    "zeta": "ζ",
    "eta": "η",
    "theta": "θ",
    "vartheta": "ϑ",
    "iota": "ι",
    "kappa": "κ",
    "lambda": "λ",
    "mu": "μ",
    "nu": "ν",
    "xi": "ξ",
    "omicron": "ο",
    "pi": "π",
    "varpi": "ϖ",
    "rho": "ρ",
    "varrho": "ϱ",
    "sigma": "σ",
    "varsigma": "ς",
    "tau": "τ",
    "upsilon": "υ",
    "phi": "φ",
    "varphi": "ϕ",
    "chi": "χ",
    "psi": "ψ",
    "omega": "ω",
    "Gamma": "Γ",
    "Delta": "Δ",
    "Theta": "Θ",
    "Lambda": "Λ",
    "Xi": "Ξ",
    "Pi": "Π",
    "Sigma": "Σ",
    "Upsilon": "Υ",
    "Phi": "Φ",
    "Psi": "Ψ",
    "Omega": "Ω",
}

_OPERATORS: Dict[str, str] = {
    "pm": "±",
    "mp": "∓",
    "times": "×",
    "div": "÷",
    "cdot": "·",
    "ast": "∗",
    "star": "⋆",
    "circ": "∘",
    "bullet": "•",
    "oplus": "⊕",
    "ominus": "⊖",
    "otimes": "⊗",
    "odot": "⊙",
    "le": "≤",
    "leq": "≤",
    "ge": "≥",
    "geq": "≥",
    "ne": "≠",
    "neq": "≠",
    "approx": "≈",
    "equiv": "≡",
    "sim": "∼",
    "simeq": "≃",
    "cong": "≅",
    "propto": "∝",
    "ll": "≪",
    "gg": "≫",
    "infty": "∞",
    "partial": "∂",
    "nabla": "∇",
    "forall": "∀",
    "exists": "∃",
    "nexists": "∄",
    "emptyset": "∅",
    "varnothing": "∅",
    "in": "∈",
    "notin": "∉",
    "ni": "∋",
    "subset": "⊂",
    "subseteq": "⊆",
    "supset": "⊃",
    "supseteq": "⊇",
    "cup": "∪",
    "cap": "∩",
    "setminus": "∖",
    "wedge": "∧",
    "land": "∧",
    "vee": "∨",
    "lor": "∨",
    "neg": "¬",
    "lnot": "¬",
    "to": "→",
    "rightarrow": "→",
    "leftarrow": "←",
    "gets": "←",
    "leftrightarrow": "↔",
    "Rightarrow": "⇒",
    "Leftarrow": "⇐",
    "Leftrightarrow": "⇔",
    "implies": "⇒",
    "iff": "⇔",
    "mapsto": "↦",
    "uparrow": "↑",
    "downarrow": "↓",
    "sum": "∑",
    "prod": "∏",
    "coprod": "∐",
    "int": "∫",
    "iint": "∬",
    "iiint": "∭",
    "oint": "∮",
    "angle": "∠",
    "perp": "⊥",
    "parallel": "∥",
    "mid": "∣",
    "vdash": "⊢",
    "dashv": "⊣",
    "models": "⊨",
    "top": "⊤",
    "bot": "⊥",
    "therefore": "∴",
    "because": "∵",
    "ldots": "…",
    "dots": "…",
    "cdots": "⋯",
    "vdots": "⋮",
    "ddots": "⋱",
    "prime": "′",
    "degree": "°",
    "ell": "ℓ",
    "hbar": "ℏ",
    "Re": "ℜ",
    "Im": "ℑ",
    "aleph": "ℵ",
    "wp": "℘",
    "langle": "⟨",
    "rangle": "⟩",
    "lfloor": "⌊",
    "rfloor": "⌋",
    "lceil": "⌈",
    "rceil": "⌉",
    "vert": "|",
    "lvert": "|",
    "rvert": "|",
    "Vert": "‖",
    "lVert": "‖",
    "rVert": "‖",
    "backslash": "∖",
}

SYMBOLS: Dict[str, str] = {**_GREEK, **_OPERATORS}

#: Commands that are typographic spacing rather than symbols.
_SPACING = {
    ",": "\u2009",  # thin space
    ":": "\u2005",  # medium space
    ";": "\u2004",  # thick space
    "!": "",  # negative thin space (ignored)
    " ": " ",
    "~": "\u00a0",
    "quad": "\u2003",
    "qquad": "\u2003\u2003",
}

#: Combining accents for ``\\hat{x}`` and friends.
_ACCENTS = {
    "hat": "\u0302",
    "widehat": "\u0302",
    "tilde": "\u0303",
    "widetilde": "\u0303",
    "vec": "\u20d7",
    "dot": "\u0307",
    "ddot": "\u0308",
    "acute": "\u0301",
    "grave": "\u0300",
    "breve": "\u0306",
    "check": "\u030c",
}

_OVERLINE = {"bar", "overline"}

#: Functions typeset upright with a thin space before their argument.
_FUNCTIONS = {
    "sin",
    "cos",
    "tan",
    "cot",
    "sec",
    "csc",
    "arcsin",
    "arccos",
    "arctan",
    "sinh",
    "cosh",
    "tanh",
    "log",
    "ln",
    "lg",
    "exp",
    "lim",
    "max",
    "min",
    "sup",
    "inf",
    "det",
    "gcd",
    "deg",
    "arg",
    "dim",
    "ker",
    "hom",
}

_DELIMITERS = {
    "langle": "⟨",
    "rangle": "⟩",
    "lfloor": "⌊",
    "rfloor": "⌋",
    "lceil": "⌈",
    "rceil": "⌉",
    "vert": "|",
    "lvert": "|",
    "rvert": "|",
    "Vert": "‖",
    "lVert": "‖",
    "rVert": "‖",
    "backslash": "∖",
    "{": "{",
    "}": "}",
    "|": "‖",
}

_DOUBLE_STRUCK_UPPER = "𝔸𝔹ℂ𝔻𝔼𝔽𝔾ℍ𝕀𝕁𝕂𝕃𝕄ℕ𝕆ℙℚℝ𝕊𝕋𝕌𝕍𝕎𝕏𝕐ℤ"
_DOUBLE_STRUCK_LOWER = "𝕒𝕓𝕔𝕕𝕖𝕗𝕘𝕙𝕚𝕛𝕜𝕝𝕞𝕟𝕠𝕡𝕢𝕣𝕤𝕥𝕦𝕧𝕨𝕩𝕪𝕫"
_DOUBLE_STRUCK_DIGITS = "𝟘𝟙𝟚𝟛𝟜𝟝𝟞𝟟𝟠𝟡"
_SCRIPT_UPPER = "𝒜ℬ𝒞𝒟ℰℱ𝒢ℋℐ𝒥𝒦ℒℳ𝒩𝒪𝒫𝒬ℛ𝒮𝒯𝒰𝒱𝒲𝒳𝒴𝒵"
_SCRIPT_LOWER = "𝒶𝒷𝒸𝒹ℯ𝒻ℊ𝒽𝒾𝒿𝓀𝓁𝓂𝓃ℴ𝓅𝓆𝓇𝓈𝓉𝓊𝓋𝓌𝓍𝓎𝓏"

#: Inline ``$...$``.  Currency such as ``$5 and $10`` does not match: the
#: closing dollar may not be followed by a digit and the content may not end
#: with whitespace.  Escaped dollars are ignored by the lookbehind.
_DOLLAR_MATH = re.compile(
    r"(?<![\\$])\$(?![\s$])([^$\n]+?)(?<![\s\\])\$(?![0-9$])"
)
#: Inline ``\\(...\\)`` (people who paste TeX often use it).
_PAREN_MATH = re.compile(r"(?<!\\)\\\((.+?)\\\)", re.DOTALL)


def _escape(text: str) -> str:
    """Escape text for Pango/HTML markup without touching quotes."""
    return html.escape(text, quote=False)


def _escape_text_command(text: str) -> str:
    """Unescape TeX's escaped punctuation inside ``\\text{...}``."""
    replacements = {
        r"\{": "{",
        r"\}": "}",
        r"\%": "%",
        r"\$": "$",
        r"\&": "&",
        r"\#": "#",
        r"\_": "_",
        r"\\": " ",
    }
    for source, target in replacements.items():
        text = text.replace(source, target)
    return text


def _map_chars(
    text: str,
    upper: str,
    lower: str,
    digits: str = "",
) -> str:
    """Map ASCII letters/digits through Unicode alphabet strings."""
    result = []
    for char in text:
        if "A" <= char <= "Z":
            result.append(upper[ord(char) - ord("A")])
        elif "a" <= char <= "z":
            result.append(lower[ord(char) - ord("a")])
        elif digits and "0" <= char <= "9":
            result.append(digits[ord(char) - ord("0")])
        else:
            result.append(char)
    return "".join(result)


def _needs_parens(raw: str) -> bool:
    """Wrap compound fraction parts in parentheses, not single symbols."""
    text = raw.strip()
    if len(text) <= 1:
        return False
    if re.fullmatch(r"(?:\\[A-Za-z]+|[A-Za-z0-9.]+)", text):
        return False
    return True


def _unescape_source(source: str) -> str:
    return (
        source.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


# ---------------------------------------------------------------------------
# Output markup
# ---------------------------------------------------------------------------
class _PangoMarkup:
    """Render parsed math as Pango markup for ``Gtk.Label`` and PangoCairo."""

    newline = "\n"

    @staticmethod
    def text(value: str) -> str:
        return _escape(value)

    @staticmethod
    def sup(inner: str) -> str:
        return f"<sup>{inner}</sup>"

    @staticmethod
    def sub(inner: str) -> str:
        return f"<sub>{inner}</sub>"

    @staticmethod
    def frac(
        numerator: str,
        denominator: str,
        numerator_parens: bool,
        denominator_parens: bool,
    ) -> str:
        if numerator_parens:
            numerator = f"({numerator})"
        if denominator_parens:
            denominator = f"({denominator})"
        return f"<sup>{numerator}</sup>⁄<sub>{denominator}</sub>"

    @staticmethod
    def sqrt(index: Optional[str], index_raw: str, body: str) -> str:
        overline = f'<span overline="single">{body}</span>'
        if index is None:
            return f"√{overline}"
        if index_raw in {"3", "4"}:
            return f"{'∛' if index_raw == '3' else '∜'}{overline}"
        return f"<sup>{index}</sup>√{overline}"

    @staticmethod
    def overline(inner: str) -> str:
        return f'<span overline="single">{inner}</span>'

    @staticmethod
    def underline(inner: str) -> str:
        return f"<u>{inner}</u>"

    @staticmethod
    def bold(inner: str) -> str:
        return f"<b>{inner}</b>"

    @staticmethod
    def italic(inner: str) -> str:
        return f"<i>{inner}</i>"

    @staticmethod
    def upright(inner: str) -> str:
        return f'<span style="normal">{inner}</span>'

    @staticmethod
    def accent(inner: str, mark: str) -> str:
        return f"{inner}{mark}"


class _HtmlMarkup:
    """Render parsed math as a small HTML fragment."""

    newline = "<br>"

    @staticmethod
    def text(value: str) -> str:
        return _escape(value)

    @staticmethod
    def sup(inner: str) -> str:
        return f"<sup>{inner}</sup>"

    @staticmethod
    def sub(inner: str) -> str:
        return f"<sub>{inner}</sub>"

    @staticmethod
    def frac(
        numerator: str,
        denominator: str,
        numerator_parens: bool,
        denominator_parens: bool,
    ) -> str:
        if numerator_parens:
            numerator = f"({numerator})"
        if denominator_parens:
            denominator = f"({denominator})"
        return f"<sup>{numerator}</sup>⁄<sub>{denominator}</sub>"

    @staticmethod
    def sqrt(index: Optional[str], index_raw: str, body: str) -> str:
        overline = (
            '<span style="text-decoration: overline">' + body + "</span>"
        )
        if index is None:
            return f"√{overline}"
        if index_raw in {"3", "4"}:
            return f"{'∛' if index_raw == '3' else '∜'}{overline}"
        return f"<sup>{index}</sup>√{overline}"

    @staticmethod
    def overline(inner: str) -> str:
        return f'<span style="text-decoration: overline">{inner}</span>'

    @staticmethod
    def underline(inner: str) -> str:
        return f"<u>{inner}</u>"

    @staticmethod
    def bold(inner: str) -> str:
        return f"<b>{inner}</b>"

    @staticmethod
    def italic(inner: str) -> str:
        return f"<i>{inner}</i>"

    @staticmethod
    def upright(inner: str) -> str:
        return f'<span style="font-style: normal">{inner}</span>'

    @staticmethod
    def accent(inner: str, mark: str) -> str:
        return f"{inner}{mark}"


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------
class _Parser:
    """A small recursive-descent parser for the supported TeX subset."""

    def __init__(self, source: str, markup) -> None:
        self.source = source or ""
        self.markup = markup
        self.index = 0

    # -- low-level helpers ------------------------------------------------
    def _consume(self, char: str) -> bool:
        if self.index < len(self.source) and self.source[self.index] == char:
            self.index += 1
            return True
        return False

    def _skip_spaces(self) -> None:
        while self.index < len(self.source) and self.source[self.index] in " \t\n\r":
            self.index += 1

    def parse(self, stop: Optional[str] = None) -> str:
        parts: list[str] = []
        while self.index < len(self.source):
            char = self.source[self.index]
            if stop is not None and char == stop:
                break
            if char == "\\":
                parts.append(self._command())
            elif char == "^":
                self.index += 1
                parts.append(self.markup.sup(self._argument()[1]))
            elif char == "_":
                self.index += 1
                parts.append(self.markup.sub(self._argument()[1]))
            elif char == "{":
                self.index += 1
                parts.append(self.parse(stop="}"))
                self._consume("}")
            elif char == "}":
                self.index += 1
                parts.append(self.markup.text("}"))
            elif char in " \t\n\r":
                self.index += 1
                parts.append(" ")
            elif char == "&":
                self.index += 1
                parts.append(" ")
            else:
                parts.append(self.markup.text(char))
                self.index += 1
        return "".join(parts)

    def _argument(self) -> Tuple[str, str]:
        """Return ``(raw_source, markup)`` for one macro argument."""
        self._skip_spaces()
        if self.index >= len(self.source):
            return "", ""
        start = self.index
        if self.source[self.index] == "{":
            self.index += 1
            inner_start = self.index
            markup = self.parse(stop="}")
            raw = self.source[inner_start:self.index]
            self._consume("}")
            return raw, markup
        if self.source[self.index] == "\\":
            markup = self._command()
            return self.source[start:self.index], markup
        char = self.source[self.index]
        self.index += 1
        return char, self.markup.text(char)

    def _raw_argument(self) -> str:
        """Take a macro argument verbatim (used by ``\\text`` and ``\\begin``)."""
        self._skip_spaces()
        if self.index >= len(self.source):
            return ""
        if self.source[self.index] != "{":
            char = self.source[self.index]
            self.index += 1
            return char
        self.index += 1
        start = self.index
        depth = 1
        while self.index < len(self.source):
            char = self.source[self.index]
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    break
            self.index += 1
        raw = self.source[start:self.index]
        self._consume("}")
        return raw

    def _optional_raw(self) -> Optional[str]:
        self._skip_spaces()
        if self.index >= len(self.source) or self.source[self.index] != "[":
            return None
        end = self.source.find("]", self.index + 1)
        if end == -1:
            return None
        value = self.source[self.index + 1:end]
        self.index = end + 1
        return value

    def _parse_fragment(self, source: str) -> str:
        return _Parser(source, self.markup).parse()

    def _delimiter(self) -> str:
        self._skip_spaces()
        if self.index >= len(self.source):
            return ""
        if self.source[self.index] == "\\":
            self.index += 1
            if self.index < len(self.source) and not self.source[self.index].isalpha():
                char = self.source[self.index]
                self.index += 1
                return self.markup.text(_DELIMITERS.get(char, char))
            start = self.index
            while self.index < len(self.source) and self.source[self.index].isalpha():
                self.index += 1
            name = self.source[start:self.index]
            return self.markup.text(_DELIMITERS.get(name, ""))
        char = self.source[self.index]
        self.index += 1
        if char == ".":
            return ""
        return self.markup.text(char)

    # -- commands ---------------------------------------------------------
    def _command(self) -> str:
        self.index += 1  # the backslash
        if self.index >= len(self.source):
            return self.markup.text("\\")
        char = self.source[self.index]
        if char == "\\":
            self.index += 1
            return self.markup.newline
        if not char.isalpha():
            self.index += 1
            if char == " ":
                return " "
            if char in _SPACING:
                return _SPACING[char]
            if char in "{}%$&#_":
                return self.markup.text(char)
            if char == "|":
                return self.markup.text("‖")
            return self.markup.text(char)
        start = self.index
        while self.index < len(self.source) and self.source[self.index].isalpha():
            self.index += 1
        return self._named(self.source[start:self.index])

    def _named(self, name: str) -> str:
        markup = self.markup
        if name in _FUNCTIONS:
            return markup.upright(markup.text(name)) + "\u2009"
        if name in _SPACING:
            return _SPACING[name]
        if name in SYMBOLS:
            return markup.text(SYMBOLS[name])
        if name in {"text", "textrm", "textnormal", "mbox"}:
            raw = _escape_text_command(self._raw_argument())
            return markup.text(" ".join(raw.split()))
        if name in {"mathrm", "operatorname"}:
            return markup.upright(self._argument()[1])
        if name == "mathit":
            return markup.italic(self._argument()[1])
        if name in {"mathbf", "bm", "boldsymbol"}:
            return markup.bold(self._argument()[1])
        if name in {"mathbb", "Bbb"}:
            return markup.text(
                _map_chars(
                    self._raw_argument(),
                    _DOUBLE_STRUCK_UPPER,
                    _DOUBLE_STRUCK_LOWER,
                    _DOUBLE_STRUCK_DIGITS,
                )
            )
        if name in {"mathcal", "mathscr"}:
            return markup.text(
                _map_chars(
                    self._raw_argument(),
                    _SCRIPT_UPPER,
                    _SCRIPT_LOWER,
                )
            )
        if name in {"frac", "dfrac", "tfrac", "cfrac"}:
            numerator_raw, numerator = self._argument()
            denominator_raw, denominator = self._argument()
            return markup.frac(
                numerator,
                denominator,
                _needs_parens(numerator_raw),
                _needs_parens(denominator_raw),
            )
        if name == "binom":
            upper_raw, upper = self._argument()
            lower_raw, lower = self._argument()
            return (
                markup.text("C(")
                + upper
                + markup.text(", ")
                + lower
                + markup.text(")")
            )
        if name == "sqrt":
            index_raw = self._optional_raw()
            _body_raw, body = self._argument()
            if index_raw is None:
                return markup.sqrt(None, "", body)
            return markup.sqrt(
                self._parse_fragment(index_raw.strip()),
                index_raw.strip(),
                body,
            )
        if name in _OVERLINE:
            return markup.overline(self._argument()[1])
        if name == "underline":
            return markup.underline(self._argument()[1])
        if name in _ACCENTS:
            return markup.accent(self._argument()[1], _ACCENTS[name])
        if name in {"left", "right", "middle"}:
            return self._delimiter()
        if name == "not":
            _raw, body = self._argument()
            replacements = {
                "=": "≠",
                "∈": "∉",
                "∋": "∌",
                "⊂": "⊄",
                "⊃": "⊅",
                "⊆": "⊈",
                "⊇": "⊉",
                "<": "≮",
                ">": "≯",
            }
            return markup.text(replacements.get(body, "\u0338" + body))
        if name in {"pmod", "bmod", "mod"}:
            return markup.text(" mod ")
        if name == "begin":
            environment = self._raw_argument().strip()
            terminator = "\\end{" + environment + "}"
            end = self.source.find(terminator, self.index)
            if end == -1:
                content = self.source[self.index:]
                self.index = len(self.source)
            else:
                content = self.source[self.index:end]
                self.index = end + len(terminator)
            return self._environment(environment, content)
        if name == "end":
            self._raw_argument()
            return ""
        if name in {
            "displaystyle",
            "textstyle",
            "scriptstyle",
            "scriptscriptstyle",
            "limits",
            "nolimits",
            "relax",
        }:
            return ""
        # Unknown commands stay visible as literal text instead of vanishing.
        if self.index < len(self.source) and self.source[self.index] == "{":
            return markup.text(
                "\\" + name + "{" + self._raw_argument() + "}"
            )
        return markup.text("\\" + name)

    def _environment(self, environment: str, content: str) -> str:
        rows = []
        for row in content.strip().split("\\\\"):
            if not row.strip():
                continue
            cells = [
                self._parse_fragment(cell.strip())
                for cell in row.split("&")
            ]
            rows.append("  ".join(cells))
        body = " ; ".join(rows)
        wrappers = {
            "matrix": ("", ""),
            "smallmatrix": ("", ""),
            "pmatrix": ("(", ")"),
            "bmatrix": ("[", "]"),
            "Bmatrix": ("{", "}"),
            "vmatrix": ("|", "|"),
            "Vmatrix": ("‖", "‖"),
            "cases": ("{", ""),
            "aligned": ("", ""),
            "align": ("", ""),
        }
        left, right = wrappers.get(environment, ("", ""))
        markup = self.markup
        return markup.text(left) + body + markup.text(right)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def latex_to_pango(source: str) -> str:
    """Convert a LaTeX expression to Pango markup."""
    value = str(source or "").strip()
    if not value:
        return ""
    try:
        return _Parser(value, _PangoMarkup()).parse()
    except Exception:
        return _unescape_source(value)


def latex_to_html(source: str) -> str:
    """Convert a LaTeX expression to an inline HTML fragment."""
    value = str(source or "").strip()
    if not value:
        return ""
    try:
        return _Parser(value, _HtmlMarkup()).parse()
    except Exception:
        return _unescape_source(value)


def inline_math_fragment(source: str, html_mode: bool = False) -> str:
    """Render inline math with the CSS hook used by Notes."""
    if html_mode:
        return f'<span class="math">{latex_to_html(source)}</span>'
    return latex_to_pango(source)


def replace_inline(text: str, render) -> str:
    """Replace ``$...$`` and ``\\(...\\)`` spans through *render*.

    ``render`` receives the raw TeX source and returns markup.  A plain
    currency phrase such as ``$5 and $10`` is left untouched.
    """
    if "$" not in text and "\\(" not in text:
        return text

    def insert(match: re.Match[str]) -> str:
        return render(match.group(1).strip())

    text = _PAREN_MATH.sub(insert, text)
    return _DOLLAR_MATH.sub(insert, text)


__all__ = [
    "SYMBOLS",
    "inline_math_fragment",
    "latex_to_html",
    "latex_to_pango",
    "replace_inline",
]
