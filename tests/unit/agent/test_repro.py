"""Does a reproduction test fail for the right reason? Read from the output of pytest run with `--tb=native`.

A test written to show a bug is only worth something if it fails on the code as it is, and fails because
of the bug: an assertion about what the code does, or an exception raised in the code. A test that cannot be
imported, does not parse, finds nothing, passes, or breaks on its own typo "reproduces" nothing, and the agent
is told why.

The samples are the real output of pytest 8 on Python 3.12, with the paths moved to /work and most of pytest's
own frames left out (they are the same in every traceback).
"""

from prova.agent.repro import ReproCheck, classify_repro

ROOT = "/work"
PYTEST_FRAMES = """\
  File "/usr/local/lib/python3.12/site-packages/_pytest/runner.py", line 361, in from_call
    result: TResult | None = func()
                             ^^^^^^
  File "/usr/local/lib/python3.12/site-packages/_pytest/python.py", line 187, in pytest_pyfunc_call
    result = testfunction(**testargs)
             ^^^^^^^^^^^^^^^^^^^^^^^^"""

ASSERTION = f"""\
___________________________________ test_add ___________________________________
Traceback (most recent call last):
{PYTEST_FRAMES}
  File "/work/repro/test_assert.py", line 3, in test_add
    assert add(1, 2) == 3
AssertionError: assert -1 == 3
 +  where -1 = add(1, 2)"""

IN_CODE = f"""\
___________________________________ test_get ___________________________________
Traceback (most recent call last):
{PYTEST_FRAMES}
  File "/work/repro/test_code_exc.py", line 3, in test_get
    assert get({{}}, "x") is None
           ^^^^^^^^^^^^
  File "/work/lib.py", line 3, in get
    return d[k]
           ~^^^
KeyError: 'x'"""

THROUGH_A_LIBRARY = f"""\
__________________________________ test_parse __________________________________
Traceback (most recent call last):
{PYTEST_FRAMES}
  File "/work/repro/test_lib_exc.py", line 3, in test_parse
    assert parse("not json") is None
           ^^^^^^^^^^^^^^^^^
  File "/work/lib.py", line 5, in parse
    return json.loads(text)
           ^^^^^^^^^^^^^^^^
  File "/usr/local/lib/python3.12/json/__init__.py", line 346, in loads
    return _default_decoder.decode(s)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/usr/local/lib/python3.12/json/decoder.py", line 356, in raw_decode
    raise JSONDecodeError("Expecting value", s, err.value) from None
json.decoder.JSONDecodeError: Expecting value: line 1 column 1 (char 0)"""

DID_NOT_RAISE = f"""\
_________________________________ test_raises __________________________________
Traceback (most recent call last):
{PYTEST_FRAMES}
  File "/work/repro/test_raises.py", line 4, in test_raises
    with pytest.raises(ValueError):
         ^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/usr/local/lib/python3.12/site-packages/_pytest/raises.py", line 699, in __exit__
    fail(f"DID NOT RAISE {{self.expected_exceptions[0].__name__}}")
  File "/usr/local/lib/python3.12/site-packages/_pytest/outcomes.py", line 162, in __call__
    raise Failed(msg=reason, pytrace=pytrace)
Failed: DID NOT RAISE ValueError"""

IN_THE_TEST = f"""\
__________________________________ test_typo ___________________________________
Traceback (most recent call last):
{PYTEST_FRAMES}
  File "/work/repro/test_test_exc.py", line 3, in test_typo
    assert add(1, 2) == expected
                        ^^^^^^^^
NameError: name 'expected' is not defined"""

SUMMARY_LINES = {
    "assertion": "FAILED repro/test_assert.py::test_add - assert -1 == 3",
    "in_code": "FAILED repro/test_code_exc.py::test_get - KeyError: 'x'",
    "library": "FAILED repro/test_lib_exc.py::test_parse - json.decoder.JSONDecodeError: Expe...",
    "raises": "FAILED repro/test_raises.py::test_raises - Failed: DID NOT RAISE ValueError",
    "typo": "FAILED repro/test_test_exc.py::test_typo - NameError: name 'expected' is not ...",
}


def run(*sections: str, summary: list[str], tail: str = "1 failed in 0.02s") -> str:
    """The output of a run with failures: dots, the FAILURES section, the short summary, the last line."""
    return "\n".join(
        [
            "F" * len(sections) + " " * 60 + "[100%]",
            "=" * 35 + " FAILURES " + "=" * 35,
            *sections,
            "=" * 28 + " short test summary info " + "=" * 28,
            *summary,
            tail,
        ]
    )


def check(output: str, exit_code: int = 1, *, truncated: bool = False) -> ReproCheck:
    return classify_repro(output, exit_code=exit_code, truncated=truncated, root=ROOT)


