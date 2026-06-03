"""
Cycle open/close events.
"""

from cycle_manager import CycleManager
from models import Artifact


def test_scn_opens_scn_application_cycle(tmp_path):
    cm = CycleManager(tmp_path / "active", tmp_path / "archive")
    scn = Artifact(type="SCN", sender="SG", id="SCN-SG-001", content="...")
    cm.check_cycle_events(scn, recipients=["SE"])
    assert cm.get_cycle_by_participants("SG", "SE") is not None


def test_val_from_sg_closes_scn_application_cycle(tmp_path):
    cm = CycleManager(tmp_path / "active", tmp_path / "archive")
    scn = Artifact(type="SCN", sender="SG", id="SCN-SG-001", content="...")
    cm.check_cycle_events(scn, recipients=["SE"])

    val = Artifact(type="VAL", sender="SG", id="VAL-SG-001",
                   references=["SCN-SG-001"], content="ok")
    cm.check_cycle_events(val, recipients=["SE"])
    assert cm.get_cycle_by_participants("SG", "SE") is None


def test_tcn_two_certificate_lifecycle(tmp_path):
    cm = CycleManager(tmp_path / "active", tmp_path / "archive")
    tcn = Artifact(type="TCN", sender="TG", id="TCN-TG-001", content="...")
    cm.check_cycle_events(tcn, recipients=["TE"])

    val_first = Artifact(type="VAL", sender="TG", id="VAL-TG-001",
                         certificate="first", references=["TCN-TG-001"], content="ok")
    cm.check_cycle_events(val_first, recipients=["TE"])
    # First certificate does NOT close the cycle
    assert cm.get_cycle_by_participants("TG", "TE") is not None

    val_second = Artifact(type="VAL", sender="TG", id="VAL-TG-002",
                          certificate="second", references=["TCN-TG-001"], content="ok")
    cm.check_cycle_events(val_second, recipients=["TE"])
    assert cm.get_cycle_by_participants("TG", "TE") is None


def test_tcn_build_certificate_closes_cycle(tmp_path):
    """Canonical Framework v5/v6 name is `build` (Spec §6.1); `second` is the
    legacy alias covered above. This locks in the canonical name directly."""
    cm = CycleManager(tmp_path / "active", tmp_path / "archive")
    tcn = Artifact(type="TCN", sender="TG", id="TCN-TG-010", content="...")
    cm.check_cycle_events(tcn, recipients=["TE"])
    val_full = Artifact(type="VAL", sender="TG", id="VAL-TG-010",
                        certificate="full", references=["TCN-TG-010"], content="ok")
    cm.check_cycle_events(val_full, recipients=["TE"])
    assert cm.get_cycle_by_participants("TG", "TE") is not None   # full doesn't close
    val_build = Artifact(type="VAL", sender="TG", id="VAL-TG-011",
                         certificate="build", references=["TCN-TG-010"], content="ok")
    cm.check_cycle_events(val_build, recipients=["TE"])
    assert cm.get_cycle_by_participants("TG", "TE") is None       # build closes


def test_prop_opens_and_auth_closes_prop_exchange(tmp_path):
    cm = CycleManager(tmp_path / "active", tmp_path / "archive")
    prop = Artifact(type="PROP", sender="SG", id="PROP-SG-001", priority="P1", content="...")
    cm.check_cycle_events(prop, recipients=["OP"])
    assert cm.get_cycle_by_participants("SG", "OP") is not None

    auth = Artifact(type="AUTH", sender="OP", id="AUTH-OP-001",
                    references=["PROP-SG-001"], disposition="approve", content="ok")
    cm.check_cycle_events(auth, recipients=["SG"])
    assert cm.get_cycle_by_participants("SG", "OP") is None


def test_tfr_opens_triage_and_tri_closes_it(tmp_path):
    """Spec §6.1 — triage opens on TFR-BTA-NNN, closes on TG producing TRI/ESC/TCN."""
    cm = CycleManager(tmp_path / "active", tmp_path / "archive")
    tfr = Artifact(type="TFR", sender="BTA", id="TFR-BTA-001", content="failures")
    cm.check_cycle_events(tfr, recipients=["TG"])
    assert cm.get_active_by_type("triage") is not None

    tri = Artifact(type="TRI", sender="TG", id="TRI-TG-001",
                   references=["TFR-BTA-001"], content="build defect")
    cm.check_cycle_events(tri, recipients=["BR"])
    assert cm.get_active_by_type("triage") is None
    # TRI also opens build_remediation
    assert cm.get_active_by_type("build_remediation") is not None


def test_tfr_opens_triage_and_esc_closes_it(tmp_path):
    """ESC from TG also closes triage."""
    cm = CycleManager(tmp_path / "active", tmp_path / "archive")
    tfr = Artifact(type="TFR", sender="BTA", id="TFR-BTA-002", content="failures")
    cm.check_cycle_events(tfr, recipients=["TG"])
    esc = Artifact(type="ESC", sender="TG", id="ESC-TG-001",
                   references=["TFR-BTA-002"], content="scope defect")
    cm.check_cycle_events(esc, recipients=["SG"])
    assert cm.get_active_by_type("triage") is None


def test_tfr_opens_triage_and_tcn_closes_it(tmp_path):
    """TCN from TG (test-model defect) also closes triage."""
    cm = CycleManager(tmp_path / "active", tmp_path / "archive")
    tfr = Artifact(type="TFR", sender="BTA", id="TFR-BTA-003", content="failures")
    cm.check_cycle_events(tfr, recipients=["TG"])
    tcn = Artifact(type="TCN", sender="TG", id="TCN-TG-001", content="test fix")
    cm.check_cycle_events(tcn, recipients=["TE"])
    assert cm.get_active_by_type("triage") is None


def test_brq_from_ext_opens_build_results_and_vr_closes_it(tmp_path):
    """Spec §6.1 — build_results opens on BRQ from EXT, closes on VR from BTA."""
    cm = CycleManager(tmp_path / "active", tmp_path / "archive")
    brq = Artifact(type="BRQ", sender="EXT", id="BRQ-EXT-001",
                   content="results")
    cm.check_cycle_events(brq, recipients=["BR"])
    assert cm.get_active_by_type("build_results") is not None

    vr = Artifact(type="VR", sender="BTA", id="VR-BTA-001",
                  references=["TSR-BR-001"], content="pass/fail")
    cm.check_cycle_events(vr, recipients=["BR"])
    assert cm.get_active_by_type("build_results") is None


def test_pro_scope_to_br_opens_build_scope_qa_supersedes(tmp_path):
    cm = CycleManager(tmp_path / "active", tmp_path / "archive")
    p1 = Artifact(type="PRO-SCOPE", sender="SG", id="PRO-SCOPE-001", content="v1")
    cm.check_cycle_events(p1, recipients=["SA", "BR", "TG", "TA"])
    first = cm.get_active_by_type("build_scope_qa")
    assert first is not None

    p2 = Artifact(type="PRO-SCOPE", sender="SG", id="PRO-SCOPE-002", content="v2")
    cm.check_cycle_events(p2, recipients=["SA", "BR", "TG", "TA"])
    second = cm.get_active_by_type("build_scope_qa")
    assert second is not None
    assert second.opening_artifact != first.opening_artifact
