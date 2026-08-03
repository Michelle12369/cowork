from langgraph.checkpoint.base import empty_checkpoint

from app.agent import session_state


def _put_real_checkpoint(session_id: str) -> None:
    config = {"configurable": {"thread_id": session_id, "checkpoint_ns": ""}}
    session_state.checkpointer.put(config, empty_checkpoint(), {}, {})


def test_has_checkpoint_never_seen_session_leaves_storage_untouched() -> None:
    # `InMemorySaver.storage` is a defaultdict -- indexing it (what `checkpointer.get(...)`
    # does internally) creates a permanent empty entry for every session id ever queried.
    # `has_checkpoint` MUST answer without that side effect (see S2-4).
    assert session_state.has_checkpoint("never-seen-session") is False
    assert "never-seen-session" not in session_state.checkpointer.storage


def test_has_checkpoint_session_with_real_checkpoint_returns_true() -> None:
    _put_real_checkpoint("session-with-history")

    assert session_state.has_checkpoint("session-with-history") is True
