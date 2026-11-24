"""The workspace the agent works in, and what it changed there.

The agent gets a copy of repo/ and nothing else: no hidden tests, no task.yaml, no reference patch. What it
changed is found by comparing files with the original, not by asking git: the agent's code runs in a sandbox
that has this folder mounted, so it could rewrite a `.git/config` or a hook, and the host must not run
anything the agent chose.
"""

from pathlib import Path

import pytest

from bench.workspace import Changes, collect_changes, prepare_workspace
from tests.unit.bench.test_task import make_task


def write(root: Path, name: str, text: str) -> None:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def original(tmp_path: Path) -> Path:
    folder = tmp_path / "original"
    write(folder, "a.py", "x = 1\ny = 2\n")
    write(folder, "pkg/b.py", "def f():\n    return 1\n")
    write(folder, "README.md", "docs\n")
    return folder


@pytest.fixture
def work(tmp_path: Path, original: Path) -> Path:
    folder = tmp_path / "work"
    folder.mkdir()
    for path in original.rglob("*"):
        if path.is_file():
            write(folder, str(path.relative_to(original)), path.read_text(encoding="utf-8"))
    return folder


# --- What the agent is given ----------------------------------------------------------------------


def test_the_agent_gets_the_files_of_repo_and_nothing_else_of_the_task(tmp_path: Path) -> None:
    task = make_task(tmp_path / "tasks")
    write(task / "repo", "mod.py", "x = 1\n")
    write(task / "repo", "pkg/inner.py", "y = 2\n")
    write(task / "hidden_tests", "test_fix.py", "SECRET\n")
    write(task, "reference.patch", "THE ANSWER\n")
    workspace = tmp_path / "agent"
    prepare_workspace(task, workspace)
    seen = sorted(str(p.relative_to(workspace)) for p in workspace.rglob("*") if p.is_file())
    assert seen == ["mod.py", "pkg/inner.py"]
    assert not any(
        "SECRET" in p.read_text() or "THE ANSWER" in p.read_text()
        for p in workspace.rglob("*")
        if p.is_file()
    )


def test_caches_of_python_are_not_copied(tmp_path: Path) -> None:
    task = make_task(tmp_path / "tasks")
    write(task / "repo", "mod.py", "x = 1\n")
    write(task / "repo", "__pycache__/mod.cpython-312.pyc", "junk")
    workspace = tmp_path / "agent"
    prepare_workspace(task, workspace)
    assert not (workspace / "__pycache__").exists()


def test_a_workspace_that_already_exists_is_refused_and_not_mixed_with_another_run(tmp_path: Path) -> None:
    task = make_task(tmp_path / "tasks")
    workspace = tmp_path / "agent"
    workspace.mkdir()
    with pytest.raises(FileExistsError):
        prepare_workspace(task, workspace)


# --- What the agent changed -----------------------------------------------------------------------


def test_nothing_changed_is_an_empty_set_of_changes(original: Path, work: Path) -> None:
    changes = collect_changes(original, work)
    assert changes == Changes(files=(), patch="", added=0, removed=0)


def test_an_edited_file_is_listed_with_a_patch_and_the_count_of_lines(original: Path, work: Path) -> None:
    write(work, "a.py", "x = 1\ny = 20\nz = 3\n")
    changes = collect_changes(original, work)
    assert changes.files == ("a.py",)
    assert (changes.added, changes.removed) == (2, 1)
    assert "--- a/a.py" in changes.patch and "+++ b/a.py" in changes.patch
    assert "-y = 2" in changes.patch and "+y = 20" in changes.patch and "+z = 3" in changes.patch


def test_a_new_file_and_a_deleted_file_are_both_changes(original: Path, work: Path) -> None:
    write(work, "pkg/new.py", "one\ntwo\n")
    (work / "README.md").unlink()
    changes = collect_changes(original, work)
    assert changes.files == ("README.md", "pkg/new.py")
    assert (changes.added, changes.removed) == (2, 1)
    assert "--- /dev/null" in changes.patch and "+++ /dev/null" in changes.patch


