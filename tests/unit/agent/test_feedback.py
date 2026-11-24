"""Feedback for a loop that fixes and tests again: a few lines, built from facts.

What the agent is told after a failed attempt comes from two places it cannot colour: the real change
(compared before and after, not what the model says it did) and the output of the tests run after the
change. The feedback is short on purpose: a model given 400 lines of traceback loses the one line that
matters. When the output does not show which tests failed, the feedback says so and does not guess.
"""

from prova.agent.feedback import Lesson, describe_changes, make_lesson, structured_feedback
from prova.agent.fingerprint import failure_fingerprint
from tests.unit.agent.test_fingerprint import EQ, KEY, SUMMARY, output

Changes = dict[str, tuple[str | None, str | None]]  # path -> (before, after); None is "not there"


def lesson(attempt: int, tried: str, because: str, fingerprint: str | None) -> Lesson:
    return Lesson(attempt=attempt, tried=tried, failed_because=because, fingerprint=fingerprint)


# --- what was tried, from the files and not from the model ------------------------------------------


def test_a_changed_file_is_described_by_its_name_the_lines_added_and_removed_and_the_new_lines() -> None:
    text = describe_changes({"x.py": ("a\nb\n", "a\nc\nd\n")})
    assert "x.py" in text and "+2" in text and "-1" in text
    assert "c" in text and "d" in text  # a look at what was written


def test_a_file_that_was_made_or_deleted_is_said_to_be_made_or_deleted() -> None:
    assert "created" in describe_changes({"new.py": (None, "a\nb\n")})
    assert "deleted" in describe_changes({"old.py": ("a\n", None)})


def test_a_file_that_is_the_same_before_and_after_is_not_mentioned() -> None:
    assert describe_changes({"same.py": ("a\n", "a\n")}) == "no file was changed"
    assert "same.py" not in describe_changes({"same.py": ("a\n", "a\n"), "x.py": ("a\n", "b\n")})


def test_long_new_lines_and_many_lines_are_cut_and_the_cut_is_shown() -> None:
    text = describe_changes({"x.py": ("", "".join(f"line {i} " + "y" * 200 + "\n" for i in range(20)))})
    assert "more line" in text and "…" in text
    assert len(text) < 800


def test_many_files_are_listed_up_to_a_limit_and_the_rest_are_counted() -> None:
    changes: Changes = {f"f{i}.py": ("a\n", "b\n") for i in range(9)}
    text = describe_changes(changes)
    assert "f0.py" in text and "f8.py" not in text and "4 more file" in text


# --- the lesson of one attempt ----------------------------------------------------------------------


def test_a_lesson_has_what_was_tried_why_it_failed_and_the_fingerprint_of_the_failure() -> None:
    made = make_lesson(2, {"x.py": ("a\n", "b\n")}, output(EQ, KEY))
    assert made.attempt == 2 and "x.py" in made.tried
    assert "test_a.py::test_eq" in made.failed_because and "KeyError" in made.failed_because
    assert made.fingerprint == failure_fingerprint(output(EQ, KEY))


def test_a_lesson_names_a_few_failing_tests_and_counts_the_rest() -> None:
    lines = [f"FAILED t.py::test_{i} - KeyError: {i}" for i in range(8)]
    made = make_lesson(1, {"x.py": ("a\n", "b\n")}, output(*lines))
    assert "t.py::test_0" in made.failed_because and "t.py::test_7" not in made.failed_because
    assert "and 3 more" in made.failed_because
    few = make_lesson(1, {"x.py": ("a\n", "b\n")}, output(*lines[:2]))
    assert "more" not in few.failed_because


def test_a_lesson_from_output_that_shows_no_failure_says_so_and_has_no_fingerprint() -> None:
    made = make_lesson(1, {"x.py": ("a\n", "b\n")}, "Traceback ...\nRuntimeError: boom")
    assert made.fingerprint is None and "could not tell" in made.failed_because


# --- the feedback -----------------------------------------------------------------------------------


def test_the_feedback_names_the_failing_tests_with_the_kind_of_error_and_the_message() -> None:
    text = structured_feedback(output(EQ, KEY), [])
    assert "2" in text and "test_a.py::test_eq" in text and "assert 1 == 2" in text
    assert "test_a.py::test_key" in text and "KeyError" in text


def test_the_feedback_is_short_whatever_the_size_of_the_output() -> None:
    failures = [f"FAILED test_big.py::test_{i} - assert {i} == 0" for i in range(60)]
    noise = "\n".join(f"E   assert {i} == 0 TRACEBACK-NOISE" for i in range(400))
    text = structured_feedback(output(*failures, before=noise), [])
    assert len(text.splitlines()) <= 20
    assert "TRACEBACK-NOISE" not in text and "more" in text  # the rest of the failures are counted


def test_a_failure_that_comes_back_is_named_with_the_attempt_it_came_back_from() -> None:
    past = failure_fingerprint(output(EQ))
    history = [lesson(1, "edited x.py", "test_eq", past), lesson(2, "edited y.py", "other", "f" * 64)]
    assert "same failure as in attempt 1" in structured_feedback(output(EQ), history)
    assert "same failure" not in structured_feedback(output(KEY), history)


def test_a_failure_that_came_back_several_times_names_all_the_attempts() -> None:
    same = failure_fingerprint(output(EQ))
    history = [lesson(1, "a", "b", same), lesson(2, "c", "d", "f" * 64), lesson(3, "e", "f", same)]
    assert "same failure as in attempts 1, 3" in structured_feedback(output(EQ), history)


def test_what_was_tried_is_one_line_for_each_attempt_even_if_it_changed_many_files() -> None:
    tried = describe_changes({"a.py": ("x\n", "y\n"), "b.py": ("x\n", "y\n")})
    assert "\n" in tried  # the description itself has several lines
    text = structured_feedback(output(EQ), [lesson(1, tried, "because", "f" * 64)])
    section = text.split("Already tried")[1]
    assert section.count("\n") == 1  # the header ends its line, and the attempt is the only line after it
    assert "a.py" in section and "b.py" in section  # and all of it is there


def test_two_outputs_with_no_fingerprint_are_not_the_same_failure() -> None:
    history = [lesson(1, "edited x.py", "could not tell", None)]
    assert "same failure" not in structured_feedback("garbage", history)


def test_the_feedback_lists_what_was_already_tried_but_only_the_last_few_attempts() -> None:
    history = [lesson(n, f"tried number {n}", f"because {n}", "f" * 64) for n in range(1, 7)]
    text = structured_feedback(output(EQ), history)
    assert "tried number 6" in text and "because 6" in text and "tried number 4" in text
    assert "tried number 3" not in text


def test_the_latest_lesson_is_listed_with_what_was_tried_but_never_compared_with() -> None:
    latest = lesson(2, "edited x.py", "test_eq", failure_fingerprint(output(EQ)))
    text = structured_feedback(output(EQ), [], latest=latest)
    assert "attempt 2: edited x.py" in text and "same failure" not in text


def test_with_no_history_there_is_no_list_of_what_was_tried() -> None:
    assert "already tried" not in structured_feedback(output(EQ), [])


def test_output_that_does_not_show_a_failure_is_said_not_to_and_its_end_is_shown() -> None:
    text = structured_feedback("\n".join(f"line {i}" for i in range(100)), [])
    assert "could not tell which tests failed" in text
    assert "line 99" in text and "line 50" not in text and len(text.splitlines()) <= 20


def test_a_run_that_passed_is_not_described_as_a_failure() -> None:
    text = structured_feedback(f"{SUMMARY}\n1 passed in 0.00s", [])
    assert "could not tell" in text