IMPORT_ERROR = """
==================================== ERRORS ====================================
______________________ ERROR collecting repro/test_imp.py ______________________
ImportError while importing test module '/work/repro/test_imp.py'.
Hint: make sure your test modules/packages have valid Python names.
Traceback:
repro/test_imp.py:1: in <module>
    from lib import nothing_here
E   ImportError: cannot import name 'nothing_here' from 'lib' (/work/lib.py)
=========================== short test summary info ============================
ERROR repro/test_imp.py
!!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!
1 error in 0.06s"""

SYNTAX_ERROR = """
==================================== ERRORS ====================================
______________________ ERROR collecting repro/test_syn.py ______________________
/usr/local/lib/python3.12/ast.py:52: in parse
    return compile(source, filename, mode, flags,
E     File "/work/repro/test_syn.py", line 1
E       def test_x(:
E                  ^
E   SyntaxError: invalid syntax
=========================== short test summary info ============================
ERROR repro/test_syn.py
!!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!
1 error in 0.10s"""


# --- failures for the right reason -----------------------------------------------------------------


def test_an_assertion_about_what_the_code_does_is_a_reproduction() -> None:
    result = check(run(ASSERTION, summary=[SUMMARY_LINES["assertion"]]))
    assert result.valid is True and result.kind == "assertion"
    assert [t.test for t in result.tests] == ["test_add"]


def test_an_exception_raised_in_the_code_is_a_reproduction() -> None:
    result = check(run(IN_CODE, summary=[SUMMARY_LINES["in_code"]]))
    assert result.valid is True and result.kind == "exception_in_code"
    assert "lib.py" in result.tests[0].detail and "KeyError" in result.tests[0].detail


def test_an_exception_raised_in_a_library_that_the_code_called_counts_for_the_code() -> None:
    result = check(run(THROUGH_A_LIBRARY, summary=[SUMMARY_LINES["library"]]))
    assert result.valid is True and result.kind == "exception_in_code"
    assert "lib.py" in result.tests[0].detail  # the deepest frame of the repo, not json/decoder.py


def test_an_exception_that_was_expected_and_did_not_come_is_a_reproduction() -> None:
    result = check(run(DID_NOT_RAISE, summary=[SUMMARY_LINES["raises"]]))
    assert result.valid is True and result.kind == "assertion"


# --- failures for the wrong reason ------------------------------------------------------------------


def test_an_exception_raised_in_the_test_itself_is_not_a_reproduction() -> None:
    result = check(run(IN_THE_TEST, summary=[SUMMARY_LINES["typo"]]))
    assert result.valid is False and result.kind == "exception_in_test"
    assert "NameError" in result.reason and "test_typo" in result.reason


def test_one_test_that_fails_for_the_wrong_reason_spoils_the_others() -> None:
    output = run(ASSERTION, IN_THE_TEST, summary=[SUMMARY_LINES["assertion"], SUMMARY_LINES["typo"]])
    result = check(output)
    assert result.valid is False and result.kind == "exception_in_test"
    assert {t.kind for t in result.tests} == {"assertion", "exception_in_test"}


def test_a_test_that_cannot_be_imported_is_not_a_reproduction() -> None:
    result = check(IMPORT_ERROR, exit_code=2)
    assert result.valid is False and result.kind == "import_error"
    assert "nothing_here" in result.reason or "ImportError" in result.reason


def test_a_test_that_does_not_parse_is_not_a_reproduction() -> None:
    result = check(SYNTAX_ERROR, exit_code=2)
    assert result.valid is False and result.kind == "syntax_error"


def test_tests_that_pass_reproduce_nothing() -> None:
    result = check("..                                     [100%]\n2 passed in 0.01s", exit_code=0)
    assert result.valid is False and result.kind == "passes"
    assert "pass" in result.reason


def test_no_test_found_reproduces_nothing() -> None:
    result = check("no tests ran in 0.00s", exit_code=5)
    assert result.valid is False and result.kind == "no_tests"


# --- output that cannot be trusted ------------------------------------------------------------------


def test_output_that_was_cut_is_not_read() -> None:
    result = check(run(ASSERTION, summary=[SUMMARY_LINES["assertion"]]), truncated=True)
    assert result.valid is False and result.kind == "unreadable"


def test_an_exit_code_of_another_kind_is_not_read() -> None:
    result = check("INTERNALERROR> boom", exit_code=3)
    assert result.valid is False and result.kind == "unreadable"


def test_a_failed_run_whose_failures_cannot_be_found_is_not_read() -> None:
    result = check("something went wrong", exit_code=1)
    assert result.valid is False and result.kind == "unreadable"


def test_a_traceback_printed_by_the_test_is_not_taken_for_its_own() -> None:
    printed = IN_THE_TEST + (
        "\n----------------------------- Captured stdout call -----------------------------\n"
        'Traceback (most recent call last):\n  File "/work/lib.py", line 1, in f\nKeyError: fake'
    )
    result = check(run(printed, summary=[SUMMARY_LINES["typo"]]))
    assert result.kind == "exception_in_test"


