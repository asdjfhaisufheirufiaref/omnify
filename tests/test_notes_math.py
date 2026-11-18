"""Tests for the offline LaTeX subset used by Notes."""

from __future__ import annotations

import unittest

from omnify.modules.notes_engine import Note
from omnify.modules.notes_math import (
    latex_to_html,
    latex_to_pango,
    replace_inline,
)
from omnify.modules.notes_render import (
    inline_pango,
    note_to_html,
    parse_markdown,
)


class LatexSymbolTest(unittest.TestCase):
    def test_greek_letters_and_arrows(self) -> None:
        markup = latex_to_pango(r"\alpha + \beta \to \infty")
        self.assertIn("α", markup)
        self.assertIn("β", markup)
        self.assertIn("→", markup)
        self.assertIn("∞", markup)

    def test_superscripts_and_subscripts(self) -> None:
        markup = latex_to_pango("x^2 + y_1")
        self.assertIn("x<sup>2</sup>", markup)
        self.assertIn("y<sub>1</sub>", markup)

    def test_fraction(self) -> None:
        markup = latex_to_pango(r"\frac{1}{2}")
        self.assertIn("<sup>1</sup>⁄<sub>2</sub>", markup)
        compound = latex_to_pango(r"\frac{a+b}{c}")
        self.assertIn("<sup>(a+b)</sup>", compound)

    def test_square_and_cube_roots(self) -> None:
        markup = latex_to_pango(r"\sqrt{x+1}")
        self.assertIn('√<span overline="single">x+1</span>', markup)
        cube = latex_to_pango(r"\sqrt[3]{x}")
        self.assertIn('∛<span overline="single">x</span>', cube)
        fourth = latex_to_pango(r"\sqrt[4]{x}")
        self.assertIn("∜", fourth)

    def test_text_is_escaped(self) -> None:
        markup = latex_to_pango(r"\text{a < b & c}")
        self.assertIn("a &lt; b &amp; c", markup)

    def test_blackboard_bold(self) -> None:
        markup = latex_to_pango(r"\mathbb{R} \subset \mathbb{C}")
        self.assertIn("ℝ", markup)
        self.assertIn("ℂ", markup)
        self.assertIn("⊂", markup)

    def test_mathbf_and_mathrm(self) -> None:
        self.assertIn("<b>v</b>", latex_to_pango(r"\mathbf{v}"))
        self.assertIn("<span style=\"normal\">kg</span>", latex_to_pango(r"\mathrm{kg}"))

    def test_matrix(self) -> None:
        markup = latex_to_pango(
            r"\begin{pmatrix} a & b \\ c & d \end{pmatrix}"
        )
        self.assertTrue(markup.startswith("("))
        self.assertIn("a  b ; c  d", markup)
        self.assertTrue(markup.endswith(")"))

    def test_unknown_command_stays_visible(self) -> None:
        markup = latex_to_pango(r"\unknown{x}")
        self.assertIn(r"\unknown{x}", markup)

    def test_html_output_escapes_text(self) -> None:
        markup = latex_to_html(r"\text{a < b}")
        self.assertIn("a &lt; b", markup)
        fraction = latex_to_html(r"\frac{1}{2}")
        self.assertIn("<sup>1</sup>⁄<sub>2</sub>", fraction)


class InlineMathTest(unittest.TestCase):
    def test_replaces_both_delimiters(self) -> None:
        rendered = replace_inline(
            r"Math $x^2$ and \(y_1\).", lambda source: f"[{source}]"
        )
        self.assertEqual(rendered, "Math [x^2] and [y_1].")

    def test_currency_is_left_alone(self) -> None:
        text = "Costs $5 and $10, then $x$."
        rendered = replace_inline(text, lambda source: f"[{source}]")
        self.assertIn("$5 and $10", rendered)
        self.assertIn("[x]", rendered)

    def test_escaped_dollar_is_not_math(self) -> None:
        rendered = replace_inline(r"Price \$5 and $x$", lambda source: "M")
        self.assertIn(r"\$5", rendered)
        self.assertTrue(rendered.endswith("M"))

    def test_pango_inline_math_is_not_emphasised(self) -> None:
        markup = inline_pango("Formula $x_1 + x_2$ stays math.")
        self.assertIn("<sub>1</sub>", markup)
        self.assertIn("<sub>2</sub>", markup)
        self.assertNotIn("<i>", markup)

    def test_code_span_keeps_tex_verbatim(self) -> None:
        markup = inline_pango("`$x^2$`")
        self.assertIn("$x^2$", markup)
        self.assertNotIn("<sup>", markup)


class DisplayMathTest(unittest.TestCase):
    def test_dollar_block(self) -> None:
        blocks = parse_markdown("Before\n\n$$\n\\frac{a}{b}\n$$\n\nAfter")
        self.assertEqual(
            [block.kind for block in blocks],
            ["paragraph", "math", "paragraph"],
        )
        self.assertEqual(blocks[1].text, r"\frac{a}{b}")

    def test_bracket_block(self) -> None:
        blocks = parse_markdown("\\[\nx^2\n\\]")
        self.assertEqual([block.kind for block in blocks], ["math"])
        self.assertEqual(blocks[0].text, "x^2")

    def test_single_line_block(self) -> None:
        blocks = parse_markdown("$$E=mc^2$$")
        self.assertEqual([block.kind for block in blocks], ["math"])
        self.assertEqual(blocks[0].text, "E=mc^2")

    def test_unclosed_block_stays_paragraph(self) -> None:
        blocks = parse_markdown("$$\nx^2\nstill text")
        self.assertNotIn("math", [block.kind for block in blocks])
        combined = "\n".join(block.text for block in blocks)
        self.assertIn("still text", combined)

    def test_html_export_renders_display_math(self) -> None:
        note = Note(
            label="Physics",
            contents="Inline $x^2$ works.\n\n$$\n\\frac{a}{b}\n$$",
        )
        html = note_to_html(note)
        self.assertIn('class="math"', html)
        self.assertIn("<sup>2</sup>", html)
        self.assertIn('class="math math-display"', html)
        self.assertIn("⁄", html)

    def test_html_export_keeps_literal_text_safe(self) -> None:
        note = Note(label="Safe", contents="$$\\text{a < b}$$")
        html = note_to_html(note)
        self.assertIn("a &lt; b", html)
        self.assertNotIn("a < b", html)


if __name__ == "__main__":
    unittest.main(verbosity=2)
