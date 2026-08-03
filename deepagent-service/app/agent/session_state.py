"""Process-lifetime checkpointer for cross-turn conversation state -- module-level singleton,
shared across every request for the process's whole lifetime. Losing this state on restart
is a documented, accepted degradation for v1, not a bug.
"""

from langgraph.checkpoint.memory import InMemorySaver

# Single checkpointer instance for the whole process lifetime. `thread_id` (= sessionId) scoping
# isolates each conversation's messages: a brand-new build_agent(..., checkpointer=checkpointer)
# call every request, reusing this same InMemorySaver object, resumes a thread's prior messages
# with no duplication and no cross-thread leakage.
checkpointer = InMemorySaver()


def has_checkpoint(session_id: str) -> bool:
    """True iff `checkpointer` already holds prior-turn history for this thread -- the
    seed-vs-resume decision point for callers. NEVER both seed and resume in the same turn,
    or every prior message duplicates into context.

    Answers via `.get(...)` chains against `InMemorySaver.storage` directly instead of
    `checkpointer.get(...)`, which deserializes the whole message history just to answer a
    boolean and -- because `storage` is a `defaultdict` -- would create a permanent empty
    entry for every session id ever queried, including ones that never ran (see S2-4)."""
    checkpoint_namespaces = checkpointer.storage.get(session_id, {})
    return bool(checkpoint_namespaces.get("", {}))


def reset_for_tests() -> None:
    """Test-only: an autouse fixture calls this before/after every test so sessionId reuse
    across unrelated tests never leaks checkpointed history. Production code MUST NOT call
    this -- state is expected to persist for the process lifetime (see module docstring)."""
    global checkpointer
    checkpointer = InMemorySaver()