def test_a_failure_with_no_frame_in_the_repo_is_not_read() -> None:
    outside = f"""\
___________________________________ test_x _____________________________________
Traceback (most recent call last):
{PYTEST_FRAMES}
RuntimeError: from nowhere"""
    result = check(run(outside, summary=["FAILED repro/test_x.py::test_x - RuntimeError: from nowhere"]))
    assert result.valid is False and result.kind == "unreadable"


# --- the edges ---------------------------------------------------------------------------------------


def section(name: str, *body: str) -> str:
    return "\n".join([f"{'_' * 20} {name} {'_' * 20}", *body])


def frames(*files: str) -> list[str]:
    return [f'  File "{f}", line 1, in f\n    x()' for f in files]


def test_a_summary_that_looks_right_is_not_read_with_an_exit_code_of_another_kind() -> None:
    result = check(run(ASSERTION, summary=[SUMMARY_LINES["assertion"]]), exit_code=3)
    assert result.kind == "unreadable"


def test_a_failure_with_no_section_or_no_traceback_is_not_read() -> None:
    no_section = run(ASSERTION, summary=[SUMMARY_LINES["assertion"], "FAILED repro/test_b.py::test_b - x"])
    assert check(no_section).kind == "unreadable"
    no_traceback = section("test_add", "something else")
    assert check(run(no_traceback, summary=[SUMMARY_LINES["assertion"]])).kind == "unreadable"


def test_the_kind_of_error_is_its_name_without_its_module() -> None:
    result = check(run(THROUGH_A_LIBRARY, summary=[SUMMARY_LINES["library"]]))
    assert result.tests[0].detail.startswith("JSONDecodeError raised in the code")


def test_a_library_kept_inside_the_repo_does_not_count_as_the_code() -> None:
    body = ["Traceback (most recent call last):", *frames("/work/repro/test_v.py"), *frames(
        "/work/.venv/lib/python3.12/site-packages/requests/api.py"
    ), "ConnectionError: refused"]  # fmt: skip
    result = check(
        run(section("test_v", *body), summary=["FAILED repro/test_v.py::test_v - ConnectionError"])
    )
    assert result.kind == "exception_in_test"  # the test called the library itself


def test_a_helper_next_to_the_reproduction_tests_is_part_of_the_test() -> None:
    body = ["Traceback (most recent call last):", *frames("/work/repro/test_h.py", "/work/repro/helpers.py"),
            "TypeError: bad helper"]  # fmt: skip
    result = check(run(section("test_h", *body), summary=["FAILED repro/test_h.py::test_h - TypeError"]))
    assert result.kind == "exception_in_test"


def test_of_chained_exceptions_the_last_one_counts() -> None:
    body = [
        "Traceback (most recent call last):", *frames("/work/lib.py"), "KeyError: 'x'", "",
        "During handling of the above exception, another exception occurred:", "",
        "Traceback (most recent call last):", *frames("/work/repro/test_c.py"), "NameError: name 'y'",
    ]  # fmt: skip
    result = check(run(section("test_c", *body), summary=["FAILED repro/test_c.py::test_c - NameError"]))
    assert result.kind == "exception_in_test"


def test_the_first_problem_is_the_kind_given() -> None:
    outside = section("test_x", "Traceback (most recent call last):", "RuntimeError: from nowhere")
    summary = [SUMMARY_LINES["typo"], "FAILED repro/test_x.py::test_x - RuntimeError"]
    assert check(run(IN_THE_TEST, outside, summary=summary)).kind == "exception_in_test"
    assert check(run(IN_THE_TEST, outside, summary=summary[::-1])).kind == "unreadable"


def test_a_root_given_with_a_slash_at_the_end_is_the_same_root() -> None:
    output = run(IN_CODE, summary=[SUMMARY_LINES["in_code"]])
    assert classify_repro(output, exit_code=1, truncated=False, root="/work/").kind == "exception_in_code"


def test_a_test_in_a_class_is_found_by_the_name_pytest_gives_its_section() -> None:
    body = [
        "Traceback (most recent call last):",
        *frames("/work/repro/test_k.py", "/work/lib.py"),
        "KeyError: 1",
    ]
    output = run(
        section("TestGet.test_k", *body), summary=["FAILED repro/test_k.py::TestGet::test_k - KeyError"]
    )
    assert check(output).kind == "exception_in_code"


def test_a_test_file_at_the_top_of_the_repo_is_told_from_the_code_next_to_it() -> None:
    def one(*files: str) -> str:
        body = ["Traceback (most recent call last):", *frames(*files), "NameError: name 'z'"]
        return run(section("test_r", *body), summary=["FAILED test_r.py::test_r - NameError"])

    assert check(one("/work/test_r.py")).kind == "exception_in_test"
    assert check(one("/work/test_r.py", "/work/lib.py")).kind == "exception_in_code"
