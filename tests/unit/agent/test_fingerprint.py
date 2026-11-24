"""The fingerprint of a failure: the same mistake gives the same value, wherever and whenever the tests ran.

A loop that fixes, runs the tests and fixes again needs to know when it is going round in a circle. The
fingerprint is what it compares. It is made of which tests failed and with what kind of error, and of
nothing that changes from one run to the next (line numbers, temporary folders, times, the values in a
message). Output that does not show a failure gives no fingerprint at all: "nothing to compare" is not "the
same as before". The samples are the real shapes of pytest's output.
"""

import pytest

from prova.agent.fingerprint import failure_fingerprint

SUMMARY = "=========================== short test summary info ============================"


def output(*lines: str, tail: str = "3 failed, 1 passed in 0.03s", before: str = "") -> str:
    return "\n".join([before, SUMMARY, *lines, tail]) if before else "\n".join([SUMMARY, *lines, tail])


EQ = "FAILED test_a.py::test_eq - assert 1 == 2"
KEY = "FAILED test_a.py::test_key - KeyError: 'x'"
PARAM = "FAILED test_a.py::test_param[a] - ValueError: bad /private/var/folders/10/2h1..."


def fp(*lines: str, **kw: str) -> str | None:
    return failure_fingerprint(output(*lines, **kw))


# --- the same failure is the same fingerprint --------------------------------------------------------


def test_a_fingerprint_is_a_sha256() -> None:
    value = fp(EQ, KEY)
    assert value is not None and len(value) == 64


def test_the_order_of_the_failures_does_not_matter() -> None:
    assert fp(EQ, KEY, PARAM) == fp(PARAM, EQ, KEY)


def test_the_time_the_counts_and_the_progress_dots_do_not_matter() -> None:
    one = output(EQ, tail="1 failed in 0.03s", before="F.\n[100%]")
    other = output(EQ, tail="1 failed, 40 passed in 12.58s", before="F" + "." * 40 + "\n[100%]")
    assert failure_fingerprint(one) == failure_fingerprint(other)


def test_the_values_in_the_message_do_not_matter_only_the_kind_of_error() -> None:
    assert fp("FAILED t.py::test_x - assert 1 == 2") == fp("FAILED t.py::test_x - assert 'a' == 'b'")
    assert fp("FAILED t.py::test_x - KeyError: 'a'") == fp("FAILED t.py::test_x - KeyError: 'zzz'")


def test_a_temporary_folder_in_the_name_of_a_test_does_not_matter() -> None:
    one = fp("FAILED t.py::test_x[/private/var/folders/10/2h1/T/pytest-1/a0] - ValueError: x")
    other = fp("FAILED t.py::test_x[/tmp/pytest-of-me/pytest-77/b9] - ValueError: x")
    assert one == other


def test_the_same_failure_twice_is_one_failure() -> None:
    assert fp(EQ, EQ) == fp(EQ)


# --- another failure is another fingerprint ---------------------------------------------------------


def test_another_test_failing_is_another_fingerprint() -> None:
    assert fp(EQ) != fp(KEY)
    assert fp(EQ) != fp(EQ, KEY)


def test_the_same_test_failing_in_another_way_is_another_fingerprint() -> None:
    assert fp("FAILED t.py::test_x - KeyError: 'a'") != fp("FAILED t.py::test_x - ValueError: a")
    assert fp("FAILED t.py::test_x - assert 1 == 2") != fp("FAILED t.py::test_x - KeyError: 'a'")


def test_a_test_that_is_in_another_file_or_class_is_another_test() -> None:
    assert fp("FAILED a.py::test_x - assert 1") != fp("FAILED b.py::test_x - assert 1")
    assert fp("FAILED a.py::TestA::test_x - assert 1") != fp("FAILED a.py::test_x - assert 1")


def test_a_failure_with_no_message_has_a_kind_of_its_own() -> None:
    assert fp("FAILED t.py::test_x") != fp("FAILED t.py::test_x - assert 1")


