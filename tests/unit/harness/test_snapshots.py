"""Tests for prova.harness.snapshots.SnapshotStore: remember the original, give it back."""

import os
from pathlib import Path

import pytest

from prova.errors import SnapshotError
from prova.harness.snapshots import SnapshotStore
from prova.tools.base import RunContext

ROOT_USER = hasattr(os, "geteuid") and os.geteuid() == 0


@pytest.fixture
def work(tmp_path: Path) -> Path:
    directory = tmp_path / "work"
    directory.mkdir()
    return directory


# --- Existing files ----------------------------------------------------------


def test_rollback_gives_back_the_original_bytes(work: Path) -> None:
    file = work / "a.txt"
    file.write_bytes(b"one\r\ntwo\x00\xff")  # not even valid text: the store copies bytes
    store = SnapshotStore()

    store.ensure(file)
    file.write_text("changed", encoding="utf-8")
    store.rollback(file)

    assert file.read_bytes() == b"one\r\ntwo\x00\xff"


def test_only_the_first_ensure_counts_so_the_original_is_what_comes_back(work: Path) -> None:
    file = work / "a.txt"
    file.write_text("original", encoding="utf-8")
    store = SnapshotStore()

    store.ensure(file)
    file.write_text("first edit", encoding="utf-8")
    store.ensure(file)  # a second edit: must not replace the original with the first edit
    file.write_text("second edit", encoding="utf-8")
    store.rollback(file)

    assert file.read_text(encoding="utf-8") == "original"


def test_after_a_rollback_the_file_can_be_tracked_again(work: Path) -> None:
    file = work / "a.txt"
    file.write_text("v1", encoding="utf-8")
    store = SnapshotStore()
    store.ensure(file)
    file.write_text("v2", encoding="utf-8")
    store.rollback(file)

    store.ensure(file)  # the file is "v1" again, and that is the new original
    file.write_text("v3", encoding="utf-8")
    store.rollback(file)

    assert file.read_text(encoding="utf-8") == "v1"


# --- New files and the directories made for them -----------------------------


def test_a_file_that_did_not_exist_is_removed_by_rollback(work: Path) -> None:
    file = work / "new.txt"
    store = SnapshotStore()

    store.ensure(file)
    file.write_text("hello", encoding="utf-8")
    store.rollback(file)

    assert not file.exists()


def test_directories_made_for_a_new_file_are_removed_but_existing_ones_stay(work: Path) -> None:
    (work / "existing").mkdir()
    file = work / "existing" / "a" / "b" / "new.txt"
    store = SnapshotStore()

    store.ensure(file)
    file.parent.mkdir(parents=True)
    file.write_text("hello", encoding="utf-8")
    store.rollback(file)

    assert not (work / "existing" / "a").exists()
    assert (work / "existing").is_dir()


def test_a_made_directory_that_now_holds_something_else_is_kept(work: Path) -> None:
    file = work / "pkg" / "new.txt"
    store = SnapshotStore()
    store.ensure(file)
    file.parent.mkdir()
    file.write_text("hello", encoding="utf-8")
    (work / "pkg" / "someone_elses.txt").write_text("keep", encoding="utf-8")

    store.rollback(file)

    assert not file.exists()
    assert (work / "pkg" / "someone_elses.txt").read_text(encoding="utf-8") == "keep"


# --- Misuse is loud ----------------------------------------------------------


def test_rolling_back_a_path_that_was_never_tracked_is_an_error(work: Path) -> None:
    with pytest.raises(SnapshotError, match="not tracked"):
        SnapshotStore().rollback(work / "never.txt")


def test_a_second_rollback_of_the_same_path_is_an_error(work: Path) -> None:
    file = work / "a.txt"
    file.write_text("x", encoding="utf-8")
    store = SnapshotStore()
    store.ensure(file)
    store.rollback(file)
    with pytest.raises(SnapshotError, match="not tracked"):
        store.rollback(file)


def test_a_directory_cannot_be_tracked(work: Path) -> None:
    with pytest.raises(SnapshotError, match="directory"):
        SnapshotStore().ensure(work)


# --- Everything at once ------------------------------------------------------


def test_rollback_all_undoes_every_change_and_forgets_them(work: Path) -> None:
    old = work / "old.txt"
    old.write_text("original", encoding="utf-8")
    new = work / "sub" / "new.txt"
    store = SnapshotStore()

    store.ensure(old)
    old.write_text("changed", encoding="utf-8")
    store.ensure(new)
    new.parent.mkdir()
    new.write_text("created", encoding="utf-8")
    assert store.paths == (old, new)  # in the order they were first touched

    store.rollback_all()

    assert old.read_text(encoding="utf-8") == "original"
    assert not (work / "sub").exists()
    assert store.paths == ()


@pytest.mark.skipif(ROOT_USER, reason="root can write to a read-only file, so nothing fails")
def test_rollback_all_tries_everything_then_reports_what_it_could_not_do(work: Path) -> None:
    stuck, fine = work / "stuck.txt", work / "fine.txt"
    stuck.write_text("original", encoding="utf-8")
    fine.write_text("original", encoding="utf-8")
    store = SnapshotStore()
    store.ensure(stuck)
    store.ensure(fine)
    stuck.write_text("changed", encoding="utf-8")
    fine.write_text("changed", encoding="utf-8")
    stuck.chmod(0o400)  # the restore of this one cannot write

    try:
        with pytest.raises(SnapshotError) as caught:
            store.rollback_all()
    finally:
        stuck.chmod(0o600)

    assert "stuck.txt" in str(caught.value)
    assert fine.read_text(encoding="utf-8") == "original"  # the others were still restored


def test_discard_forgets_the_snapshots_and_leaves_the_files_alone(work: Path) -> None:
    file = work / "a.txt"
    file.write_text("original", encoding="utf-8")
    store = SnapshotStore()
    store.ensure(file)
    file.write_text("kept", encoding="utf-8")

    store.discard()

    assert store.paths == ()
    assert file.read_text(encoding="utf-8") == "kept"


# --- On the run context ------------------------------------------------------


def test_every_run_context_has_its_own_store(work: Path) -> None:
    first, second = RunContext(workspace=work), RunContext(workspace=work)
    assert isinstance(first.snapshots, SnapshotStore)
    assert first.snapshots is not second.snapshots


# --- A write that did not happen ---------------------------------------------


def test_abandon_removes_the_directories_made_for_a_write_that_failed(work: Path) -> None:
    file = work / "a" / "b" / "new.txt"
    store = SnapshotStore()
    store.ensure(file)
    file.parent.mkdir(parents=True)  # the directories were made, then the write itself failed

    store.abandon(file)

    assert not (work / "a").exists()
    assert store.paths == ()


def test_abandon_never_deletes_a_file_someone_else_made_in_the_meantime(work: Path) -> None:
    file = work / "a" / "new.txt"
    store = SnapshotStore()
    store.ensure(file)
    file.parent.mkdir()
    file.write_text("not ours", encoding="utf-8")  # appeared between the check and the write

    store.abandon(file)

    assert file.read_text(encoding="utf-8") == "not ours"
    assert store.paths == ()


def test_abandon_copes_with_directories_that_were_never_made(work: Path) -> None:
    store = SnapshotStore()
    store.ensure(work / "a" / "b" / "new.txt")  # the mkdir failed before making anything
    store.abandon(work / "a" / "b" / "new.txt")
    assert store.paths == ()
