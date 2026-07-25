"""Safe shared parsing and evaluation for local math expressions."""

from __future__ import annotations

import ast
import math
import operator
import re
from collections.abc import Callable
from decimal import Decimal, localcontext

_PROMPT_PREFIX_RE = re.compile(
    r"^(?:cuanto\s+es|cuánto\s+es|calcula|calculame|"
    r"dime\s+cuanto\s+es|cuanto\s+da|what\s+is|"
    r"calculate|compute)\s+",
    re.IGNORECASE,
)


def normalize_math_expression(
    value: str,
    *,
    strip_prompt: bool = False,
) -> str:
    text = str(value or "").strip()
    if strip_prompt:
        text = _PROMPT_PREFIX_RE.sub("", text)
    text = (
        text.replace("multiplicado por", "*")
        .replace("multiplied by", "*")
        .replace("dividido entre", "/")
        .replace("dividido por", "/")
        .replace("divided by", "/")
        .replace("por la", "*")
        .replace("por el", "*")
        .replace(" por ", " * ")
        .replace(" times ", " * ")
        .replace(" entre ", " / ")
        .replace(" over ", " / ")
        .replace(" mas ", " + ")
        .replace(" más ", " + ")
        .replace(" plus ", " + ")
        .replace(" menos ", " - ")
        .replace(" minus ", " - ")
        .replace("×", "*")
        .replace("÷", "/")
        .replace("π", "pi")
        .replace("^", "**")
    )
    text = re.sub(
        r"√\s*(\d+(?:\.\d+)?|\([^)]+\))",
        r"sqrt(\1)",
        text,
    )
    text = re.sub(
        r"∛\s*(\d+(?:\.\d+)?|\([^)]+\))",
        r"cbrt(\1)",
        text,
    )
    return text.replace("√", "sqrt").replace("∛", "cbrt").strip("?. \t\r\n")


_CONSTANTS = {
    "pi": Decimal(str(math.pi)),
    "e": Decimal(str(math.e)),
    "tau": Decimal(str(math.tau)),
}
_BINARY_OPERATORS: dict[type[ast.operator], Callable[[Decimal, Decimal], Decimal]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
}
_ZERO_SENSITIVE_OPERATORS = (ast.Div, ast.FloorDiv, ast.Mod)


def _decimal_math(function: Callable[[float], float], value: Decimal) -> Decimal:
    return Decimal(str(function(float(value))))


def _sqrt(value: Decimal) -> Decimal:
    if value < 0:
        raise ValueError("negative sqrt")
    return _decimal_math(math.sqrt, value)


def _positive_math(
    function: Callable[[float], float],
    value: Decimal,
    error_message: str,
) -> Decimal:
    if value <= 0:
        raise ValueError(error_message)
    return _decimal_math(function, value)


def _log10(value: Decimal) -> Decimal:
    return _positive_math(math.log10, value, "invalid log arg")


def _natural_log(value: Decimal) -> Decimal:
    return _positive_math(math.log, value, "invalid ln arg")


def _round_single(value: Decimal) -> Decimal:
    return Decimal(str(round(float(value))))


def _floor(value: Decimal) -> Decimal:
    return Decimal(str(math.floor(float(value))))


def _ceil(value: Decimal) -> Decimal:
    return Decimal(str(math.ceil(float(value))))


_UNARY_FUNCTIONS: dict[str, Callable[[Decimal], Decimal]] = {
    "sqrt": _sqrt,
    "raiz": _sqrt,
    "cbrt": lambda value: _decimal_math(math.cbrt, value),
    "abs": abs,
    "sin": lambda value: _decimal_math(math.sin, value),
    "cos": lambda value: _decimal_math(math.cos, value),
    "tan": lambda value: _decimal_math(math.tan, value),
    "log": _log10,
    "ln": _natural_log,
    "exp": lambda value: _decimal_math(math.exp, value),
    "floor": _floor,
    "ceil": _ceil,
    "round": _round_single,
}


def _evaluate_binary_operation(node: ast.BinOp) -> Decimal:
    left = _evaluate_node(node.left)
    right = _evaluate_node(node.right)
    if isinstance(node.op, _ZERO_SENSITIVE_OPERATORS) and right == 0:
        raise ZeroDivisionError("division by zero")
    if isinstance(node.op, ast.Pow):
        if abs(right) > 100:
            raise ValueError("unsafe exponent")
        if right == right.to_integral_value():
            return left ** int(right)
        return Decimal(str(float(left) ** float(right)))
    operation = _BINARY_OPERATORS.get(type(node.op))
    if operation is None:
        raise ValueError("unsupported arithmetic operator")
    return operation(left, right)


def _evaluate_function_call(node: ast.Call) -> Decimal:
    if not isinstance(node.func, ast.Name) or node.keywords:
        raise ValueError("unsupported function call")
    function = node.func.id.lower()
    arguments = [_evaluate_node(argument) for argument in node.args]
    if len(arguments) == 1 and function in _UNARY_FUNCTIONS:
        return _UNARY_FUNCTIONS[function](arguments[0])
    if len(arguments) == 2 and function == "round":
        return Decimal(str(round(float(arguments[0]), int(arguments[1]))))
    if len(arguments) == 2 and function == "log":
        return Decimal(str(math.log(float(arguments[0]), float(arguments[1]))))
    raise ValueError(f"unsupported function {function}")


def _evaluate_node(node: ast.AST) -> Decimal:
    if isinstance(node, ast.Expression):
        return _evaluate_node(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return Decimal(str(node.value))
    if isinstance(node, ast.Name):
        try:
            return _CONSTANTS[node.id.lower()]
        except KeyError as exc:
            raise ValueError(f"unsupported variable {node.id}") from exc
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        value = _evaluate_node(node.operand)
        return value if isinstance(node.op, ast.UAdd) else -value
    if isinstance(node, ast.BinOp):
        return _evaluate_binary_operation(node)
    if isinstance(node, ast.Call):
        return _evaluate_function_call(node)
    raise ValueError("unsupported arithmetic node")


def evaluate_math_expression(expression: str) -> Decimal:
    clean_expression = str(expression or "").strip()
    if not clean_expression:
        raise ValueError("empty expression")
    if len(clean_expression) > 200:
        raise ValueError("expression too long")
    parsed = ast.parse(clean_expression, mode="eval")
    with localcontext() as context:
        context.prec = 28
        return _evaluate_node(parsed)


def format_math_number(
    value: Decimal,
    *,
    group_thousands: bool = True,
) -> str:
    if not value.is_finite():
        raise ValueError("non-finite arithmetic result")
    if value == value.to_integral_value():
        return f"{int(value):,}" if group_thousands else str(int(value))
    rendered = format(value.normalize(), "f").rstrip("0").rstrip(".")
    if not group_thousands:
        return rendered
    integer, dot, fraction = rendered.partition(".")
    integer = f"{int(integer):,}"
    return integer + (dot + fraction if fraction else "")
