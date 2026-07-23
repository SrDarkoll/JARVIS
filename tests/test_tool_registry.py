from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest
from core.command_pipeline.tool_registry import ToolRegistryService


class FakeTool:
    def __init__(self, name: str) -> None:
        self.name = name


def test_registry_snapshot_stays_stable_during_replacement() -> None:
    registry = ToolRegistryService([FakeTool("one")])
    first = registry.snapshot()

    second = registry.replace([FakeTool("two")])

    assert first.version == 0
    assert set(first.by_name) == {"one"}
    assert second.version == 1
    assert set(registry.snapshot().by_name) == {"two"}


def test_registry_snapshot_mapping_is_immutable() -> None:
    registry = ToolRegistryService([FakeTool("one")])
    snapshot = registry.snapshot()

    with pytest.raises(TypeError):
        snapshot.by_name["two"] = FakeTool("two")  # type: ignore[index]


def test_registry_rejects_duplicate_or_blank_tool_names() -> None:
    with pytest.raises(ValueError, match="duplicate_tool_name"):
        ToolRegistryService([FakeTool("same"), FakeTool("same")])

    with pytest.raises(ValueError, match="invalid_tool_name"):
        ToolRegistryService([FakeTool(" ")])


def test_concurrent_readers_observe_only_complete_snapshots() -> None:
    registry = ToolRegistryService([FakeTool("tool-0")])

    def replace(index: int) -> None:
        registry.replace(
            [
                FakeTool(f"tool-{index}-a"),
                FakeTool(f"tool-{index}-b"),
            ]
        )

    def read(_index: int) -> tuple[int, tuple[str, ...]]:
        snapshot = registry.snapshot()
        return snapshot.version, tuple(sorted(snapshot.by_name))

    with ThreadPoolExecutor(max_workers=8) as pool:
        replacements = [
            pool.submit(replace, index) for index in range(1, 51)
        ]
        reads = list(pool.map(read, range(200)))
        for replacement in replacements:
            replacement.result()

    for version, names in reads:
        if version == 0:
            assert names == ("tool-0",)
        else:
            assert len(names) == 2
            assert names[0].endswith("-a")
            assert names[1].endswith("-b")
