"""Every case of bench/review/cases.yaml, checked in a real container: good patches pass every hidden test
of their task, bad ones fail at least one. A case that does not hold is wrong, whatever it was meant to be."""

import pytest

from bench.review_cases import CASES, TASKS, check_case, load_cases
from prova.sandbox.docker import DockerSandbox

pytestmark = pytest.mark.docker


@pytest.mark.parametrize("case", load_cases(CASES, TASKS), ids=lambda case: case.id)
def test_the_case_agrees_with_the_hidden_tests(case: object) -> None:
    assert check_case(case, TASKS, lambda workdir: DockerSandbox(workdir)) is None  # type: ignore[arg-type]
