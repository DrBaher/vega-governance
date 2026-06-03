"""
Routing table coverage against Framework §2.

Every (sender, type) interaction in the Framework must have a routing entry.
"""

import pytest

from router import ROUTING_TABLE, OP_BOUND_TYPES, ADMIN_BOUND_TYPES, EXTERNAL_TARGETS


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
    # SYS (§2.7) — GOV is governance → Admin OP (Spec v5 §4.1)
    ("SYS", "GOV", None, ["ADMIN_OP"]),
    # AUTH from OP (§7.2 + §13 step 4)
    ("OP",  "AUTH", None, ["SG"]),
]


@pytest.mark.parametrize("sender,doc_type,ref_type,recipients", EXPECTED_ROUTES)
def test_routing_table_covers_framework_interactions(sender, doc_type, ref_type, recipients):
    """Every framework interaction must have a routing entry with the right recipients."""
    if doc_type == "GOV" and sender == "SYS":
        # GOV is Admin-OP-bound (Spec v5 §4.2)
        assert ("SYS", "GOV") in ADMIN_BOUND_TYPES
        return
    if ref_type:
        key = (sender, doc_type, ref_type)
    else:
        key = (sender, doc_type)
    assert key in ROUTING_TABLE, f"Missing routing for {key}"
    actual = [r["to"] for r in ROUTING_TABLE[key]]
    assert set(actual) == set(recipients), f"{key}: expected {recipients}, got {actual}"


def test_prop_is_op_bound():
    assert ("SG", "PROP") in OP_BOUND_TYPES
    assert OP_BOUND_TYPES[("SG", "PROP")]["exchange_mode"] is True


def test_gov_is_admin_bound():
    # Spec v5 §4.2 — GOV (governance) goes to the Admin OP backlog, with a
    # SYS↔Admin OP exchange mode. It is NOT OP-bound anymore.
    assert ("SYS", "GOV") in ADMIN_BOUND_TYPES
    assert ("SYS", "GOV") not in OP_BOUND_TYPES
    assert ADMIN_BOUND_TYPES[("SYS", "GOV")]["notify_role"] == "ADMIN_OP"
    assert ADMIN_BOUND_TYPES[("SYS", "GOV")]["exchange_partner"] == "SYS"


def test_external_targets_recognized():
    assert "EXT" in EXTERNAL_TARGETS
    assert "DE" in EXTERNAL_TARGETS
    assert "ARCHIVE" in EXTERNAL_TARGETS
    # "OP" is intentionally NOT in EXTERNAL_TARGETS. OP routing happens before
    # the ROUTING_TABLE loop via OP_BOUND_TYPES (SG/PROP, SYS/GOV). A "to=='OP'"
    # branch inside the loop would be unreachable dead code; listing OP here
    # would falsely suggest the loop handles it.
    assert "OP" not in EXTERNAL_TARGETS
