"""Headless tests for the calculator engine.

Run with:  python3 -m unittest omnify.tests.test_calculator
"""

import math
import os
import sys
import unittest

# Make sure the package is importable when run directly.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from omnify.modules.calculator import Calculator, CalcError, _format_number  # noqa: E402


class TestCalculator(unittest.TestCase):
    def setUp(self):
        self.c = Calculator(angle_degrees=True)

    def check(self, expr, expected):
        self.assertAlmostEqual(self.c.evaluate(expr), expected, msg=repr(expr))

    def test_basics(self):
        self.check("2+2", 4)
        self.check("2+2*3", 8)
        self.check("(2+2)*3", 12)
        self.check("10/4", 2.5)
        self.check("2^10", 1024)

    def test_precedence_and_assoc(self):
        self.check("2^3^2", 512)  # right associative
        self.check("-3+5", 2)
        self.check("10%3", 1)

    def test_functions(self):
        self.check("sqrt(16)", 4)
        self.check("abs(-5)", 5)
        self.assertAlmostEqual(self.c.evaluate("pi"), math.pi)

    def test_trig_degrees(self):
        self.check("sin(90)", 1.0)
        self.check("cos(0)", 1.0)
        self.check("tan(45)", 1.0)

    def test_scientific_notation(self):
        self.check("1.5e2", 150)
        self.check("1.5e2+1", 151)

    def test_division_by_zero(self):
        with self.assertRaises(CalcError):
            self.c.evaluate("100/0")

    def test_modulo_by_zero(self):
        with self.assertRaises(CalcError):
            self.c.evaluate("10%0")

    def test_bad_syntax(self):
        for bad in ["((", "2+", "2+*3", "", "foo(1)", "1 2"]:
            with self.assertRaises(CalcError, msg=repr(bad)):
                self.c.evaluate(bad)


class TestFormat(unittest.TestCase):
    def test_int(self):
        self.assertEqual(_format_number(1024.0), "1024")

    def test_float(self):
        self.assertEqual(_format_number(2.5), "2.5")


if __name__ == "__main__":
    unittest.main(verbosity=2)
