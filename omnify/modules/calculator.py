"""Scientific and graphing calculator module.

The module is a GTK 4 front-end for the pure engine in
:mod:`omnify.modules.calc_engine`.  It provides a Desmos/Mathway-style
workspace rather than a chat interface: a scientific keypad, an expression
editor with history and suggestions, exact/decimal display, step-by-step
results, a graphing canvas with pan/zoom/trace, parameter sliders and PNG
export/copy.

Only the GTK layer lives here.  All parsing and maths is headless-testable in
``calc_engine``; the historical :class:`Calculator` name remains re-exported
for compatibility with the original test suite.
"""

from __future__ import annotations

import io
import math
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from ..core.i18n import tr
from ..core.module_context import BaseModule
from ..core.theme import CATEGORY_COLORS, parse_hex_color, theme_palette
from .calc_engine import (
    CalcError,
    Calculator as EngineCalculator,
    EvalContext,
    Matrix,
    Scalar,
    SolutionSet,
    ExprValue,
    EquationNode,
    format_number,
    format_scalar,
    format_value,
    node_variables,
    FUNCTION_NAMES,
    CONSTANT_NAMES,
)


# Historical import name used by tests and older callers.
Calculator = EngineCalculator


def _format_number(value: Any, precision: int = 10) -> str:
    """Compatibility helper for the original calculator tests."""
    try:
        return format_number(float(value), precision, exact=False)
    except (TypeError, ValueError):
        return str(value)


@dataclass
class _GraphRow:
    """Widgets and compiled state for one graph expression row."""

    box: Any
    entry: Any
    visible: Any
    swatch: Any
    node: Any = None
    error: str = ""
    color: str = "#4f8cff"


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _strip_graph_prefix(text: str) -> str:
    """Turn ``y = sin(x)``/``f(x)=…`` into a plottable expression."""
    text = text.strip()
    if "=" not in text:
        return text
    left, right = text.split("=", 1)
    left_clean = left.strip().lower()
    if left_clean in ("y", "f(x)", "g(x)", "h(x)"):
        return right.strip()
    return text


def _tick_text(value: float) -> str:
    if abs(value) < 1e-10:
        return "0"
    magnitude = abs(value)
    if magnitude >= 10000 or magnitude < 0.001:
        return f"{value:.2g}"
    return f"{value:g}"


def _nice_step(span: float, target_lines: int = 8) -> float:
    if span <= 0 or not math.isfinite(span):
        return 1.0
    raw = span / max(1, target_lines)
    exponent = math.floor(math.log10(raw))
    base = 10.0 ** exponent
    for multiplier in (1.0, 2.0, 5.0, 10.0):
        candidate = multiplier * base
        if raw <= candidate:
            return candidate
    return 10.0 * base


