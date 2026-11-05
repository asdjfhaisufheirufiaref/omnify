"""Headless tests for the advanced calculator engine.

The GUI lives in ``omnify.modules.calculator``; everything here is pure
Python and can run without a display.
"""

import math
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from omnify.modules.calc_engine import (  # noqa: E402
    Calculator,
    CalcError,
    Matrix,
    Scalar,
    EvalContext,
    format_number,
    node_variables,
)


class TestPrecedenceAndSyntax(unittest.TestCase):
    def setUp(self):
        self.c = Calculator(precision=10)

    def check(self, expression, expected):
        self.assertAlmostEqual(
            self.c.evaluate(expression), expected, msg=repr(expression)
        )

    def test_power_is_right_associative(self):
        self.check("2^3^2", 512)
        self.check("2^(3^2)", 512)
        self.check("(2^3)^2", 64)

    def test_unary_minus_is_below_power(self):
        self.check("-2^2", -4)
        self.check("(-2)^2", 4)
        self.check("2^-2", 0.25)
        self.check("2--3", 5)
        self.check("2*-3", -6)

    def test_implicit_multiplication(self):
        self.check("2(3+4)", 14)
        self.check("2pi", 2 * math.pi)
        self.check("2(3)(4)", 24)

    def test_postfix(self):
        self.check("5!", 120)
        self.check("0!", 1)
        self.check("50%", 0.5)
        self.check("200%*3", 6)
        self.check("10%3", 1)

    def test_functions_without_parentheses(self):
        self.c.angle_degrees = True
        self.check("sin 30", 0.5)
        self.check("sin(30)cos(60)", 0.25)

    def test_degree_symbols(self):
        self.c.angle_degrees = True
        self.check("sin(30°)", 0.5)
        self.check("sin 30°", 0.5)
        self.check("cos(60°)+sin(30°)", 1.0)

    def test_variables_supplied(self):
        result = self.c.evaluate_detailed("a^2+b", {"a": 3, "b": 1})
        self.assertEqual(result.exact_text, "10")

    def test_bad_syntax(self):
        for expression in (
            "",
            "(",
            "2+",
            "2+*3",
            "1 2",
            "foo(1)",
            "sin()",
            "sqrt(",
        ):
            with self.assertRaises(CalcError, msg=repr(expression)):
                self.c.evaluate(expression)


class TestExactAndComplex(unittest.TestCase):
    def setUp(self):
        self.c = Calculator(precision=10, exact_mode=True)

    def test_exact_fractions(self):
        result = self.c.evaluate_detailed("1/3 + 1/6")
        self.assertEqual(result.exact_text, "0.5")
        self.assertEqual(result.decimal_text, "")
        result = self.c.evaluate_detailed("1/3")
        self.assertEqual(result.exact_text, "1/3")
        self.assertEqual(result.decimal_text, "0.3333333333")

    def test_terminating_decimals_are_exact(self):
        self.assertEqual(self.c.evaluate_detailed("0.1+0.2").exact_text, "0.3")
        self.assertEqual(self.c.evaluate_detailed("2/8").exact_text, "0.25")
        self.assertEqual(self.c.evaluate_detailed("5/2").exact_text, "2.5")

    def test_complex_arithmetic(self):
        cases = {
            "i^2": "-1",
            "(1+i)*(1-i)": "2",
            "sqrt(-4)": "2i",
            "(1+i)^2": "2i",
            "conj(3+4i)": "3 - 4i",
            "re(3+4i)": "3",
            "im(3+4i)": "4",
            "abs(3+4i)": "5",
        }
        for expression, expected in cases.items():
            self.assertEqual(
                self.c.evaluate_detailed(expression).exact_text,
                expected,
                msg=expression,
            )

    def test_complex_fractional_powers(self):
        self.assertEqual(self.c.evaluate_detailed("(-4)^(1/2)").exact_text, "2i")
        self.assertEqual(self.c.evaluate_detailed("(-4)^(3/2)").exact_text, "-8i")
        self.assertEqual(self.c.evaluate_detailed("(-4)^(-1/2)").exact_text, "-0.5i")
        self.assertEqual(self.c.evaluate_detailed("(-4)^(-3/2)").exact_text, "0.125i")

    def test_degree_phase(self):
        c = Calculator(angle_degrees=True)
        self.assertEqual(c.evaluate_detailed("arg(1+i)").exact_text, "45")
        c.angle_degrees = False
        self.assertAlmostEqual(c.evaluate("arg(1+i)"), math.pi / 4)