def test_a_test_that_could_not_be_collected_is_found_in_its_error_section() -> None:
    errors = "_____ ERROR collecting test_b.py _____\nE   ModuleNotFoundError: No module named 'x'\nE   more"
    one = failure_fingerprint(output("ERROR test_b.py", tail="1 error in 0.06s", before=errors))
    other = failure_fingerprint(
        output("ERROR test_c.py", tail="1 error in 0.06s", before=errors.replace("test_b", "test_c"))
    )
    assert one is not None and one != other
    syntax = errors.replace("ModuleNotFoundError", "SyntaxError")
    assert one != failure_fingerprint(output("ERROR test_b.py", tail="1 error", before=syntax))


def test_a_plain_assert_and_an_assertion_error_with_a_message_are_the_same_kind() -> None:
    assert fp("FAILED t.py::test_x - assert 1 == 2") == fp("FAILED t.py::test_x - AssertionError: boom")


def test_a_failure_and_an_error_of_the_same_test_are_not_the_same() -> None:
    assert fp("FAILED t.py::test_x - KeyError: 'a'") != fp("ERROR t.py::test_x - KeyError: 'a'")


def test_a_message_of_a_form_nobody_knows_is_not_the_same_as_no_message() -> None:
    assert fp("FAILED t.py::test_x") != fp("FAILED t.py::test_x - something went wrong here")


def test_the_first_error_line_of_a_collecting_section_names_the_error() -> None:
    first = "_____ ERROR collecting test_b.py _____\nE   ValueError: a\nE   KeyError: b"
    only = "_____ ERROR collecting test_b.py _____\nE   ValueError: a"
    assert failure_fingerprint(output("ERROR test_b.py", before=first)) == failure_fingerprint(
        output("ERROR test_b.py", before=only)
    )


def test_an_error_line_of_another_section_is_not_the_error_of_a_file_that_could_not_be_collected() -> None:
    other = "_____ ERROR collecting test_b.py _____\n_____ test_other _____\nE   KeyError: z"
    nothing = "_____ ERROR collecting test_b.py _____"
    assert failure_fingerprint(output("ERROR test_b.py", before=other)) == failure_fingerprint(
        output("ERROR test_b.py", before=nothing)
    )


def test_the_last_summary_is_the_one_believed_and_lines_after_its_end_are_not() -> None:
    printed = f"{SUMMARY}\nFAILED fake.py::test_fake - KeyError: 'x'"  # a banner printed by a test, earlier
    assert failure_fingerprint(output(EQ, before=printed)) == fp(EQ)
    after = "\n".join([output(EQ), "=================== 1 failed in 0.03s ===================", KEY])
    assert failure_fingerprint(after) == fp(EQ)


# --- no fingerprint ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "",
        ".\n1 passed in 0.00s",  # the tests passed
        "no tests ran in 0.00s",  # nothing was collected
        "Traceback (most recent call last):\n  File x\nRuntimeError: boom",  # the tests did not even start
        "3 failed, 1 passed in 0.03s",  # it says that tests failed but not which
        "pytest: error: unrecognized arguments: --nope",
    ],
)
def test_output_that_does_not_show_which_tests_failed_has_no_fingerprint(text: str) -> None:
    assert failure_fingerprint(text) is None


def test_a_summary_with_no_failed_line_has_no_fingerprint() -> None:
    assert failure_fingerprint(output("SKIPPED [1] test_a.py:3: no reason", tail="1 skipped")) is None


def test_only_the_summary_is_believed_a_failed_line_printed_by_a_test_is_not_a_failure() -> None:
    spoof = "FAILED fake.py::test_fake - KeyError: 'x'\n1 passed in 0.00s"  # printed, before the summary
    assert failure_fingerprint(spoof) is None
    assert failure_fingerprint(output(EQ, before=spoof)) == fp(EQ)
