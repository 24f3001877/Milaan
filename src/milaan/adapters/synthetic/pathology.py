"""The documented pathology catalogue.

Mirrors `exception_category` in Schema §5.3 exactly, so an injected pathology and the
exception category the engine later assigns it are directly comparable — that comparison
*is* the pathology table on the Run Dashboard (UI/UX §3, S5).

Each entry is deliberately a short, honest description of what real-world condition the
pathology simulates, not marketing copy — a reviewer reading this file should immediately
recognise the reconciliation problem it stands in for (PDR §1.1).
"""

from __future__ import annotations

PATHOLOGY_CATALOGUE: dict[str, str] = {
    "missing_in_bank": "Settlement line exists, but no bank credit ever covers it this period.",
    "missing_in_gateway": "An order was paid but the gateway settlement report never lists it.",
    "orphan_bank_credit": "A bank credit arrives with no settlement line explaining it.",
    "amount_mismatch": "Settlement gross differs from the order's recorded gross.",
    "fee_variance": (
        "Reported fee/tax deviates from the rate-card-expected figure beyond tolerance."
    ),
    "duplicate_utr": "A settlement line's UTR collides with an unrelated batch's UTR.",
    "partial_settlement": "One order's payment is split across two settlement lines.",
    "period_boundary_timing": "Settlement falls in-period but its bank credit lands next period.",
    "netted_refund_unlinked": "A refund line nets into a credit with no traceable order reference.",
    "chargeback_debit_unlinked": "A chargeback debit with no traceable order reference.",
    "unknown_adjustment": "An adjustment line with no order reference and no clear cause.",
    "ambiguous_multi_candidate": "A malformed payment_id forces inference among same-day, "
    "same-amount candidates with no single confident answer.",
}

# Equal weight by default — tune per category for a specific demo scenario, but always
# disclose the mix actually used (Implementation Plan §6.5 risk register: "tune the
# generator honestly and disclose the mix, never quietly make the data easy").
DEFAULT_WEIGHTS: dict[str, float] = dict.fromkeys(PATHOLOGY_CATALOGUE, 1.0)

# The compressed 7-day variant (Implementation Plan §6.4) cuts to 6 pathology classes.
COMPRESSED_CATEGORIES: tuple[str, ...] = (
    "missing_in_bank",
    "missing_in_gateway",
    "orphan_bank_credit",
    "amount_mismatch",
    "fee_variance",
    "partial_settlement",
)
