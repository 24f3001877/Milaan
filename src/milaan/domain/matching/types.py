"""Match result types, shared across all cascade tiers (T1-T4).

`MatchGroupResult` is mutable and grows across passes: T1 creates order<->settlement
groups; T2 either merges a group's settlement lines into a bank-confirmed group or attaches
a bank_txn member directly to the existing group. This mirrors the schema's intent that one
match_group is "a single auditable unit" tying order(s) to settlement line(s) to a bank
credit (Schema §5.2) — it is not one group per tier, but one group whose evidence
accumulates as later tiers run.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from decimal import Decimal

from milaan.domain.money import Money


@dataclass(slots=True)
class MatchMemberResult:
    entity_type: str  # "order" | "settlement_line" | "bank_txn"
    entity_id: uuid.UUID
    allocated_amount: Money


@dataclass(slots=True)
class MatchGroupResult:
    tier: str  # "T1_PAYMENT_ID" | "T2_UTR" | "T3_ALLOCATION" | "T4_FEE"
    confidence: Decimal
    rule_id: str
    members: list[MatchMemberResult] = field(default_factory=list)

    def member_ids(self, entity_type: str) -> set[uuid.UUID]:
        return {m.entity_id for m in self.members if m.entity_type == entity_type}

    def has_member(self, entity_type: str, entity_id: uuid.UUID) -> bool:
        return any(m.entity_type == entity_type and m.entity_id == entity_id for m in self.members)

    def add_member(self, member: MatchMemberResult) -> None:
        """Upsert semantics: a later tier's evidence about an entity's allocated_amount
        supersedes an earlier tier's. This matters concretely for T1->T2: T1 records a
        settlement line's amount as its `gross` (all it knows before a bank credit exists),
        but the C5 balance invariant needs the line's `net` once a bank_txn joins the group.
        A naive 'skip if already present' guard would silently keep the stale T1 value and
        the group would fail the allocation-balance check by exactly the fee+tax delta —
        which is precisely the bug this method used to have."""
        for i, existing in enumerate(self.members):
            if existing.entity_type == member.entity_type and existing.entity_id == member.entity_id:
                self.members[i] = member
                return
        self.members.append(member)

    def upgrade(self, tier: str, extra_rule_id: str) -> None:
        """Record that a later tier added evidence to this group without discarding the
        earlier tier's contribution — the rule_id becomes a trail, not a single label."""
        self.tier = tier
        if extra_rule_id not in self.rule_id.split("+"):
            self.rule_id = f"{self.rule_id}+{extra_rule_id}"
