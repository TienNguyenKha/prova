"""The price table of the benchmark: `pricing.yaml`, loaded strictly.

A money limit with no price behind it cannot be kept, so a model that is not in the table is an error, never a
default. The table has a date and a source so that a cost in a result can be tied to the numbers it came from.
"""

from datetime import date
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from prova.agent.budget import Pricing


class PricingError(Exception):
    """The price table is wrong, missing, or has no price for the model that was asked for."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ModelPrice(_Strict):
    input_per_mtok: float = Field(gt=0)
    output_per_mtok: float = Field(gt=0)
    max_prompt_tokens: int | None = Field(default=None, gt=0)  # the price holds only up to this prompt size


class PriceTable(_Strict):
    as_of: date
    source: str = Field(min_length=1)
    models: dict[str, ModelPrice] = Field(min_length=1)

    def pricing_for(self, model: str) -> Pricing:
        price = self.models.get(model)
        if price is None:
            known = ", ".join(sorted(self.models))
            raise PricingError(f"there is no price for the model {model!r}; the table knows: {known}")
        return Pricing(
            input_per_mtok=price.input_per_mtok,
            output_per_mtok=price.output_per_mtok,
            max_prompt_tokens=price.max_prompt_tokens,
        )


def load_price_table(path: Path) -> PriceTable:
    try:
        data: object = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise PricingError(f"{path}: no such file") from None
    except yaml.YAMLError as e:
        raise PricingError(f"{path}: not valid YAML ({type(e).__name__})") from None
    if not isinstance(data, dict):
        raise PricingError(f"{path}: pricing.yaml must be a mapping with as_of, source and models")
    try:
        return PriceTable.model_validate(data)
    except ValidationError as e:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in error['loc'])}: {error['msg']}" for error in e.errors()
        )
        raise PricingError(f"{path}: {problems}") from None
