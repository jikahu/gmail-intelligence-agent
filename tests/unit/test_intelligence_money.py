"""Money extraction and — most importantly — account masking (Phase 6, §7)."""

from __future__ import annotations

from app.intelligence import money


def test_symbol_prefixed_amount() -> None:
    found = money.extract_money("Total $1,234.56 today")
    assert (found[0].currency, found[0].amount) == ("USD", 1234.56)


def test_code_prefixed_kes() -> None:
    found = money.extract_money("Please pay KES 5,000")
    assert (found[0].currency, found[0].amount) == ("KES", 5000.0)


def test_amount_then_currency_word() -> None:
    found = money.extract_money("a refund of 99 euros")
    assert (found[0].currency, found[0].amount) == ("EUR", 99.0)


def test_bare_number_is_not_money() -> None:
    assert money.extract_money("order 12345 confirmed, item 7") == []


def test_primary_prefers_amount_due_over_subtotal() -> None:
    text = "Subtotal $10.00. Shipping $3.00. Amount due $42.50."
    primary = money.primary_money(text)
    assert primary is not None
    assert primary.amount == 42.50


# -------- account masking: the safety-critical behaviour --------


def test_full_card_number_is_reduced_to_last_four() -> None:
    refs = money.extract_account_refs("card number 4111 1111 1111 1234")
    assert [r.last4 for r in refs] == ["1234"]


def test_masked_reference_keeps_last_four() -> None:
    assert money.extract_account_refs("account ****5678")[0].last4 == "5678"


def test_ending_in_pattern() -> None:
    assert money.extract_account_refs("your card ending in 4321")[0].last4 == "4321"


def test_random_long_number_without_account_wording_is_not_captured() -> None:
    # A tracking number is not an account reference.
    assert money.extract_account_refs("tracking number 12345678901234") == []


def test_every_reference_is_at_most_four_digits() -> None:
    samples = [
        "account 12345678",
        "acct: 999888777666",
        "card ****1234",
        "ending in 0001",
        "IBAN GB29 NWBK 6016 1331 9268 19",
    ]
    for sample in samples:
        for ref in money.extract_account_refs(sample):
            assert len(ref.last4) <= 4


def test_mask_number_helper() -> None:
    assert money.mask_number("4111-1111-1111-1234") == "1234"
    assert money.mask_number("12") == ""


def test_account_ref_dataclass_has_no_full_number_field() -> None:
    # A guard against someone adding a full-number field later.
    fields = money.AccountRef.__dataclass_fields__
    assert set(fields) == {"last4", "original_text"}