class TestMatrices(unittest.TestCase):
    def setUp(self):
        self.c = Calculator(exact_mode=True)

    def value(self, expression):
        return self.c.evaluate_detailed(expression).value

    def test_determinant(self):
        self.assertEqual(
            self.c.evaluate_detailed("det([[1,2],[3,4]])").exact_text, "-2"
        )
        self.assertEqual(
            self.c.evaluate_detailed(
                "det([[1,2,3],[0,1,4],[5,6,0]])"
            ).exact_text,
            "1",
        )

    def test_inverse(self):
        result = self.value("inv([[1,2],[3,4]])")
        self.assertIsInstance(result, Matrix)
        self.assertEqual(result.format(), "[[-2, 1], [1.5, -0.5]]")
        product = self.value("[[1,2],[3,4]]*inv([[1,2],[3,4]])")
        self.assertEqual(product.format(), "[[1, 0], [0, 1]]")

    def test_multiply_and_transpose(self):
        product = self.value("[[1,2],[3,4]]*[[5,6],[7,8]]")
        self.assertEqual(product.format(), "[[19, 22], [43, 50]]")
        transposed = self.value("transpose([[1,2,3],[4,5,6]])")
        self.assertEqual(transposed.format(), "[[1, 4], [2, 5], [3, 6]]")

    def test_rref_and_singular(self):
        reduced = self.value("rref([[1,2,3],[2,4,7]])")
        self.assertEqual(reduced.format(), "[[1, 2, 0], [0, 0, 1]]")
        with self.assertRaises(CalcError):
            self.c.evaluate("inv([[1,2],[2,4]])")

    def test_linear_system(self):
        result = self.value("solve([[2,1],[1,-1]],[5,1])")
        self.assertEqual(result.format(), "[[2], [1]]")

    def test_matrix_addition_and_scaling(self):
        self.assertEqual(
            self.value("2*[[1,2],[3,4]]").format(),
            "[[2, 4], [6, 8]]",
        )
        self.assertEqual(
            self.value("[[1,2],[3,4]]/2").format(),
            "[[0.5, 1], [1.5, 2]]",
        )
        self.assertEqual(
            self.value("[[1,2],[3,4]]+[[1,0],[0,1]]").format(),
            "[[2, 2], [3, 5]]",
        )


class TestSolving(unittest.TestCase):
    def setUp(self):
        self.c = Calculator(exact_mode=True, angle_degrees=False)

    def test_linear_and_quadratic(self):
        result = self.c.evaluate_detailed("solve(2x+3=7,x)")
        self.assertEqual(result.exact_text, "x = 2")
        result = self.c.evaluate_detailed("solve(x^2=4,x)")
        self.assertEqual(result.exact_text, "x = 2, -2")
        result = self.c.evaluate_detailed("solve(x^2+2x+1=0,x)")
        self.assertEqual(result.exact_text, "x = -1")
        result = self.c.evaluate_detailed("solve(x^2+1=0,x)")
        self.assertEqual(result.exact_text, "x = i, -i")

    def test_identity_and_contradiction(self):
        with self.assertRaises(CalcError):
            self.c.evaluate("solve(2=2,x)")
        with self.assertRaises(CalcError):
            self.c.evaluate("solve(2=3,x)")

    def test_solution_bounds(self):
        self.assertEqual(
            self.c.evaluate_detailed("solve(x^2=4,x,0,10)").exact_text,
            "x = 2",
        )
        self.assertEqual(
            self.c.evaluate_detailed("solve(x^2=4,x,-10,0)").exact_text,
            "x = -2",
        )
        with self.assertRaises(CalcError):
            self.c.evaluate("solve(x^2=4,x,3,4)")

    def test_numeric_fallback(self):
        value = self.c.evaluate_detailed("solve(x^3=8,x)").value
        roots = [float(v.real) for v in value.values]
        self.assertTrue(any(abs(root - 2) < 1e-8 for root in roots))

    def test_numeric_trig(self):
        value = self.c.evaluate_detailed("solve(sin(x)=0.5,x)").value
        roots = [float(v.real) for v in value.values]
        self.assertTrue(any(abs(root - math.pi / 6) < 1e-6 for root in roots))

    def test_top_level_equation_is_rejected(self):
        with self.assertRaises(CalcError):
            self.c.evaluate("x^2=4")


class TestCalculus(unittest.TestCase):
    def setUp(self):
        self.c = Calculator(exact_mode=True, angle_degrees=True)

    def test_derivatives(self):
        cases = {
            "diff(x^3,x)": "3*x^2",
            "diff(sin(x),x)": "cos(x)",
            "diff(cos(x),x)": "-sin(x)",
            "diff(ln(x),x)": "1/x",
            "diff(exp(x),x)": "exp(x)",
            "diff(sqrt(x),x)": "1/(2*sqrt(x))",
        }
        for expression, expected in cases.items():
            self.assertEqual(
                self.c.evaluate_detailed(expression).exact_text,
                expected,
                msg=expression,
            )

    def test_derivative_at_point(self):
        result = self.c.evaluate_detailed("diff(x^2,x,3)")
        self.assertEqual(result.exact_text, "6")

    def test_indefinite_integrals(self):
        cases = {
            "integral(x^2,x)": "(1/3)*x^3 + C",
            "integral(2*x+1,x)": "x^2 + x + C",
            "integral(sin(x),x)": "-cos(x) + C",
            "integral(exp(x),x)": "exp(x) + C",
            "integral(1/x,x)": "ln(abs(x)) + C",
        }
        for expression, expected in cases.items():
            self.assertEqual(
                self.c.evaluate_detailed(expression).exact_text,
                expected,
                msg=expression,
            )

    def test_definite_integrals_use_radians(self):
        self.assertEqual(
            self.c.evaluate_detailed("integral(sin(x),x,0,pi)").exact_text,
            "2",
        )
        self.assertEqual(
            self.c.evaluate_detailed("integral(x^2,x,0,1)").exact_text,
            "1/3",
        )
        self.assertEqual(
            self.c.evaluate_detailed("integral(cos(x),x,0,pi)").exact_text,
            "0",
        )

    def test_numeric_integral_fallback(self):
        result = self.c.evaluate_detailed("integral(sin(x^2),x,0,1)")
        self.assertAlmostEqual(self.c.evaluate("integral(sin(x^2),x,0,1)"), 0.3102683018, places=7)


