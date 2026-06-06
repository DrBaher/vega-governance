"""
Routing table coverage against Framework §2.

Every (sender, type) interaction in the Framework must have a routing entry.
"""

import pytest

from router import ROUTING_TABLE, EXTERNAL_TARGETS


# Expected agent-to-agent interactions from Framework §2.
# Format: (sender, type, ref_type-or-None, expected_recipients)
EXPECTED_ROUTES = [
    # Scope (§2.1)
    ("SA", "FND", None, ["SG"]),
    ("SG", "SCN", None, ["SE"]),
    ("SE", "DOC", None, ["SG"]),
    ("SE", "NOTE", None, ["SG"]),
    ("SG", "VAL", None, ["SE"]),
    ("SG", "REV", None, ["SE"]),
    ("SG", "REJ", "FND", ["SA"]),
    ("SG", "REJ", "DEV", ["BR"]),
    ("SG", "REJ", "ESC", ["TG"]),
    ("SG", "PRO-SCOPE", None, ["SA", "BR", "TG", "TA"]),
    # Test (§2.2)
    ("TG", "TCN", None, ["TE"]),
    ("TE", "DOC", None, ["TG"]),
    ("TE", "NOTE", None, ["TG"]),
    ("TG", "VAL", None, ["TE"]),
    ("TG", "REV", None, ["TE"]),
    ("TG", "REJ", "FND", ["TA"]),
    ("TA", "FND", None, ["TG"]),
    ("TG", "PRO-TEST-FULL", None, ["TA", "BTA"]),
    ("TG", "PRO-TEST-BUILD", None, ["BR"]),
    # Cross (§2.3)
    ("TG", "ESC", None, ["SG"]),
    # Build (§2.4)
    ("BR", "TSR", None, ["BTA"]),
    ("BTA", "TFR", None, ["TG"]),
    ("BTA", "VR", None, ["BR"]),
    ("BR", "DEV", None, ["SG"]),
    ("TG", "TRI", None, ["BR"]),
    # External (§2.5, §2.6)
    ("BR", "BRP", None, ["EXT"]),
    ("EXT", "BRQ", None, ["BR"]),
    ("SG", "DE_OUT", None, ["DE"]),
    # Backlog-bound (Spec v5 §4.1-4.2) — now in the merged ROUTING_TABLE
    ("SG", "PROP", None, ["OP"]),          # → op_backlog, notify OP
    ("SYS", "GOV", None, ["ADMIN_OP"]),    # → admin_backlog, notify ADMIN_OP
    # AUTH from OP (§7.2 + §13 step 4)
    ("OP",  "AUTH", None, ["SG"]),
]


@pytest.mark.parametrize("sender,doc_type,ref_type,recipients", EXPECTED_ROUTES)
def test_routing_table_covers_framework_interactions(sender, doc_type, ref_type, recipients):
    """Every framework interaction must have a routing entry with the right recipients."""
    key = (sender, doc_type, ref_type) if ref_type else (sender, doc_type)
    assert key in ROUTING_TABLE, f"Missing routing for {key}"
    actual = [r["to"] for r in ROUTING_TABLE[key]]
    assert set(actual) == set(recipients), f"{key}: expected {recipients}, got {actual}"


def test_prop_is_backlog_route_in_table():
    # Spec v5 §4.1-4.2 — PROP is a backlog route in the merged ROUTING_TABLE.
    route = ROUTING_TABLE[("SG", "PROP")][0]
    assert route["to"] == "OP"
    assert route["backlog"] == "op_backlog/pending"
    assert route["notify_role"] == "OP"
    assert route["exchange_mode"] is True and route["exchange_partner"] == "SG"


def test_gov_is_backlog_route_in_table():
    # Spec v5 §4.1-4.2 — GOV routes to the Admin OP backlog via the merged table.
    route = ROUTING_TABLE[("SYS", "GOV")][0]
    assert route["to"] == "ADMIN_OP"
    assert route["backlog"] == "admin_backlog/pending"
    assert route["notify_role"] == "ADMIN_OP"
    assert route["exchange_partner"] == "SYS"


def test_external_targets_recognized():
    assert "EXT" in EXTERNAL_TARGETS
    assert "DE" in EXTERNAL_TARGETS
    assert "ARCHIVE" in EXTERNAL_TARGETS
    # "OP"/"ADMIN_OP" are NOT external targets — they're handled by the `backlog`
    # branch inside the ROUTING_TABLE loop, not the external-relay path.
    assert "OP" not in EXTERNAL_TARGETS
