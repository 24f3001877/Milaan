"""c5 allocation integrity and audit log privileges

Revision ID: 1c80198d6308
Revises: 71fc580c5fd8
Create Date: 2026-08-25 08:10:01.805568

This migration adds the two things SQLAlchemy's declarative layer cannot express, both
carrying constraint C5 (Schema §5.4):

1. `uniq_active_member` — a record must never belong to two ACTIVE match groups. Postgres
   partial-index predicates must be immutable expressions and cannot contain a subquery
   (`WHERE group_id IN (SELECT ...)` is not legal DDL), so — per the schema doc's own
   fallback note — this is implemented as a trigger-maintained boolean flag
   (`is_active_member`) with a plain partial unique index on that flag. The intent is what
   matters: double allocation must be impossible in the data, not merely avoided in code.

2. `trg_allocation_balances` — a deferred constraint trigger asserting that, within a match
   group that includes a bank credit, the settlement lines allocated to it sum exactly to
   that credit. Deferred to COMMIT so a group can be built incrementally across several
   inserts in one transaction (e.g. the T3 solver attaching members one at a time).

3. Audit-log append-only enforced by privilege: the runtime application role loses UPDATE
   and DELETE on `audit_log` entirely. Tamper-evidence is then a property of the grant
   table, not application discipline (Schema §5.4).
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "1c80198d6308"
down_revision: str | None = "71fc580c5fd8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- 1. Active-membership integrity (no double allocation) -----------------------
    op.execute("ALTER TABLE match_member ADD COLUMN is_active_member BOOLEAN NOT NULL DEFAULT true")

    op.execute(
        """
        CREATE OR REPLACE FUNCTION sync_match_member_active_flag() RETURNS TRIGGER AS $$
        BEGIN
            SELECT (mg.status <> 'rejected') INTO NEW.is_active_member
            FROM match_group mg WHERE mg.id = NEW.group_id;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_sync_match_member_active_flag
        BEFORE INSERT OR UPDATE OF group_id ON match_member
        FOR EACH ROW EXECUTE FUNCTION sync_match_member_active_flag();
        """
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION propagate_match_group_status_to_members() RETURNS TRIGGER AS $$
        BEGIN
            IF NEW.status IS DISTINCT FROM OLD.status THEN
                UPDATE match_member
                SET is_active_member = (NEW.status <> 'rejected')
                WHERE group_id = NEW.id;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_propagate_match_group_status
        AFTER UPDATE OF status ON match_group
        FOR EACH ROW EXECUTE FUNCTION propagate_match_group_status_to_members();
        """
    )

    op.execute(
        """
        CREATE UNIQUE INDEX uniq_active_member
        ON match_member (run_id, entity_type, entity_id)
        WHERE is_active_member;
        """
    )

    # --- 2. Allocation-balance integrity (T3 credit-vs-settlement-lines) --------------
    op.execute(
        """
        CREATE OR REPLACE FUNCTION assert_allocation_balances() RETURNS TRIGGER AS $$
        DECLARE
            g_id UUID;
            bank_total NUMERIC(20,4);
            settlement_total NUMERIC(20,4);
        BEGIN
            g_id := COALESCE(NEW.group_id, OLD.group_id);

            SELECT SUM(allocated_amount) INTO bank_total
            FROM match_member WHERE group_id = g_id AND entity_type = 'bank_txn';

            IF bank_total IS NULL THEN
                RETURN NULL;
            END IF;

            SELECT SUM(allocated_amount) INTO settlement_total
            FROM match_member WHERE group_id = g_id AND entity_type = 'settlement_line';

            IF settlement_total IS DISTINCT FROM bank_total THEN
                RAISE EXCEPTION
                    'match_group % allocation imbalance: bank credit % != settlement lines %',
                    g_id, bank_total, COALESCE(settlement_total, 0);
            END IF;

            RETURN NULL;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE CONSTRAINT TRIGGER trg_allocation_balances
        AFTER INSERT OR UPDATE OR DELETE ON match_member
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION assert_allocation_balances();
        """
    )

    # --- 3. Audit log append-only by privilege ----------------------------------------
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'milaan_app') THEN
                CREATE ROLE milaan_app LOGIN PASSWORD 'milaan_app_dev_only';
            END IF;
        END
        $$;
        """
    )
    op.execute("GRANT USAGE ON SCHEMA public TO milaan_app")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO milaan_app")
    op.execute("GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO milaan_app")
    op.execute("REVOKE UPDATE, DELETE ON audit_log FROM milaan_app")


def downgrade() -> None:
    op.execute("GRANT UPDATE, DELETE ON audit_log TO milaan_app")
    op.execute("DROP TRIGGER IF EXISTS trg_allocation_balances ON match_member")
    op.execute("DROP FUNCTION IF EXISTS assert_allocation_balances()")
    op.execute("DROP INDEX IF EXISTS uniq_active_member")
    op.execute("DROP TRIGGER IF EXISTS trg_propagate_match_group_status ON match_group")
    op.execute("DROP FUNCTION IF EXISTS propagate_match_group_status_to_members()")
    op.execute("DROP TRIGGER IF EXISTS trg_sync_match_member_active_flag ON match_member")
    op.execute("DROP FUNCTION IF EXISTS sync_match_member_active_flag()")
    op.execute("ALTER TABLE match_member DROP COLUMN is_active_member")
