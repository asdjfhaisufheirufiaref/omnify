"""Pure math engine for the Omnify calculator.

The engine is deliberately independent of GTK so it can be tested headlessly.
It supports exact rational arithmetic, complex numbers, matrices, symbolic
differentiation/integration, equation solving, unit conversion and a
Desmos-style expression parser with implicit multiplication.

The parser is a hand-written recursive-descent parser; no ``eval`` is used.
Numbers are represented by :class:`Scalar` values whose real and imaginary
parts are :class:`fractions.Fraction` where possible and ``float`` otherwise.
"""

from __future__ import annotations

import cmath
import math
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation, localcontext
from fractions import Fraction
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Union

Number = Union[Fraction, float]


class CalcError(Exception):
    """Raised for invalid expressions, domains and unsupported operations."""


# ---------------------------------------------------------------------------
# Numeric helpers
# ---------------------------------------------------------------------------
def _as_number(value: Any) -> Number:
    """Convert an int/float/Fraction/Decimal into our number representation."""
    if isinstance(value, Fraction):
        return value
    if isinstance(value, bool):
        return Fraction(int(value))
    if isinstance(value, int):
        return Fraction(value)
    if isinstance(value, Decimal):
        return Fraction(value)
    if isinstance(value, float):
        if value == 0.0:
            return Fraction(0)
        return value
    if isinstance(value, str):
        try:
            return Fraction(Decimal(value))
        except (InvalidOperation, ValueError):
            raise CalcError(f"Invalid number '{value}'")
    raise CalcError("Expected a number")


def _is_exact_number(value: Number) -> bool:
    return isinstance(value, Fraction)


def _num_add(left: Number, right: Number) -> Number:
    if isinstance(left, Fraction) and isinstance(right, Fraction):
        return left + right
    return float(left) + float(right)


def _num_sub(left: Number, right: Number) -> Number:
    if isinstance(left, Fraction) and isinstance(right, Fraction):
        return left - right
    return float(left) - float(right)


def _num_mul(left: Number, right: Number) -> Number:
    if isinstance(left, Fraction) and isinstance(right, Fraction):
        return left * right
    return float(left) * float(right)


def _num_div(left: Number, right: Number) -> Number:
    if right == 0:
        raise CalcError("Division by zero")
    if isinstance(left, Fraction) and isinstance(right, Fraction):
        return left / right
    return float(left) / float(right)


def _num_neg(value: Number) -> Number:
    return -value


def _num_abs(value: Number) -> Number:
    return abs(value)


def _num_sign(value: Number) -> int:
    if value > 0:
        return 1
    if value < 0:
        return -1
    return 0


def _num_is_zero(value: Number) -> bool:
    return value == 0


def _num_sqrt(value: Number) -> Scalar:
    """Square root of a non-negative real number (exact when possible)."""
    if isinstance(value, Fraction):
        if value < 0:
            raise CalcError("Square root of a negative number")
        numerator = _integer_root(value.numerator, 2)
        denominator = _integer_root(value.denominator, 2)
        if numerator is not None and denominator is not None:
            return Scalar(Fraction(numerator, denominator))
        return Scalar(math.sqrt(float(value)))
    return Scalar(math.sqrt(float(value)))


def _integer_root(value: int, degree: int) -> Optional[int]:
    """Exact integer *degree*-th root, or ``None``."""
    if degree <= 0 or value < 0:
        return None
    if value in (0, 1):
        return value
    if degree == 1:
        return value
    low = 0
    high = 1
    while high ** degree < value:
        high *= 2
    while low <= high:
        middle = (low + high) // 2
        powered = middle ** degree
        if powered == value:
            return middle
        if powered < value:
            low = middle + 1
        else:
            high = middle - 1
    return None


def _rational_power_exact(base: Fraction, exponent: Fraction) -> Optional[Scalar]:
    """Try to compute ``base ** exponent`` with exact rational/complex parts."""
    if exponent.denominator == 1:
        power = int(exponent)
        if base == 0 and power < 0:
            raise CalcError("Division by zero")
        return Scalar(base ** power)
    numerator = exponent.numerator
    degree = exponent.denominator
    if degree > 64 or abs(numerator) > 64:
        return None
    if base < 0:
        if degree % 2 == 0:
            # Exact complex roots are only practical for square roots.
            if degree == 2:
                magnitude = _rational_power_exact(
                    -base, Fraction(abs(numerator), 2)
                )
                if magnitude is None or not magnitude.is_real:
                    return None
                phase = abs(numerator) % 4
                imaginary_sign = 1 if phase == 1 else -1
                result = magnitude.mul(
                    Scalar(Fraction(0), Fraction(imaginary_sign))
                )
                if numerator < 0:
                    result = Scalar(Fraction(1)).div(result)
                return result
            return None
        if degree % 2 == 1:
            root_value = _rational_power_exact(-base, Fraction(1, degree))
            if root_value is None:
                return None
            negative = Scalar(-root_value.real, -root_value.imag)
            result = Scalar(Fraction(1))
            for _ in range(abs(numerator)):
                result = result.mul(negative)
            if numerator < 0:
                result = Scalar(Fraction(1)).div(result)
            return result
        return None
    root_n = _integer_root(base.numerator, degree)
    root_d = _integer_root(base.denominator, degree)
    if root_n is None or root_d is None:
        return None
    root = Scalar(Fraction(root_n, root_d))
    result = Scalar(Fraction(1))
    for _ in range(abs(numerator)):
        result = result.mul(root)
    if numerator < 0:
        result = Scalar(Fraction(1)).div(result)
    return result


# ---------------------------------------------------------------------------
# Scalar values
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Scalar:
    """A real or complex number with exact rational parts when possible."""

    real: Number = Fraction(0)
    imag: Number = Fraction(0)

    def __post_init__(self) -> None:
        object.__setattr__(self, "real", _as_number(self.real))
        object.__setattr__(self, "imag", _as_number(self.imag))

    # -- properties ----------------------------------------------------
    @property
    def is_exact(self) -> bool:
        return _is_exact_number(self.real) and _is_exact_number(self.imag)

    @property
    def is_real(self) -> bool:
        return _num_is_zero(self.imag)

    @property
    def is_zero(self) -> bool:
        return _num_is_zero(self.real) and _num_is_zero(self.imag)

    @property
    def is_integer(self) -> bool:
        return (
            self.is_real
            and isinstance(self.real, Fraction)
            and self.real.denominator == 1
        )

    def as_float(self) -> float:
        if self.is_real:
            return float(self.real)
        raise CalcError("The result is complex, not a real number")

    def as_complex(self) -> complex:
        return complex(float(self.real), float(self.imag))

    # -- arithmetic ----------------------------------------------------
    def add(self, other: "Scalar") -> "Scalar":
        return Scalar(
            _num_add(self.real, other.real),
            _num_add(self.imag, other.imag),
        )

    def sub(self, other: "Scalar") -> "Scalar":
        return Scalar(
            _num_sub(self.real, other.real),
            _num_sub(self.imag, other.imag),
        )

    def mul(self, other: "Scalar") -> "Scalar":
        if self.is_real and other.is_real:
            return Scalar(_num_mul(self.real, other.real))
        return Scalar(
            _num_sub(
                _num_mul(self.real, other.real),
                _num_mul(self.imag, other.imag),
            ),
            _num_add(
                _num_mul(self.real, other.imag),
                _num_mul(self.imag, other.real),
            ),
        )

    def div(self, other: "Scalar") -> "Scalar":
        if other.is_zero:
            raise CalcError("Division by zero")
        if self.is_real and other.is_real:
            return Scalar(_num_div(self.real, other.real))
        denominator = _num_add(
            _num_mul(other.real, other.real),
            _num_mul(other.imag, other.imag),
        )
        real = _num_div(
            _num_add(
                _num_mul(self.real, other.real),
                _num_mul(self.imag, other.imag),
            ),
            denominator,
        )
        imag = _num_div(
            _num_sub(
                _num_mul(self.imag, other.real),
                _num_mul(self.real, other.imag),
            ),
            denominator,
        )
        return Scalar(real, imag)

    def neg(self) -> "Scalar":
        return Scalar(_num_neg(self.real), _num_neg(self.imag))

    def abs(self) -> "Scalar":
        if self.is_real:
            return Scalar(_num_abs(self.real))
        return _num_sqrt(
            _num_add(
                _num_mul(self.real, self.real),
                _num_mul(self.imag, self.imag),
            )
        )

    def pow(self, other: "Scalar") -> "Scalar":
        if other.is_zero:
            return Scalar(Fraction(1))
        if self.is_zero:
            if other.is_real and not other.imag and _num_sign(other.real) < 0:
                raise CalcError("Division by zero")
            return Scalar(Fraction(0))
        if other.is_real and isinstance(other.real, Fraction):
            exponent = other.real
            if self.is_real:
                exact = _rational_power_exact(self.real, exponent)
                if exact is not None:
                    return exact
                if self.real < 0:
                    try:
                        value = complex(self.as_complex()) ** complex(
                            float(exponent)
                        )
                    except (OverflowError, ValueError) as exc:
                        raise CalcError("Power error") from exc
                    return Scalar.from_complex(value)
                try:
                    return Scalar(
                        math.pow(float(self.real), float(exponent))
                    )
                except (OverflowError, ValueError) as exc:
                    raise CalcError("Power error") from exc
            # Complex base with a rational exponent.
            if exponent.denominator == 1:
                power = int(exponent)
                result = Scalar(Fraction(1))
                base = self if power >= 0 else Scalar(Fraction(1)).div(self)
                for _ in range(abs(power)):
                    result = result.mul(base)
                return result
        try:
            value = self.as_complex() ** other.as_complex()
        except (OverflowError, ValueError, ZeroDivisionError) as exc:
            raise CalcError("Power error") from exc
        return Scalar.from_complex(value)

    # -- factories -----------------------------------------------------
    @classmethod
    def from_complex(cls, value: complex) -> "Scalar":
        real = value.real
        imag = value.imag
        # Snap values that are visually integers/floats with tiny noise.
        real = _snap_float(real)
        imag = _snap_float(imag)
        return cls(real, imag)

    @classmethod
    def from_value(cls, value: Any) -> "Scalar":
        if isinstance(value, Scalar):
            return value
        if isinstance(value, complex):
            return cls(value.real, value.imag)
        return cls(value)


def _snap_float(value: float) -> Number:
    """Remove floating-point fuzz (e.g. 1.9999999999999998 → 2.0)."""
    if value == 0.0:
        return Fraction(0)
    if math.isfinite(value):
        nearest = round(value)
        if abs(value - nearest) < 1e-12:
            return Fraction(nearest)
    return value


# ---------------------------------------------------------------------------
# Display formatting
# ---------------------------------------------------------------------------
def _terminating_decimal(value: Fraction) -> Optional[str]:
    """Exact decimal text for a fraction with a terminating expansion."""
    denominator = value.denominator
    reduced = denominator
    while reduced % 2 == 0:
        reduced //= 2
    while reduced % 5 == 0:
        reduced //= 5
    if reduced != 1:
        return None
    try:
        with localcontext() as context:
            context.prec = max(
                40,
                len(str(abs(value.numerator))) + len(str(denominator)) + 10,
            )
            decimal_value = Decimal(value.numerator) / Decimal(denominator)
            text = format(decimal_value, "f")
    except (InvalidOperation, ValueError):
        return None
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def format_number(
    value: Number,
    precision: int = 10,
    exact: bool = True,
    fraction_limit: int = 1_000_000,
) -> str:
    """Format one real number for display."""
    precision = max(0, min(16, int(precision)))
    if isinstance(value, Fraction):
        if value.denominator == 1:
            return str(value.numerator)
        if exact and value.denominator <= fraction_limit:
            terminating = _terminating_decimal(value)
            if terminating is not None and len(terminating) <= 40:
                return terminating
            return f"{value.numerator}/{value.denominator}"
        if exact and value.denominator > fraction_limit:
            terminating = _terminating_decimal(value)
            if terminating is not None and len(terminating) <= 40:
                return terminating
        value = float(value)
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            raise CalcError("Math error")
        if value == 0.0:
            return "0"
        magnitude = abs(value)
        if magnitude >= 1e12 or magnitude < 10 ** (-max(1, precision)):
            text = f"{value:.{max(1, precision)}g}"
        else:
            text = f"{value:.{precision}f}"
            if "." in text:
                text = text.rstrip("0").rstrip(".")
        if text in ("-0", ""):
            return "0"
        return text
    return str(value)


def _sign_text(value: Number) -> str:
    return "-" if _num_sign(value) < 0 else "+"


def format_scalar(
    value: Scalar,
    precision: int = 10,
    exact: bool = True,
) -> str:
    """Format a scalar, including complex values."""
    if value.is_real:
        return format_number(value.real, precision, exact)
    real_text = ""
    if not _num_is_zero(value.real):
        real_text = format_number(value.real, precision, exact)
    imag_abs = _num_abs(value.imag)
    imag_text = format_number(imag_abs, precision, exact)
    if imag_text == "1":
        imag_text = "i"
    else:
        imag_text = imag_text + "i"
    if not real_text:
        return ("-" if _num_sign(value.imag) < 0 else "") + imag_text
    return f"{real_text} {_sign_text(value.imag)} {imag_text}"


def format_value(
    value: Any,
    precision: int = 10,
    exact: bool = True,
) -> str:
    """Format any engine value for display."""
    if isinstance(value, Scalar):
        return format_scalar(value, precision, exact)
    if isinstance(value, Matrix):
        return value.format(precision, exact)
    if isinstance(value, ExprValue):
        return value.text
    if isinstance(value, SolutionSet):
        return value.format(precision, exact)
    if isinstance(value, str):
        return value
    return str(value)


