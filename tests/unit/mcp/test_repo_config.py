"""The mcp.yaml of this repository is itself checked: a typo should not wait for a real run to show up."""

from pathlib import Path

from prova.harness.permissions import Permission
from prova.mcp.config import load_mcp_config

ROOT = Path(__file__).resolve().parents[3]


def test_the_repository_config_is_valid_and_lists_only_the_demo_server() -> None:
    config = load_mcp_config(ROOT / "mcp.yaml")
    assert list(config.servers) == ["knowledge"]
    assert config.servers["knowledge"].permission == Permission.READ  # the demo server only reads
    assert config.base_dir == ROOT


def test_the_server_the_config_points_at_exists() -> None:
    script = load_mcp_config(ROOT / "mcp.yaml").servers["knowledge"].command[-1]
    assert (ROOT / script).is_file()