class TestUnits(unittest.TestCase):
    def setUp(self):
        self.c = Calculator(exact_mode=True)

    def test_length(self):
        self.assertEqual(self.c.evaluate_detailed("5 km to m").exact_text, "5000 m")
        self.assertEqual(self.c.evaluate_detailed("2 in to cm").exact_text, "5.08 cm")
        self.assertEqual(
            self.c.evaluate_detailed("1 mile to km").exact_text,
            "1.609344 km",
        )

    def test_mass_time_volume(self):
        self.assertEqual(self.c.evaluate_detailed("1 h to min").exact_text, "60 min")
        self.assertEqual(self.c.evaluate_detailed("1 L to mL").exact_text, "1000 mL")
        self.assertAlmostEqual(
            self.c.evaluate("1 lb to kg"), 0.45359237, places=8
        )

    def test_temperature(self):
        self.assertEqual(self.c.evaluate_detailed("0 C to F").exact_text, "32 °F")
        self.assertEqual(
            self.c.evaluate_detailed("100 C to F").exact_text, "212 °F"
        )
        self.assertEqual(
            self.c.evaluate_detailed("0 K to C").exact_text, "-273.15 °C"
        )
        self.assertEqual(
            self.c.evaluate_detailed("32 F to C").exact_text, "0 °C"
        )

    def test_speed_angle_data(self):
        self.assertEqual(
            self.c.evaluate_detailed("1 m/s to km/h").exact_text, "3.6 km/h"
        )
        self.assertAlmostEqual(
            self.c.evaluate("180 deg to rad"), math.pi, places=10
        )
        self.assertEqual(
            self.c.evaluate_detailed("1 GiB to MiB").exact_text, "1024 MiB"
        )

    def test_in_as_both_inch_and_conversion(self):
        self.assertEqual(
            self.c.evaluate_detailed("5 in in cm").exact_text, "12.7 cm"
        )

    def test_convert_function(self):
        self.assertEqual(self.c.evaluate_detailed('convert(5,"km","m")').exact_text, "5000")

    def test_bad_units(self):
        for expression in (
            "5 m to s",
            "5 nonsense to m",
            "5 m to nonsense",
            "5 C to m",
        ):
            with self.assertRaises(CalcError, msg=repr(expression)):
                self.c.evaluate(expression)


class TestFormattingAndMisc(unittest.TestCase):
    def test_precision_zero(self):
        self.assertEqual(format_number(100.0, 0, exact=False), "100")
        self.assertEqual(format_number(99.6, 0, exact=False), "100")

    def test_precision_rounding(self):
        self.assertEqual(format_number(1 / 3, 4, exact=False), "0.3333")
        self.assertEqual(format_number(1 / 3, 6, exact=False), "0.333333")

    def test_compatibility_evaluate(self):
        c = Calculator(angle_degrees=True)
        self.assertAlmostEqual(c.evaluate("2+2"), 4.0)
        self.assertAlmostEqual(c.evaluate("sin(90)"), 1.0)
        with self.assertRaises(CalcError):
            c.evaluate("sqrt()")

    def test_numeric_evaluator(self):
        c = Calculator()
        self.assertAlmostEqual(c.evaluate_numeric("sin(x)", {"x": math.pi / 2}), 1.0)
        self.assertIsNone(c.evaluate_numeric("sqrt(x)", {"x": -1}))
        self.assertIsNone(c.evaluate_numeric("1/x", {"x": 0}))

    def test_node_variables(self):
        node = Calculator().parse("a*sin(x)+b")
        self.assertEqual(node_variables(node), {"a", "b", "x"})
        node = Calculator().parse("y=sin(x)")
        self.assertEqual(node_variables(node), {"x", "y"})

    def test_ans(self):
        c = Calculator()
        c.evaluate_detailed("2+3")
        self.assertEqual(c.evaluate_detailed("Ans*2").exact_text, "10")

    def test_large_integer_root(self):
        c = Calculator()
        # 1000003^3; a float root guess would be off by more than 2.
        value = 1000003 ** 3
        result = c.evaluate_detailed(f"root(3,{value})").exact_text
        self.assertEqual(result, "1000003")


if __name__ == "__main__":
    unittest.main(verbosity=2)