# ---------------------------------------------------------------------------
# Matrices
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Matrix:
    """An immutable matrix of :class:`Scalar` entries."""

    rows: Tuple[Tuple[Scalar, ...], ...]

    def __post_init__(self) -> None:
        if not self.rows:
            raise CalcError("An empty matrix is not allowed")
        width = len(self.rows[0])
        if width == 0:
            raise CalcError("An empty matrix is not allowed")
        normalized = []
        for row in self.rows:
            if len(row) != width:
                raise CalcError("Matrix rows must have the same length")
            normalized.append(tuple(Scalar.from_value(item) for item in row))
        object.__setattr__(self, "rows", tuple(normalized))

    @classmethod
    def from_values(cls, rows: Iterable[Iterable[Any]]) -> "Matrix":
        return cls(
            tuple(tuple(Scalar.from_value(item) for item in row) for row in rows)
        )

    @property
    def shape(self) -> Tuple[int, int]:
        return (len(self.rows), len(self.rows[0]))

    def is_square(self) -> bool:
        rows, columns = self.shape
        return rows == columns

    def entry(self, row: int, column: int) -> Scalar:
        return self.rows[row][column]

    def add(self, other: "Matrix") -> "Matrix":
        if self.shape != other.shape:
            raise CalcError("Matrix dimensions must match for addition")
        return Matrix(
            tuple(
                tuple(
                    self.rows[r][c].add(other.rows[r][c])
                    for c in range(self.shape[1])
                )
                for r in range(self.shape[0])
            )
        )

    def sub(self, other: "Matrix") -> "Matrix":
        if self.shape != other.shape:
            raise CalcError("Matrix dimensions must match for subtraction")
        return Matrix(
            tuple(
                tuple(
                    self.rows[r][c].sub(other.rows[r][c])
                    for c in range(self.shape[1])
                )
                for r in range(self.shape[0])
            )
        )

    def scale(self, value: Scalar) -> "Matrix":
        return Matrix(
            tuple(
                tuple(entry.mul(value) for entry in row) for row in self.rows
            )
        )

    def mul(self, other: "Matrix") -> "Matrix":
        rows, columns = self.shape
        other_rows, other_columns = other.shape
        if columns != other_rows:
            raise CalcError(
                "Matrix dimensions are incompatible for multiplication"
            )
        product = []
        for r in range(rows):
            row = []
            for c in range(other_columns):
                total = Scalar(Fraction(0))
                for k in range(columns):
                    total = total.add(
                        self.rows[r][k].mul(other.rows[k][c])
                    )
                row.append(total)
            product.append(tuple(row))
        return Matrix(tuple(product))

    def transpose(self) -> "Matrix":
        return Matrix(
            tuple(
                tuple(self.rows[r][c] for r in range(self.shape[0]))
                for c in range(self.shape[1])
            )
        )

    def det(self) -> Scalar:
        rows, columns = self.shape
        if rows != columns:
            raise CalcError("Determinant requires a square matrix")
        if rows == 1:
            return self.rows[0][0]
        data = [list(row) for row in self.rows]
        sign = 1
        result = Scalar(Fraction(1))
        for column in range(columns):
            pivot = None
            for row in range(column, rows):
                if not data[row][column].is_zero:
                    pivot = row
                    break
            if pivot is None:
                return Scalar(Fraction(0))
            if pivot != column:
                data[column], data[pivot] = data[pivot], data[column]
                sign = -sign
            pivot_value = data[column][column]
            result = result.mul(pivot_value)
            for row in range(column + 1, rows):
                factor = data[row][column].div(pivot_value)
                for c in range(column, columns):
                    data[row][c] = data[row][c].sub(
                        factor.mul(data[column][c])
                    )
        if sign < 0:
            result = result.neg()
        return result

    def inverse(self) -> "Matrix":
        rows, columns = self.shape
        if rows != columns:
            raise CalcError("Inverse requires a square matrix")
        identity = Matrix(
            tuple(
                tuple(
                    Scalar(Fraction(1 if r == c else 0))
                    for c in range(columns)
                )
                for r in range(rows)
            )
        )
        augmented = [
            list(self.rows[r]) + list(identity.rows[r]) for r in range(rows)
        ]
        for column in range(columns):
            pivot = None
            for row in range(column, rows):
                if not augmented[row][column].is_zero:
                    pivot = row
                    break
            if pivot is None:
                raise CalcError("Matrix is singular; it has no inverse")
            if pivot != column:
                augmented[column], augmented[pivot] = (
                    augmented[pivot],
                    augmented[column],
                )
            pivot_value = augmented[column][column]
            augmented[column] = [
                entry.div(pivot_value) for entry in augmented[column]
            ]
            for row in range(rows):
                if row == column:
                    continue
                factor = augmented[row][column]
                if factor.is_zero:
                    continue
                augmented[row] = [
                    augmented[row][c].sub(
                        factor.mul(augmented[column][c])
                    )
                    for c in range(2 * columns)
                ]
        return Matrix(
            tuple(tuple(row[columns:]) for row in augmented)
        )

    def rref(self) -> "Matrix":
        rows, columns = self.shape
        data = [list(row) for row in self.rows]
        lead = 0
        for row in range(rows):
            if lead >= columns:
                break
            pivot = row
            while data[pivot][lead].is_zero:
                pivot += 1
                if pivot == rows:
                    pivot = row
                    lead += 1
                    if lead == columns:
                        return Matrix(tuple(tuple(r) for r in data))
            data[row], data[pivot] = data[pivot], data[row]
            pivot_value = data[row][lead]
            data[row] = [entry.div(pivot_value) for entry in data[row]]
            for r in range(rows):
                if r == row:
                    continue
                factor = data[r][lead]
                if factor.is_zero:
                    continue
                data[r] = [
                    data[r][c].sub(factor.mul(data[row][c]))
                    for c in range(columns)
                ]
            lead += 1
        return Matrix(tuple(tuple(row) for row in data))

    def solve(self, vector: "Matrix") -> "Matrix":
        """Solve ``self * x = vector`` for a column (or row) vector."""
        rows, columns = self.shape
        if rows != columns:
            raise CalcError("A square coefficient matrix is required")
        if vector.shape[0] == 1:
            entries = list(vector.rows[0])
        elif vector.shape[1] == 1:
            entries = [row[0] for row in vector.rows]
        else:
            raise CalcError("The right-hand side must be a vector")
        if len(entries) != rows:
            raise CalcError("The right-hand side has the wrong length")
        rhs = Matrix(tuple((entry,) for entry in entries))
        return self.inverse().mul(rhs)

    def format(self, precision: int = 10, exact: bool = True) -> str:
        rows = []
        for row in self.rows:
            rows.append(
                "["
                + ", ".join(
                    format_scalar(entry, precision, exact) for entry in row
                )
                + "]"
            )
        return "[" + ", ".join(rows) + "]"


# ---------------------------------------------------------------------------
# Result containers
# ---------------------------------------------------------------------------
@dataclass
class ExprValue:
    """A symbolic expression returned by ``diff``/``integral``."""

    node: "Node"
    text: str
    steps: List[str] = field(default_factory=list)


@dataclass
class SolutionSet:
    """A set of solutions for an equation."""

    variable: str
    values: List[Scalar]
    steps: List[str] = field(default_factory=list)

    def format(self, precision: int = 10, exact: bool = True) -> str:
        values = ", ".join(
            format_scalar(value, precision, exact) for value in self.values
        )
        return f"{self.variable} = {values}"


@dataclass
class EvalResult:
    """Everything the UI needs to display one evaluation."""

    value: Any
    expression: str
    exact_text: str
    decimal_text: str
    steps: List[str] = field(default_factory=list)
    unit: Optional[str] = None

    def primary_text(self, exact_mode: bool = True) -> str:
        if exact_mode:
            return self.exact_text
        return self.decimal_text or self.exact_text

    def secondary_text(self, exact_mode: bool = True) -> str:
        if not exact_mode:
            return ""
        if not self.decimal_text or self.decimal_text == self.exact_text:
            return ""
        return "≈ " + self.decimal_text


# ---------------------------------------------------------------------------
# Expression AST
# ---------------------------------------------------------------------------
class Node:
    """Base class for expression nodes."""

    precedence = 6

    def eval(self, env: Dict[str, Any], ctx: "EvalContext") -> Any:
        raise NotImplementedError

    def format(self, parent_precedence: int = 0) -> str:
        raise NotImplementedError

    def contains_var(self, name: str) -> bool:
        return False

    def diff(self, variable: str, steps: List[str]) -> "Node":
        raise CalcError("This expression cannot be differentiated")

    def simplify(self) -> "Node":
        return self

    def is_number(self) -> bool:
        return isinstance(self, NumNode)

    def is_zero(self) -> bool:
        return isinstance(self, NumNode) and self.value.is_zero

    def is_one(self) -> bool:
        return (
            isinstance(self, NumNode)
            and self.value.is_real
            and self.value.real == 1
        )


class NumNode(Node):
    precedence = 6

    def __init__(self, value: Scalar) -> None:
        self.value = value

    def eval(self, env: Dict[str, Any], ctx: "EvalContext") -> Scalar:
        return self.value

    def format(self, parent_precedence: int = 0) -> str:
        text = format_scalar(self.value, exact=True)
        if parent_precedence > 1 and any(char in text for char in "+-*/^"):
            return f"({text})"
        return text

    def diff(self, variable: str, steps: List[str]) -> Node:
        return NumNode(Scalar(Fraction(0)))


class VarNode(Node):
    precedence = 6

    def __init__(self, name: str) -> None:
        self.name = name

    def eval(self, env: Dict[str, Any], ctx: "EvalContext") -> Any:
        if self.name in env:
            return env[self.name]
        if self.name in _CONSTANTS:
            return _CONSTANTS[self.name]
        if self.name == "ans":
            if ctx.ans is None:
                raise CalcError("Ans is not available yet")
            return ctx.ans
        raise CalcError(f"Unknown variable '{self.name}'")

    def format(self, parent_precedence: int = 0) -> str:
        return self.name

    def contains_var(self, name: str) -> bool:
        return self.name == name

    def diff(self, variable: str, steps: List[str]) -> Node:
        return NumNode(
            Scalar(Fraction(1 if self.name == variable else 0))
        )


class StringNode(Node):
    precedence = 6

    def __init__(self, value: str) -> None:
        self.value = value

    def eval(self, env: Dict[str, Any], ctx: "EvalContext") -> str:
        return self.value

    def format(self, parent_precedence: int = 0) -> str:
        return f'"{self.value}"'


class UnaryNode(Node):
    precedence = 3

    def __init__(self, op: str, operand: Node) -> None:
        self.op = op
        self.operand = operand

    def eval(self, env: Dict[str, Any], ctx: "EvalContext") -> Any:
        value = self.operand.eval(env, ctx)
        if not isinstance(value, Scalar):
            raise CalcError("Unary minus requires a number")
        result = value.neg() if self.op == "-" else value
        if (
            ctx.record_steps
            and len(ctx.steps) < 60
            and self.op == "-"
            and not isinstance(self.operand, NumNode)
        ):
            ctx.steps.append(
                f"−({format_scalar(value, exact=True)}) = "
                f"{format_scalar(result, exact=True)}"
            )
        return result

    def format(self, parent_precedence: int = 0) -> str:
        text = self.op + self.operand.format(self.precedence)
        if self.precedence < parent_precedence:
            return f"({text})"
        return text

    def contains_var(self, name: str) -> bool:
        return self.operand.contains_var(name)

    def diff(self, variable: str, steps: List[str]) -> Node:
        return UnaryNode(
            "-", self.operand.diff(variable, steps)
        ).simplify()

    def simplify(self) -> Node:
        operand = self.operand.simplify()
        if self.op == "-":
            if operand.is_number():
                return NumNode(operand.value.neg())
            if isinstance(operand, UnaryNode) and operand.op == "-":
                return operand.operand
        return UnaryNode(self.op, operand)


