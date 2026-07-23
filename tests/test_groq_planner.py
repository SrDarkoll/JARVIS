from types import SimpleNamespace

import pytest
from core.command_pipeline.groq_planner import GroqPlanner
from core.command_pipeline.models import CommandRequest, PlanSource


class FakeModel:
    def __init__(self, response):
        self.response = response
        self.calls = 0
        self.messages = None

    def invoke(self, messages):
        self.calls += 1
        self.messages = messages
        return self.response


def _request(request_id: str = "groq-1") -> CommandRequest:
    return CommandRequest.create(
        text="latest Python security news",
        profile_id="admin",
        channel="chat",
        request_id=request_id,
    )


def _response(*, content="", tool_calls=None):
    return SimpleNamespace(content=content, tool_calls=tool_calls)


def test_groq_tool_calls_become_a_plan_without_execution():
    response = _response(
        tool_calls=[
            {
                "id": "call-1",
                "name": "buscar_en_internet",
                "args": {"query": "latest Python security news"},
            }
        ],
    )
    model = FakeModel(response)
    planner = GroqPlanner(
        model,
        allowed_tools={"buscar_en_internet"},
    )
    messages = [object()]

    plan = planner.plan(_request(), messages)

    assert model.calls == 1
    assert model.messages is messages
    assert plan.source is PlanSource.GROQ
    assert plan.request_id == "groq-1"
    assert plan.direct_response == ""
    assert plan.steps[0].tool_name == "buscar_en_internet"
    assert plan.steps[0].step_id == "call-1"
    assert dict(plan.steps[0].arguments) == {
        "query": "latest Python security news"
    }


def test_groq_content_becomes_a_clean_direct_response():
    planner = GroqPlanner(
        FakeModel(
            _response(
                content="<think>private reasoning</think>Ready.",
                tool_calls=[],
            )
        ),
        allowed_tools=set(),
    )

    plan = planner.plan(_request(), [])

    assert plan.steps == ()
    assert plan.direct_response == "Ready."


@pytest.mark.parametrize(
    ("tool_call", "error"),
    [
        (
            {"id": "call-1", "name": "unknown", "args": {}},
            "invalid_groq_tool_call",
        ),
        (
            {
                "id": "call-1",
                "name": "buscar_en_internet",
                "args": ["not", "a", "mapping"],
            },
            "invalid_groq_tool_call",
        ),
    ],
)
def test_groq_rejects_unknown_tools_and_non_dict_arguments(
    tool_call,
    error,
):
    planner = GroqPlanner(
        FakeModel(_response(tool_calls=[tool_call])),
        allowed_tools={"buscar_en_internet"},
    )

    with pytest.raises(ValueError, match=f"^{error}$"):
        planner.plan(_request(), [])


def test_groq_rejects_more_than_five_steps():
    tool_calls = [
        {
            "id": f"call-{index}",
            "name": "buscar_en_internet",
            "args": {"query": f"query {index}"},
        }
        for index in range(6)
    ]
    planner = GroqPlanner(
        FakeModel(_response(tool_calls=tool_calls)),
        allowed_tools={"buscar_en_internet"},
    )

    with pytest.raises(ValueError, match="^groq_plan_too_large$"):
        planner.plan(_request(), [])


def test_groq_rejects_duplicate_canonical_operations():
    planner = GroqPlanner(
        FakeModel(
            _response(
                tool_calls=[
                    {
                        "id": "call-1",
                        "name": "buscar_en_internet",
                        "args": {"query": "same query", "limit": 3},
                    },
                    {
                        "id": "call-2",
                        "name": "buscar_en_internet",
                        "args": {"limit": 3, "query": "same query"},
                    },
                ]
            )
        ),
        allowed_tools={"buscar_en_internet"},
    )

    with pytest.raises(ValueError, match="^duplicate_plan_operation$"):
        planner.plan(_request(), [])


def test_groq_rejects_mixed_content_and_tool_calls():
    planner = GroqPlanner(
        FakeModel(
            _response(
                content="I already changed it.",
                tool_calls=[
                    {
                        "id": "call-1",
                        "name": "buscar_en_internet",
                        "args": {"query": "latest news"},
                    }
                ],
            )
        ),
        allowed_tools={"buscar_en_internet"},
    )

    with pytest.raises(ValueError, match="^mixed_groq_plan$"):
        planner.plan(_request(), [])


def test_groq_rejects_non_positive_step_limit():
    with pytest.raises(ValueError, match="^invalid_groq_max_steps$"):
        GroqPlanner(
            FakeModel(_response()),
            allowed_tools=set(),
            max_steps=0,
        )
