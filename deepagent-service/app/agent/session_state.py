"""Process-lifetime checkpointer for cross-turn conversation state -- module-level singleton,
shared across every request for the process's whole lifetime. Losing this state on restart
is a documented, accepted degradation for v1, not a bug.
"""

from app.agent.runtime import load_runtime

# Single checkpointer instance for the whole process lifetime. `thread_id` (= sessionId) scoping
# isolates each conversation's messages: a brand-new build_agent(..., checkpointer=checkpointer)
# call every request, reusing this same InMemorySaver object, resumes a thread's prior messages
# with no duplication and no cross-thread leakage.
# checkpointer 由 runtime 提供——internal 環境可換成自家實作而不動本檔。
checkpointer = load_runtime().build_checkpointer()


def has_checkpoint(session_id: str) -> bool:
    """True iff `checkpointer` already holds prior-turn history for this thread -- the
    seed-vs-resume decision point for callers. NEVER both seed and resume in the same turn,
    or every prior message duplicates into context."""
    thread_config = {"configurable": {"thread_id": session_id}}
    return checkpointer.get(thread_config) is not None


def reset_for_tests() -> None:
    """Test-only: an autouse fixture calls this before/after every test so sessionId reuse
    across unrelated tests never leaks checkpointed history. Production code MUST NOT call
    this -- state is expected to persist for the process lifetime (see module docstring)."""
    global checkpointer
    checkpointer = load_runtime().build_checkpointer()
