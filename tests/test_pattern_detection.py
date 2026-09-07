from unittest.mock import MagicMock

from app.llm.pattern_detection import suggest_duplicate


def _make_llm_client(enabled=True, response=None):
    client = MagicMock()
    client.enabled = enabled
    client.complete.return_value = response
    return client


def _make_session_with_candidates(candidates):
    session = MagicMock()
    session.execute.return_value.all.return_value = [(c,) for c in candidates]
    return session


def test_disabled_client_returns_none_without_querying():
    session = MagicMock()
    llm_client = _make_llm_client(enabled=False)

    result = suggest_duplicate(session, llm_client, "High CPU", "svc", "vert")

    assert result is None
    session.execute.assert_not_called()


def test_no_existing_candidates_returns_none_without_calling_llm():
    session = _make_session_with_candidates([])
    llm_client = _make_llm_client(enabled=True)

    result = suggest_duplicate(session, llm_client, "High CPU", "svc", "vert")

    assert result is None
    llm_client.complete.assert_not_called()


def test_llm_says_none_returns_none():
    session = _make_session_with_candidates(["CPU usage critical"])
    llm_client = _make_llm_client(enabled=True, response="NONE")

    result = suggest_duplicate(session, llm_client, "High CPU", "svc", "vert")

    assert result is None


def test_verbatim_match_is_accepted():
    session = _make_session_with_candidates(["CPU usage critical", "Disk full"])
    llm_client = _make_llm_client(enabled=True, response="CPU usage critical")

    result = suggest_duplicate(session, llm_client, "High CPU", "svc", "vert")

    assert result is not None
    assert result.likely_duplicate_of == "CPU usage critical"


def test_non_verbatim_hallucinated_response_is_rejected():
    # LLM invents a name that wasn't in the candidate list -- must not be trusted.
    session = _make_session_with_candidates(["CPU usage critical", "Disk full"])
    llm_client = _make_llm_client(enabled=True, response="Memory pressure warning")

    result = suggest_duplicate(session, llm_client, "High CPU", "svc", "vert")

    assert result is None
