"""Tests for run profiles: permissions.yaml says what a run may do. A bad config blocks, it never relaxes."""

import textwrap
from pathlib import Path

import pytest

from prova.errors import ConfigError, ErrorCode
from prova.harness.permissions import Permission, Profile, get_profile, load_profiles
from prova.sandbox.base import ExecResult
from prova.tools.base import RunContext
from tests.fakes import WriterTool, make_tool_registry

GOOD = """
profiles:
  review:
    allow: [read]
    deny_paths: ["hidden_tests/**"]
  fix:
    allow: [read, write, exec]
    write_paths: ["src/**", "tests/**"]
    deny_paths: [".git/**", "hidden_tests/**"]
"""
REPO_ROOT = Path(__file__).resolve().parents[3]


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "permissions.yaml"
    path.write_text(textwrap.dedent(text), encoding="utf-8")
    return path


# --- Loading ------------------------------------------------------------------


def test_profiles_are_loaded_by_name(tmp_path: Path) -> None:
    profiles = load_profiles(write(tmp_path, GOOD))

    assert set(profiles) == {"review", "fix"}
    assert profiles["review"].allow == frozenset({Permission.READ})
    assert profiles["fix"].allow == frozenset({Permission.READ, Permission.WRITE, Permission.EXEC})
    assert profiles["fix"].write_paths == ("src/**", "tests/**")
    assert profiles["review"].write_paths == ()  # nothing is writable unless it is listed


def test_the_shipped_permissions_file_loads_and_the_review_profile_can_never_write() -> None:
    profiles = load_profiles(REPO_ROOT / "permissions.yaml")
    assert {"review", "fix"} <= set(profiles)
    assert profiles["review"].allow == frozenset({Permission.READ})  # the invariant of review mode


# --- A run context built from a profile -------------------------------------


def test_a_context_takes_its_permissions_from_the_profile(tmp_path: Path) -> None:
    profile = get_profile(load_profiles(write(tmp_path, GOOD)), "fix")
    ctx = RunContext.from_profile(profile, tmp_path)
    assert ctx.workspace == tmp_path
    assert ctx.permissions == profile.allow
    assert ctx.strict is False
    assert RunContext.from_profile(profile, tmp_path, strict=True).strict is True


def test_the_default_deny_list_always_applies_on_top_of_the_profile_list(tmp_path: Path) -> None:
    profile = get_profile(load_profiles(write(tmp_path, GOOD)), "review")
    deny = RunContext.from_profile(profile, tmp_path).deny

    assert deny.matches("hidden_tests/a.py")  # the profile's own pattern
    assert deny.matches(".env")  # the default list: the profile did not have to remember it
    assert not deny.matches("src/app.py")


def test_in_review_mode_a_write_tool_is_not_offered_and_is_denied_if_the_model_asks(tmp_path: Path) -> None:
    writer = WriterTool()
    tools = make_tool_registry(writer)
    profiles = load_profiles(write(tmp_path, GOOD))

    review = RunContext.from_profile(profiles["review"], tmp_path, strict=True)
    assert tools.to_api_tools(review.permissions) == []  # hidden from the model
    result = tools.dispatch("write_thing", {"text": "x"}, review)  # and still refused when asked by name
    assert result.error_code is ErrorCode.PERMISSION_DENIED
    assert writer.ran == 0

    fix = RunContext.from_profile(profiles["fix"], tmp_path, strict=True)
    assert tools.dispatch("write_thing", {"text": "x"}, fix).success
    assert writer.ran == 1


def test_an_unknown_profile_is_an_error_and_never_falls_back_to_another(tmp_path: Path) -> None:
    profiles = load_profiles(write(tmp_path, GOOD))
    with pytest.raises(ConfigError, match="nope") as exc:
        get_profile(profiles, "nope")
    assert "review" in str(exc.value) and "fix" in str(exc.value)  # it lists what exists


# --- A broken config blocks the run ------------------------------------------


def test_a_missing_file_is_a_config_error_that_names_the_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="nowhere.yaml"):
        load_profiles(tmp_path / "nowhere.yaml")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("profiles: [unclosed", "YAML"),
        ("- just\n- a list\n", "profiles"),
        ("something_else: 1\n", "profiles"),
        ("profiles: {}\n", "no profiles"),
        ("profiles:\n  review: {allow: [read]}\nextra: 1\n", "extra"),
    ],
    ids=["bad syntax", "not a mapping", "no profiles key", "empty profiles", "unknown top-level key"],
)
def test_a_broken_file_is_a_config_error(tmp_path: Path, text: str, expected: str) -> None:
    with pytest.raises(ConfigError, match=expected):
        load_profiles(write(tmp_path, text))


@pytest.mark.parametrize(
    ("profile", "expected"),
    [
        ("{alow: [read]}", "alow"),  # a typo in a key must not be ignored
        ("{allow: [reed]}", "allow"),  # an unknown permission
        ("{deny_paths: [x]}", "allow"),  # `allow` is required: there is no default permission
        ("{allow: [read], deny_paths: ['a//b']}", "deny_paths"),  # a bad pattern is found at load time
        ("{allow: [read], write_paths: ['/abs']}", "write_paths"),
    ],
    ids=["unknown key", "unknown permission", "missing allow", "bad deny pattern", "bad write pattern"],
)
def test_a_broken_profile_is_a_config_error_that_names_the_problem(
    tmp_path: Path, profile: str, expected: str
) -> None:
    with pytest.raises(ConfigError, match=expected) as exc:
        load_profiles(write(tmp_path, f"profiles:\n  review: {profile}\n"))
    assert "review" in str(exc.value)  # and says which profile


def test_a_yaml_file_cannot_make_us_run_code(tmp_path: Path) -> None:
    marker = tmp_path / "executed"
    text = f"profiles:\n  review: !!python/object/apply:os.system ['touch {marker}']\n"
    with pytest.raises(ConfigError):
        load_profiles(write(tmp_path, text))
    assert not marker.exists()  # safe_load refused the tag, nothing ran


def test_a_context_from_a_profile_has_no_sandbox_unless_one_is_given(tmp_path: Path) -> None:
    profile = Profile(allow=frozenset({Permission.READ, Permission.EXEC}))

    assert RunContext.from_profile(profile, tmp_path).sandbox is None  # no sandbox: no command runs

    class Marker:
        def run(
            self, argv: list[str], *, timeout_s: float, cwd: str = "/work", on_output: object = None
        ) -> ExecResult:
            raise AssertionError("not called")

        def close(self) -> None:
            pass

    sandbox = Marker()
    assert RunContext.from_profile(profile, tmp_path, sandbox=sandbox).sandbox is sandbox
