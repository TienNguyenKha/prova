"""The prompts of a task (the user turn after the issue), kept as files next to the system prompt's layers.

They were text in the code, outside the prompt hash: a result could not say which words a run was given.
Now each is a template in src/prova/prompts/tasks/, filled in one pass (a value is never read again, so
braces in it stay as they are, and str.format is never used), and its hash goes into the config of the
agents that use it.
"""

from pathlib import Path

import pytest

from prova.prompts.tasks import TASK_PROMPTS, TaskPromptError, task_prompt, task_prompt_hash


def test_every_task_prompt_has_a_template_and_a_hash() -> None:
    assert set(TASK_PROMPTS) == {"fix_goal", "fix_feedback", "repro", "adversary", "self_review"}
    hashes = {name: task_prompt_hash(name) for name in TASK_PROMPTS}
    assert all(len(h) == 64 for h in hashes.values()) and len(set(hashes.values())) == len(hashes)


def test_the_words_are_the_ones_the_code_used_before() -> None:
    assert (
        task_prompt("fix_goal", check="pytest -q")
        == "The goal is met when this command exits with code 0: pytest -q"
    )
    assert task_prompt("fix_feedback", feedback="1 failed") == "The last attempt did not meet it:\n1 failed"
    assert task_prompt("repro", folder="repro").startswith(
        "Write a test in repro/ (for example repro/test_bug.py)"
    )
    assert "write tests in attack/ that assert" in task_prompt("adversary", folder="attack")
    assert task_prompt("self_review").startswith("Above are the report of a bug and the patch you wrote")


def test_a_value_is_put_in_once_and_never_read_again() -> None:
    text = task_prompt("fix_feedback", feedback="E   KeyError: '{feedback}' and {check} and {}")
    assert text.endswith("E   KeyError: '{feedback}' and {check} and {}")


def test_a_missing_or_unknown_value_is_an_error_that_names_it() -> None:
    with pytest.raises(TaskPromptError, match="check"):
        task_prompt("fix_goal")
    with pytest.raises(TaskPromptError, match="colour"):
        task_prompt("fix_goal", check="x", colour="red")
    with pytest.raises(TaskPromptError, match="nothing"):
        task_prompt("nothing")


def test_the_hash_is_the_hash_of_the_template_so_any_change_of_words_changes_it(tmp_path: Path) -> None:
    (tmp_path / "fix_goal.md").write_text("Done when {check} exits with 0.\n", encoding="utf-8")
    other = task_prompt_hash("fix_goal", templates=tmp_path)
    assert other != task_prompt_hash("fix_goal")
    assert task_prompt("fix_goal", templates=tmp_path, check="x") == "Done when x exits with 0."