class BinaryNode(Node):
    PRECEDENCE = {"+": 1, "-": 1, "*": 2, "/": 2, "%": 2, "^": 4}

    def __init__(self, op: str, left: Node, right: Node) -> None:
        self.op = op
        self.left = left
        self.right = right
        self.precedence = self.PRECEDENCE.get(op, 1)

    def eval(self, env: Dict[str, Any], ctx: "EvalContext") -> Any:
        left = self.left.eval(env, ctx)
        right = self.right.eval(env, ctx)
        if self.op == "+":
            result = _add_values(left, right)
        elif self.op == "-":
            result = _sub_values(left, right)
        elif self.op == "*":
            result = _mul_values(left, right)
        elif self.op == "/":
            result = _div_values(left, right)
        elif self.op == "%":
            result = _mod_values(left, right)
        elif self.op == "^":
            result = _pow_values(left, right)
        else:
            raise CalcError(f"Unknown operator '{self.op}'")
        if ctx.record_steps and len(ctx.steps) < 60:
            left_text = format_value(left, exact=True)
            right_text = format_value(right, exact=True)
            result_text = format_value(result, exact=True)
            ctx.steps.append(
                f"{left_text} {self.op} {right_text} = {result_text}"
            )
        return result

    def format(self, parent_precedence: int = 0) -> str:
        if self.op == "^":
            left = self.left.format(self.precedence + 1)
            right = self.right.format(self.precedence)
        else:
            left = self.left.format(self.precedence)
            right = self.right.format(self.precedence + 1)
        if self.op in ("+", "-"):
            text = f"{left} {self.op} {right}"
        else:
            text = f"{left}{self.op}{right}"
        if self.precedence < parent_precedence:
            return f"({text})"
        return text

    def contains_var(self, name: str) -> bool:
        return self.left.contains_var(name) or self.right.contains_var(name)

    def diff(self, variable: str, steps: List[str]) -> Node:
        left = self.left
        right = self.right
        if self.op in ("+", "-"):
            return BinaryNode(
                self.op,
                left.diff(variable, steps),
                right.diff(variable, steps),
            ).simplify()
        if self.op == "*":
            steps.append(
                "Product rule: (u·v)' = u'·v + u·v'."
            )
            return BinaryNode(
                "+",
                BinaryNode("*", left.diff(variable, steps), right),
                BinaryNode("*", left, right.diff(variable, steps)),
            ).simplify()
        if self.op == "/":
            steps.append(
                "Quotient rule: (u/v)' = (u'·v − u·v') / v²."
            )
            return BinaryNode(
                "/",
                BinaryNode(
                    "-",
                    BinaryNode("*", left.diff(variable, steps), right),
                    BinaryNode("*", left, right.diff(variable, steps)),
                ),
                BinaryNode("^", right, NumNode(Scalar(Fraction(2)))),
            ).simplify()
        if self.op == "^":
            if not right.contains_var(variable):
                steps.append(
                    "Power rule: d/dx(uⁿ) = n·uⁿ⁻¹·u'."
                )
                exponent = right
                return BinaryNode(
                    "*",
                    BinaryNode(
                        "*",
                        exponent,
                        BinaryNode(
                            "^",
                            left,
                            BinaryNode(
                                "-",
                                exponent,
                                NumNode(Scalar(Fraction(1))),
                            ),
                        ),
                    ),
                    left.diff(variable, steps),
                ).simplify()
            steps.append(
                "General power rule: d/dx(uᵛ) = uᵛ·(v'·ln(u) + v·u'/u)."
            )
            return BinaryNode(
                "*",
                BinaryNode("^", left, right),
                BinaryNode(
                    "+",
                    BinaryNode(
                        "*",
                        right.diff(variable, steps),
                        CallNode("ln", [left]),
                    ),
                    BinaryNode(
                        "*",
                        right,
                        BinaryNode(
                            "/",
                            left.diff(variable, steps),
                            left,
                        ),
                    ),
                ),
            ).simplify()
        raise CalcError(f"Cannot differentiate operator '{self.op}'")

    def simplify(self) -> Node:
        left = self.left.simplify()
        right = self.right.simplify()
        if self.op in ("+", "-") and right.is_zero():
            return left
        if self.op == "+" and left.is_zero():
            return right
        if self.op == "-" and left.is_zero():
            return UnaryNode("-", right).simplify()
        if self.op in ("+", "-", "*", "/", "%") and left.is_number() and right.is_number():
            folded = self._fold_numbers(left, right)
            if not isinstance(folded, BinaryNode):
                return folded
        if self.op in ("+", "-") and right.is_zero():
            return left
        if self.op == "+" and left.is_zero():
            return right
        if self.op == "-" and left.is_zero():
            return UnaryNode("-", right).simplify()
        if self.op == "*":
            if left.is_zero() or right.is_zero():
                return NumNode(Scalar(Fraction(0)))
            if left.is_one():
                return right
            if right.is_one():
                return left
        if self.op == "/" and right.is_one():
            return left
        if (
            self.op == "/"
            and right.is_number()
            and right.value.is_real
            and not right.value.is_zero
            and not left.is_number()
        ):
            return BinaryNode(
                "*",
                left,
                NumNode(Scalar(Fraction(1)).div(right.value)),
            ).simplify()
        if self.op == "^":
            if right.is_zero():
                return NumNode(Scalar(Fraction(1)))
            if right.is_one():
                return left
            if left.is_number() and right.is_number():
                folded = self._fold_numbers(left, right, power=True)
                if not isinstance(folded, BinaryNode):
                    return folded
        return BinaryNode(self.op, left, right)

    def _fold_numbers(self, left: Node, right: Node, power: bool = False) -> Node:
        try:
            left_value = left.eval({}, EvalContext(record_steps=False))
            right_value = right.eval({}, EvalContext(record_steps=False))
            if power:
                result = _pow_values(left_value, right_value)
            elif self.op == "*":
                result = _mul_values(left_value, right_value)
            elif self.op == "+":
                result = _add_values(left_value, right_value)
            elif self.op == "-":
                result = _sub_values(left_value, right_value)
            elif self.op == "/":
                result = _div_values(left_value, right_value)
            else:
                return self
            return NumNode(result)
        except CalcError:
            return self


class PostfixNode(Node):
    precedence = 5

    def __init__(self, op: str, operand: Node) -> None:
        self.op = op
        self.operand = operand

    def eval(self, env: Dict[str, Any], ctx: "EvalContext") -> Any:
        value = self.operand.eval(env, ctx)
        if not isinstance(value, Scalar):
            raise CalcError("Postfix operators require a number")
        if self.op == "!":
            result = _factorial(value)
        elif self.op == "%":
            result = value.div(Scalar(Fraction(100)))
        else:
            raise CalcError(f"Unknown postfix operator '{self.op}'")
        if ctx.record_steps and len(ctx.steps) < 60:
            ctx.steps.append(
                f"{format_scalar(value, exact=True)}{self.op} = "
                f"{format_scalar(result, exact=True)}"
            )
        return result

    def format(self, parent_precedence: int = 0) -> str:
        text = self.operand.format(self.precedence) + self.op
        if self.precedence < parent_precedence:
            return f"({text})"
        return text

    def contains_var(self, name: str) -> bool:
        return self.operand.contains_var(name)

    def diff(self, variable: str, steps: List[str]) -> Node:
        raise CalcError(
            "Factorials and percentages are not differentiable"
        )

    def simplify(self) -> Node:
        operand = self.operand.simplify()
        if operand.is_number():
            try:
                value = operand.value
                if self.op == "!":
                    return NumNode(_factorial(value))
                if self.op == "%":
                    return NumNode(value.div(Scalar(Fraction(100))))
            except CalcError:
                pass
        return PostfixNode(self.op, operand)


class CallNode(Node):
    precedence = 6

    def __init__(self, name: str, args: Sequence[Node]) -> None:
        self.name = name
        self.args = list(args)

    def eval(self, env: Dict[str, Any], ctx: "EvalContext") -> Any:
        name = self.name
        if name in ("diff", "derivative"):
            return self._eval_diff(env, ctx)
        if name in ("integral", "integrate"):
            return self._eval_integral(env, ctx)
        if name == "solve":
            return self._eval_solve(env, ctx)
        values = [arg.eval(env, ctx) for arg in self.args]
        result = call_function(name, values, ctx)
        if ctx.record_steps and len(ctx.steps) < 60:
            args_text = ", ".join(
                format_value(value, exact=True) for value in values
            )
            ctx.steps.append(
                f"{name}({args_text}) = "
                f"{format_value(result, exact=True)}"
            )
        return result

    def format(self, parent_precedence: int = 0) -> str:
        args = ", ".join(arg.format() for arg in self.args)
        return f"{self.name}({args})"

    def contains_var(self, name: str) -> bool:
        return any(arg.contains_var(name) for arg in self.args)

    def simplify(self) -> Node:
        return CallNode(self.name, [arg.simplify() for arg in self.args])

    def diff(self, variable: str, steps: List[str]) -> Node:
        if len(self.args) != 1:
            raise CalcError(
                f"Cannot differentiate {self.name} with this number of arguments"
            )
        argument = self.args[0]
        inner = argument.diff(variable, steps)
        name = self.name
        one = NumNode(Scalar(Fraction(1)))
        two = NumNode(Scalar(Fraction(2)))
        if name == "sin":
            steps.append("Chain rule with d/dx sin(u) = cos(u)·u'.")
            return BinaryNode("*", CallNode("cos", [argument]), inner).simplify()
        if name == "cos":
            steps.append("Chain rule with d/dx cos(u) = −sin(u)·u'.")
            return UnaryNode(
                "-",
                BinaryNode("*", CallNode("sin", [argument]), inner),
            ).simplify()
        if name == "tan":
            steps.append("Chain rule with d/dx tan(u) = u'/cos²(u).")
            return BinaryNode(
                "/",
                inner,
                BinaryNode("^", CallNode("cos", [argument]), two),
            ).simplify()
        if name == "asin":
            steps.append("Chain rule with d/dx asin(u) = u'/√(1−u²).")
            return BinaryNode(
                "/",
                inner,
                CallNode(
                    "sqrt",
                    [
                        BinaryNode(
                            "-",
                            one,
                            BinaryNode("^", argument, two),
                        )
                    ],
                ),
            ).simplify()
        if name == "acos":
            steps.append("Chain rule with d/dx acos(u) = −u'/√(1−u²).")
            return UnaryNode(
                "-",
                BinaryNode(
                    "/",
                    inner,
                    CallNode(
                        "sqrt",
                        [
                            BinaryNode(
                                "-",
                                one,
                                BinaryNode("^", argument, two),
                            )
                        ],
                    ),
                ),
            ).simplify()
        if name == "atan":
            steps.append("Chain rule with d/dx atan(u) = u'/(1+u²).")
            return BinaryNode(
                "/",
                inner,
                BinaryNode("+", one, BinaryNode("^", argument, two)),
            ).simplify()
        if name == "sinh":
            steps.append("Chain rule with d/dx sinh(u) = cosh(u)·u'.")
            return BinaryNode(
                "*", CallNode("cosh", [argument]), inner
            ).simplify()
        if name == "cosh":
            steps.append("Chain rule with d/dx cosh(u) = sinh(u)·u'.")
            return BinaryNode(
                "*", CallNode("sinh", [argument]), inner
            ).simplify()
        if name == "tanh":
            steps.append("Chain rule with d/dx tanh(u) = u'/cosh²(u).")
            return BinaryNode(
                "/",
                inner,
                BinaryNode("^", CallNode("cosh", [argument]), two),
            ).simplify()
        if name == "asinh":
            return BinaryNode(
                "/",
                inner,
                CallNode(
                    "sqrt",
                    [
                        BinaryNode(
                            "+", BinaryNode("^", argument, two), one
                        )
                    ],
                ),
            ).simplify()
        if name == "acosh":
            return BinaryNode(
                "/",
                inner,
                CallNode(
                    "sqrt",
                    [
                        BinaryNode(
                            "-", BinaryNode("^", argument, two), one
                        )
                    ],
                ),
            ).simplify()
        if name == "atanh":
            return BinaryNode(
                "/",
                inner,
                BinaryNode(
                    "-", one, BinaryNode("^", argument, two)
                ),
            ).simplify()
        if name == "ln":
            steps.append("Chain rule with d/dx ln(u) = u'/u.")
            return BinaryNode("/", inner, argument).simplify()
        if name in ("log", "log10"):
            return BinaryNode(
                "/",
                inner,
                BinaryNode(
                    "*",
                    argument,
                    CallNode("ln", [NumNode(Scalar(Fraction(10)))]),
                ),
            ).simplify()
        if name == "log2":
            return BinaryNode(
                "/",
                inner,
                BinaryNode(
                    "*",
                    argument,
                    CallNode("ln", [NumNode(Scalar(Fraction(2)))]),
                ),
            ).simplify()
        if name == "exp":
            steps.append("Chain rule with d/dx eᵘ = eᵘ·u'.")
            return BinaryNode(
                "*", CallNode("exp", [argument]), inner
            ).simplify()
        if name == "sqrt":
            steps.append("Chain rule with d/dx √u = u'/(2√u).")
            return BinaryNode(
                "/",
                inner,
                BinaryNode("*", two, CallNode("sqrt", [argument])),
            ).simplify()
        if name == "cbrt":
            return BinaryNode(
                "/",
                inner,
                BinaryNode(
                    "*",
                    NumNode(Scalar(Fraction(3))),
                    BinaryNode("^", CallNode("cbrt", [argument]), two),
                ),
            ).simplify()
        if name == "abs":
            return BinaryNode(
                "*", CallNode("sign", [argument]), inner
            ).simplify()
        if name == "sec":
            return BinaryNode(
                "*",
                CallNode("sec", [argument]),
                BinaryNode("*", CallNode("tan", [argument]), inner),
            ).simplify()
        if name == "csc":
            return UnaryNode(
                "-",
                BinaryNode(
                    "*",
                    CallNode("csc", [argument]),
                    BinaryNode("*", CallNode("cot", [argument]), inner),
                ),
            ).simplify()
        if name == "cot":
            return UnaryNode(
                "-",
                BinaryNode(
                    "*",
                    BinaryNode("^", CallNode("csc", [argument]), two),
                    inner,
                ),
            ).simplify()
        raise CalcError(f"Cannot differentiate {name}(...)")

    # -- symbolic special forms ---------------------------------------
    def _var_name(self, node: Node) -> str:
        if isinstance(node, VarNode):
            return node.name
        if isinstance(node, StringNode):
            return node.value
        raise CalcError("Expected a variable name, for example x")

    def _eval_diff(self, env: Dict[str, Any], ctx: "EvalContext") -> Any:
        if len(self.args) not in (2, 3):
            raise CalcError("diff expects an expression, a variable and an optional point")
        variable = self._var_name(self.args[1])
        steps: List[str] = [
            f"Differentiate {self.args[0].format()} with respect to {variable}."
        ]
        if ctx.angle_degrees:
            steps.append("Calculus is evaluated in radians.")
        derivative = self.args[0].diff(variable, steps).simplify()
        text = derivative.format()
        steps.append(f"Result: {text}")
        if len(self.args) == 3:
            point = self.args[2].eval(env, ctx)
            if not isinstance(point, Scalar):
                raise CalcError("The evaluation point must be a number")
            point_env = dict(env)
            point_env[variable] = point
            value = derivative.eval(
                point_env, EvalContext(record_steps=False, angle_degrees=False)
            )
            steps.append(
                f"Evaluate at {variable} = {format_scalar(point)}: "
                f"{format_value(value)}"
            )
            ctx.steps.extend(steps)
            return value
        ctx.steps.extend(steps)
        return ExprValue(derivative, text, steps)

    def _eval_integral(self, env: Dict[str, Any], ctx: "EvalContext") -> Any:
        if len(self.args) not in (2, 3, 4):
            raise CalcError(
                "integral expects (expression, variable[, lower, upper])"
            )
        variable = self._var_name(self.args[1])
        expression = self.args[0]
        if len(self.args) == 4:
            lower = self.args[2].eval(env, ctx)
            upper = self.args[3].eval(env, ctx)
            if not isinstance(lower, Scalar) or not isinstance(upper, Scalar):
                raise CalcError("Integration bounds must be numbers")
            value = definite_integral(
                expression, variable, lower, upper, env, False
            )
            steps = [
                f"Definite integral of {expression.format()} "
                f"from {format_scalar(lower)} to {format_scalar(upper)}.",
                "Calculus is evaluated in radians.",
                "Evaluate the antiderivative at the bounds "
                "(numeric Simpson fallback when needed).",
                f"Result: {format_scalar(value)}",
            ]
            ctx.steps.extend(steps)
            return value
        steps: List[str] = [
            f"Integrate {expression.format()} with respect to {variable}."
        ]
        if ctx.angle_degrees:
            steps.append("Calculus is evaluated in radians.")
        antiderivative = integrate_node(expression, variable, steps)
        text = antiderivative.format()
        steps.append(f"Result: {text} + C")
        ctx.steps.extend(steps)
        return ExprValue(antiderivative, text + " + C", steps)

    def _eval_solve(self, env: Dict[str, Any], ctx: "EvalContext") -> Any:
        if len(self.args) not in (2, 3, 4):
            raise CalcError("solve expects (equation, variable)")
        # Matrix form: solve(A, b) solves a linear system A·x = b.
        if len(self.args) == 2:
            try:
                first = self.args[0].eval(env, ctx)
                second = self.args[1].eval(env, ctx)
            except CalcError:
                first = second = None
            if isinstance(first, Matrix) and isinstance(second, Matrix):
                solution = first.solve(second)
                steps = [
                    "Solve the linear system A·x = b.",
                    f"A = {first.format()}, b = {second.format()}",
                    f"x = {solution.format()}",
                ]
                ctx.steps.extend(steps)
                return solution
        variable = self._var_name(self.args[1])
        expression = self.args[0]
        lower = None
        upper = None
        if len(self.args) >= 3:
            lower_value = self.args[2].eval(env, ctx)
            upper_value = self.args[3].eval(env, ctx) if len(self.args) == 4 else None
            if not isinstance(lower_value, Scalar):
                raise CalcError("The domain bound must be a number")
            lower = lower_value
            if upper_value is not None and not isinstance(upper_value, Scalar):
                raise CalcError("The domain bound must be a number")
            upper = upper_value
        if isinstance(expression, EquationNode):
            target = BinaryNode("-", expression.left, expression.right)
            shown = expression.format()
        else:
            target = expression
            shown = f"{expression.format()} = 0"
        steps: List[str] = [f"Solve {shown} for {variable}."]
        values = solve_equation(
            target, variable, env, ctx, lower, upper, steps
        )
        steps.append(
            f"Solution: {variable} = "
            + ", ".join(format_scalar(value) for value in values)
        )
        ctx.steps.extend(steps)
        return SolutionSet(variable, values, steps)


