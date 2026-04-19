from server.intent.keywords import Intent, keyword_match, normalize


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
    assert keyword_match("done") == Intent.END_SESSION
    assert keyword_match("end session") == Intent.END_SESSION
    assert keyword_match("stop") == Intent.END_SESSION


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
