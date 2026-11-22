"""Tests for prova.harness.redact: secrets never reach a log, and ordinary text is left alone."""

from prova.harness.redact import REDACTED, Redactor

# Fake secrets with the SHAPE of real ones. None of them is a real credential.
ANTHROPIC_LIKE = "sk-ant-api03-AbC123_xyz-9876-QwErTy"
GITHUB_LIKE = "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"
GITHUB_PAT_LIKE = "github_pat_11ABCDEFG0abcdefghij_klmnopqrstuvwxyz0123456789"
AWS_LIKE = "AKIAIOSFODNN7EXAMPLE"
PEM = (
    "-----BEGIN OPENSSH PRIVATE KEY-----\n"
    "b3BlbnNzaC1rZXktdjEAAAAA\nAAAAB3NzaC1yc2E=\n"
    "-----END OPENSSH PRIVATE KEY-----"
)

redactor = Redactor()


def test_the_marker_is_a_fixed_string() -> None:
    assert REDACTED == "[REDACTED]"


# --- Secrets with a recognisable shape ---------------------------------------


def test_an_anthropic_style_key_inside_a_sentence_is_redacted() -> None:
    assert redactor.redact(f"the key is {ANTHROPIC_LIKE} ok") == "the key is [REDACTED] ok"


def test_github_tokens_are_redacted() -> None:
    assert redactor.redact(f"a {GITHUB_LIKE} b") == "a [REDACTED] b"
    assert redactor.redact(f"a {GITHUB_PAT_LIKE} b") == "a [REDACTED] b"
    for prefix in ("gho_", "ghs_", "ghu_", "ghr_"):
        assert redactor.redact(f"x {prefix}{'Z9' * 18} y") == "x [REDACTED] y"


def test_an_aws_access_key_id_is_redacted() -> None:
    assert redactor.redact(f"id={AWS_LIKE};") == "id=[REDACTED];"


def test_a_private_key_block_is_redacted_whole_and_the_text_around_it_is_kept() -> None:
    text = f"before\n{PEM}\nafter"
    assert redactor.redact(text) == "before\n[REDACTED]\nafter"


def test_every_secret_in_a_text_is_redacted_not_only_the_first() -> None:
    text = f"{ANTHROPIC_LIKE} and {GITHUB_LIKE} and {ANTHROPIC_LIKE}"
    assert redactor.redact(text) == "[REDACTED] and [REDACTED] and [REDACTED]"


def test_the_structure_around_a_secret_survives_json_and_search_output() -> None:
    assert redactor.redact(f'{{"value": "{ANTHROPIC_LIKE}"}}') == '{"value": "[REDACTED]"}'
    line = f"src/app.py:3: key = '{ANTHROPIC_LIKE}'"
    assert redactor.redact(line) == "src/app.py:3: key = '[REDACTED]'"


# --- Ordinary text is not touched ---------------------------------------------


def test_text_without_secrets_is_returned_unchanged() -> None:
    text = "def check_stop_reason(response):  # ANTHROPIC_API_KEY is read in config.py\n    return 42\n"
    assert redactor.redact(text) == text
    assert redactor.redact("") == ""


def test_lookalikes_are_not_redacted() -> None:
    for text in [
        "risk-ant-eater-colony-study",  # contains `sk-ant-` inside a word
        "ghp_short",  # too short to be a token
        "AKIA",  # a prefix alone is not a key id
        "da39a3ee5e6b4b0d3255bfef95601890afd80709",  # a git hash: long and random-looking, but not a secret
        "sk-learn is a library",
    ]:
        assert redactor.redact(text) == text


def test_redacting_twice_gives_the_same_result() -> None:
    once = redactor.redact(f"a {ANTHROPIC_LIKE} b {PEM}")
    assert redactor.redact(once) == once


# --- Values of sensitive environment variables ---------------------------------


def test_the_value_of_a_sensitive_variable_is_redacted_even_when_it_has_no_known_shape() -> None:
    environ = {"MY_SERVICE_TOKEN": "plain-value-with-no-shape-123", "PATH": "/usr/bin"}
    from_env = Redactor.from_environ(environ)
    assert from_env.redact("sent plain-value-with-no-shape-123 over") == "sent [REDACTED] over"
    assert from_env.redact("PATH is /usr/bin") == "PATH is /usr/bin"  # not a sensitive variable


def test_a_variable_counts_as_sensitive_when_one_word_of_its_name_says_so() -> None:
    environ = {
        "ANTHROPIC_API_KEY": "value-one-aaaa",
        "AWS_SECRET_ACCESS_KEY": "value-two-bbbb",
        "db_password": "value-three-cccc",  # lower case too
        "KEYBOARD_LAYOUT": "english-us-international",  # contains KEY, but not as a word of its own
    }
    from_env = Redactor.from_environ(environ)
    for hidden in ("value-one-aaaa", "value-two-bbbb", "value-three-cccc"):
        assert from_env.redact(f"x {hidden} y") == "x [REDACTED] y"
    assert from_env.redact("english-us-international") == "english-us-international"


def test_short_values_are_ignored_so_that_ordinary_text_is_not_mangled() -> None:
    from_env = Redactor.from_environ({"DEBUG_KEY": "1", "OTHER_TOKEN": "yes"})
    assert from_env.redact("debug 1 yes 1111") == "debug 1 yes 1111"


def test_the_longest_value_is_redacted_first_so_no_tail_is_left_behind() -> None:
    environ = {"FIRST_KEY": "abcdefgh1234", "SECOND_KEY": "abcdefgh1234-and-more"}
    from_env = Redactor.from_environ(environ)
    assert from_env.redact("see abcdefgh1234-and-more here") == "see [REDACTED] here"


def test_every_occurrence_of_a_secret_value_is_redacted() -> None:
    from_env = Redactor.from_environ({"SOME_KEY": "repeated-value-xyz"})
    assert from_env.redact("repeated-value-xyz / repeated-value-xyz") == "[REDACTED] / [REDACTED]"


def test_known_shapes_and_environment_values_work_together() -> None:
    from_env = Redactor.from_environ({"SOME_KEY": "value-without-shape"})
    assert from_env.redact(f"{ANTHROPIC_LIKE} value-without-shape") == "[REDACTED] [REDACTED]"


# --- Structured data (a trajectory event is a dict) -----------------------------


def test_strings_are_redacted_at_any_depth_of_nested_data() -> None:
    data = {"a": ["note", {"b": ANTHROPIC_LIKE}], "c": (GITHUB_LIKE, "fine")}
    assert redactor.redact_data(data) == {"a": ["note", {"b": "[REDACTED]"}], "c": ("[REDACTED]", "fine")}


def test_keys_are_redacted_too() -> None:
    assert redactor.redact_data({ANTHROPIC_LIKE: 1}) == {"[REDACTED]": 1}


def test_the_original_data_is_never_modified() -> None:
    data = {"a": [ANTHROPIC_LIKE]}
    redactor.redact_data(data)
    assert data == {"a": [ANTHROPIC_LIKE]}


def test_values_that_are_not_strings_pass_through_unchanged() -> None:
    data = {"count": 3, "ratio": 0.5, "flag": True, "nothing": None}
    assert redactor.redact_data(data) == data


def test_a_private_key_block_that_was_cut_before_its_end_line_is_still_redacted() -> None:
    cut = "before\n-----BEGIN RSA PRIVATE KEY-----\nMIIEpAIBAAKCAQEA\nnot the end of the key"
    assert redactor.redact(cut) == "before\n[REDACTED]"