class EquationNode(Node):
    precedence = 0

    def __init__(self, left: Node, right: Node) -> None:
        self.left = left
        self.right = right

    def eval(self, env: Dict[str, Any], ctx: "EvalContext") -> Any:
        raise CalcError("An equation can only be used with solve()")

    def format(self, parent_precedence: int = 0) -> str:
        return f"{self.left.format()}={self.right.format()}"

    def contains_var(self, name: str) -> bool:
        return self.left.contains_var(name) or self.right.contains_var(name)

    def diff(self, variable: str, steps: List[str]) -> Node:
        return BinaryNode("-", self.left, self.right).diff(variable, steps)

    def simplify(self) -> Node:
        return EquationNode(self.left.simplify(), self.right.simplify())


class ListNode(Node):
    precedence = 6

    def __init__(self, items: Sequence[Node]) -> None:
        self.items = list(items)

    def eval(self, env: Dict[str, Any], ctx: "EvalContext") -> Matrix:
        if not self.items:
            raise CalcError("An empty list is not allowed")
        values = [item.eval(env, ctx) for item in self.items]
        if all(isinstance(value, Scalar) for value in values):
            return Matrix((tuple(values),))
        if all(isinstance(value, Matrix) for value in values):
            rows = []
            for value in values:
                if value.shape[0] == 1:
                    rows.append(value.rows[0])
                else:
                    rows.extend(value.rows)
            return Matrix(tuple(rows))
        raise CalcError("Lists must contain numbers or rows of numbers")

    def format(self, parent_precedence: int = 0) -> str:
        return "[" + ", ".join(item.format() for item in self.items) + "]"

    def contains_var(self, name: str) -> bool:
        return any(item.contains_var(name) for item in self.items)

    def simplify(self) -> Node:
        return ListNode([item.simplify() for item in self.items])


# ---------------------------------------------------------------------------
# Evaluation context and value operations
# ---------------------------------------------------------------------------
@dataclass
class EvalContext:
    """Per-evaluation options and step collection."""

    angle_degrees: bool = False
    ans: Optional[Scalar] = None
    steps: List[str] = field(default_factory=list)
    record_steps: bool = True

    def add_step(self, text: str) -> None:
        if self.record_steps and len(self.steps) < 60:
            self.steps.append(text)


_CONSTANTS: Dict[str, Scalar] = {
    "pi": Scalar(math.pi),
    "e": Scalar(math.e),
    "tau": Scalar(math.tau),
    "phi": Scalar((1.0 + math.sqrt(5.0)) / 2.0),
    "i": Scalar(Fraction(0), Fraction(1)),
}

CONSTANT_NAMES = tuple(sorted(_CONSTANTS))


def _add_values(left: Any, right: Any) -> Any:
    if isinstance(left, Scalar) and isinstance(right, Scalar):
        return left.add(right)
    if isinstance(left, Matrix) and isinstance(right, Matrix):
        return left.add(right)
    raise CalcError(
        f"Cannot add {_value_kind(left)} and {_value_kind(right)}"
    )


def _sub_values(left: Any, right: Any) -> Any:
    if isinstance(left, Scalar) and isinstance(right, Scalar):
        return left.sub(right)
    if isinstance(left, Matrix) and isinstance(right, Matrix):
        return left.sub(right)
    raise CalcError(
        f"Cannot subtract {_value_kind(right)} from {_value_kind(left)}"
    )


def _mul_values(left: Any, right: Any) -> Any:
    if isinstance(left, Scalar) and isinstance(right, Scalar):
        return left.mul(right)
    if isinstance(left, Matrix) and isinstance(right, Scalar):
        return left.scale(right)
    if isinstance(left, Scalar) and isinstance(right, Matrix):
        return right.scale(left)
    if isinstance(left, Matrix) and isinstance(right, Matrix):
        return left.mul(right)
    raise CalcError(
        f"Cannot multiply {_value_kind(left)} and {_value_kind(right)}"
    )


def _div_values(left: Any, right: Any) -> Any:
    if isinstance(left, Scalar) and isinstance(right, Scalar):
        return left.div(right)
    if isinstance(left, Matrix) and isinstance(right, Scalar):
        return left.scale(Scalar(Fraction(1)).div(right))
    raise CalcError(
        f"Cannot divide {_value_kind(left)} by {_value_kind(right)}"
    )


def _pow_values(left: Any, right: Any) -> Any:
    if isinstance(left, Scalar) and isinstance(right, Scalar):
        return left.pow(right)
    raise CalcError("Exponentiation requires numbers")


def _mod_values(left: Any, right: Any) -> Any:
    if not isinstance(left, Scalar) or not isinstance(right, Scalar):
        raise CalcError("Modulo requires numbers")
    if not left.is_real or not right.is_real:
        raise CalcError("Modulo requires real numbers")
    if right.real == 0:
        raise CalcError("Modulo by zero")
    if isinstance(left.real, Fraction) and isinstance(right.real, Fraction):
        return Scalar(left.real % right.real)
    return Scalar(float(left.real) % float(right.real))


def _value_kind(value: Any) -> str:
    if isinstance(value, Scalar):
        return "a number"
    if isinstance(value, Matrix):
        return "a matrix"
    if isinstance(value, str):
        return "text"
    if isinstance(value, ExprValue):
        return "an expression"
    if isinstance(value, SolutionSet):
        return "a solution set"
    return "this value"


def _factorial(value: Scalar) -> Scalar:
    if not value.is_real:
        raise CalcError("Factorial requires a real number")
    if isinstance(value.real, Fraction) and value.real.denominator == 1:
        number = value.real.numerator
        if number < 0:
            raise CalcError("Factorial is not defined for negative integers")
        if number > 5000:
            raise CalcError("Factorial is too large")
        return Scalar(Fraction(math.factorial(number)))
    number = float(value.real)
    if number < 0 and number == int(number):
        raise CalcError("Factorial is not defined for negative integers")
    try:
        return Scalar(math.gamma(number + 1.0))
    except (ValueError, OverflowError) as exc:
        raise CalcError("Factorial domain error") from exc


# ---------------------------------------------------------------------------
# Function registry
# ---------------------------------------------------------------------------
FUNCTION_NAMES = tuple(
    sorted(
        {
            "sqrt", "cbrt", "root", "sin", "cos", "tan", "sec", "csc",
            "cot", "asin", "acos", "atan", "atan2", "sinh", "cosh",
            "tanh", "asinh", "acosh", "atanh", "ln", "log", "log2",
            "log10", "exp", "abs", "floor", "ceil", "round", "sign",
            "fact", "gamma", "erf", "min", "max", "gcd", "lcm", "mod",
            "ncr", "npr", "hypot", "det", "inv", "transpose", "rref",
            "solve", "diff", "derivative", "integral", "integrate",
            "convert", "degrees", "radians", "sum", "prod", "mean",
            "median", "re", "im", "conj", "arg", "rand",
        }
    )
)


def _require_count(name: str, args: Sequence[Any], count: int) -> None:
    if len(args) != count:
        raise CalcError(
            f"{name} expects {count} argument{'s' if count != 1 else ''}"
        )


def _require_range(
    name: str, args: Sequence[Any], minimum: int, maximum: int
) -> None:
    if not minimum <= len(args) <= maximum:
        raise CalcError(
            f"{name} expects between {minimum} and {maximum} arguments"
        )


def _scalar_arg(name: str, args: Sequence[Any], index: int) -> Scalar:
    value = args[index]
    if not isinstance(value, Scalar):
        raise CalcError(f"{name} expects a number")
    return value


def _real_arg(name: str, args: Sequence[Any], index: int) -> float:
    value = _scalar_arg(name, args, index)
    if not value.is_real:
        raise CalcError(f"{name} expects a real number")
    return float(value.real)


def _matrix_arg(name: str, args: Sequence[Any], index: int) -> Matrix:
    value = args[index]
    if not isinstance(value, Matrix):
        raise CalcError(f"{name} expects a matrix")
    return value


def _flatten_numbers(name: str, args: Sequence[Any]) -> List[Scalar]:
    if len(args) == 1 and isinstance(args[0], Matrix):
        matrix = args[0]
        values = [entry for row in matrix.rows for entry in row]
    else:
        values = list(args)
    if not all(isinstance(value, Scalar) for value in values):
        raise CalcError(f"{name} expects numbers")
    out = []
    for value in values:
        if not value.is_real:
            raise CalcError(f"{name} expects real numbers")
        out.append(value)
    return out


def _snap(value: float) -> float:
    if abs(value) < 1e-12:
        return 0.0
    if abs(value - 1.0) < 1e-12:
        return 1.0
    if abs(value + 1.0) < 1e-12:
        return -1.0
    return value


def _sin_degrees(value: Scalar) -> Scalar:
    if value.is_real and isinstance(value.real, Fraction):
        degrees = value.real % 360
        table = {
            Fraction(0): Fraction(0),
            Fraction(30): Fraction(1, 2),
            Fraction(90): Fraction(1),
            Fraction(150): Fraction(1, 2),
            Fraction(180): Fraction(0),
            Fraction(210): Fraction(-1, 2),
            Fraction(270): Fraction(-1),
            Fraction(330): Fraction(-1, 2),
        }
        if degrees in table:
            return Scalar(table[degrees])
    return Scalar(_snap(math.sin(math.radians(float(value.real)))))


def _cos_degrees(value: Scalar) -> Scalar:
    if value.is_real and isinstance(value.real, Fraction):
        degrees = value.real % 360
        table = {
            Fraction(0): Fraction(1),
            Fraction(60): Fraction(1, 2),
            Fraction(90): Fraction(0),
            Fraction(120): Fraction(-1, 2),
            Fraction(180): Fraction(-1),
            Fraction(240): Fraction(-1, 2),
            Fraction(270): Fraction(0),
            Fraction(300): Fraction(1, 2),
        }
        if degrees in table:
            return Scalar(table[degrees])
    return Scalar(_snap(math.cos(math.radians(float(value.real)))))


def _tan_degrees(value: Scalar) -> Scalar:
    if value.is_real and isinstance(value.real, Fraction):
        degrees = value.real % 180
        if degrees == 90:
            raise CalcError("tan is undefined at 90°")
        table = {
            Fraction(0): Fraction(0),
            Fraction(45): Fraction(1),
            Fraction(135): Fraction(-1),
        }
        if degrees in table:
            return Scalar(table[degrees])
    return Scalar(_snap(math.tan(math.radians(float(value.real)))))


def _trig_real(
    name: str, value: Scalar, angle_degrees: bool
) -> Scalar:
    if not value.is_real:
        try:
            complex_value = value.as_complex()
            if name == "sin":
                return Scalar.from_complex(cmath.sin(complex_value))
            if name == "cos":
                return Scalar.from_complex(cmath.cos(complex_value))
            if name == "tan":
                return Scalar.from_complex(cmath.tan(complex_value))
        except (ValueError, OverflowError) as exc:
            raise CalcError(f"{name} domain error") from exc
    if name == "sin":
        if angle_degrees:
            return _sin_degrees(value)
        return Scalar(_snap(math.sin(float(value.real))))
    if name == "cos":
        if angle_degrees:
            return _cos_degrees(value)
        return Scalar(_snap(math.cos(float(value.real))))
    if name == "tan":
        if angle_degrees:
            return _tan_degrees(value)
        cosine = math.cos(float(value.real))
        if abs(cosine) < 1e-12:
            raise CalcError("tan is undefined at this angle")
        return Scalar(_snap(math.tan(float(value.real))))
    raise CalcError(f"Unknown trigonometric function '{name}'")


