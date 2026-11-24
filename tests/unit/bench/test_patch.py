"""Reading a unified diff: the lines each file got and lost, numbered as in the new file."""

from bench.patch import FilePatch, parse_patch


def test_the_added_lines_carry_their_number_in_the_new_file_and_the_removed_lines_their_text() -> None:
    patch = (
        "--- a/a.py\n+++ b/a.py\n@@ -2,3 +2,4 @@\n keep\n-old\n+new one\n+new two\n keep\n"
        "@@ -10 +11 @@\n-x\n+y\n"
    )
    assert parse_patch(patch) == {
        "a.py": FilePatch(added=((3, "new one"), (4, "new two"), (11, "y")), removed=("old", "x"))
    }


def test_each_file_gets_its_own_lines() -> None:
    patch = "--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-p\n+q\n--- a/b.py\n+++ b/b.py\n@@ -1 +1 @@\n-r\n+s\n"
    result = parse_patch(patch)
    assert result["a.py"].added == ((1, "q"),) and result["b.py"].removed == ("r",)


def test_a_new_file_has_added_lines_under_its_new_name() -> None:
    patch = "--- /dev/null\n+++ b/new.py\n@@ -0,0 +1,2 @@\n+one\n+two\n"
    assert parse_patch(patch) == {"new.py": FilePatch(added=((1, "one"), (2, "two")), removed=())}


def test_a_deleted_file_has_removed_lines_under_its_old_name() -> None:
    patch = "--- a/gone.py\n+++ /dev/null\n@@ -1,2 +0,0 @@\n-one\n-two\n"
    assert parse_patch(patch) == {"gone.py": FilePatch(added=(), removed=("one", "two"))}


def test_a_removed_line_that_looks_like_a_header_is_still_a_line() -> None:
    patch = "--- a/a.py\n+++ b/a.py\n@@ -1,2 +1 @@\n keep\n--- not a header\n"
    assert parse_patch(patch) == {"a.py": FilePatch(added=(), removed=("-- not a header",))}


def test_an_added_line_that_looks_like_a_header_is_still_a_line() -> None:
    patch = "--- a/a.py\n+++ b/a.py\n@@ -1 +1,2 @@\n keep\n+++ not a header\n"
    assert parse_patch(patch) == {"a.py": FilePatch(added=((2, "++ not a header"),), removed=())}


def test_the_marker_for_a_missing_newline_and_the_notes_about_other_files_are_not_lines() -> None:
    patch = (
        "--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-x\n\\ No newline at end of file\n"
        "+y\n\\ No newline at end of file\n"
        "Binary file data.bin differs\nSymlink or file type changed: ln\n"
    )
    assert parse_patch(patch) == {"a.py": FilePatch(added=((1, "y"),), removed=("x",))}


def test_a_patch_with_no_text_changes_is_empty() -> None:
    assert parse_patch("") == {}
    assert parse_patch("Binary file data.bin differs\n") == {}
