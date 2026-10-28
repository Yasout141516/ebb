"""What each model costs on a paid plan, to estimate a call's cost from its tokens."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

MILLION = Decimal(1_000_000)


@dataclass(frozen=True)
class Price:
    input_per_m: Decimal  # USD per million input tokens
    output_per_m: Decimal  # USD per million output tokens (reasoning tokens included)
    source: str  # the provider's page the numbers were read from
    as_of: date
    free_tier: bool  # Ebb uses the provider's free plan, so real spend is $0


# Keyed by model name. A model left out has no known price: its calls record no cost.
# mistral-medium-latest is left out: Mistral's pricing page lists Medium 3.5 ($1.5 / $7.5),
# but no Mistral page said which version the -latest alias serves on 2026-10-28.
PRICES: dict[str, Price] = {
    # Groq's free plan: console.groq.com/docs/rate-limits lists "Free Plan Limits".
    "openai/gpt-oss-120b": Price(
        Decimal("0.15"),
        Decimal("0.60"),
        "https://console.groq.com/docs/model/openai/gpt-oss-120b",
        date(2026, 10, 28),
        free_tier=True,
    ),
}


def cost(model: str, input_tokens: int | None, output_tokens: int | None) -> Decimal | None:
    """Estimated USD for these tokens; None when the price or either count is unknown."""
    price = PRICES.get(model)
    if price is None or input_tokens is None or output_tokens is None:
        return None
    return (input_tokens * price.input_per_m + output_tokens * price.output_per_m) / MILLION