def _inverse_trig(
    name: str, value: Scalar, angle_degrees: bool
) -> Scalar:
    if not value.is_real:
        try:
            function = getattr(cmath, name)
            return Scalar.from_complex(function(value.as_complex()))
        except (ValueError, OverflowError) as exc:
            raise CalcError(f"{name} domain error") from exc
    number = float(value.real)
    if name == "asin" and not -1.0 <= number <= 1.0:
        return Scalar.from_complex(cmath.asin(number))
    if name == "acos" and not -1.0 <= number <= 1.0:
        return Scalar.from_complex(cmath.acos(number))
    function = getattr(math, name)
    try:
        result = function(number)
    except ValueError as exc:
        raise CalcError(f"{name} domain error") from exc
    if angle_degrees:
        result = math.degrees(result)
    return Scalar(_snap(result))


def _sqrt_value(value: Scalar) -> Scalar:
    if value.is_real:
        if value.real < 0:
            magnitude = _num_sqrt(-value.real)
            return Scalar(Fraction(0), magnitude.real)
        return _num_sqrt(value.real)
    try:
        return Scalar.from_complex(cmath.sqrt(value.as_complex()))
    except (ValueError, OverflowError) as exc:
        raise CalcError("Square root domain error") from exc


def _real_or_complex_function(
    name: str, value: Scalar
) -> Scalar:
    if value.is_real and name in ("ln", "log", "log2", "log10"):
        number = float(value.real)
        if number > 0:
            function = {
                "ln": math.log,
                "log": math.log10,
                "log10": math.log10,
                "log2": math.log2,
            }[name]
            return Scalar(_snap(function(number)))
        if number == 0:
            raise CalcError(f"{name}(0) is undefined")
        function = {
            "ln": cmath.log,
            "log": lambda x: cmath.log10(x),
            "log10": lambda x: cmath.log10(x),
            "log2": lambda x: cmath.log(x) / math.log(2.0),
        }[name]
        return Scalar.from_complex(function(number))
    try:
        complex_value = value.as_complex()
        if name == "ln":
            return Scalar.from_complex(cmath.log(complex_value))
        if name in ("log", "log10"):
            return Scalar.from_complex(cmath.log10(complex_value))
        if name == "log2":
            return Scalar.from_complex(cmath.log(complex_value) / math.log(2.0))
    except (ValueError, OverflowError) as exc:
        raise CalcError(f"{name} domain error") from exc
    raise CalcError(f"Unknown logarithm '{name}'")


def call_function(
    name: str, args: Sequence[Any], ctx: EvalContext
) -> Any:
    """Evaluate a named function with already-evaluated arguments."""
    name = name.lower()
    if name in ("sin", "cos", "tan", "sec", "csc", "cot"):
        _require_count(name, args, 1)
        value = _scalar_arg(name, args, 0)
        base = name
        if name == "sec":
            base = "cos"
        elif name == "csc":
            base = "sin"
        elif name == "cot":
            base = "tan"
        result = _trig_real(base, value, ctx.angle_degrees)
        if name == "sec":
            return Scalar(Fraction(1)).div(result)
        if name == "csc":
            return Scalar(Fraction(1)).div(result)
        if name == "cot":
            return Scalar(Fraction(1)).div(result)
        return result
    if name in ("asin", "acos", "atan"):
        _require_count(name, args, 1)
        return _inverse_trig(
            name, _scalar_arg(name, args, 0), ctx.angle_degrees
        )
    if name == "atan2":
        _require_count(name, args, 2)
        y = _real_arg(name, args, 0)
        x = _real_arg(name, args, 1)
        result = math.atan2(y, x)
        if ctx.angle_degrees:
            result = math.degrees(result)
        return Scalar(_snap(result))
    if name in ("sinh", "cosh", "tanh", "asinh", "acosh", "atanh"):
        _require_count(name, args, 1)
        value = _scalar_arg(name, args, 0)
        function = getattr(math, name)
        try:
            if value.is_real:
                return Scalar(_snap(function(float(value.real))))
            return Scalar.from_complex(getattr(cmath, name)(value.as_complex()))
        except (ValueError, OverflowError) as exc:
            raise CalcError(f"{name} domain error") from exc
    if name == "sqrt":
        _require_count(name, args, 1)
        return _sqrt_value(_scalar_arg(name, args, 0))
    if name == "cbrt":
        _require_count(name, args, 1)
        value = _scalar_arg(name, args, 0)
        return value.pow(Scalar(Fraction(1, 3)))
    if name == "root":
        _require_count(name, args, 2)
        degree = _scalar_arg(name, args, 0)
        if not degree.is_real or not degree.is_integer:
            raise CalcError("The root degree must be an integer")
        number = int(degree.real)
        if number == 0:
            raise CalcError("The root degree must not be zero")
        return _scalar_arg(name, args, 1).pow(
            Scalar(Fraction(1, number))
        )
    if name in ("ln", "log", "log2", "log10"):
        _require_range(name, args, 1, 2)
        value = _scalar_arg(name, args, 0)
        if name == "log" and len(args) == 2:
            base = _scalar_arg(name, args, 1)
            if base.is_real and float(base.real) > 0 and float(base.real) != 1:
                try:
                    return Scalar(
                        _snap(
                            math.log(float(value.real), float(base.real))
                        )
                    )
                except (ValueError, TypeError):
                    pass
            return _real_or_complex_function(
                "ln", value
            ).div(_real_or_complex_function("ln", base))
        return _real_or_complex_function(name, value)
    if name == "exp":
        _require_count(name, args, 1)
        value = _scalar_arg(name, args, 0)
        try:
            if value.is_real:
                return Scalar(math.exp(float(value.real)))
            return Scalar.from_complex(cmath.exp(value.as_complex()))
        except (OverflowError, ValueError) as exc:
            raise CalcError("exp overflow") from exc
    if name == "abs":
        _require_count(name, args, 1)
        return _scalar_arg(name, args, 0).abs()
    if name in ("floor", "ceil"):
        _require_count(name, args, 1)
        value = _scalar_arg(name, args, 0)
        if not value.is_real:
            raise CalcError(f"{name} requires a real number")
        function = math.floor if name == "floor" else math.ceil
        return Scalar(Fraction(function(float(value.real))))
    if name == "round":
        _require_range(name, args, 1, 2)
        value = _scalar_arg(name, args, 0)
        if not value.is_real:
            raise CalcError("round requires a real number")
        digits = int(_real_arg(name, args, 1)) if len(args) == 2 else 0
        return Scalar(Fraction(round(float(value.real), digits)))
    if name == "sign":
        _require_count(name, args, 1)
        value = _scalar_arg(name, args, 0)
        if not value.is_real:
            raise CalcError("sign requires a real number")
        return Scalar(Fraction(_num_sign(value.real)))
    if name in ("fact", "gamma"):
        _require_count(name, args, 1)
        value = _scalar_arg(name, args, 0)
        if name == "fact":
            return _factorial(value)
        if not value.is_real:
            raise CalcError("gamma requires a real number")
        try:
            return Scalar(math.gamma(float(value.real)))
        except (ValueError, OverflowError) as exc:
            raise CalcError("gamma domain error") from exc
    if name == "erf":
        _require_count(name, args, 1)
        return Scalar(math.erf(_real_arg(name, args, 0)))
    if name in ("min", "max"):
        values = _flatten_numbers(name, args)
        if not values:
            raise CalcError(f"{name} needs at least one number")
        selector = min if name == "min" else max
        return selector(values, key=lambda value: float(value.real))
    if name == "sum":
        values = _flatten_numbers(name, args)
        total = Scalar(Fraction(0))
        for value in values:
            total = total.add(value)
        return total
    if name == "prod":
        values = _flatten_numbers(name, args)
        total = Scalar(Fraction(1))
        for value in values:
            total = total.mul(value)
        return total
    if name in ("mean", "median"):
        import statistics

        values = [float(value.real) for value in _flatten_numbers(name, args)]
        if not values:
            raise CalcError(f"{name} needs at least one number")
        if name == "mean":
            return Scalar(sum(values) / len(values))
        return Scalar(statistics.median(values))
    if name in ("gcd", "lcm"):
        values = _flatten_numbers(name, args)
        if not values:
            raise CalcError(f"{name} needs at least one number")
        integers = []
        for value in values:
            if not value.is_integer:
                raise CalcError(f"{name} requires integers")
            integers.append(int(value.real))
        result = integers[0]
        for number in integers[1:]:
            result = math.gcd(result, number) if name == "gcd" else math.lcm(result, number)
        return Scalar(Fraction(result))
    if name in ("ncr", "npr"):
        _require_count(name, args, 2)
        n = _scalar_arg(name, args, 0)
        r = _scalar_arg(name, args, 1)
        if not n.is_integer or not r.is_integer:
            raise CalcError(f"{name} requires integers")
        n_int = int(n.real)
        r_int = int(r.real)
        if n_int < 0 or r_int < 0 or r_int > n_int:
            raise CalcError(f"{name} requires 0 ≤ r ≤ n")
        if name == "ncr":
            result = math.comb(n_int, r_int)
        else:
            result = math.perm(n_int, r_int)
        return Scalar(Fraction(result))
    if name == "mod":
        _require_count(name, args, 2)
        return _mod_values(args[0], args[1])
    if name == "hypot":
        _require_range(name, args, 2, 8)
        values = [_real_arg(name, args, index) for index in range(len(args))]
        return Scalar(math.hypot(*values))
    if name == "det":
        _require_count(name, args, 1)
        return _matrix_arg(name, args, 0).det()
    if name == "inv":
        _require_count(name, args, 1)
        return _matrix_arg(name, args, 0).inverse()
    if name == "transpose":
        _require_count(name, args, 1)
        return _matrix_arg(name, args, 0).transpose()
    if name == "rref":
        _require_count(name, args, 1)
        return _matrix_arg(name, args, 0).rref()
    if name in ("re", "im"):
        _require_count(name, args, 1)
        value = _scalar_arg(name, args, 0)
        return Scalar(value.real if name == "re" else value.imag)
    if name == "conj":
        _require_count(name, args, 1)
        value = _scalar_arg(name, args, 0)
        return Scalar(value.real, _num_neg(value.imag))
    if name == "arg":
        _require_count(name, args, 1)
        value = _scalar_arg(name, args, 0)
        angle = math.atan2(float(value.imag), float(value.real))
        if ctx.angle_degrees:
            angle = math.degrees(angle)
        return Scalar(_snap(angle))
    if name == "rand":
        import random

        _require_range(name, args, 0, 2)
        if len(args) == 0:
            return Scalar(random.random())
        low = _real_arg(name, args, 0)
        high = _real_arg(name, args, 1) if len(args) == 2 else 1.0
        return Scalar(random.uniform(low, high))
    if name == "degrees":
        _require_count(name, args, 1)
        return Scalar(math.degrees(_real_arg(name, args, 0)))
    if name == "radians":
        _require_count(name, args, 1)
        return Scalar(math.radians(_real_arg(name, args, 0)))
    if name == "convert":
        _require_count(name, args, 3)
        value = _scalar_arg(name, args, 0)
        source = args[1]
        target = args[2]
        if not isinstance(source, str) or not isinstance(target, str):
            raise CalcError("convert expects unit names in quotes")
        return convert_units(value, source, target)
    raise CalcError(f"Unknown function '{name}'")


# ---------------------------------------------------------------------------
# Tokenizer and parser
# ---------------------------------------------------------------------------
_NUMBER_RE = re.compile(r"(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?")
_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_SUPERSCRIPT_MAP = {
    "⁰": "^0",
    "¹": "^1",
    "²": "^2",
    "³": "^3",
    "⁴": "^4",
    "⁵": "^5",
    "⁶": "^6",
    "⁷": "^7",
    "⁸": "^8",
    "⁹": "^9",
}


def preprocess(expr: str) -> str:
    """Normalise friendly Unicode input into ASCII parser syntax."""
    if not isinstance(expr, str):
        raise CalcError("Expression must be text")
    text = expr
    replacements = {
        "×": "*",
        "·": "*",
        "÷": "/",
        "−": "-",
        "–": "-",
        "—": "-",
        "π": " pi ",
        "√": "sqrt ",
        "∫": "integral ",
        "∞": " inf ",
        "∣": "|",
    }
    for source, target in replacements.items():
        text = text.replace(source, target)
    for source, target in _SUPERSCRIPT_MAP.items():
        text = text.replace(source, target)
    # Degree sign: angle degrees in normal math, but keep °C/°F for units.
    # Wrap a numeric degree argument so that "sin 30°" binds the whole
    # degree value to the function rather than multiplying sin(30).
    text = re.sub(
        r"(\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)\s*°(?![CFK])",
        r"(\1*__degree__)",
        text,
    )
    output = []
    for index, char in enumerate(text):
        if char == "°":
            following = text[index + 1 : index + 2]
            if following not in ("C", "F", "K"):
                output.append("*__degree__ ")
                continue
        output.append(char)
    return "".join(output)


@dataclass(frozen=True)
class Token:
    kind: str
    value: str


