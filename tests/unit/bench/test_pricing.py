"""The price table: what a model costs, from a file with a date and a source. No price, no money limit."""

from datetime import date
from pathlib import Path

import pytest
import yaml

from bench.pricing import PricingError, load_price_table
from prova.llm.usage import UsageLog, UsageRecord

REAL = Path(__file__).resolve().parents[3] / "bench" / "pricing.yaml"
GOOD = {
    "as_of": "2026-11-03",
    "source": "a note on where the numbers come from",
    "models": {
        "model-a": {"input_per_mtok": 2.0, "output_per_mtok": 10.0},
        "model-b": {"input_per_mtok": 0.1, "output_per_mtok": 0.5, "max_prompt_tokens": 100_000},
    },
}


def write(tmp_path: Path, data: object) -> Path:
    path = tmp_path / "pricing.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


# --- The real table -------------------------------------------------------------------------------


def test_the_real_table_has_the_two_models_the_agent_uses_and_says_where_it_comes_from() -> None:
    table = load_price_table(REAL)
    assert table.as_of == date(2026, 11, 3) and table.source.strip()
    sonnet, haiku = table.pricing_for("claude-sonnet-5-5"), table.pricing_for("claude-haiku-5-5")
    assert (sonnet.input_per_mtok, sonnet.output_per_mtok) == (2.0, 10.0)
    assert (haiku.input_per_mtok, haiku.output_per_mtok) == (0.1, 0.5)
    assert haiku.max_prompt_tokens == 100_000


# --- Loading --------------------------------------------------------------------------------------


def test_a_good_table_gives_the_price_of_each_model(tmp_path: Path) -> None:
    table = load_price_table(write(tmp_path, GOOD))
    assert table.pricing_for("model-a").output_per_mtok == 10.0
    assert table.pricing_for("model-a").max_prompt_tokens is None


def test_a_model_with_no_price_is_an_error_that_names_it_and_the_known_ones(tmp_path: Path) -> None:
    table = load_price_table(write(tmp_path, GOOD))
    with pytest.raises(PricingError, match="model-z") as caught:
        table.pricing_for("model-z")
    assert "model-a" in str(caught.value) and "model-b" in str(caught.value)


@pytest.mark.parametrize("missing", ["as_of", "source", "models"])
def test_a_table_needs_its_date_its_source_and_its_models(tmp_path: Path, missing: str) -> None:
    data = {key: value for key, value in GOOD.items() if key != missing}
    with pytest.raises(PricingError, match=missing):
        load_price_table(write(tmp_path, data))


def test_a_table_with_no_model_is_refused(tmp_path: Path) -> None:
    with pytest.raises(PricingError, match="models"):
        load_price_table(write(tmp_path, {**GOOD, "models": {}}))


def test_an_unknown_field_is_refused_and_not_ignored(tmp_path: Path) -> None:
    models = {"model-a": {"input_per_mtok": 2.0, "output_per_mtok": 10.0, "cache_read_per_mtok": 0.2}}
    with pytest.raises(PricingError, match="cache_read_per_mtok"):
        load_price_table(write(tmp_path, {**GOOD, "models": models}))


@pytest.mark.parametrize("bad", [0, -1.0])
def test_a_price_must_be_above_zero(tmp_path: Path, bad: float) -> None:
    models = {"model-a": {"input_per_mtok": bad, "output_per_mtok": 10.0}}
    with pytest.raises(PricingError, match="input_per_mtok"):
        load_price_table(write(tmp_path, {**GOOD, "models": models}))


@pytest.mark.parametrize("content", ["- a list\n", "", "key: [unclosed\n"])
def test_a_file_that_is_not_a_mapping_is_refused(tmp_path: Path, content: str) -> None:
    path = tmp_path / "pricing.yaml"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(PricingError, match=r"pricing\.yaml"):
        load_price_table(path)


def test_a_missing_file_is_an_error_and_not_an_empty_table(tmp_path: Path) -> None:
    with pytest.raises(PricingError, match="no such file|not found|does not exist"):
        load_price_table(tmp_path / "nope.yaml")


# --- A price that holds only up to a size of prompt -----------------------------------------------


def test_a_turn_with_a_bigger_prompt_than_the_price_covers_stops_the_count(tmp_path: Path) -> None:
    pricing = load_price_table(write(tmp_path, GOOD)).pricing_for("model-b")
    log = UsageLog()
    log.add(UsageRecord(turn=1, input_tokens=100_000, output_tokens=10))
    assert pricing.cost(log) > 0  # exactly at the edge is covered
    log.add(UsageRecord(turn=2, input_tokens=100_001, output_tokens=10))
    with pytest.raises(ValueError, match="100000"):
        pricing.cost(log)
