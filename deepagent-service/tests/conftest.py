import pytest

from app.agent import session_state


@pytest.fixture(autouse=True)
def _reset_session_state():
    session_state.reset_for_tests()
    yield
    session_state.reset_for_tests()