def tokenize(expr: str) -> List[Token]:
    """Tokenize an expression after :func:`preprocess`."""
    text = preprocess(expr)
    tokens: List[Token] = []
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        if char.isspace():
            index += 1
            continue
        match = _NUMBER_RE.match(text, index)
        if match:
            tokens.append(Token("num", match.group(0)))
            index = match.end()
            continue
        match = _NAME_RE.match(text, index)
        if match:
            tokens.append(Token("name", match.group(0).lower()))
            index = match.end()
            continue
        if char in "\"'":
            quote = char
            index += 1
            start = index
            while index < length and text[index] != quote:
                index += 1
            if index >= length:
                raise CalcError("Unterminated text value")
            tokens.append(Token("string", text[start:index]))
            index += 1
            continue
        if text.startswith("**", index):
            tokens.append(Token("op", "^"))
            index += 2
            continue
        if text.startswith("==", index):
            tokens.append(Token("op", "="))
            index += 2
            continue
        if char in "+-*/%^!(),=[]{};":
            if char == "{":
                tokens.append(Token("op", "["))
            elif char == "}":
                tokens.append(Token("op", "]"))
            else:
                tokens.append(Token("op", char))
            index += 1
            continue
        raise CalcError(f"Unexpected character '{char}'")
    tokens.append(Token("eof", ""))
    return tokens


class Parser:
    """Recursive-descent parser producing :class:`Node` trees."""

    def __init__(self, tokens: Sequence[Token]) -> None:
        self.tokens = list(tokens)
        self.pos = 0

    def peek(self, offset: int = 0) -> Token:
        position = self.pos + offset
        if position >= len(self.tokens):
            return Token("eof", "")
        return self.tokens[position]

    def advance(self) -> Token:
        token = self.peek()
        self.pos += 1
        return token

    def expect(self, kind: str, value: str) -> Token:
        token = self.peek()
        if token.kind != kind or token.value != value:
            raise CalcError(f"Expected '{value}'")
        return self.advance()

    def parse(self) -> Node:
        if self.peek().kind == "eof":
            raise CalcError("Empty expression")
        node = self.parse_expression()
        if self.peek().kind != "eof":
            token = self.peek()
            shown = token.value or token.kind
            raise CalcError(f"Unexpected trailing input '{shown}'")
        return node

    def parse_expression(self) -> Node:
        node = self.parse_term()
        while True:
            token = self.peek()
            if token.kind == "op" and token.value in ("+", "-"):
                self.advance()
                right = self.parse_term()
                node = BinaryNode(token.value, node, right)
                continue
            if token.kind == "op" and token.value == "=":
                self.advance()
                right = self.parse_expression()
                node = EquationNode(node, right)
                continue
            break
        return node

    def parse_term(self) -> Node:
        node = self.parse_unary()
        while True:
            token = self.peek()
            if token.kind == "op" and token.value in ("*", "/"):
                self.advance()
                right = self.parse_unary()
                node = BinaryNode(token.value, node, right)
                continue
            if token.kind == "op" and token.value == "%":
                if self._starts_primary(self.peek(1)):
                    self.advance()
                    right = self.parse_unary()
                    node = BinaryNode("%", node, right)
                    continue
                break
            if token.kind == "name" and token.value == "mod":
                self.advance()
                right = self.parse_unary()
                node = BinaryNode("%", node, right)
                continue
            if self._implicit_multiplication_allowed():
                right = self.parse_unary()
                node = BinaryNode("*", node, right)
                continue
            break
        return node

    def _implicit_multiplication_allowed(self) -> bool:
        token = self.peek()
        if not self._starts_primary(token):
            return False
        previous = self.tokens[self.pos - 1] if self.pos > 0 else None
        if (
            previous is not None
            and previous.kind == "num"
            and token.kind == "num"
        ):
            return False
        return True

    @staticmethod
    def _starts_primary(token: Token) -> bool:
        if token.kind in ("num", "name"):
            return True
        return token.kind == "op" and token.value in ("(", "[", "{")

    def parse_unary(self) -> Node:
        token = self.peek()
        if token.kind == "op" and token.value in ("+", "-"):
            self.advance()
            operand = self.parse_unary()
            if token.value == "+":
                return operand
            return UnaryNode("-", operand)
        return self.parse_power()

    def parse_power(self) -> Node:
        base = self.parse_postfix()
        token = self.peek()
        if token.kind == "op" and token.value == "^":
            self.advance()
            exponent = self.parse_unary()
            return BinaryNode("^", base, exponent)
        return base

    def parse_postfix(self) -> Node:
        node = self.parse_primary()
        while True:
            token = self.peek()
            if token.kind == "op" and token.value == "!":
                self.advance()
                node = PostfixNode("!", node)
                continue
            if token.kind == "op" and token.value == "%":
                if self._starts_primary(self.peek(1)):
                    break
                self.advance()
                node = PostfixNode("%", node)
                continue
            break
        return node

    def parse_primary(self) -> Node:
        token = self.advance()
        if token.kind == "num":
            try:
                value = Fraction(Decimal(token.value))
            except (InvalidOperation, ValueError) as exc:
                raise CalcError(f"Invalid number '{token.value}'") from exc
            return NumNode(Scalar(value))
        if token.kind == "string":
            return StringNode(token.value)
        if token.kind == "name":
            name = token.value
            if name in FUNCTION_NAMES:
                if self.peek().kind == "op" and self.peek().value == "(":
                    return self._parse_call(name)
                if self._starts_primary(self.peek()):
                    argument = self.parse_unary()
                    return CallNode(name, [argument])
                raise CalcError(f"Expected an argument after {name}")
            return VarNode(name)
        if token.kind == "op" and token.value == "(":
            node = self.parse_expression()
            self.expect("op", ")")
            return node
        if token.kind == "op" and token.value in ("[", "{"):
            return self._parse_list(token.value)
        if token.kind == "eof":
            raise CalcError("Unexpected end of expression")
        raise CalcError(f"Unexpected token '{token.value}'")

    def _parse_call(self, name: str) -> CallNode:
        self.expect("op", "(")
        args: List[Node] = []
        if self.peek().kind == "op" and self.peek().value == ")":
            self.advance()
            return CallNode(name, args)
        while True:
            args.append(self.parse_expression())
            token = self.peek()
            if token.kind == "op" and token.value == ",":
                self.advance()
                continue
            if token.kind == "op" and token.value == ")":
                self.advance()
                break
            raise CalcError("Expected ',' or ')' in function arguments")
        return CallNode(name, args)

    def _parse_list(self, opening: str) -> Node:
        closing = "]" if opening in ("[", "{") else ")"
        rows: List[List[Node]] = []
        current: List[Node] = []
        while True:
            token = self.peek()
            if token.kind == "op" and token.value == closing:
                self.advance()
                break
            if token.kind == "op" and token.value == ";":
                self.advance()
                if not current:
                    raise CalcError("Empty matrix row")
                rows.append(current)
                current = []
                continue
            current.append(self.parse_expression())
            token = self.peek()
            if token.kind == "op" and token.value == ",":
                self.advance()
                continue
            if token.kind == "op" and token.value == ";":
                continue
            if token.kind == "op" and token.value == closing:
                continue
            raise CalcError("Expected ',' or ']' in a list")
        if current:
            rows.append(current)
        if not rows:
            raise CalcError("An empty list is not allowed")
        if len(rows) == 1:
            return ListNode(rows[0])
        return ListNode([ListNode(row) for row in rows])


# ---------------------------------------------------------------------------
# Polynomial helpers, solving and symbolic integration
# ---------------------------------------------------------------------------
def _poly_trim(coefficients: List[Scalar]) -> List[Scalar]:
    while len(coefficients) > 1 and coefficients[-1].is_zero:
        coefficients.pop()
    return coefficients


def _poly_add(left: List[Scalar], right: List[Scalar]) -> List[Scalar]:
    size = max(len(left), len(right))
    result = []
    for index in range(size):
        a = left[index] if index < len(left) else Scalar(Fraction(0))
        b = right[index] if index < len(right) else Scalar(Fraction(0))
        result.append(a.add(b))
    return _poly_trim(result)


def _poly_sub(left: List[Scalar], right: List[Scalar]) -> List[Scalar]:
    size = max(len(left), len(right))
    result = []
    for index in range(size):
        a = left[index] if index < len(left) else Scalar(Fraction(0))
        b = right[index] if index < len(right) else Scalar(Fraction(0))
        result.append(a.sub(b))
    return _poly_trim(result)


def _poly_scale(coefficients: List[Scalar], factor: Scalar) -> List[Scalar]:
    return _poly_trim([entry.mul(factor) for entry in coefficients])


def _poly_mul(left: List[Scalar], right: List[Scalar]) -> List[Scalar]:
    result = [Scalar(Fraction(0)) for _ in range(len(left) + len(right) - 1)]
    for i, a in enumerate(left):
        for j, b in enumerate(right):
            result[i + j] = result[i + j].add(a.mul(b))
    return _poly_trim(result)


def _poly_coeffs(
    node: Node,
    variable: str,
    env: Dict[str, Any],
    ctx: EvalContext,
) -> Optional[List[Scalar]]:
    """Return polynomial coefficients, or ``None`` if not polynomial."""
    node = node.simplify()
    if isinstance(node, NumNode):
        return [node.value]
    if isinstance(node, VarNode):
        if node.name == variable:
            return [Scalar(Fraction(0)), Scalar(Fraction(1))]
        value = node.eval(env, EvalContext(record_steps=False))
        if not isinstance(value, Scalar):
            return None
        return [value]
    if isinstance(node, UnaryNode):
        inner = _poly_coeffs(node.operand, variable, env, ctx)
        if inner is None:
            return None
        return _poly_scale(inner, Scalar(Fraction(-1)))
    if isinstance(node, BinaryNode):
        left = _poly_coeffs(node.left, variable, env, ctx)
        right = _poly_coeffs(node.right, variable, env, ctx)
        if left is None or right is None:
            return None
        if node.op == "+":
            return _poly_add(left, right)
        if node.op == "-":
            return _poly_sub(left, right)
        if node.op == "*":
            return _poly_mul(left, right)
        if node.op == "/":
            if len(right) != 1 or right[0].is_zero:
                return None
            return _poly_scale(left, Scalar(Fraction(1)).div(right[0]))
        if node.op == "^":
            if len(right) != 1 or not right[0].is_real or not right[0].is_integer:
                return None
            power = int(right[0].real)
            if power < 0 or power > 12:
                return None
            result = [Scalar(Fraction(1))]
            for _ in range(power):
                result = _poly_mul(result, left)
            return result
        return None
    if isinstance(node, CallNode):
        try:
            value = node.eval(
                env,
                EvalContext(
                    record_steps=False, angle_degrees=ctx.angle_degrees
                ),
            )
        except CalcError:
            return None
        if isinstance(value, Scalar):
            return [value]
        return None
    if isinstance(node, EquationNode):
        return _poly_coeffs(
            BinaryNode("-", node.left, node.right), variable, env, ctx
        )
    return None


def _linear_coeffs(
    node: Node, variable: str, env: Dict[str, Any], ctx: EvalContext
) -> Optional[Tuple[Scalar, Scalar]]:
    coefficients = _poly_coeffs(node, variable, env, ctx)
    if coefficients is None or len(coefficients) > 2:
        return None
    a = coefficients[1] if len(coefficients) > 1 else Scalar(Fraction(0))
    b = coefficients[0]
    return a, b


def _quadratic_roots(a: Scalar, b: Scalar, c: Scalar) -> List[Scalar]:
    discriminant = b.mul(b).sub(
        Scalar(Fraction(4)).mul(a).mul(c)
    )
    root = _sqrt_value(discriminant)
    denominator = Scalar(Fraction(2)).mul(a)
    return [
        b.neg().add(root).div(denominator),
        b.neg().sub(root).div(denominator),
    ]


def _filter_solutions(
    roots: List[Scalar],
    lower: Optional[Scalar],
    upper: Optional[Scalar],
) -> List[Scalar]:
    if lower is None and upper is None:
        return roots
    low = lower.as_float() if lower is not None else None
    high = upper.as_float() if upper is not None else None
    filtered: List[Scalar] = []
    for root in roots:
        if not root.is_real:
            continue
        value = float(root.real)
        if low is not None and value < low - 1e-9:
            continue
        if high is not None and value > high + 1e-9:
            continue
        filtered.append(root)
    if not filtered:
        raise CalcError("No solutions in the selected range")
    return filtered


def solve_equation(
    target: Node,
    variable: str,
    env: Dict[str, Any],
    ctx: EvalContext,
    lower: Optional[Scalar],
    upper: Optional[Scalar],
    steps: List[str],
) -> List[Scalar]:
    """Solve ``target = 0`` for *variable*, returning solutions."""
    coefficients = _poly_coeffs(target, variable, env, ctx)
    if coefficients is not None:
        coefficients = _poly_trim(coefficients)
        degree = len(coefficients) - 1
        if degree == 0:
            if coefficients[0].is_zero:
                raise CalcError("The equation is true for every value")
            raise CalcError("The equation has no solution")
        if degree == 1:
            a = coefficients[1]
            b = coefficients[0]
            root = b.neg().div(a)
            steps.append(
                f"Linear equation: {format_scalar(a)}·{variable} + "
                f"{format_scalar(b)} = 0."
            )
            steps.append(
                f"{variable} = −b/a = {format_scalar(root)}."
            )
            return _filter_solutions([root], lower, upper)
        if degree == 2:
            a, b, c = coefficients[2], coefficients[1], coefficients[0]
            discriminant = b.mul(b).sub(
                Scalar(Fraction(4)).mul(a).mul(c)
            )
            steps.append(
                f"Quadratic equation: {format_scalar(a)}·{variable}² + "
                f"{format_scalar(b)}·{variable} + {format_scalar(c)} = 0."
            )
            steps.append(
                f"Discriminant Δ = b² − 4ac = {format_scalar(discriminant)}."
            )
            roots = _quadratic_roots(a, b, c)
            steps.append(
                f"{variable} = (−b ± √Δ) / (2a)."
            )
            if roots[0].sub(roots[1]).is_zero:
                return _filter_solutions([roots[0]], lower, upper)
            return _filter_solutions(roots, lower, upper)
    # General numeric fallback.
    low = lower.as_float() if lower is not None else -10.0
    high = upper.as_float() if upper is not None else 10.0
    if not high > low:
        raise CalcError("The upper bound must be greater than the lower bound")
    steps.append(
        f"Search for sign changes of the expression on [{low:g}, {high:g}]."
    )
    samples = 2000
    previous_x = low
    previous_value = _evaluate_real(target, variable, previous_x, env, ctx)
    roots: List[Scalar] = []
    for index in range(1, samples + 1):
        current_x = low + (high - low) * index / samples
        current_value = _evaluate_real(target, variable, current_x, env, ctx)
        if previous_value is not None and current_value is not None:
            if abs(previous_value) < 1e-10:
                _append_root(roots, previous_x)
            if previous_value * current_value < 0:
                root = _bisect_root(
                    target, variable, env, ctx, previous_x, current_x
                )
                if root is not None:
                    _append_root(roots, root)
        previous_x = current_x
        previous_value = current_value
    if not roots:
        raise CalcError("No real solutions found in the selected range")
    steps.append(
        "Numeric roots: "
        + ", ".join(format_scalar(value, 10, False) for value in roots)
    )
    return roots[:20]


