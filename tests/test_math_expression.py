from __future__ import annotations

import pytest
from core.brain import router
from tools.utilities import evaluar_expresion_matematica
from utils.math_expression import (
    evaluate_math_expression,
    format_math_number,
    normalize_math_expression,
)


def test_shared_math_evaluator_supports_existing_functions():
    expression = normalize_math_expression("2^8 + sqrt(144)")
    result = evaluate_math_expression(expression)

    assert format_math_number(result) == "268"


def test_shared_math_evaluator_rejects_unsafe_exponent():
    with pytest.raises(ValueError, match="unsafe exponent"):
        evaluate_math_expression("2 ** 101")


def test_router_and_tool_use_the_same_math_result():
    router_result = router._try_arithmetic_reply(
        "cuanto es 2^8 + sqrt(144)",
        "es",
    )
    tool_result = evaluar_expresion_matematica.invoke({"expresion": "cuanto es 2^8 + sqrt(144)"})

    assert router_result and "268" in router_result
    assert "268" in tool_result
