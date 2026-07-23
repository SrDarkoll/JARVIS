from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from core.command_pipeline.models import CommandRequest, CommandResponse
from langchain_core.messages import HumanMessage
from services.memory_manager import MemoryManager


def test_concurrent_snapshots_never_copy_history_between_profiles() -> None:
    manager = MemoryManager()
    manager.set_profile_history(
        "memory_admin",
        [HumanMessage(content="admin secret")],
    )
    manager.set_profile_history(
        "memory_guest",
        [HumanMessage(content="guest fact")],
    )

    with ThreadPoolExecutor(max_workers=2) as pool:
        admin = pool.submit(manager.snapshot, "memory_admin")
        guest = pool.submit(manager.snapshot, "memory_guest")

    assert [message.content for message in admin.result().history] == [
        "admin secret"
    ]
    assert [message.content for message in guest.result().history] == [
        "guest fact"
    ]
    assert admin.result().profile_id == "memory_admin"
    assert guest.result().profile_id == "memory_guest"


def test_message_counts_are_atomic_and_isolated_by_profile() -> None:
    manager = MemoryManager()

    def increment(profile_id: str) -> int:
        return manager.next_message_count(profile_id)

    with ThreadPoolExecutor(max_workers=8) as pool:
        admin_counts = list(
            pool.map(lambda _index: increment("counter_admin"), range(100))
        )
        guest_counts = list(
            pool.map(lambda _index: increment("counter_guest"), range(100))
        )

    assert sorted(admin_counts) == list(range(1, 101))
    assert sorted(guest_counts) == list(range(1, 101))
    assert manager.snapshot("counter_admin").message_count == 100
    assert manager.snapshot("counter_guest").message_count == 100


def test_append_interaction_updates_only_the_request_profile() -> None:
    manager = MemoryManager()
    manager.set_profile_history("interaction_admin", [])
    manager.set_profile_history(
        "interaction_guest",
        [HumanMessage(content="keep me")],
    )
    request = CommandRequest.create(
        text="hello",
        profile_id="interaction_admin",
        channel="chat",
        request_id="memory-request",
    )
    response = CommandResponse(
        request_id=request.request_id,
        text="hello back",
        should_listen=False,
        outcome="succeeded",
    )

    manager.append_interaction(request, response)

    assert [
        message.content for message in manager.get_history("interaction_admin")
    ] == ["hello", "hello back"]
    assert [
        message.content for message in manager.get_history("interaction_guest")
    ] == ["keep me"]