def _evaluate_real(
    node: Node,
    variable: str,
    x_value: float,
    env: Dict[str, Any],
    ctx: EvalContext,
) -> Optional[float]:
    local_env = dict(env)
    local_env[variable] = Scalar(x_value)
    try:
        value = node.eval(
            local_env,
            EvalContext(
                angle_degrees=ctx.angle_degrees,
                ans=ctx.ans,
                record_steps=False,
            ),
        )
    except CalcError:
        return None
    if not isinstance(value, Scalar) or not value.is_real:
        return None
    return float(value.real)


def _bisect_root(
    node: Node,
    variable: str,
    env: Dict[str, Any],
    ctx: EvalContext,
    low: float,
    high: float,
) -> Optional[float]:
    low_value = _evaluate_real(node, variable, low, env, ctx)
    high_value = _evaluate_real(node, variable, high, env, ctx)
    if low_value is None or high_value is None:
        return None
    for _ in range(80):
        middle = (low + high) / 2.0
        middle_value = _evaluate_real(node, variable, middle, env, ctx)
        if middle_value is None:
            return None
        if abs(middle_value) < 1e-13:
            return middle
        if low_value * middle_value <= 0:
            high = middle
            high_value = middle_value
        else:
            low = middle
            low_value = middle_value
    return (low + high) / 2.0


def _append_root(roots: List[Scalar], value: float) -> None:
    for existing in roots:
        if abs(float(existing.real) - value) < 1e-7:
            return
    roots.append(Scalar(value))


def integrate_node(
    node: Node, variable: str, steps: List[str]
) -> Node:
    """A small symbolic integrator for common one-variable forms."""
    node = node.simplify()
    # Polynomials are handled term-by-term.
    coefficients = _poly_coeffs(node, variable, {}, EvalContext(record_steps=False))
    if coefficients is not None and len(coefficients) > 1:
        terms: List[Node] = []
        for power in range(len(coefficients) - 1, -1, -1):
            coefficient = coefficients[power]
            if coefficient.is_zero:
                continue
            new_power = power + 1
            scaled = coefficient.div(Scalar(Fraction(new_power)))
            term = BinaryNode(
                "*",
                NumNode(scaled),
                BinaryNode(
                    "^",
                    VarNode(variable),
                    NumNode(Scalar(Fraction(new_power))),
                ),
            )
            terms.append(term)
        if not terms:
            return NumNode(Scalar(Fraction(0)))
        result = terms[0]
        for term in terms[1:]:
            result = BinaryNode("+", result, term)
        steps.append("Integrate the polynomial term by term.")
        return result.simplify()
    if isinstance(node, NumNode):
        steps.append("The integral of a constant is the constant times x.")
        return BinaryNode("*", node, VarNode(variable)).simplify()
    if isinstance(node, VarNode):
        if node.name != variable:
            return BinaryNode("*", node, VarNode(variable)).simplify()
        steps.append("Power rule for integrals.")
        return BinaryNode(
            "/",
            BinaryNode(
                "^", VarNode(variable), NumNode(Scalar(Fraction(2)))
            ),
            NumNode(Scalar(Fraction(2))),
        )
    if isinstance(node, UnaryNode):
        return UnaryNode(
            "-", integrate_node(node.operand, variable, steps)
        ).simplify()
    if isinstance(node, BinaryNode):
        if node.op in ("+", "-"):
            return BinaryNode(
                node.op,
                integrate_node(node.left, variable, steps),
                integrate_node(node.right, variable, steps),
            ).simplify()
        if node.op == "*":
            if not node.left.contains_var(variable):
                return BinaryNode(
                    "*",
                    node.left,
                    integrate_node(node.right, variable, steps),
                ).simplify()
            if not node.right.contains_var(variable):
                return BinaryNode(
                    "*",
                    node.right,
                    integrate_node(node.left, variable, steps),
                ).simplify()
        if node.op == "/":
            if not node.right.contains_var(variable):
                return BinaryNode(
                    "/",
                    integrate_node(node.left, variable, steps),
                    node.right,
                ).simplify()
            if not node.left.contains_var(variable) and isinstance(
                node.right, VarNode
            ):
                steps.append("The integral of 1/x is ln|x|.")
                return BinaryNode(
                    "*",
                    node.left,
                    CallNode("ln", [CallNode("abs", [node.right])]),
                ).simplify()
        if node.op == "^" and isinstance(node.left, VarNode):
            if (
                isinstance(node.right, NumNode)
                and node.right.value.is_real
                and isinstance(node.right.value.real, Fraction)
                and node.right.value.real.denominator == 1
            ):
                power = node.right.value.real.numerator
                if power == -1:
                    steps.append("The integral of x⁻¹ is ln|x|.")
                    return CallNode("ln", [CallNode("abs", [node.left])])
                new_power = power + 1
                steps.append(
                    "Power rule: ∫xⁿ dx = xⁿ⁺¹/(n+1)."
                )
                return BinaryNode(
                    "/",
                    BinaryNode(
                        "^",
                        node.left,
                        NumNode(Scalar(Fraction(new_power))),
                    ),
                    NumNode(Scalar(Fraction(new_power))),
                ).simplify()
        if node.op == "^":
            linear = _linear_coeffs(node.left, variable, {}, EvalContext(record_steps=False))
            if linear is not None and isinstance(node.right, NumNode):
                a, _b = linear
                power = node.right.value
                if power.is_real and isinstance(power.real, Fraction):
                    exponent = power.real
                    if exponent == -1:
                        return BinaryNode(
                            "/",
                            CallNode(
                                "ln", [CallNode("abs", [node.left])]
                            ),
                            NumNode(a),
                        ).simplify()
                    new_power = exponent + 1
                    return BinaryNode(
                        "/",
                        BinaryNode(
                            "^",
                            node.left,
                            NumNode(Scalar(new_power)),
                        ),
                        NumNode(a.mul(Scalar(new_power))),
                    ).simplify()
    if isinstance(node, CallNode) and len(node.args) == 1:
        linear = _linear_coeffs(
            node.args[0], variable, {}, EvalContext(record_steps=False)
        )
        if linear is not None:
            a, _b = linear
            argument = node.args[0]
            if node.name == "sin":
                steps.append("∫sin(u) du = −cos(u).")
                return BinaryNode(
                    "/",
                    UnaryNode("-", CallNode("cos", [argument])),
                    NumNode(a),
                ).simplify()
            if node.name == "cos":
                steps.append("∫cos(u) du = sin(u).")
                return BinaryNode(
                    "/",
                    CallNode("sin", [argument]),
                    NumNode(a),
                ).simplify()
            if node.name == "exp":
                steps.append("∫eᵘ du = eᵘ.")
                return BinaryNode(
                    "/",
                    CallNode("exp", [argument]),
                    NumNode(a),
                ).simplify()
            if node.name == "sqrt":
                steps.append("Rewrite √u as u^(1/2), then use the power rule.")
                return integrate_node(
                    BinaryNode(
                        "^",
                        argument,
                        NumNode(Scalar(Fraction(1, 2))),
                    ),
                    variable,
                    steps,
                )
            if node.name == "ln" and isinstance(argument, VarNode):
                steps.append("∫ln(x) dx = x·ln(x) − x.")
                return BinaryNode(
                    "-",
                    BinaryNode("*", VarNode(variable), CallNode("ln", [VarNode(variable)])),
                    VarNode(variable),
                )
    raise CalcError(
        "This integral is not supported symbolically; use definite bounds "
        "for a numeric result"
    )


def numeric_integral(
    node: Node,
    variable: str,
    lower: Scalar,
    upper: Scalar,
    env: Dict[str, Any],
    angle_degrees: bool,
) -> Scalar:
    """Composite Simpson integration for a real-valued expression."""
    if not lower.is_real or not upper.is_real:
        raise CalcError("Integration bounds must be real")
    low = float(lower.real)
    high = float(upper.real)
    if high == low:
        return Scalar(Fraction(0))
    intervals = 2000
    if intervals % 2:
        intervals += 1
    step = (high - low) / intervals
    total = 0.0
    for index in range(intervals + 1):
        x_value = low + step * index
        value = _evaluate_real(
            node,
            variable,
            x_value,
            env,
            EvalContext(angle_degrees=angle_degrees, record_steps=False),
        )
        if value is None:
            raise CalcError("The integrand is undefined in this interval")
        weight = 1 if index in (0, intervals) else (4 if index % 2 else 2)
        total += weight * value
    return Scalar(total * step / 3.0)


def definite_integral(
    node: Node,
    variable: str,
    lower: Scalar,
    upper: Scalar,
    env: Dict[str, Any],
    angle_degrees: bool,
) -> Scalar:
    """Try an exact antiderivative, then fall back to Simpson's rule."""
    try:
        steps: List[str] = []
        antiderivative = integrate_node(node, variable, steps)
        local_env = dict(env)
        local_env[variable] = lower
        low_value = antiderivative.eval(
            local_env,
            EvalContext(angle_degrees=False, record_steps=False),
        )
        local_env[variable] = upper
        high_value = antiderivative.eval(
            local_env,
            EvalContext(angle_degrees=False, record_steps=False),
        )
        if isinstance(low_value, Scalar) and isinstance(high_value, Scalar):
            return high_value.sub(low_value)
    except CalcError:
        pass
    return numeric_integral(node, variable, lower, upper, env, angle_degrees)


# ---------------------------------------------------------------------------
# Units
# ---------------------------------------------------------------------------
# canonical -> (dimension, factor to the base unit)
_UNIT_TABLE: Dict[str, Tuple[str, Number]] = {
    # length (base: metre)
    "m": ("length", Fraction(1)),
    "km": ("length", Fraction(1000)),
    "cm": ("length", Fraction(1, 100)),
    "mm": ("length", Fraction(1, 1000)),
    "um": ("length", Fraction(1, 1_000_000)),
    "mi": ("length", Fraction(201168, 125)),
    "yd": ("length", Fraction(1143, 1250)),
    "ft": ("length", Fraction(381, 1250)),
    "in": ("length", Fraction(127, 5000)),
    "nmi": ("length", Fraction(1852)),
    # mass (base: kilogram)
    "kg": ("mass", Fraction(1)),
    "g": ("mass", Fraction(1, 1000)),
    "mg": ("mass", Fraction(1, 1_000_000)),
    "lb": ("mass", Fraction(45359237, 100_000_000)),
    "oz": ("mass", Fraction(28349523125, 1_000_000_000_000)),
    "tonne": ("mass", Fraction(1000)),
    # time (base: second)
    "s": ("time", Fraction(1)),
    "ms": ("time", Fraction(1, 1000)),
    "us": ("time", Fraction(1, 1_000_000)),
    "min": ("time", Fraction(60)),
    "h": ("time", Fraction(3600)),
    "day": ("time", Fraction(86400)),
    "week": ("time", Fraction(604800)),
    "year": ("time", Fraction(31557600)),
    # volume (base: litre)
    "L": ("volume", Fraction(1)),
    "mL": ("volume", Fraction(1, 1000)),
    "gal": ("volume", Fraction(3785411784, 1_000_000_000)),
    "floz": ("volume", Fraction(295735295625, 10_000_000_000_000)),
    # angle (base: radian)
    "rad": ("angle", Fraction(1)),
    "deg": ("angle", math.pi / 180.0),
    "grad": ("angle", math.pi / 200.0),
    # speed (base: metre/second)
    "m/s": ("speed", Fraction(1)),
    "km/h": ("speed", Fraction(5, 18)),
    "mph": ("speed", Fraction(44704, 100_000)),
    "knot": ("speed", Fraction(1852, 3600)),
    # data (base: byte)
    "B": ("data", Fraction(1)),
    "KB": ("data", Fraction(1000)),
    "MB": ("data", Fraction(1_000_000)),
    "GB": ("data", Fraction(1_000_000_000)),
    "TB": ("data", Fraction(1_000_000_000_000)),
    "KiB": ("data", Fraction(1024)),
    "MiB": ("data", Fraction(1_048_576)),
    "GiB": ("data", Fraction(1_073_741_824)),
}

