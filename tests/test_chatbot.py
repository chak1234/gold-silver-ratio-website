import pytest

from goldsilver.chatbot import DEFAULT_INVESTMENT_USD, _parse_amount, respond
from goldsilver.market import Quote

QUOTE = Quote(gold_usd=2000.0, silver_usd=25.0, usd_cad=1.4, usd_inr=88.0, as_of=1)


@pytest.mark.parametrize(
    ("question", "title"),
    [
        ("What is the current gold price?", "Gold price"),
        ("How much is silver?", "Silver price"),
        ("What is the gold/silver ratio?", "Gold/silver ratio"),
        ("Why does the ratio matter?", "What the ratio means"),
        ("Show me the historical trend", "The ratio in historical context"),
        ("Prices in Canada", "Prices in Canada (CAD)"),
        ("Silver price in rupees", "Prices in India (INR)"),
        ("Show me prices per gram", "Price per gram"),
        ("Compare gold and silver", "Gold compared with silver"),
        ("How much gold can I buy with $10,000?", "What $10,000.00 buys"),
        ("Should I invest 2,500 in silver?", "What $2,500.00 buys"),
        ("hello there", "Here is what you can ask me"),
    ],
)
def test_routing(question, title):
    assert respond(question, QUOTE).title == title


@pytest.mark.parametrize("question", ["a decade of decadence", "cadmium", "mainframe"])
def test_keywords_match_whole_words_only(question):
    assert respond(question, QUOTE).title == "Here is what you can ask me"


@pytest.mark.parametrize(
    ("text", "amount"),
    [
        ("$1,000,000 please", 1_000_000),
        ("$2,500.50", 2500.5),
        ("buy 10k of gold", 10_000),
        ("invest 2 million", 2_000_000),
        ("invest some money", DEFAULT_INVESTMENT_USD),
        ("$0", DEFAULT_INVESTMENT_USD),
        ("$99999999999999999", DEFAULT_INVESTMENT_USD),
    ],
)
def test_parse_amount(text, amount):
    assert _parse_amount(text) == amount


def test_investment_values():
    reply = respond("buy $2000", QUOTE)
    assert reply.rows[0] == ("Gold", "1.0000 oz at $2,000.00/oz")
    assert reply.rows[1] == ("Silver", "80.00 oz at $25.00/oz")


def test_ratio_reply_compares_to_average():
    reply = respond("ratio", QUOTE)
    assert ("Current", "80.00:1") in reply.rows
    assert ("Versus average", "+33.3%") in reply.rows


def test_price_questions_without_data_say_so():
    assert respond("gold price", None).title == "Prices are unavailable right now"


def test_education_works_without_data():
    assert respond("explain the ratio", None).title == "What the ratio means"


def test_text_rendering_includes_all_parts():
    text = respond("ratio", QUOTE).to_text()
    assert "Gold/silver ratio" in text
    assert "Current: 80.00:1" in text
    assert "not financial advice" in text