def test_files_are_listed_in_a_stable_order_with_slashes(original: Path, work: Path) -> None:
    write(work, "pkg/b.py", "changed\n")
    write(work, "a.py", "changed\n")
    assert collect_changes(original, work).files == ("a.py", "pkg/b.py")


@pytest.mark.parametrize(
    "junk", ["__pycache__/a.cpython-312.pyc", ".pytest_cache/v/cache", ".coverage", "pkg/mod.pyc"]
)
def test_what_python_and_the_tools_leave_behind_is_not_a_change(
    original: Path, work: Path, junk: str
) -> None:
    write(work, junk, "noise")
    assert collect_changes(original, work).files == ()


def test_a_file_that_is_not_text_is_listed_and_not_read_into_the_patch(original: Path, work: Path) -> None:
    (work / "data.bin").write_bytes(b"\xff\xfe\x00\x01")
    changes = collect_changes(original, work)
    assert changes.files == ("data.bin",)
    assert (changes.added, changes.removed) == (0, 0)
    assert "Binary file data.bin" in changes.patch


def test_a_symlink_is_a_change_and_is_never_followed(tmp_path: Path, original: Path, work: Path) -> None:
    secret = tmp_path / "host-secret.txt"
    secret.write_text("TOPSECRET\n", encoding="utf-8")
    (work / "link.txt").symlink_to(secret)
    changes = collect_changes(original, work)
    assert changes.files == ("link.txt",)
    assert "TOPSECRET" not in changes.patch


def test_a_symlink_in_the_original_that_became_a_file_is_a_change(
    tmp_path: Path, original: Path, work: Path
) -> None:
    target = tmp_path / "t.txt"
    target.write_text("T\n", encoding="utf-8")
    (original / "ln").symlink_to(target)
    write(work, "ln", "now a file\n")
    assert collect_changes(original, work).files == ("ln",)


def test_the_change_is_the_same_whatever_the_order_of_the_files_on_disk(original: Path, work: Path) -> None:
    write(work, "z.py", "1\n")
    write(work, "b.py", "2\n")
    first = collect_changes(original, work)
    assert first == collect_changes(original, work)
    assert first.files == ("b.py", "z.py")


def test_a_file_too_big_to_read_safely_is_listed_and_not_diffed(original: Path, work: Path) -> None:
    (work / "huge.txt").write_text("a" * 1_000_001, encoding="utf-8")
    changes = collect_changes(original, work)
    assert changes.files == ("huge.txt",) and "too large" in changes.patch
    assert (changes.added, changes.removed) == (0, 0)


def test_a_file_that_ends_without_a_newline_is_marked_as_such(original: Path, work: Path) -> None:
    write(work, "a.py", "x = 1\ny = 2")
    assert "\\ No newline at end of file" in collect_changes(original, work).patch


def test_a_file_that_is_not_valid_utf8_is_binary(original: Path, work: Path) -> None:
    (work / "latin.txt").write_bytes("caf\xe9".encode("latin-1"))
    assert "Binary file latin.txt" in collect_changes(original, work).patch


def test_a_symlink_that_changes_where_it_points_is_a_change(
    tmp_path: Path, original: Path, work: Path
) -> None:
    one, two = tmp_path / "one", tmp_path / "two"
    one.write_text("1", encoding="utf-8")
    two.write_text("2", encoding="utf-8")
    (original / "ln").symlink_to(one)
    (work / "ln").symlink_to(one)
    assert collect_changes(original, work).files == ()
    (work / "ln").unlink()
    (work / "ln").symlink_to(two)
    assert collect_changes(original, work).files == ("ln",)


def test_a_symlinked_folder_is_one_entry_and_is_not_walked(
    tmp_path: Path, original: Path, work: Path
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "inside.txt").write_text("TOPSECRET", encoding="utf-8")
    (work / "folder").symlink_to(outside)
    changes = collect_changes(original, work)
    assert changes.files == ("folder",) and "TOPSECRET" not in changes.patch
