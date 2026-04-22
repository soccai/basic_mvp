import pytest

from server.intent.keywords import Intent, keyword_match, normalize
from server.intent.router import IntentRouter


def test_start_variants():
    assert keyword_match("start") == Intent.START_SESSION
    assert keyword_match("begin") == Intent.START_SESSION
    assert keyword_match("let's go") == Intent.START_SESSION
    assert keyword_match("I'm ready") == Intent.START_SESSION
    assert keyword_match("start session") == Intent.START_SESSION
    assert keyword_match("ready") == Intent.START_SESSION


def test_end_variants():
    assert keyword_match("I'm done") == Intent.END_SESSION
    assert keyword_match("finish") == Intent.END_SESSION
    assert keyword_match("done") is None  # "done" alone is too ambiguous
    assert keyword_match("end session") == Intent.END_SESSION
    assert keyword_match("stop") == Intent.END_SESSION
    assert keyword_match("terminate") == Intent.END_SESSION


def test_guidance_variants():
    assert keyword_match("what should i do") == Intent.REQUEST_GUIDANCE
    assert keyword_match("help") == Intent.REQUEST_GUIDANCE
    assert keyword_match("what's my day") == Intent.REQUEST_GUIDANCE
    assert keyword_match("what did i do") == Intent.REQUEST_GUIDANCE
    assert keyword_match("show my timeline") == Intent.REQUEST_GUIDANCE


def test_unclear():
    assert keyword_match("banana") is None
    assert keyword_match("") is None
    assert keyword_match("   ") is None


def test_normalization():
    assert keyword_match("  START!! ") == Intent.START_SESSION
    assert keyword_match("I'M DONE.") == Intent.END_SESSION
    assert keyword_match("HELP!!!") == Intent.REQUEST_GUIDANCE


def test_normalize_function():
    assert normalize("  Hello WORLD!! ") == "hello world"
    assert normalize("I'm   ready") == "i'm ready"


def test_finance_variants():
    assert keyword_match("i need money") == Intent.REQUEST_FINANCE
    assert keyword_match("send me money") == Intent.REQUEST_FINANCE
    assert keyword_match("ask my parents") == Intent.REQUEST_FINANCE


def test_word_boundary_no_false_positives():
    """Single-word patterns must not match inside longer words."""
    assert keyword_match("I understand") is None
    assert keyword_match("attend the meeting") is None
    assert keyword_match("amend the document") is None
    assert keyword_match("restart the server") is None
    assert keyword_match("upstart process") is None
    assert keyword_match("condone the behavior") is None
    assert keyword_match("unstoppable force") is None
    assert keyword_match("that was helpful") is None
    assert keyword_match("I already did it") is None
    assert keyword_match("that task is incomplete") is None
    assert keyword_match("I already finished that") is None


def test_conversation_greetings():
    assert keyword_match("good morning") == Intent.CONVERSATION
    assert keyword_match("good afternoon") == Intent.CONVERSATION
    assert keyword_match("good evening") == Intent.CONVERSATION
    assert keyword_match("good night") == Intent.CONVERSATION
    assert keyword_match("hello") == Intent.CONVERSATION
    assert keyword_match("hi") == Intent.CONVERSATION
    assert keyword_match("hey") == Intent.CONVERSATION
    assert keyword_match("how are you") == Intent.CONVERSATION
    assert keyword_match("how's your day") == Intent.CONVERSATION
    assert keyword_match("thanks") == Intent.CONVERSATION
    assert keyword_match("thank you") == Intent.CONVERSATION
    assert keyword_match("morning") == Intent.CONVERSATION
    assert keyword_match("yo") == Intent.CONVERSATION


def test_conversation_does_not_override_intents():
    """Greetings must not collide with more specific intents."""
    assert keyword_match("start session") == Intent.START_SESSION
    assert keyword_match("end session") == Intent.END_SESSION
    assert keyword_match("help") == Intent.REQUEST_GUIDANCE
    assert keyword_match("ready") == Intent.START_SESSION
    assert keyword_match("stop") == Intent.END_SESSION


class StubOllama:
    def __init__(self, intent: Intent | None):
        self.intent = intent
        self.calls = 0

    async def classify(self, transcript: str) -> Intent | None:
        self.calls += 1
        return self.intent


@pytest.mark.asyncio
async def test_active_session_defaults_to_guidance_without_llm():
    ollama = StubOllama(Intent.START_SESSION)
    router = IntentRouter(ollama_client=ollama)

    result = await router.classify("banana", "session_active")

    assert result.intent == Intent.REQUEST_GUIDANCE
    assert result.method == "session_default"
    assert ollama.calls == 0


@pytest.mark.asyncio
async def test_idle_transcript_still_uses_llm_fallback():
    ollama = StubOllama(Intent.UNCLEAR)
    router = IntentRouter(ollama_client=ollama)

    result = await router.classify("banana", "idle")

    assert result.intent == Intent.UNCLEAR
    assert result.method == "llm"
    assert ollama.calls == 1
