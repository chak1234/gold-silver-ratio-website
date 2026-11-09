"""Rule-based assistant shared by the website widget and the desktop app.

Replies are structured data rather than HTML, so each client renders them safely.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

from .market import GRAMS_PER_TROY_OUNCE, Quote

LONG_RUN_AVERAGE_RATIO = 60
MODERN_LOW_RATIO = 15
MODERN_HIGH_RATIO = 125
DEFAULT_INVESTMENT_USD = 10_000
MAX_INVESTMENT_USD = 1_000_000_000_000
NOT_ADVICE = "Educational information only, not financial advice."

SYMBOLS = {"USD": "$", "CAD": "CA$", "EUR": "€", "INR": "₹"}
MULTIPLIERS = {"k": 1e3, "thousand": 1e3, "m": 1e6, "million": 1e6}

_EXPLAIN = re.compile(r"\b(explain|means?|meaning|define|definition|why|matters?|important)\b")
_INVEST = re.compile(r"\b(invest\w*|buy|purchase|afford|calculate)\b")
_DOLLAR_AMOUNT = re.compile(r"\$\s*\d")
_AMOUNT = re.compile(
    r"(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?\s*(k|m|thousand|million)?\b",
)
_CANADA = re.compile(r"\b(canada|canadian|cad)\b")
_EUROPE = re.compile(r"\b(europe|european|eur|euros?)\b")
_INDIA = re.compile(r"\b(india|indian|inr|rupees?)\b")
_HISTORY = re.compile(r"\b(history|historical|past|trends?|all[- ]time)\b")
_RATIO = re.compile(r"\bratio\b")
_COMPARE = re.compile(r"\b(compare|comparison|difference|versus|vs)\b")
_GOLD = re.compile(r"\bgold\b")
_SILVER = re.compile(r"\bsilver\b")
_GRAM = re.compile(r"\b(grams?|prices?)\b")


@dataclass(frozen=True)
class Reply:
    """A chatbot answer: an optional table of rows surrounded by short text."""

    title: str
    paragraphs: list[str] = field(default_factory=list)
    rows: list[tuple[str, str]] = field(default_factory=list)
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_text(self) -> str:
        lines = [self.title]
        if self.rows:
            lines += ["", *(f"{label}: {value}" for label, value in self.rows)]
        if self.paragraphs:
            lines += ["", *self.paragraphs]
        if self.note:
            lines += ["", self.note]
        return "\n".join(lines)


def _money(amount: float, currency: str) -> str:
    return f"{SYMBOLS[currency]}{amount:,.2f}"


def _parse_amount(question: str) -> float:
    match = _AMOUNT.search(question)
    if not match:
        return DEFAULT_INVESTMENT_USD
    whole, fraction, suffix = match.groups()
    amount = float(whole.replace(",", "") + (fraction or "")) * MULTIPLIERS.get(suffix, 1)
    return amount if 0 < amount <= MAX_INVESTMENT_USD else DEFAULT_INVESTMENT_USD


def _help() -> Reply:
    return Reply(
        title="Here is what you can ask me",
        paragraphs=[
            "What is the gold price?",
            "What is the silver price?",
            "What is the gold/silver ratio?",
            "What are prices in Canada, Europe or India?",
            "Show me prices per gram.",
            "How much gold can I buy with $10,000?",
            "Why does the ratio matter?",
        ],
    )


def _unavailable() -> Reply:
    return Reply(
        title="Prices are unavailable right now",
        paragraphs=["I can't reach the market data provider. Please try again shortly."],
    )


def _explain(quote: Quote | None) -> Reply:
    paragraphs = [
        "The gold/silver ratio is how many ounces of silver it takes to buy one ounce of gold.",
        "Investors use it to judge the relative value of the two metals. A high reading can "
        "suggest silver is cheap relative to gold, and a low reading the opposite. It says "
        "nothing about whether either metal is cheap in absolute terms.",
    ]
    if quote:
        paragraphs.append(f"The ratio is currently {quote.ratio:.2f}:1.")
    return Reply(title="What the ratio means", paragraphs=paragraphs, note=NOT_ADVICE)


def _history(quote: Quote | None) -> Reply:
    rows = [
        ("Long-run average", f"about {LONG_RUN_AVERAGE_RATIO}:1"),
        ("Modern low", f"about {MODERN_LOW_RATIO}:1"),
        ("Modern high", f"about {MODERN_HIGH_RATIO}:1"),
    ]
    if quote:
        rows.insert(0, ("Current", f"{quote.ratio:.2f}:1"))
    return Reply(
        title="The ratio in historical context",
        rows=rows,
        paragraphs=["The ratio tends to rise in periods of economic stress."],
        note="Long-run figures are approximate.",
    )


def _ratio(quote: Quote) -> Reply:
    difference = (quote.ratio - LONG_RUN_AVERAGE_RATIO) / LONG_RUN_AVERAGE_RATIO * 100
    position = "above" if difference >= 0 else "below"
    return Reply(
        title="Gold/silver ratio",
        rows=[
            ("Current", f"{quote.ratio:.2f}:1"),
            ("Long-run average", f"about {LONG_RUN_AVERAGE_RATIO}:1"),
            ("Versus average", f"{difference:+.1f}%"),
        ],
        paragraphs=[
            f"It takes {quote.ratio:.2f} ounces of silver to buy one ounce of gold, "
            f"{abs(difference):.0f}% {position} the long-run average.",
        ],
        note=NOT_ADVICE,
    )


def _metal(quote: Quote, metal: str) -> Reply:
    rows = [
        (
            currency,
            f"{_money(quote.price_per_ounce(metal, currency), currency)}/oz · "
            f"{_money(quote.price_per_gram(metal, currency), currency)}/g",
        )
        for currency in SYMBOLS
    ]
    return Reply(title=f"{metal.capitalize()} price", rows=rows)


def _country(quote: Quote, name: str, currency: str) -> Reply:
    gold, silver = (quote.price_per_gram(m, currency) for m in ("gold", "silver"))
    return Reply(
        title=f"Prices in {name} ({currency})",
        rows=[
            ("Gold, 1 g", _money(gold, currency)),
            ("Gold, 10 g", _money(gold * 10, currency)),
            ("Silver, 1 g", _money(silver, currency)),
            ("Silver, 100 g", _money(silver * 100, currency)),
        ],
        note=f"1 USD = {quote.rate(currency):.4f} {currency}",
    )


def _per_gram(quote: Quote) -> Reply:
    rows = [
        (
            currency,
            f"Gold {_money(quote.price_per_gram('gold', currency), currency)} · "
            f"Silver {_money(quote.price_per_gram('silver', currency), currency)}",
        )
        for currency in SYMBOLS
    ]
    return Reply(
        title="Price per gram",
        rows=rows,
        note=f"1 troy ounce = {GRAMS_PER_TROY_OUNCE} grams",
    )


def _compare(quote: Quote) -> Reply:
    return Reply(
        title="Gold compared with silver",
        rows=[
            ("Gold", f"{_money(quote.gold_usd, 'USD')}/oz"),
            ("Silver", f"{_money(quote.silver_usd, 'USD')}/oz"),
            ("Ratio", f"{quote.ratio:.2f}:1"),
        ],
        paragraphs=[f"By weight, gold costs about {quote.ratio:.0f} times as much as silver."],
    )


def _investment(quote: Quote, question: str) -> Reply:
    amount = _parse_amount(question)
    return Reply(
        title=f"What {_money(amount, 'USD')} buys",
        rows=[
            ("Gold", f"{amount / quote.gold_usd:,.4f} oz at {_money(quote.gold_usd, 'USD')}/oz"),
            (
                "Silver",
                f"{amount / quote.silver_usd:,.2f} oz at {_money(quote.silver_usd, 'USD')}/oz",
            ),
        ],
        note=f"Spot prices, before dealer premiums and fees. {NOT_ADVICE}",
    )


def respond(question: str, quote: Quote | None) -> Reply:
    """Answer a question. ``quote`` is None when market data is unavailable."""
    text = question.lower()

    if _EXPLAIN.search(text):
        return _explain(quote)
    if _HISTORY.search(text):
        return _history(quote)

    has_gold, has_silver = bool(_GOLD.search(text)), bool(_SILVER.search(text))
    wants_investment = bool(_INVEST.search(text) or _DOLLAR_AMOUNT.search(text))
    needs_quote = (
        wants_investment
        or has_gold
        or has_silver
        or any(p.search(text) for p in (_CANADA, _EUROPE, _INDIA, _RATIO, _GRAM, _COMPARE))
    )
    if not needs_quote:
        return _help()
    if quote is None:
        return _unavailable()

    if wants_investment:
        return _investment(quote, text)
    if _CANADA.search(text):
        return _country(quote, "Canada", "CAD")
    if _EUROPE.search(text):
        return _country(quote, "Europe", "EUR")
    if _INDIA.search(text):
        return _country(quote, "India", "INR")
    if _RATIO.search(text):
        return _ratio(quote)
    if _COMPARE.search(text) or (has_gold and has_silver):
        return _compare(quote)
    if has_gold:
        return _metal(quote, "gold")
    if has_silver:
        return _metal(quote, "silver")
    return _per_gram(quote)