class CalculatorModule(BaseModule):
    """Scientific calculator and function grapher."""

    id = "calculator"
    name = "Calculator"
    icon = "accessories-calculator-symbolic"
    description = (
        "Scientific, exact and graphing calculator with steps, history, "
        "matrices, calculus, complex numbers and unit conversion."
    )
    group = "Tools"

    SETTINGS_KEY = "calculator_settings"
    HISTORY_KEY = "calculator_history"
    GRAPH_KEY = "calculator_graph"
    PARAMS_KEY = "calculator_params"
    MAX_HISTORY = 100
    CURVE_COLORS = (
        "#4f8cff",
        "#f0883e",
        "#3fb56f",
        "#e0629a",
        "#a06cf0",
        "#2fb6c4",
        "#e0636e",
        "#e6b455",
        "#9aa0ab",
    )

    def __init__(self, context) -> None:
        super().__init__(context)
        self._calc = EngineCalculator()
        self._history: List[Dict[str, Any]] = []
        self._load_state()
        self._last_result = None
        self._last_expression = ""
        self._history_index = -1
        self._graph_rows: List[_GraphRow] = []
        self._params: Dict[str, float] = self._load_json(self.PARAMS_KEY, {})
        if not isinstance(self._params, dict):
            self._params = {}
        self._graph_view: Tuple[float, float, float, float] = (
            -10.0,
            10.0,
            -6.0,
            6.0,
        )
        self._drag_ranges: Optional[Tuple[float, float, float, float]] = None
        self._pointer: Optional[Tuple[float, float]] = None
        self._trace: Optional[Tuple[float, float, _GraphRow]] = None
        self._graph_recompile_id = 0
        self._suggestion_apply = None
        self._param_names: List[str] = []
        self._param_widgets: Dict[str, Any] = {}
        self._history_visible = bool(self._settings.get("history_visible", True))
        self._mode = str(self._settings.get("mode", "scientific"))
        if self._mode not in ("scientific", "graph"):
            self._mode = "scientific"

    # ------------------------------------------------------------------
    # Persistence helpers
    # ------------------------------------------------------------------
    def _load_json(self, key: str, default: Any) -> Any:
        try:
            value = self.ctx.storage.load(key, default)
        except Exception:
            return default
        return value if value is not None else default

    def _save_json(self, key: str, value: Any) -> None:
        try:
            self.ctx.storage.save(key, value)
        except Exception:
            pass

    def _load_state(self) -> None:
        raw_settings = self._load_json(self.SETTINGS_KEY, {})
        if not isinstance(raw_settings, dict):
            raw_settings = {}
        self._settings = {
            "angle_degrees": bool(raw_settings.get("angle_degrees", False)),
            "exact": bool(raw_settings.get("exact", True)),
            "precision": int(raw_settings.get("precision", 10) or 10),
            "mode": str(raw_settings.get("mode", "scientific")),
            "history_visible": bool(raw_settings.get("history_visible", True)),
        }
        self._settings["precision"] = max(0, min(16, self._settings["precision"]))
        self._calc.angle_degrees = self._settings["angle_degrees"]
        self._calc.exact_mode = self._settings["exact"]
        self._calc.precision = self._settings["precision"]
        raw_history = self._load_json(self.HISTORY_KEY, [])
        if isinstance(raw_history, list):
            for item in raw_history:
                if not isinstance(item, dict):
                    continue
                expression = str(item.get("expression", "")).strip()
                exact = str(item.get("exact", ""))
                decimal = str(item.get("decimal", ""))
                if expression:
                    self._history.append(
                        {
                            "expression": expression,
                            "exact": exact,
                            "decimal": decimal,
                        }
                    )
        self._history = self._history[: self.MAX_HISTORY]
        raw_graph = self._load_json(self.GRAPH_KEY, None)
        self._saved_graph = raw_graph if isinstance(raw_graph, list) else None

    def _save_settings(self) -> None:
        self._settings["angle_degrees"] = bool(
            self._calc.angle_degrees
        )
        self._settings["exact"] = bool(self._calc.exact_mode)
        self._settings["precision"] = int(self._calc.precision)
        self._save_json(self.SETTINGS_KEY, dict(self._settings))

    def _save_history(self) -> None:
        self._save_json(self.HISTORY_KEY, self._history[: self.MAX_HISTORY])

    def _save_graph(self) -> None:
        payload = []
        for row in self._graph_rows:
            payload.append(
                {
                    "expression": row.entry.get_text(),
                    "visible": bool(row.visible.get_active()),
                    "color": row.color,
                }
            )
        self._save_json(self.GRAPH_KEY, payload)

    def _save_params(self) -> None:
        self._save_json(self.PARAMS_KEY, dict(self._params))

    # ------------------------------------------------------------------
    # View construction
    # ------------------------------------------------------------------
    def build_view(self):
        from gi.repository import GLib, Gtk

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        root.get_style_context().add_class("calc-root")
        root.set_margin_top(10)
        root.set_margin_bottom(12)
        root.set_margin_start(12)
        root.set_margin_end(12)
        self._root = root
        self._build_header(root)

        self._mode_stack = Gtk.Stack()
        self._mode_stack.set_hhomogeneous(False)
        self._mode_stack.set_vhomogeneous(False)
        self._mode_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self._mode_stack.set_hexpand(True)
        self._mode_stack.set_vexpand(True)
        self._scientific_page = self._build_scientific_page()
        self._graph_page = self._build_graph_page()
        self._mode_stack.add_titled(
            self._scientific_page, "scientific", "Scientific"
        )
        self._mode_stack.add_titled(self._graph_page, "graph", "Graphing")
        self._mode_switcher.set_stack(self._mode_stack)
        root.append(self._mode_stack)
        self._mode_stack.connect(
            "notify::visible-child", self._on_mode_changed
        )
        self._mode_stack.set_visible_child_name(self._mode)
        self._update_history_visibility()
        self._render_history()
        self._update_suggestions()
        self._update_ans_label()
        GLib.idle_add(self._focus_input_guarded)
        return root

    def _build_header(self, root) -> None:
        from gi.repository import Gtk

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        title_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        title = Gtk.Label(label="Calculator", xalign=0.0)
        title.get_style_context().add_class("omnify-card-title")
        subtitle = Gtk.Label(
            label="Scientific · exact results · graphing · steps",
            xalign=0.0,
        )
        subtitle.get_style_context().add_class("omnify-dim")
        title_box.append(title)
        title_box.append(subtitle)
        header.append(title_box)

        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        header.append(spacer)

        self._mode_switcher = Gtk.StackSwitcher()
        header.append(self._mode_switcher)

        self._angle_rad = Gtk.ToggleButton(label="RAD")
        self._angle_deg = Gtk.ToggleButton(label="DEG")
        self._angle_deg.set_group(self._angle_rad)
        self._angle_rad.set_tooltip_text("Trigonometric functions use radians")
        self._angle_deg.set_tooltip_text("Trigonometric functions use degrees")
        if self._calc.angle_degrees:
            self._angle_deg.set_active(True)
        else:
            self._angle_rad.set_active(True)
        self._angle_rad.connect("toggled", self._on_angle_toggled)
        self._angle_deg.connect("toggled", self._on_angle_toggled)
        header.append(self._angle_rad)
        header.append(self._angle_deg)

        self._exact_check = Gtk.CheckButton(label="Exact")
        self._exact_check.set_active(bool(self._calc.exact_mode))
        self._exact_check.set_tooltip_text(
            "Prefer exact fractions and symbolic results, with a decimal approximation"
        )
        self._exact_check.connect("toggled", self._on_exact_toggled)
        header.append(self._exact_check)

        precision_label = Gtk.Label(label="Digits")
        precision_label.get_style_context().add_class("omnify-dim")
        header.append(precision_label)
        self._precision_combo = Gtk.ComboBoxText()
        for value in ("4", "6", "8", "10", "12", "14", "16"):
            self._precision_combo.append_text(value)
        precision_values = ["4", "6", "8", "10", "12", "14", "16"]
        try:
            self._precision_combo.set_active(
                precision_values.index(str(self._calc.precision))
            )
        except ValueError:
            self._precision_combo.set_active(3)
        self._precision_combo.connect("changed", self._on_precision_changed)
        self._precision_combo.set_tooltip_text("Decimal digits in the result")
        header.append(self._precision_combo)

        self._history_button = Gtk.ToggleButton(label="History")
        self._history_button.set_active(self._history_visible)
        self._history_button.set_tooltip_text(
            "Show or hide the saved calculation history"
        )
        self._history_button.connect("toggled", self._on_history_toggled)
        header.append(self._history_button)
        root.append(header)

    # ------------------------------------------------------------------
    # Scientific page
    # ------------------------------------------------------------------
    def _build_scientific_page(self):
        from gi.repository import Gtk

        page = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        main = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=9)
        main.set_hexpand(True)

        main.append(self._build_display_card())
        main.append(self._build_input_area())

        self._suggestion_wrap = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=5
        )
        self._suggestion_wrap.get_style_context().add_class("omnify-dim")
        suggestion_label = Gtk.Label(label="Complete:")
        suggestion_label.get_style_context().add_class("omnify-dim")
        self._suggestion_wrap.append(suggestion_label)
        self._suggestion_box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=4
        )
        self._suggestion_wrap.append(self._suggestion_box)
        self._suggestion_wrap.set_visible(False)
        main.append(self._suggestion_wrap)

        main.append(self._build_function_chips())
        main.append(self._build_keypad())
        page.append(main)

        self._history_panel = self._build_history_panel()
        page.append(self._history_panel)
        return page

    def _build_display_card(self):
        from gi.repository import Gtk, Pango

        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        card.get_style_context().add_class("calc-display")
        card.set_vexpand(False)

        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self._expr_label = Gtk.Label(label="Ready", xalign=0.0)
        self._expr_label.get_style_context().add_class("calc-expression")
        self._expr_label.set_ellipsize(Pango.EllipsizeMode.END)
        self._expr_label.set_hexpand(True)
        top.append(self._expr_label)

        copy_result = Gtk.Button(label="Copy result")
        copy_result.get_style_context().add_class("calc-chip")
        copy_result.set_tooltip_text("Copy the displayed result")
        copy_result.connect("clicked", lambda _b: self._copy_result())
        top.append(copy_result)
        copy_expression = Gtk.Button(label="Copy expr")
        copy_expression.get_style_context().add_class("calc-chip")
        copy_expression.set_tooltip_text("Copy the expression")
        copy_expression.connect("clicked", lambda _b: self._copy_expression())
        top.append(copy_expression)
        card.append(top)

        self._result_label = Gtk.Label(label="0", xalign=0.0)
        self._result_label.get_style_context().add_class("calc-result")
        self._result_label.get_style_context().add_class("omnify-mono")
        self._result_label.set_selectable(True)
        self._result_label.set_wrap(True)
        self._result_label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self._result_label.set_xalign(0.0)
        card.append(self._result_label)

        self._secondary_label = Gtk.Label(label="", xalign=0.0)
        self._secondary_label.get_style_context().add_class("calc-secondary")
        self._secondary_label.get_style_context().add_class("omnify-mono")
        self._secondary_label.set_selectable(True)
        self._secondary_label.set_visible(False)
        card.append(self._secondary_label)

        bottom = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._ans_label = Gtk.Label(label="", xalign=0.0)
        self._ans_label.get_style_context().add_class("omnify-dim")
        self._ans_label.set_selectable(True)
        bottom.append(self._ans_label)
        card.append(bottom)

        self._steps_expander = Gtk.Expander(label="Step-by-step")
        self._steps_expander.set_visible(False)
        self._steps_expander.set_margin_top(2)
        self._steps_label = Gtk.Label(label="", xalign=0.0)
        self._steps_label.set_selectable(True)
        self._steps_label.set_wrap(True)
        self._steps_label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self._steps_label.get_style_context().add_class("omnify-dim")
        self._steps_label.get_style_context().add_class("calc-steps")
        self._steps_label.set_margin_top(4)
        self._steps_expander.set_child(self._steps_label)
        card.append(self._steps_expander)
        return card

    def _build_input_area(self):
        from gi.repository import Gtk

        area = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        label = Gtk.Label(label="Expression", xalign=0.0)
        label.get_style_context().add_class("omnify-dim")
        header.append(label)
        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        header.append(spacer)
        undo = Gtk.Button(label="↶")
        undo.set_tooltip_text("Undo (Ctrl+Z)")
        undo.get_style_context().add_class("calc-chip")
        undo.connect("clicked", lambda _b: self._buffer_undo())
        header.append(undo)
        redo = Gtk.Button(label="↷")
        redo.set_tooltip_text("Redo (Ctrl+Y)")
        redo.get_style_context().add_class("calc-chip")
        redo.connect("clicked", lambda _b: self._buffer_redo())
        header.append(redo)
        clear = Gtk.Button(label="Clear")
        clear.set_tooltip_text("Clear the expression (Ctrl+L)")
        clear.get_style_context().add_class("calc-chip")
        clear.connect("clicked", lambda _b: self._clear_expression())
        header.append(clear)
        area.append(header)

        scrolled = Gtk.ScrolledWindow()
        scrolled.get_style_context().add_class("calc-input")
        scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_min_content_height(82)
        scrolled.set_max_content_height(150)
        self._input_scroll = scrolled
        self._input = Gtk.TextView()
        self._input.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self._input.set_monospace(True)
        self._input.set_top_margin(7)
        self._input.set_bottom_margin(7)
        self._input.set_left_margin(8)
        self._input.set_right_margin(8)
        self._input.set_accepts_tab(False)
        self._input.set_hexpand(True)
        self._input.set_vexpand(True)
        buffer = self._input.get_buffer()
        buffer.set_enable_undo(True)
        buffer.connect("changed", self._on_input_changed)
        scrolled.set_child(self._input)
        area.append(scrolled)

        key_controller = Gtk.EventControllerKey()
        key_controller.connect("key-pressed", self._on_input_key_pressed)
        self._input.add_controller(key_controller)
        return area

    def _build_function_chips(self):
        from gi.repository import Gtk

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        functions = (
            ("det", "det()", 1),
            ("inv", "inv()", 1),
            ("solve", "solve(, x)", 4),
            ("diff", "diff(, x)", 4),
            ("∫", "integral(, x)", 4),
            ("matrix", "[[,],]", 4),
            ("convert", 'convert(, "km", "m")', 12),
            ("mod", " mod ", 0),
        )
        for label, text, offset in functions:
            button = Gtk.Button(label=label)
            button.get_style_context().add_class("calc-chip")
            button.set_tooltip_text(f"Insert {text.strip()}")
            button.connect(
                "clicked",
                lambda _b, value=text, back=offset: self._insert_text(
                    value, back
                ),
            )
            box.append(button)
        return box

    def _build_keypad(self):
        from gi.repository import Gtk

        grid = Gtk.Grid()
        grid.set_column_spacing(6)
        grid.set_row_spacing(6)
        grid.set_column_homogeneous(True)
        grid.set_row_homogeneous(True)
        grid.set_vexpand(True)
        rows = (
            (
                ("sin", "sin()", "fn", 1),
                ("cos", "cos()", "fn", 1),
                ("tan", "tan()", "fn", 1),
                ("π", "pi", "fn", 0),
                ("e", "e", "fn", 0),
                ("i", "i", "fn", 0),
            ),
            (
                ("sin⁻¹", "asin()", "fn", 1),
                ("cos⁻¹", "acos()", "fn", 1),
                ("tan⁻¹", "atan()", "fn", 1),
                ("ln", "ln()", "fn", 1),
                ("log", "log()", "fn", 1),
                ("√", "sqrt()", "fn", 1),
            ),
            (
                ("x²", "^2", "fn", 0),
                ("xʸ", "^", "fn", 0),
                ("x!", "!", "fn", 0),
                ("|x|", "abs()", "fn", 1),
                ("1/x", "1/", "fn", 0),
                ("Ans", "Ans", "fn", 0),
            ),
            (
                ("7", "7", "", 0),
                ("8", "8", "", 0),
                ("9", "9", "", 0),
                ("÷", "/", "", 0),
                ("(", "(", "", 0),
                (")", ")", "", 0),
            ),
            (
                ("4", "4", "", 0),
                ("5", "5", "", 0),
                ("6", "6", "", 0),
                ("×", "*", "", 0),
                ("[", "[", "", 0),
                ("]", "]", "", 0),
            ),
            (
                ("1", "1", "", 0),
                ("2", "2", "", 0),
                ("3", "3", "", 0),
                ("−", "-", "", 0),
                (",", ",", "", 0),
                ("∫", "integral(, x)", "fn", 4),
            ),
            (
                ("0", "0", "", 0),
                (".", ".", "", 0),
                ("±", "±", "", 0),
                ("+", "+", "", 0),
                ("=", "=", "equals", 0),
                ("AC", "AC", "danger", 0),
            ),
        )
        for row_index, row in enumerate(rows):
            for column_index, (label, action, kind, offset) in enumerate(row):
                button = Gtk.Button(label=label)
                button.get_style_context().add_class("calc-key")
                if kind == "fn":
                    button.get_style_context().add_class("calc-key-fn")
                elif kind == "equals":
                    button.get_style_context().add_class("calc-key-equals")
                elif kind == "danger":
                    button.get_style_context().add_class("calc-key-danger")
                button.connect(
                    "clicked",
                    lambda _b, value=action, back=offset: self._key_action(
                        value, back
                    ),
                )
                grid.attach(button, column_index, row_index, 1, 1)
        return grid

    # ------------------------------------------------------------------
    # History panel
    # ------------------------------------------------------------------
    def _build_history_panel(self):
        from gi.repository import Gtk

        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        panel.get_style_context().add_class("omnify-card")
        panel.set_size_request(270, -1)
        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        title = Gtk.Label(label="History", xalign=0.0)
        title.get_style_context().add_class("omnify-card-title")
        top.append(title)
        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        top.append(spacer)
        clear = Gtk.Button(label="Clear")
        clear.get_style_context().add_class("calc-chip")
        clear.set_tooltip_text("Clear all saved calculations")
        clear.connect("clicked", lambda _b: self._clear_history())
        top.append(clear)
        panel.append(top)

        self._history_scroll = Gtk.ScrolledWindow()
        self._history_scroll.set_policy(
            Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC
        )
        self._history_scroll.set_vexpand(True)
        self._history_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=3
        )
        self._history_scroll.set_child(self._history_box)
        panel.append(self._history_scroll)
        return panel

    def _render_history(self) -> None:
        from gi.repository import Gtk, Pango

        child = self._history_box.get_first_child()
        while child is not None:
            following = child.get_next_sibling()
            self._history_box.remove(child)
            child = following
        if not self._history:
            empty = Gtk.Label(
                label=tr("No calculations yet.\nPress Enter to evaluate."),
                xalign=0.0,
            )
            empty.get_style_context().add_class("omnify-dim")
            empty.set_margin_top(6)
            self._history_box.append(empty)
            return
        for entry in self._history:
            expression = str(entry.get("expression", ""))
            exact = str(entry.get("exact", ""))
            decimal = str(entry.get("decimal", ""))
            button = Gtk.Button()
            button.get_style_context().add_class("calc-history-row")
            button.set_has_frame(False)
            content = Gtk.Box(
                orientation=Gtk.Orientation.VERTICAL, spacing=1
            )
            expression_label = Gtk.Label(
                label=expression, xalign=0.0
            )
            expression_label.get_style_context().add_class("calc-expression")
            expression_label.set_ellipsize(Pango.EllipsizeMode.END)
            result_text = exact or decimal or "—"
            result_label = Gtk.Label(label=result_text, xalign=0.0)
            result_label.get_style_context().add_class("omnify-mono")
            result_label.set_ellipsize(Pango.EllipsizeMode.END)
            content.append(expression_label)
            content.append(result_label)
            button.set_child(content)
            button.set_tooltip_text("Load this expression")
            button.connect(
                "clicked",
                lambda _b, value=expression: self._load_from_history(value),
            )
            self._history_box.append(button)

    def _clear_history(self) -> None:
        self._history = []
        self._history_index = -1
        self._save_history()
        self._render_history()
        self._notify("History cleared")

    def _add_history(self, expression: str, result) -> None:
        exact = result.exact_text
        decimal = result.decimal_text
        if self._history and self._history[0].get("expression") == expression:
            self._history[0] = {
                "expression": expression,
                "exact": exact,
                "decimal": decimal,
            }
        else:
            self._history.insert(
                0,
                {
                    "expression": expression,
                    "exact": exact,
                    "decimal": decimal,
                },
            )
        self._history = self._history[: self.MAX_HISTORY]
        self._history_index = -1
        self._save_history()
        self._render_history()
        try:
            self._history_scroll.get_vadjustment().set_value(0.0)
        except Exception:
            pass

    def _load_from_history(self, expression: str) -> None:
        self._set_input_text(expression)
        self._focus_input_guarded()

    # ------------------------------------------------------------------
    # Input handling
    # ------------------------------------------------------------------
    def _buffer(self):
        return self._input.get_buffer()

    def _input_text(self) -> str:
        buffer = self._buffer()
        start, end = buffer.get_bounds()
        return buffer.get_text(start, end, True)

    def _set_input_text(self, text: str) -> None:
        buffer = self._buffer()
        buffer.set_text(text)
        buffer.place_cursor(buffer.get_end_iter())

    def _clear_expression(self) -> None:
        buffer = self._buffer()
        buffer.set_text("")
        self._result_label.set_text("0")
        self._expr_label.set_text(tr("Ready"))
        self._secondary_label.set_visible(False)
        self._steps_expander.set_visible(False)
        self._result_label.get_style_context().remove_class("calc-error")
        self._last_result = None
        self._last_expression = ""
        self._focus_input_guarded()

    def _buffer_undo(self) -> None:
        try:
            self._buffer().undo()
        except Exception:
            pass
        self._focus_input_guarded()

    def _buffer_redo(self) -> None:
        try:
            self._buffer().redo()
        except Exception:
            pass
        self._focus_input_guarded()

    def _insert_text(self, text: str, cursor_back: int = 0) -> None:
        buffer = self._buffer()
        if buffer.get_has_selection():
            buffer.delete_selection(True, True)
        buffer.begin_user_action()
        buffer.insert_at_cursor(text)
        if cursor_back:
            mark = buffer.get_insert()
            position = buffer.get_iter_at_mark(mark)
            position.backward_chars(cursor_back)
            buffer.place_cursor(position)
        buffer.end_user_action()
        self._focus_input_guarded()

    def _insert_pair(self, opening: str, closing: str) -> None:
        buffer = self._buffer()
        buffer.begin_user_action()
        if buffer.get_has_selection():
            start, end = buffer.get_selection_bounds()
            selected = buffer.get_text(start, end, True)
            buffer.delete(start, end)
            buffer.insert_at_cursor(opening + selected + closing)
        else:
            buffer.insert_at_cursor(opening + closing)
            position = buffer.get_iter_at_mark(buffer.get_insert())
            position.backward_chars(1)
            buffer.place_cursor(position)
        buffer.end_user_action()
        self._focus_input_guarded()

    def _key_action(self, action: str, cursor_back: int = 0) -> None:
        if action == "=":
            self._evaluate()
            return
        if action == "AC":
            self._clear_expression()
            return
        if action == "±":
            self._toggle_sign()
            return
        if action == "(":
            self._insert_pair("(", ")")
            return
        if action == "[":
            self._insert_pair("[", "]")
            return
        self._insert_text(action, cursor_back)

    def _toggle_sign(self) -> None:
        buffer = self._buffer()
        if buffer.get_has_selection():
            start, end = buffer.get_selection_bounds()
            text = buffer.get_text(start, end, True)
            replacement = text[1:] if text.startswith("-") else "-" + text
            buffer.begin_user_action()
            buffer.delete(start, end)
            buffer.insert_at_cursor(replacement)
            buffer.end_user_action()
        else:
            self._insert_text("-")
        self._focus_input_guarded()

    def _on_input_changed(self, _buffer) -> None:
        self._update_suggestions()

    def _current_word(self) -> Optional[Tuple[str, int, int]]:
        buffer = self._buffer()
        cursor = buffer.get_iter_at_mark(buffer.get_insert())
        offset = cursor.get_offset()
        start, _end = buffer.get_bounds()
        text = buffer.get_text(start, cursor, True)
        match = re.search(r"([A-Za-z_][A-Za-z0-9_]*)$", text)
        if not match:
            return None
        prefix = match.group(1)
        if not prefix:
            return None
        return prefix, offset - len(prefix), offset

    def _update_suggestions(self) -> None:
        if not hasattr(self, "_suggestion_box"):
            return
        word = self._current_word()
        while True:
            child = self._suggestion_box.get_first_child()
            if child is None:
                break
            self._suggestion_box.remove(child)
        self._suggestion_apply = None
        if word is None:
            self._suggestion_wrap.set_visible(False)
            return
        prefix, start, end = word
        names = set(FUNCTION_NAMES) | set(CONSTANT_NAMES) | {"ans", "x", "y", "a", "b"}
        matches = sorted(
            name for name in names
            if name.startswith(prefix.lower()) and name.lower() != prefix.lower()
        )[:8]
        if not matches:
            self._suggestion_wrap.set_visible(False)
            return
        from gi.repository import Gtk

        for name in matches:
            button = Gtk.Button(label=name)
            button.get_style_context().add_class("calc-chip")
            button.connect(
                "clicked",
                lambda _b, value=name, begin=start, finish=end: self._apply_completion(
                    value, begin, finish
                ),
            )
            self._suggestion_box.append(button)
        first = matches[0]
        self._suggestion_apply = lambda: self._apply_completion(
            first, start, end
        )
        self._suggestion_wrap.set_visible(True)

    def _apply_completion(self, name: str, start: int, end: int) -> None:
        buffer = self._buffer()
        start_iter = buffer.get_iter_at_offset(start)
        end_iter = buffer.get_iter_at_offset(end)
        buffer.begin_user_action()
        buffer.delete(start_iter, end_iter)
        buffer.insert_at_cursor(name)
        buffer.end_user_action()
        self._suggestion_wrap.set_visible(False)
        self._focus_input_guarded()

    def _on_input_key_pressed(self, _controller, keyval, _keycode, state):
        from gi.repository import Gdk

        control = bool(state & Gdk.ModifierType.CONTROL_MASK)
        shift = bool(state & Gdk.ModifierType.SHIFT_MASK)
        if control and keyval in (Gdk.KEY_l, Gdk.KEY_L):
            self._clear_expression()
            return True
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            if shift:
                return False
            self._evaluate()
            return True
        if keyval == Gdk.KEY_Escape:
            self._suggestion_wrap.set_visible(False)
            self._clear_expression()
            return True
        if keyval == Gdk.KEY_Tab:
            if self._suggestion_apply is not None:
                self._suggestion_apply()
                return True
            return False
        if keyval == Gdk.KEY_Up and not control:
            text = self._input_text()
            if "\n" not in text:
                self._history_navigate(-1)
                return True
        if keyval == Gdk.KEY_Down and not control:
            text = self._input_text()
            if "\n" not in text:
                self._history_navigate(1)
                return True
        if keyval == Gdk.KEY_BackSpace and not control:
            if self._delete_empty_pair():
                return True
            return False
        character = Gdk.keyval_to_unicode(keyval)
        if character:
            char = chr(character)
            if char in "([{":
                closing = {"(": ")", "[": "]", "{": "}"}[char]
                self._insert_pair(char, closing)
                return True
            if char in ")]}":
                buffer = self._buffer()
                cursor = buffer.get_iter_at_mark(buffer.get_insert())
                next_iter = cursor.copy()
                if next_iter.forward_char():
                    next_char = buffer.get_text(cursor, next_iter, True)
                    if next_char == char:
                        buffer.place_cursor(next_iter)
                        return True
                return False
        return False

    def _delete_empty_pair(self) -> bool:
        buffer = self._buffer()
        if buffer.get_has_selection():
            return False
        cursor = buffer.get_iter_at_mark(buffer.get_insert())
        before = cursor.copy()
        after = cursor.copy()
        if not before.backward_char() or not after.forward_char():
            return False
        pair = buffer.get_text(before, after, True)
        if pair in ("()", "[]", "{}"):
            buffer.begin_user_action()
            buffer.delete(before, after)
            buffer.end_user_action()
            return True
        return False

    def _history_navigate(self, direction: int) -> None:
        if not self._history:
            return
        if self._history_index < 0:
            self._history_index = 0 if direction < 0 else 0
        else:
            self._history_index = max(
                0,
                min(len(self._history) - 1, self._history_index + direction),
            )
        expression = self._history[self._history_index].get("expression", "")
        self._set_input_text(str(expression))
        self._focus_input_guarded()

    def _focus_input_guarded(self):
        if self._input is not None:
            try:
                if self._input.get_mapped() and self._input.get_realized():
                    self._input.grab_focus()
            except Exception:
                pass
        return False

    def _on_history_toggled(self, button) -> None:
        self._history_visible = bool(button.get_active())
        self._settings["history_visible"] = self._history_visible
        self._save_settings()
        self._update_history_visibility()

    def _update_history_visibility(self) -> None:
        if hasattr(self, "_history_panel"):
            self._history_panel.set_visible(self._history_visible)
            if hasattr(self, "_history_button"):
                self._history_button.set_active(self._history_visible)

    # ------------------------------------------------------------------
    # Evaluation and display
    # ------------------------------------------------------------------
    def _evaluate(self, record_history: bool = True) -> None:
        expression = self._input_text().strip()
        if not expression:
            return
        self._last_expression = expression
        try:
            result = self._calc.evaluate_detailed(expression)
        except CalcError as exc:
            self._show_error(str(exc))
            return
        except Exception as exc:  # pragma: no cover - defensive GUI guard
            self._show_error(f"{tr('Calculation error:')} {exc}")
            return
        self._last_result = result
        self._render_result(result)
        self._update_ans_label()
        if record_history:
            self._add_history(expression, result)

    def _show_error(self, message: str) -> None:
        self._last_result = None
        self._expr_label.set_text(self._last_expression or tr("Expression"))
        self._result_label.set_text(tr(message))
        self._result_label.get_style_context().add_class("calc-error")
        self._secondary_label.set_visible(False)
        self._steps_expander.set_visible(False)
        self._update_ans_label()

    def _display_texts(self, result) -> Tuple[str, str]:
        exact_mode = bool(self._calc.exact_mode)
        value = result.value
        if isinstance(value, (Scalar, Matrix)):
            exact = format_value(value, self._calc.precision, True)
            decimal = format_value(value, self._calc.precision, False)
            if result.unit:
                exact = f"{exact} {result.unit}"
                decimal = f"{decimal} {result.unit}"
            if exact_mode:
                secondary = "" if decimal == exact else f"≈ {decimal}"
                return exact, secondary
            return decimal or exact, ""
        if isinstance(value, SolutionSet):
            exact = value.format(self._calc.precision, True)
            decimal = value.format(self._calc.precision, False)
            if exact_mode:
                secondary = "" if decimal == exact else f"≈ {decimal}"
                return exact, secondary
            return decimal or exact, ""
        if isinstance(value, ExprValue):
            return value.text, ""
        return result.primary_text(exact_mode), result.secondary_text(exact_mode)

    def _render_result(self, result) -> None:
        exact_mode = bool(self._calc.exact_mode)
        primary, secondary = self._display_texts(result)
        self._expr_label.set_text(result.expression)
        self._result_label.get_style_context().remove_class("calc-error")
        self._result_label.set_text(primary or "0")
        self._secondary_label.set_text(secondary)
        self._secondary_label.set_visible(bool(secondary))
        steps = list(result.steps)
        if steps:
            self._steps_label.set_text("\n".join(steps))
            self._steps_expander.set_visible(True)
            symbolic = isinstance(result.value, (ExprValue, SolutionSet))
            self._steps_expander.set_expanded(symbolic or len(steps) > 4)
        else:
            self._steps_label.set_text("")
            self._steps_expander.set_visible(False)

    def _update_ans_label(self) -> None:
        if not hasattr(self, "_ans_label"):
            return
        value = self._calc.ans
        if value is None:
            self._ans_label.set_text("")
            return
        self._ans_label.set_text(
            "Ans = " + format_value(value, self._calc.precision, self._calc.exact_mode)
        )

    def _on_exact_toggled(self, button) -> None:
        self._calc.exact_mode = bool(button.get_active())
        self._save_settings()
        if self._last_result is not None:
            self._render_result(self._last_result)
        self._update_ans_label()

    def _on_precision_changed(self, combo) -> None:
        values = ("4", "6", "8", "10", "12", "14", "16")
        index = combo.get_active()
        if 0 <= index < len(values):
            self._calc.precision = int(values[index])
            self._save_settings()
            if self._last_result is not None:
                self._render_result(self._last_result)
            self._update_ans_label()

    def _on_angle_toggled(self, button) -> None:
        if not button.get_active():
            return
        self._calc.angle_degrees = self._angle_deg.get_active()
        self._settings["angle_degrees"] = self._calc.angle_degrees
        self._save_settings()
        if self._last_expression:
            try:
                result = self._calc.evaluate_detailed(self._last_expression)
            except CalcError:
                return
            self._last_result = result
            self._render_result(result)
            self._update_ans_label()
        if hasattr(self, "_graph_area"):
            self._graph_area.queue_draw()

    def _on_mode_changed(self, stack, _param) -> None:
        current = stack.get_visible_child_name() or "scientific"
        self._mode = current
        self._settings["mode"] = current
        self._save_settings()
        if current == "graph" and hasattr(self, "_graph_area"):
            self._graph_area.queue_draw()
        elif current == "scientific":
            self._focus_input_guarded()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def on_show(self) -> None:
        if self._mode == "scientific":
            try:
                from gi.repository import GLib

                GLib.idle_add(self._focus_input_guarded)
            except Exception:
                pass
        if hasattr(self, "_graph_area"):
            self._graph_area.queue_draw()

    def on_hide(self) -> None:
        self._save_settings()
        self._save_history()
        self._save_graph()
        self._save_params()

    # ------------------------------------------------------------------
    # Clipboard and notifications
    # ------------------------------------------------------------------
    def _notify(self, message: str) -> None:
        try:
            self.ctx.notify(message)
        except Exception:
            pass

    def _copy_result(self) -> None:
        if self._last_result is None:
            self._notify("No result to copy")
            return
        text, _secondary = self._display_texts(self._last_result)
        self._copy_text(text)
        self._notify("Result copied")

    def _copy_expression(self) -> None:
        text = self._input_text().strip()
        if not text:
            self._notify("The expression is empty")
            return
        self._copy_text(text)
        self._notify("Expression copied")

    def _copy_text(self, text: str) -> None:
        from gi.repository import GObject

        try:
            clipboard = self._root.get_clipboard()
            value = GObject.Value()
            value.init(GObject.TYPE_STRING)
            value.set_string(text)
            clipboard.set(value)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Graphing page
    # ------------------------------------------------------------------
    def _build_graph_page(self):
        from gi.repository import Gtk

        page = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        side = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=7)
        side.set_size_request(320, -1)

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        title = Gtk.Label(label="Expressions", xalign=0.0)
        title.get_style_context().add_class("omnify-card-title")
        header.append(title)
        header_spacer = Gtk.Box()
        header_spacer.set_hexpand(True)
        header.append(header_spacer)
        add_button = Gtk.Button(label="+ Add")
        add_button.get_style_context().add_class("calc-chip")
        add_button.set_tooltip_text("Add a function row")
        add_button.connect("clicked", lambda _b: self._add_graph_row(""))
        header.append(add_button)
        side.append(header)

        graph_scroll = Gtk.ScrolledWindow()
        graph_scroll.set_policy(
            Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC
        )
        graph_scroll.set_min_content_height(90)
        graph_scroll.set_max_content_height(220)
        graph_scroll.set_vexpand(False)
        self._graph_rows_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=5
        )
        graph_scroll.set_child(self._graph_rows_box)
        side.append(graph_scroll)

        examples_title = Gtk.Label(label="Try an example", xalign=0.0)
        examples_title.get_style_context().add_class("omnify-dim")
        side.append(examples_title)
        examples = Gtk.FlowBox()
        examples.set_selection_mode(Gtk.SelectionMode.NONE)
        examples.set_column_spacing(4)
        examples.set_row_spacing(4)
        examples.set_max_children_per_line(2)
        for example in ("sin(x)", "x^2 - 2", "a*sin(x)", "1/x", "x^3 - 3x"):
            example_button = Gtk.Button(label=example)
            example_button.get_style_context().add_class("calc-chip")
            example_button.connect(
                "clicked",
                lambda _b, value=example: self._add_graph_row(value),
            )
            examples.append(example_button)
        side.append(examples)

        self._params_panel = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=5
        )
        params_header = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=5
        )
        params_title = Gtk.Label(label="Parameters", xalign=0.0)
        params_title.get_style_context().add_class("omnify-card-title")
        params_header.append(params_title)
        params_spacer = Gtk.Box()
        params_spacer.set_hexpand(True)
        params_header.append(params_spacer)
        reset_params = Gtk.Button(label="Reset")
        reset_params.get_style_context().add_class("calc-chip")
        reset_params.set_tooltip_text("Reset all parameter sliders to 1")
        reset_params.connect("clicked", lambda _b: self._reset_params())
        params_header.append(reset_params)
        self._params_panel.append(params_header)
        self._params_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=4
        )
        self._params_panel.append(self._params_box)
        self._params_panel.set_visible(False)
        side.append(self._params_panel)

        tip = Gtk.Label(
            label=(
                "Tip: use y = f(x), or type a letter such as a to get a "
                "parameter slider."
            ),
            xalign=0.0,
        )
        tip.get_style_context().add_class("omnify-dim")
        tip.set_wrap(True)
        side.append(tip)

        controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        for label, tooltip, callback in (
            ("Fit", "Fit the y-range to the visible curves", self._fit_graph),
            ("Reset", "Reset the view to x ∈ [−10, 10]", self._reset_graph_view),
            ("Export PNG", "Save the graph as a PNG image", self._export_graph),
            ("Copy graph", "Copy the graph image to the clipboard", self._copy_graph),
        ):
            button = Gtk.Button(label=label)
            button.get_style_context().add_class("calc-chip")
            button.set_tooltip_text(tooltip)
            button.connect("clicked", lambda _b, cb=callback: cb())
            controls.append(button)
        side.append(controls)

        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        right.set_hexpand(True)
        self._graph_area = Gtk.DrawingArea()
        self._graph_area.get_style_context().add_class("calc-graph")
        self._graph_area.set_hexpand(True)
        self._graph_area.set_vexpand(True)
        self._graph_area.set_size_request(620, 460)
        self._graph_area.set_draw_func(self._draw_graph)
        self._install_graph_controllers()
        right.append(self._graph_area)

        self._graph_status = Gtk.Label(
            label=tr("Drag to pan · Scroll to zoom · Hover to trace"),
            xalign=0.0,
        )
        self._graph_status.get_style_context().add_class("omnify-dim")
        self._graph_status.set_margin_start(4)
        right.append(self._graph_status)
        page.append(side)
        page.append(right)

        self._building_graph = True
        self._render_graph_rows()
        self._building_graph = False
        self._recompile_graph()
        return page

    def _install_graph_controllers(self):
        from gi.repository import Gtk

        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self._on_graph_motion)
        motion.connect("leave", self._on_graph_leave)
        self._graph_area.add_controller(motion)

        scroll = Gtk.EventControllerScroll.new(
            Gtk.EventControllerScrollFlags.VERTICAL
        )
        scroll.connect("scroll", self._on_graph_scroll)
        self._graph_area.add_controller(scroll)

        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", self._on_graph_drag_begin)
        drag.connect("drag-update", self._on_graph_drag_update)
        self._graph_area.add_controller(drag)

    def _render_graph_rows(self) -> None:
        while True:
            child = self._graph_rows_box.get_first_child()
            if child is None:
                break
            self._graph_rows_box.remove(child)
        self._graph_rows = []
        raw = self._saved_graph
        if raw is None:
            raw = [
                {
                    "expression": "sin(x)",
                    "visible": True,
                    "color": self.CURVE_COLORS[0],
                }
            ]
            self._saved_graph = raw
        for index, item in enumerate(raw if isinstance(raw, list) else []):
            if isinstance(item, str):
                expression = item
                visible = True
                color = self.CURVE_COLORS[index % len(self.CURVE_COLORS)]
            elif isinstance(item, dict):
                expression = str(item.get("expression", ""))
                visible = bool(item.get("visible", True))
                color = str(
                    item.get("color")
                    or self.CURVE_COLORS[index % len(self.CURVE_COLORS)]
                )
            else:
                continue
            self._add_graph_row(expression, visible=visible, color=color)
        if self._saved_graph is not None:
            self._save_graph()

    def _add_graph_row(
        self,
        expression: str = "",
        visible: bool = True,
        color: Optional[str] = None,
    ):
        from gi.repository import GLib, Gtk

        if color is None:
            used = {row.color for row in self._graph_rows}
            color = next(
                (
                    candidate
                    for candidate in self.CURVE_COLORS
                    if candidate not in used
                ),
                self.CURVE_COLORS[len(self._graph_rows) % len(self.CURVE_COLORS)],
            )
        rgb = parse_hex_color(color)
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        box.get_style_context().add_class("calc-graph-row")

        swatch = Gtk.DrawingArea()
        swatch.set_size_request(14, 14)
        swatch.set_tooltip_text("Curve colour")
        swatch.set_draw_func(
            lambda _area, cr, width, height, rgb=rgb: self._draw_swatch(
                cr, width, height, rgb
            )
        )
        box.append(swatch)

        entry = Gtk.Entry()
        entry.set_placeholder_text("y = sin(x)")
        entry.set_text(expression)
        entry.set_hexpand(True)
        entry.set_tooltip_text("Use x as the variable; y = f(x) is optional")
        box.append(entry)

        visible_button = Gtk.CheckButton()
        visible_button.set_active(visible)
        visible_button.set_tooltip_text("Show or hide this curve")
        box.append(visible_button)

        delete = Gtk.Button(label="×")
        delete.get_style_context().add_class("calc-chip")
        delete.set_tooltip_text("Remove this expression")
        box.append(delete)

        row = _GraphRow(
            box=box,
            entry=entry,
            visible=visible_button,
            swatch=swatch,
            color=color,
        )
        self._graph_rows.append(row)
        self._graph_rows_box.append(box)

        entry.connect("changed", lambda _e: self._schedule_graph_recompile())
        entry.connect("activate", lambda _e: self._recompile_graph())
        visible_button.connect(
            "toggled", lambda _b: self._on_graph_visibility_changed()
        )
        delete.connect(
            "clicked", lambda _b, target=row: self._remove_graph_row(target)
        )
        if not self._building_graph:
            self._save_graph()
            self._schedule_graph_recompile()
            GLib.idle_add(self._focus_graph_entry, entry)
        return row

    def _focus_graph_entry(self, entry) -> bool:
        try:
            if entry.get_mapped() and entry.get_realized():
                entry.grab_focus()
        except Exception:
            pass
        return False

    def _remove_graph_row(self, row: _GraphRow) -> None:
        if row in self._graph_rows:
            self._graph_rows.remove(row)
        parent = row.box.get_parent()
        if parent is not None:
            parent.remove(row.box)
        self._recompile_graph()
        self._save_graph()
        self._graph_area.queue_draw()
        self._notify("Expression removed")

    def _on_graph_visibility_changed(self) -> None:
        self._save_graph()
        self._recompile_graph()
        self._graph_area.queue_draw()

    def _draw_swatch(self, cr, width: float, height: float, rgb) -> None:
        cr.set_source_rgba(rgb[0], rgb[1], rgb[2], 1.0)
        cr.arc(
            width / 2.0,
            height / 2.0,
            max(1.0, min(width, height) / 2.0 - 1.0),
            0.0,
            2.0 * math.pi,
        )
        cr.fill()

    def _schedule_graph_recompile(self) -> None:
        if self._building_graph:
            return
        try:
            from gi.repository import GLib
        except Exception:
            return
        if self._graph_recompile_id:
            try:
                GLib.source_remove(self._graph_recompile_id)
            except Exception:
                pass
        self._graph_recompile_id = GLib.timeout_add(
            220, self._graph_recompile_timeout
        )

    def _graph_recompile_timeout(self) -> bool:
        self._graph_recompile_id = 0
        self._recompile_graph()
        return False

    def _recompile_graph(self) -> None:
        if not hasattr(self, "_graph_rows"):
            return
        errors: List[str] = []
        for row in self._graph_rows:
            raw = row.entry.get_text().strip()
            expression = _strip_graph_prefix(raw)
            entry_context = row.entry.get_style_context()
            entry_context.remove_class("calc-invalid")
            row.entry.set_tooltip_text(
                "Use x as the variable; y = f(x) is optional"
            )
            row.node = None
            row.error = ""
            if not raw:
                continue
            if "=" in expression:
                row.error = "Use y = f(x) or a function of x"
            else:
                try:
                    node = self._calc.compile(expression)
                except CalcError as exc:
                    row.error = str(exc)
                    node = None
                if node is not None:
                    variables = node_variables(node)
                    if "y" in variables:
                        row.error = "Use y = f(x); y cannot appear on both sides"
                        node = None
                    elif isinstance(node, EquationNode):
                        row.error = "Use y = f(x), not an equation"
                        node = None
                    else:
                        row.node = node
            if row.error:
                errors.append(row.error)
                entry_context.add_class("calc-invalid")
                row.entry.set_tooltip_text(row.error)
        names: Set[str] = set()
        for row in self._graph_rows:
            if row.node is None or not row.visible.get_active():
                continue
            names.update(node_variables(row.node) - {"x"})
        self._sync_params(sorted(names))
        if hasattr(self, "_graph_status"):
            if errors:
                self._graph_status.set_text(errors[0])
            else:
                self._graph_status.set_text(
                    tr("Drag to pan · Scroll to zoom · Hover to trace")
                )
        self._save_graph()
        if hasattr(self, "_graph_area"):
            self._graph_area.queue_draw()

    def _sync_params(self, names: Sequence[str]) -> None:
        if list(names) == self._param_names and self._param_widgets:
            return
        self._param_names = list(names)
        while True:
            child = self._params_box.get_first_child()
            if child is None:
                break
            self._params_box.remove(child)
        self._param_widgets = {}
        if not names:
            self._params_panel.set_visible(False)
            return
        from gi.repository import Gtk

        for name in names:
            value = _safe_float(self._params.get(name, 1.0), 1.0)
            value = max(-10.0, min(10.0, value))
            self._params[name] = value
            container = Gtk.Box(
                orientation=Gtk.Orientation.HORIZONTAL, spacing=5
            )
            container.get_style_context().add_class("calc-param")
            label = Gtk.Label(label=name, xalign=0.0)
            label.get_style_context().add_class("omnify-mono")
            label.set_size_request(20, -1)
            container.append(label)
            scale = Gtk.Scale.new_with_range(
                Gtk.Orientation.HORIZONTAL, -10.0, 10.0, 0.1
            )
            scale.set_value(value)
            scale.set_digits(2)
            scale.set_draw_value(False)
            scale.set_hexpand(True)
            scale.set_size_request(120, -1)
            container.append(scale)
            value_label = Gtk.Label(label=f"{value:.2f}", xalign=1.0)
            value_label.get_style_context().add_class("omnify-dim")
            value_label.get_style_context().add_class("omnify-mono")
            value_label.set_size_request(44, -1)
            container.append(value_label)
            scale.connect(
                "value-changed",
                lambda widget, parameter=name, output=value_label: self._on_param_changed(
                    parameter, widget.get_value(), output
                ),
            )
            self._params_box.append(container)
            self._param_widgets[name] = (scale, value_label)
        self._params_panel.set_visible(True)

    def _on_param_changed(self, name: str, value: float, label) -> None:
        self._params[name] = float(value)
        label.set_text(f"{float(value):.2f}")
        self._save_params()
        if hasattr(self, "_graph_area"):
            self._graph_area.queue_draw()

    def _reset_params(self) -> None:
        for scale, label in self._param_widgets.values():
            scale.set_value(1.0)
            label.set_text("1.00")
        for name in self._param_names:
            self._params[name] = 1.0
        self._save_params()
        if hasattr(self, "_graph_area"):
            self._graph_area.queue_draw()

    # ------------------------------------------------------------------
    # Graph coordinate helpers and drawing
    # ------------------------------------------------------------------
    def _graph_span(self) -> Tuple[float, float, float, float]:
        xmin, xmax, ymin, ymax = self._graph_view
        if xmax <= xmin:
            xmin, xmax = -10.0, 10.0
        if ymax <= ymin:
            ymin, ymax = -6.0, 6.0
        return xmin, xmax, ymin, ymax

    def _sample_row(self, row: _GraphRow, x: float, env: Dict[str, Any]) -> Optional[float]:
        if row.node is None:
            return None
        env["x"] = Scalar(float(x))
        try:
            value = row.node.eval(
                env,
                EvalContext(
                    angle_degrees=self._calc.angle_degrees,
                    ans=self._calc.ans,
                    record_steps=False,
                ),
            )
        except (CalcError, ZeroDivisionError, OverflowError, ValueError):
            return None
        if not isinstance(value, Scalar) or not value.is_real:
            return None
        result = float(value.real)
        if not math.isfinite(result):
            return None
        if abs(result) > 1e12:
            return None
        return result

    def _graph_env(self) -> Dict[str, Any]:
        return {name: Scalar(float(value)) for name, value in self._params.items()}

    def _plot_rect(self, width: float, height: float):
        return 0.0, 0.0, float(width), float(height)

    def _to_pixel(self, x: float, y: float, width: float, height: float):
        xmin, xmax, ymin, ymax = self._graph_span()
        px = (x - xmin) / (xmax - xmin) * width
        py = (ymax - y) / (ymax - ymin) * height
        return px, py

    def _to_world(self, px: float, py: float, width: float, height: float):
        xmin, xmax, ymin, ymax = self._graph_span()
        if width <= 0 or height <= 0:
            return xmin, ymax
        x = xmin + (px / width) * (xmax - xmin)
        y = ymax - (py / height) * (ymax - ymin)
        return x, y

    def _draw_graph(self, _area, cr, width: int, height: int) -> None:
        if width <= 1 or height <= 1:
            return
        try:
            self._paint_graph(
                cr, float(width), float(height), fill_background=True
            )
        except Exception:
            pass

    def _paint_graph(
        self,
        cr,
        width: float,
        height: float,
        fill_background: bool,
        show_trace: bool = True,
    ) -> None:
        import cairo

        palette = theme_palette(self.ctx.theme.mode, self.ctx.theme.accent)
        background = parse_hex_color(palette.get("--omnify-bg-alt", "#24262c"))
        foreground = parse_hex_color(palette.get("--omnify-fg", "#e7e9ee"))
        if fill_background:
            cr.set_source_rgba(background[0], background[1], background[2], 1.0)
            cr.paint()
        xmin, xmax, ymin, ymax = self._graph_span()
        if xmax <= xmin or ymax <= ymin:
            return

        def x_to_px(value: float) -> float:
            return (value - xmin) / (xmax - xmin) * width

        def y_to_px(value: float) -> float:
            return (ymax - value) / (ymax - ymin) * height

        dx = _nice_step(xmax - xmin, 8)
        dy = _nice_step(ymax - ymin, 6)

        cr.save()
        cr.rectangle(0.0, 0.0, width, height)
        cr.clip()
        cr.select_font_face(
            "Cantarell", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL
        )
        cr.set_font_size(10.0)

        # Minor grid at half-step, major grid at step, then axes.
        for fraction, alpha in ((0.5, 0.07), (1.0, 0.15)):
            cr.set_line_width(1.0)
            cr.set_source_rgba(foreground[0], foreground[1], foreground[2], alpha)
            step = dx * fraction
            start = math.floor(xmin / step) * step
            count = 0
            while start <= xmax + step * 0.01 and count < 200:
                px = x_to_px(start)
                cr.move_to(px, 0.0)
                cr.line_to(px, height)
                start += step
                count += 1
            step_y = dy * fraction
            start_y = math.floor(ymin / step_y) * step_y
            count = 0
            while start_y <= ymax + step_y * 0.01 and count < 200:
                py = y_to_px(start_y)
                cr.move_to(0.0, py)
                cr.line_to(width, py)
                start_y += step_y
                count += 1
            cr.stroke()

        axis_x = x_to_px(0.0) if xmin <= 0.0 <= xmax else None
        axis_y = y_to_px(0.0) if ymin <= 0.0 <= ymax else None
        cr.set_source_rgba(foreground[0], foreground[1], foreground[2], 0.42)
        cr.set_line_width(1.2)
        if axis_x is not None:
            cr.move_to(axis_x, 0.0)
            cr.line_to(axis_x, height)
        if axis_y is not None:
            cr.move_to(0.0, axis_y)
            cr.line_to(width, axis_y)
        cr.stroke()

        # Axis tick labels.
        cr.set_source_rgba(foreground[0], foreground[1], foreground[2], 0.62)
        start = math.floor(xmin / dx) * dx
        label_y = axis_y + 13.0 if axis_y is not None else height - 5.0
        label_y = max(12.0, min(height - 3.0, label_y))
        count = 0
        while start <= xmax + dx * 0.01 and count < 100:
            px = x_to_px(start)
            text = _tick_text(start)
            extents = cr.text_extents(text)
            cr.move_to(px - extents[2] / 2.0, label_y)
            cr.show_text(text)
            start += dx
            count += 1
        start_y = math.floor(ymin / dy) * dy
        label_x = axis_x + 5.0 if axis_x is not None else 5.0
        label_x = max(3.0, min(width - 45.0, label_x))
        count = 0
        while start_y <= ymax + dy * 0.01 and count < 100:
            py = y_to_px(start_y)
            if abs(start_y) > dy * 0.01:
                text = _tick_text(start_y)
                cr.move_to(label_x, py + 3.0)
                cr.show_text(text)
            start_y += dy
            count += 1

        # Curves.
        env = self._graph_env()
        for row in self._graph_rows:
            if row.node is None or not row.visible.get_active():
                continue
            rgb = parse_hex_color(row.color)
            cr.set_source_rgba(rgb[0], rgb[1], rgb[2], 0.96)
            cr.set_line_width(2.0)
            started = False
            previous_py: Optional[float] = None
            sample_count = max(2, min(2200, int(width / 2.0)))
            for index in range(sample_count + 1):
                px = width * index / sample_count
                x = xmin + (xmax - xmin) * index / sample_count
                y = self._sample_row(row, x, env)
                if y is None:
                    started = False
                    previous_py = None
                    continue
                py = y_to_px(y)
                if py < -height * 2.0 or py > height * 3.0:
                    started = False
                    previous_py = None
                    continue
                if (
                    not started
                    or previous_py is None
                    or abs(py - previous_py) > height * 1.4
                ):
                    cr.move_to(px, py)
                    started = True
                else:
                    cr.line_to(px, py)
                previous_py = py
            cr.stroke()

        # Legend.
        legend_rows = [
            row
            for row in self._graph_rows
            if row.visible.get_active()
            and row.node is not None
            and row.entry.get_text().strip()
        ][:7]
        if legend_rows:
            lines = []
            for row in legend_rows:
                text = _strip_graph_prefix(row.entry.get_text())
                if len(text) > 34:
                    text = text[:31] + "…"
                lines.append((row.color, text))
            max_width = max(
                (cr.text_extents(text)[2] for _color, text in lines),
                default=0.0,
            )
            box_width = min(width - 16.0, max_width + 38.0)
            box_height = len(lines) * 15.0 + 10.0
            cr.set_source_rgba(
                background[0], background[1], background[2], 0.82
            )
            cr.rectangle(8.0, 8.0, box_width, box_height)
            cr.fill()
            for index, (color, text) in enumerate(lines):
                rgb = parse_hex_color(color)
                line_y = 20.0 + index * 15.0
                cr.set_source_rgba(rgb[0], rgb[1], rgb[2], 1.0)
                cr.set_line_width(2.5)
                cr.move_to(14.0, line_y - 3.0)
                cr.line_to(28.0, line_y - 3.0)
                cr.stroke()
                cr.set_source_rgba(
                    foreground[0], foreground[1], foreground[2], 0.86
                )
                cr.move_to(33.0, line_y)
                cr.show_text(text)

        # Cursor tracer.
        if show_trace and self._trace is not None:
            trace_x, trace_y, trace_row = self._trace
            if trace_row in self._graph_rows and trace_row.visible.get_active():
                px = x_to_px(trace_x)
                py = y_to_px(trace_y)
                rgb = parse_hex_color(trace_row.color)
                cr.set_source_rgba(rgb[0], rgb[1], rgb[2], 0.45)
                cr.set_line_width(1.0)
                cr.set_dash([3.0, 4.0])
                cr.move_to(px, 0.0)
                cr.line_to(px, height)
                cr.stroke()
                cr.set_dash([])
                cr.set_source_rgba(rgb[0], rgb[1], rgb[2], 1.0)
                cr.arc(px, py, 4.0, 0.0, 2.0 * math.pi)
                cr.fill()
                label = f"x = {_tick_text(trace_x)}   y = {_tick_text(trace_y)}"
                extents = cr.text_extents(label)
                label_w = extents[2] + 12.0
                label_h = 21.0
                label_x = min(width - label_w - 4.0, max(4.0, px + 10.0))
                label_y = min(
                    height - label_h - 4.0, max(4.0, py - label_h - 8.0)
                )
                cr.set_source_rgba(
                    background[0], background[1], background[2], 0.92
                )
                cr.rectangle(label_x, label_y, label_w, label_h)
                cr.fill()
                cr.set_source_rgba(
                    foreground[0], foreground[1], foreground[2], 0.94
                )
                cr.move_to(label_x + 6.0, label_y + 14.0)
                cr.show_text(label)
        cr.restore()

    def _on_graph_motion(self, _controller, x: float, y: float) -> None:
        self._pointer = (float(x), float(y))
        if not hasattr(self, "_graph_area"):
            return
        width = self._graph_area.get_width()
        height = self._graph_area.get_height()
        if width <= 1 or height <= 1:
            return
        world_x, _world_y = self._to_world(x, y, width, height)
        env = self._graph_env()
        best_row = None
        best_y = None
        best_distance = float("inf")
        for row in self._graph_rows:
            if row.node is None or not row.visible.get_active():
                continue
            candidate = self._sample_row(row, world_x, env)
            if candidate is None:
                continue
            _px, py = self._to_pixel(world_x, candidate, width, height)
            distance = abs(py - y)
            if distance < best_distance:
                best_distance = distance
                best_row = row
                best_y = candidate
        if best_row is not None and best_y is not None:
            self._trace = (world_x, best_y, best_row)
        else:
            self._trace = None
        self._graph_area.queue_draw()

    def _on_graph_leave(self, _controller) -> None:
        self._pointer = None
        self._trace = None
        if hasattr(self, "_graph_area"):
            self._graph_area.queue_draw()

    def _on_graph_scroll(self, _controller, _dx: float, dy: float) -> bool:
        if not hasattr(self, "_graph_area") or abs(dy) < 1e-9:
            return False
        width = self._graph_area.get_width()
        height = self._graph_area.get_height()
        if width <= 1 or height <= 1:
            return False
        pointer = self._pointer
        if pointer is None:
            pointer = (width / 2.0, height / 2.0)
        factor = 0.88 if dy < 0 else 1.0 / 0.88
        self._zoom_at(pointer[0], pointer[1], factor, width, height)
        return True

    def _zoom_at(
        self, px: float, py: float, factor: float, width: float, height: float
    ) -> None:
        xmin, xmax, ymin, ymax = self._graph_span()
        world_x, world_y = self._to_world(px, py, width, height)
        fx = max(0.0, min(1.0, px / max(1.0, width)))
        fy = max(0.0, min(1.0, py / max(1.0, height)))
        new_x = (xmax - xmin) * factor
        new_y = (ymax - ymin) * factor
        self._graph_view = (
            world_x - fx * new_x,
            world_x + (1.0 - fx) * new_x,
            world_y - (1.0 - fy) * new_y,
            world_y + fy * new_y,
        )
        self._trace = None
        self._graph_area.queue_draw()

    def _on_graph_drag_begin(self, _gesture, _x: float, _y: float) -> None:
        self._drag_ranges = self._graph_span()

    def _on_graph_drag_update(self, _gesture, offset_x: float, offset_y: float) -> None:
        if self._drag_ranges is None or not hasattr(self, "_graph_area"):
            return
        width = self._graph_area.get_width()
        height = self._graph_area.get_height()
        if width <= 1 or height <= 1:
            return
        xmin, xmax, ymin, ymax = self._drag_ranges
        shift_x = -offset_x / width * (xmax - xmin)
        shift_y = offset_y / height * (ymax - ymin)
        self._graph_view = (
            xmin + shift_x,
            xmax + shift_x,
            ymin + shift_y,
            ymax + shift_y,
        )
        self._trace = None
        self._graph_area.queue_draw()

    def _fit_graph(self) -> None:
        if not hasattr(self, "_graph_area"):
            return
        width = max(320, self._graph_area.get_width() or 620)
        height = max(240, self._graph_area.get_height() or 460)
        xmin, xmax, _ymin, _ymax = self._graph_span()
        samples: List[float] = []
        env = self._graph_env()
        for row in self._graph_rows:
            if row.node is None or not row.visible.get_active():
                continue
            for index in range(900):
                x = xmin + (xmax - xmin) * index / 899.0
                y = self._sample_row(row, x, env)
                if y is not None and abs(y) < 1e7:
                    samples.append(y)
        if samples:
            samples.sort()
            low = samples[int(len(samples) * 0.04)]
            high = samples[min(len(samples) - 1, int(len(samples) * 0.96))]
            if high - low < 1e-9:
                high = low + 1.0
            padding = max(0.1 * (high - low), 0.2)
            self._graph_view = (xmin, xmax, low - padding, high + padding)
        else:
            self._graph_view = (xmin, xmax, -6.0, 6.0)
        self._trace = None
        self._graph_area.queue_draw()
        self._notify("Graph fitted")

    def _reset_graph_view(self) -> None:
        self._graph_view = (-10.0, 10.0, -6.0, 6.0)
        self._trace = None
        if hasattr(self, "_graph_area"):
            self._graph_area.queue_draw()
        self._notify("Graph view reset")

    # ------------------------------------------------------------------
    # Graph export / copy
    # ------------------------------------------------------------------
    def _render_graph_surface(self, width: int = 1600, height: int = 1000):
        import cairo

        surface = cairo.ImageSurface(
            cairo.FORMAT_ARGB32, int(width), int(height)
        )
        cr = cairo.Context(surface)
        self._paint_graph(
            cr,
            float(width),
            float(height),
            fill_background=True,
            show_trace=False,
        )
        surface.flush()
        return surface

    def _export_graph(self) -> None:
        try:
            surface = self._render_graph_surface()
        except Exception as exc:
            self._notify(f"Could not render graph: {exc}")
            return
        self._export_surface = surface
        try:
            from gi.repository import GLib, Gtk

            dialog = Gtk.FileDialog()
            dialog.set_title("Export graph as PNG")
            dialog.set_initial_name(
                time.strftime("omnify-graph-%Y%m%d-%H%M%S.png")
            )
            parent = self._root.get_root()
            dialog.save(parent, None, self._on_export_graph_selected)
        except Exception:
            self._export_graph_direct()

    def _on_export_graph_selected(self, dialog, result) -> None:
        try:
            from gi.repository import GLib

            file = dialog.save_finish(result)
        except Exception:
            self._export_surface = None
            return
        surface = getattr(self, "_export_surface", None)
        self._export_surface = None
        if surface is None:
            return
        path = file.get_path() if file is not None else None
        if not path:
            self._notify("Choose a local folder to export the PNG")
            return
        try:
            surface.write_to_png(path)
        except Exception as exc:
            self._notify(f"Export failed: {exc}")
            return
        self._notify(f"Graph exported to {path}")

    def _export_graph_direct(self) -> None:
        surface = getattr(self, "_export_surface", None)
        if surface is None:
            try:
                surface = self._render_graph_surface()
            except Exception as exc:
                self._notify(f"Could not render graph: {exc}")
                return
        self._export_surface = None
        pictures = Path.home() / "Pictures"
        try:
            pictures.mkdir(parents=True, exist_ok=True)
            path = pictures / time.strftime("omnify-graph-%Y%m%d-%H%M%S.png")
            surface.write_to_png(str(path))
            self._notify(f"Graph exported to {path}")
        except Exception as exc:
            self._notify(f"Export failed: {exc}")

    def _copy_graph(self) -> None:
        try:
            from gi.repository import Gdk, GLib

            surface = self._render_graph_surface(1200, 800)
            buffer = io.BytesIO()
            surface.write_to_png(buffer)
            data = buffer.getvalue()
            provider = Gdk.ContentProvider.new_for_bytes(
                "image/png", GLib.Bytes.new(data)
            )
            clipboard = self._graph_area.get_clipboard()
            clipboard.set_content(provider)
            self._notify("Graph copied to the clipboard")
        except Exception as exc:
            self._notify(f"Could not copy graph: {exc}")
