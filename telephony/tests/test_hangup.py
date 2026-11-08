import pytest

from voice_agent.hangup import is_explicit_hangup_request


@pytest.mark.parametrize(
    "text",
    [
        "please hang up",
        "Hang up the call",
        "end the call",
        "can you end this call",
        "disconnect me",
        "cut the call",
        "could you cut it",
        "terminate this call",
        "goodbye",
        "ok good bye",
        "thanks, bye.",
        "bye!",
    ],
)
def test_detects_explicit_requests(text):
    assert is_explicit_hangup_request(text)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        "I'm done giving my details",
        "what is the capital of France",
        "don't hang up",
        "do not end the call",
        "wait, don't disconnect",
        "hold on, goodbye is too early",
        "not yet",
        "bye the way, tell me a joke",
    ],
)
def test_ignores_everything_else(text):
    assert not is_explicit_hangup_request(text)