_UNIT_ALIASES: Dict[str, str] = {
    # length
    "meter": "m", "meters": "m", "metre": "m", "metres": "m",
    "kilometer": "km", "kilometers": "km", "kilometre": "km",
    "kilometres": "km",
    "centimeter": "cm", "centimeters": "cm", "centimetre": "cm",
    "centimetres": "cm",
    "millimeter": "mm", "millimeters": "mm", "millimetre": "mm",
    "millimetres": "mm",
    "micrometer": "um", "micrometers": "um", "micron": "um",
    "mile": "mi", "miles": "mi",
    "yard": "yd", "yards": "yd",
    "foot": "ft", "feet": "ft",
    "inch": "in", "inches": "in",
    "nauticalmile": "nmi", "nauticalmiles": "nmi",
    # mass
    "kilogram": "kg", "kilograms": "kg", "kilo": "kg", "kilos": "kg",
    "gram": "g", "grams": "g",
    "milligram": "mg", "milligrams": "mg",
    "pound": "lb", "pounds": "lb", "lbs": "lb",
    "ounce": "oz", "ounces": "oz",
    "ton": "tonne", "tons": "tonne", "tonne": "tonne", "tonnes": "tonne",
    # time
    "sec": "s", "secs": "s", "second": "s", "seconds": "s",
    "millisecond": "ms", "milliseconds": "ms",
    "microsecond": "us", "microseconds": "us",
    "minute": "min", "minutes": "min", "mins": "min",
    "hour": "h", "hours": "h", "hr": "h", "hrs": "h",
    "days": "day",
    "weeks": "week",
    "year": "year", "years": "year", "yr": "year", "yrs": "year",
    # volume
    "l": "L", "liter": "L", "liters": "L", "litre": "L", "litres": "L",
    "ml": "mL", "milliliter": "mL", "milliliters": "mL",
    "millilitre": "mL", "millilitres": "mL",
    "gallon": "gal", "gallons": "gal",
    "floz": "floz", "fluidounce": "floz", "fluidounces": "floz",
    # angle
    "degree": "deg", "degrees": "deg",
    "radian": "rad", "radians": "rad",
    "gradian": "grad", "gradians": "grad",
    # speed
    "mps": "m/s", "meterspersecond": "m/s", "metrespersecond": "m/s",
    "kmh": "km/h", "kph": "km/h", "kilometersperhour": "km/h",
    "kilometresperhour": "km/h",
    "knots": "knot", "kn": "knot",
    # data
    "byte": "B", "bytes": "B",
    "kilobyte": "KB", "kilobytes": "KB",
    "megabyte": "MB", "megabytes": "MB",
    "gigabyte": "GB", "gigabytes": "GB",
    "terabyte": "TB", "terabytes": "TB",
    "kib": "KiB", "mib": "MiB", "gib": "GiB",
}

_TEMPERATURE_UNITS = {"C", "F", "K"}
_UNIT_DISPLAY = {
    "m": "m", "km": "km", "cm": "cm", "mm": "mm", "um": "µm",
    "mi": "mi", "yd": "yd", "ft": "ft", "in": "in", "nmi": "nmi",
    "kg": "kg", "g": "g", "mg": "mg", "lb": "lb", "oz": "oz",
    "tonne": "t", "s": "s", "ms": "ms", "us": "µs", "min": "min",
    "h": "h", "day": "day", "week": "week", "year": "year",
    "L": "L", "mL": "mL", "gal": "gal", "floz": "fl oz",
    "rad": "rad", "deg": "°", "grad": "grad",
    "m/s": "m/s", "km/h": "km/h", "mph": "mph", "knot": "kn",
    "B": "B", "KB": "KB", "MB": "MB", "GB": "GB", "TB": "TB",
    "KiB": "KiB", "MiB": "MiB", "GiB": "GiB",
    "C": "°C", "F": "°F", "K": "K",
}


def _normalise_unit(unit: str) -> Optional[str]:
    if not isinstance(unit, str):
        return None
    text = unit.strip()
    if not text:
        return None
    if text.startswith("°"):
        text = text[1:]
    if text in _TEMPERATURE_UNITS:
        return text
    key = text.lower()
    if key in ("celsius", "centigrade"):
        return "C"
    if key in ("fahrenheit",):
        return "F"
    if key in ("kelvin",):
        return "K"
    if key in _UNIT_ALIASES:
        return _UNIT_ALIASES[key]
    for canonical in _UNIT_TABLE:
        if canonical.lower() == key:
            return canonical
    return None


def _convert_temperature(value: Scalar, source: str, target: str) -> Scalar:
    if not value.is_real:
        raise CalcError("Temperature conversion requires a real number")
    number = value.real
    if source == target:
        return value
    if source == "C":
        celsius = Scalar(number)
    elif source == "F":
        celsius = value.sub(Scalar(Fraction(32))).mul(
            Scalar(Fraction(5, 9))
        )
    else:
        celsius = value.sub(Scalar(Fraction(27315, 100)))
    if target == "C":
        return celsius
    if target == "F":
        return celsius.mul(Scalar(Fraction(9, 5))).add(
            Scalar(Fraction(32))
        )
    return celsius.add(Scalar(Fraction(27315, 100)))


def convert_units(value: Scalar, source: str, target: str) -> Scalar:
    """Convert *value* between compatible units."""
    source_unit = _normalise_unit(source)
    target_unit = _normalise_unit(target)
    if source_unit is None:
        raise CalcError(f"Unknown unit '{source}'")
    if target_unit is None:
        raise CalcError(f"Unknown unit '{target}'")
    if source_unit in _TEMPERATURE_UNITS or target_unit in _TEMPERATURE_UNITS:
        if source_unit not in _TEMPERATURE_UNITS or target_unit not in _TEMPERATURE_UNITS:
            raise CalcError("Temperature units cannot be mixed with other units")
        return _convert_temperature(value, source_unit, target_unit)
    source_info = _UNIT_TABLE.get(source_unit)
    target_info = _UNIT_TABLE.get(target_unit)
    if source_info is None or target_info is None:
        raise CalcError("Unknown unit")
    if source_info[0] != target_info[0]:
        raise CalcError(
            f"Cannot convert {_UNIT_DISPLAY.get(source_unit, source_unit)} "
            f"to {_UNIT_DISPLAY.get(target_unit, target_unit)}"
        )
    if not value.is_real:
        raise CalcError("Unit conversion requires a real number")
    source_factor = source_info[1]
    target_factor = target_info[1]
    base = _num_mul(value.real, source_factor)
    converted = _num_div(base, target_factor)
    return Scalar(converted)


_UNIT_TOKEN = r"[A-Za-z°µ][A-Za-z0-9°µ^/]*"


def _split_conversion(expr: str) -> Optional[Tuple[str, str, str]]:
    """Return ``(value_expression, source_unit, target_unit)`` if present."""
    text = expr.strip()
    # Prefer the unambiguous word "to".
    match = re.match(
        rf"^\s*(.+?)\s+to\s+({_UNIT_TOKEN})\s*$", text, re.IGNORECASE
    )
    target_text = None
    left_text = None
    if match:
        left_text = match.group(1)
        target_text = match.group(2)
    else:
        # Use the last "in" so that "5 in in cm" works (inches → cm).
        match = re.match(
            rf"^\s*(.+)\s+in\s+({_UNIT_TOKEN})\s*$", text, re.IGNORECASE
        )
        if match:
            left_text = match.group(1)
            target_text = match.group(2)
    if left_text is None or target_text is None:
        return None
    target_unit = _normalise_unit(target_text)
    if target_unit is None:
        return None
    # Split the left side into an expression and a source unit.
    left_match = re.match(
        rf"^\s*(.+?)\s*({_UNIT_TOKEN})\s*$", left_text, re.IGNORECASE
    )
    if not left_match:
        return None
    value_expr = left_match.group(1).strip()
    source_text = left_match.group(2)
    source_unit = _normalise_unit(source_text)
    if source_unit is None or not value_expr:
        return None
    return value_expr, source_unit, target_unit


def _unit_display(unit: str) -> str:
    return _UNIT_DISPLAY.get(unit, unit)


# ---------------------------------------------------------------------------
# Public calculator facade
# ---------------------------------------------------------------------------
class Calculator:
    """Pure calculator engine used by the GTK module and the test suite."""

    def __init__(
        self,
        angle_degrees: bool = False,
        precision: int = 10,
        exact_mode: bool = True,
    ) -> None:
        self.angle_degrees = bool(angle_degrees)
        self.precision = max(0, min(16, int(precision)))
        self.exact_mode = bool(exact_mode)
        self.ans: Optional[Any] = None

    # -- parsing -------------------------------------------------------
    def parse(self, expr: str) -> Node:
        """Parse *expr* into an AST without evaluating it."""
        if expr is None:
            raise CalcError("Empty expression")
        if not isinstance(expr, str):
            raise CalcError("Expression must be text")
        if not expr.strip():
            raise CalcError("Empty expression")
        return Parser(tokenize(expr)).parse()

    def compile(self, expr: str) -> Node:
        """Public alias for :meth:`parse` (used by the graph view)."""
        return self.parse(expr)

    # -- evaluation ----------------------------------------------------
    def _environment(self, variables: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        env: Dict[str, Any] = {
            "__degree__": (
                Scalar(Fraction(1))
                if self.angle_degrees
                else Scalar(math.pi / 180.0)
            )
        }
        if variables:
            for key, value in variables.items():
                key_text = str(key).lower()
                if isinstance(value, (Scalar, Matrix, str)):
                    env[key_text] = value
                else:
                    env[key_text] = Scalar.from_value(value)
        return env

    def _evaluate_core(
        self,
        expr: str,
        variables: Optional[Dict[str, Any]],
        record_steps: bool,
    ) -> Tuple[Any, EvalContext]:
        node = self.parse(expr)
        env = self._environment(variables)
        context = EvalContext(
            angle_degrees=self.angle_degrees,
            ans=self.ans,
            record_steps=record_steps,
        )
        if isinstance(node, EquationNode):
            raise CalcError(
                "An equation can only be used with solve(), for example "
                "solve(x^2=4, x)"
            )
        value = node.eval(env, context)
        return value, context

    def evaluate_detailed(
        self,
        expr: str,
        variables: Optional[Dict[str, Any]] = None,
    ) -> EvalResult:
        """Evaluate *expr* and return display-ready text and steps."""
        if expr is None or not isinstance(expr, str) or not expr.strip():
            raise CalcError("Empty expression")
        expression = expr.strip()
        conversion = _split_conversion(expression)
        if conversion is not None:
            value_expr, source_unit, target_unit = conversion
            inner_value, inner_context = self._evaluate_core(
                value_expr, variables, record_steps=True
            )
            if not isinstance(inner_value, Scalar):
                raise CalcError("Only a number can be converted")
            converted = convert_units(inner_value, source_unit, target_unit)
            unit = _unit_display(target_unit)
            exact = (
                format_scalar(converted, self.precision, exact=True)
                + f" {unit}"
            )
            decimal = (
                format_scalar(converted, self.precision, exact=False)
                + f" {unit}"
            )
            steps = list(inner_context.steps)
            steps.append(
                f"Convert {format_scalar(inner_value)} "
                f"{_unit_display(source_unit)} to {unit}."
            )
            steps.append(f"Result: {exact}")
            self._remember(converted)
            return EvalResult(
                value=converted,
                expression=expression,
                exact_text=exact,
                decimal_text="" if exact == decimal else decimal,
                steps=steps,
                unit=unit,
            )
        value, context = self._evaluate_core(
            expression, variables, record_steps=True
        )
        exact_text = format_value(value, self.precision, exact=True)
        decimal_text = format_value(value, self.precision, exact=False)
        if isinstance(value, (ExprValue, SolutionSet)):
            decimal_text = (
                format_value(value, self.precision, exact=False)
                if isinstance(value, SolutionSet)
                else ""
            )
        if exact_text == decimal_text:
            decimal_text = ""
        steps = list(context.steps)
        if isinstance(value, ExprValue):
            for step in value.steps:
                if step not in steps:
                    steps.append(step)
        elif isinstance(value, SolutionSet):
            for step in value.steps:
                if step not in steps:
                    steps.append(step)
        if isinstance(value, (Scalar, Matrix)):
            self._remember(value)
        return EvalResult(
            value=value,
            expression=expression,
            exact_text=exact_text,
            decimal_text=decimal_text,
            steps=steps,
        )

    def _remember(self, value: Any) -> None:
        if isinstance(value, Scalar):
            self.ans = value
        elif isinstance(value, Matrix):
            self.ans = value

    def evaluate(self, expr: str) -> Any:
        """Compatibility API: evaluate to a float (or complex) number."""
        result = self.evaluate_detailed(expr)
        value = result.value
        if not isinstance(value, Scalar):
            raise CalcError("The result is not a number")
        if value.is_real:
            return float(value.real)
        return value.as_complex()

    def evaluate_numeric(
        self,
        expression: Any,
        variables: Optional[Dict[str, Any]] = None,
    ) -> Optional[float]:
        """Evaluate quickly for graphing; return ``None`` on any error."""
        try:
            node = expression if isinstance(expression, Node) else self.parse(expression)
            env = self._environment(variables)
            value = node.eval(
                env,
                EvalContext(
                    angle_degrees=self.angle_degrees,
                    ans=self.ans,
                    record_steps=False,
                ),
            )
            if isinstance(value, Scalar) and value.is_real:
                return float(value.real)
        except (CalcError, OverflowError, ValueError, ZeroDivisionError):
            return None
        return None

    def evaluate_node(
        self,
        node: Node,
        variables: Optional[Dict[str, Any]] = None,
    ) -> Any:
        """Evaluate a compiled node (used by graph rows and tests)."""
        env = self._environment(variables)
        return node.eval(
            env,
            EvalContext(
                angle_degrees=self.angle_degrees,
                ans=self.ans,
                record_steps=False,
            ),
        )


def node_variables(node: Node) -> set[str]:
    """Return free variable names in *node* (constants excluded)."""
    names: set[str] = set()

    def visit(current: Node) -> None:
        if isinstance(current, VarNode):
            if (
                current.name not in _CONSTANTS
                and current.name not in ("ans", "__degree__")
            ):
                names.add(current.name)
        elif isinstance(current, UnaryNode):
            visit(current.operand)
        elif isinstance(current, PostfixNode):
            visit(current.operand)
        elif isinstance(current, BinaryNode):
            visit(current.left)
            visit(current.right)
        elif isinstance(current, EquationNode):
            visit(current.left)
            visit(current.right)
        elif isinstance(current, CallNode):
            for argument in current.args:
                visit(argument)
        elif isinstance(current, ListNode):
            for item in current.items:
                visit(item)

    visit(node)
    return names
