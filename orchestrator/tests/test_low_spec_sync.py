"""
LOW tier — June 2026 spec-sync items #11/#12/#13/#14 + NEW-3.

  #11    UNIVERSAL Authority Boundary (invariant validator + /sys classifier)
  #12    addendum loads for all non-minimal tiers
  #13    SC-5 scope document classification priority + per-agent tiers
  #14    SC-6 applied_via:import provenance (field, marker, SYS audit, import CLI)
  NEW-3  WikiManager.read_log shared by Telegram /log and MCP vega_log
"""

from __future__ import annotations

import types
from pathlib import Path

import pytest

from models import Artifact, WikiUpdate


def _bare_executor(**attrs):
    """A hand-built AgentExecutor for unit-testing pure helpers (mirrors the
    object.__new__ pattern already used in test_phase1_audit_fixes)."""
    from executor import AgentExecutor
    ex = object.__new__(AgentExecutor)
    for k, v in attrs.items():
        setattr(ex, k, v)
    return ex


# ─── #11 UNIVERSAL Authority Boundary ────────────────────────────────────────

def _wu(content, target="universal", file="UNIVERSAL.md"):
    return WikiUpdate(file=file, action="replace", content=content, target=target)


def test_invariants_clean_update_passes():
    ex = _bare_executor()
    clean = [_wu("Clarified the SCN naming convention for SE.")]
    assert ex._validate_universal_invariants(clean) == []


def test_invariants_flag_each_violation():
    ex = _bare_executor()
    cases = {
        "agent core responsibilities": "Remove the responsibility of SG to gate scope.",
        "V-model gates": "Agents may bypass the validation gate for hotfixes.",
        "role separation": "We will merge the SG and SE roles into one.",
        "scope protection": "SE may edit scope directly without PROP or AUTH.",
    }
    for fragment, text in cases.items():
        conflicts = ex._validate_universal_invariants([_wu(text)])
        assert conflicts, f"expected a conflict for: {fragment}"


def test_invariants_ignore_non_universal_targets():
    ex = _bare_executor()
    # An agent's OWN wiki update saying "bypass the gate" is not an Authority
    # Boundary write — only universal-targeted updates are validated.
    own = [_wu("bypass the validation gate", target="own")]
    assert ex._validate_universal_invariants(own) == []


def test_classify_sys_request():
    from executor import AgentExecutor
    assert AgentExecutor._classify_sys_request(
        "Please add a rule to scope: codes should support LCN parsing") == "scope"
    assert AgentExecutor._classify_sys_request(
        "Audit the wikis for drift and check invariant consistency") == "governance"
    assert AgentExecutor._classify_sys_request("") == "governance"


def test_execute_sys_wires_authority_boundary():
    src = (Path(__file__).resolve().parent.parent / "executor.py").read_text()
    assert "_validate_universal_invariants(wiki_updates)" in src
    # On conflict: route a GOV and drop the universal updates before apply.
    assert 'type="GOV"' in src and "AUTHORITY_BOUNDARY" in src
    assert 'getattr(u, "target", "own") != "universal"' in src


# ─── #12 addendum for all non-minimal tiers ──────────────────────────────────

FW = """\
# Framework

## 1 Document Types
sec1

## 2 Interaction Catalog
sec2

## 5 Roles
sec5

## 9 Interaction Flows
sec9

## 10 Decision Log
sec10

## 12.1 Framework Summary
summary body
"""


def test_sys_view_includes_addendum():
    from framework_parser import load_framework_view
    out = load_framework_view(FW, "SYS", addendum_text="ADDENDUM-MARKER")
    assert "ADDENDUM-MARKER" in out


def test_summary_view_includes_addendum():
    from framework_parser import load_framework_view
    out = load_framework_view(FW, "SA", addendum_text="ADDENDUM-MARKER")
    assert "ADDENDUM-MARKER" in out


def test_minimal_view_excludes_addendum():
    from framework_parser import load_framework_view
    for code in ("SE", "TE"):
        out = load_framework_view(FW, code, addendum_text="ADDENDUM-MARKER")
        assert "ADDENDUM-MARKER" not in out
        assert out == ""


# ─── #13 SC-5 scope classification ───────────────────────────────────────────

ADDENDUM_WITH_CLASSIFICATION = """\
# VEGA Test Project Addendum

## Scope Document Classification
- spec: Spec_Core_v1.md
- spec: Resolution_Rules_v2.md
- management: Build_Plan_v1.md
- management: Roadmap_v1.md

## Other section
ignored
"""


def _exec_with_scope(tmp_path, classification_block=True, model="claude-opus-4-7"):
    framework = tmp_path / "framework"; framework.mkdir(parents=True, exist_ok=True)
    if classification_block:
        (framework / "VEGA_Test_Project_Addendum.md").write_text(
            ADDENDUM_WITH_CLASSIFICATION)
    scope = tmp_path / "scope"; scope.mkdir(parents=True, exist_ok=True)
    cfg = types.SimpleNamespace(BASE_DIR=tmp_path, SCOPE_BUDGET_FRACTION=0.5)
    models = types.SimpleNamespace(get=lambda code: model)
    ex = _bare_executor(config=cfg, scope_dir=scope, models=models)
    return ex, scope


def test_load_scope_classification_parse(tmp_path):
    ex, _ = _exec_with_scope(tmp_path)
    cls = ex._load_scope_classification()
    assert cls["Spec_Core_v1.md"] == "spec"
    assert cls["Build_Plan_v1.md"] == "management"


def test_load_scope_classification_empty_without_block(tmp_path):
    ex, _ = _exec_with_scope(tmp_path, classification_block=False)
    assert ex._load_scope_classification() == {}


def test_spec_tier_excludes_management(tmp_path):
    ex, scope = _exec_with_scope(tmp_path)
    (scope / "Spec_Core_v1.md").write_text("# spec\nspec body")
    (scope / "Build_Plan_v1.md").write_text("# mgmt\nmanagement body")
    out = ex._load_scope("SA")          # SA ∈ SPEC_TIER
    assert "spec body" in out
    assert "management body" not in out


def test_full_corpus_includes_management(tmp_path):
    ex, scope = _exec_with_scope(tmp_path)
    (scope / "Spec_Core_v1.md").write_text("# spec\nspec body")
    (scope / "Build_Plan_v1.md").write_text("# mgmt\nmanagement body")
    out = ex._load_scope("SG")          # SG ∈ FULL_CORPUS
    assert "spec body" in out and "management body" in out


def test_task_scoped_loads_only_targeted(tmp_path):
    ex, scope = _exec_with_scope(tmp_path)
    (scope / "Spec_Core_v1.md").write_text("# spec\nspec body")
    (scope / "Resolution_Rules_v2.md").write_text("# rules\nrules body")
    item = Artifact(type="SCN", sender="SG", content="apply change to Spec_Core_v1",
                    id="SCN-SG-001")
    out = ex._load_scope("SE", items=[item])   # SE ∈ TASK_SCOPED
    assert "spec body" in out                   # targeted doc loaded
    assert "rules body" not in out              # untargeted doc NOT loaded
    assert "SCOPE MANIFEST" in out              # manifest always present


# ─── #14 SC-6 applied_via provenance ─────────────────────────────────────────

def test_applied_via_roundtrip():
    a = Artifact(type="INIT", sender="OP", content="bootstrap", id="INIT-OP-001",
                 applied_via="import")
    md = a.to_markdown()
    assert "applied_via: import" in md
    back = Artifact.from_markdown(md)
    assert back.applied_via == "import"


def test_applied_via_absent_by_default():
    a = Artifact(type="DOC", sender="SG", content="x", id="DOC-SG-001")
    assert "applied_via" not in a.to_markdown()
    assert Artifact.from_markdown(a.to_markdown()).applied_via is None


def test_provenance_violations(tmp_path):
    from state_manager import atomic_save_json
    sd = tmp_path / "state"; sd.mkdir()
    atomic_save_json(sd / "routing_log.json", [{"artifact_id": "SCN-SG-001"}])
    ex = _bare_executor(state_dir=sd)
    arts = [
        Artifact(type="SCN", sender="SG", content="x", id="SCN-SG-001"),   # routed
        Artifact(type="INIT", sender="OP", content="x", id="INIT-OP-001",
                 applied_via="import"),                                      # import
        Artifact(type="DOC", sender="SE", content="x", id="DOC-SE-009"),   # ORPHAN
    ]
    violations = ex._provenance_violations(arts)
    assert len(violations) == 1
    assert "DOC-SE-009" in violations[0]


def test_init_marked_import():
    src = (Path(__file__).resolve().parent.parent / "main.py").read_text()
    # INIT-OP-001 carries the import marker; import CLI exists.
    assert 'applied_via="import"' in src
    assert "async def import_artifact" in src
    assert "--import-artifact" in src


def test_execute_sys_wires_provenance_audit():
    src = (Path(__file__).resolve().parent.parent / "executor.py").read_text()
    assert "_provenance_violations(recent_artifacts)" in src
    assert "PROVENANCE VIOLATIONS" in src


# ─── NEW-2 CycleManager.check_context_usage ──────────────────────────────────

def test_check_context_usage_under_threshold(tmp_path):
    from cycle_manager import CycleManager
    cm = CycleManager(tmp_path / "cycles", tmp_path / "archive")
    cycle = cm.open_cycle("prop_exchange",
                          Artifact(type="PROP", sender="SG", content="x", id="PROP-SG-001"),
                          ["SG", "OP"], primary_agent="SG")
    cm.append_turn(cycle, "short", "OP")
    status = cm.check_context_usage(cycle, model_limit=1_000_000, warning_fraction=0.4)
    assert status["over"] is False and status["compressed"] == 0


def test_check_context_usage_compresses_over_threshold(tmp_path):
    from cycle_manager import CycleManager
    cm = CycleManager(tmp_path / "cycles", tmp_path / "archive")
    cycle = cm.open_cycle("prop_exchange",
                          Artifact(type="PROP", sender="SG", content="x", id="PROP-SG-001"),
                          ["SG", "OP"], primary_agent="SG")
    for i in range(20):
        cm.append_turn(cycle, "word " * 500, "OP")   # bloat the context
    # tiny window so estimate easily exceeds 40%
    status = cm.check_context_usage(cycle, model_limit=2000, warning_fraction=0.4)
    assert status["over"] is True
    assert status["usage_pct"] > 0.4


def test_check_context_usage_no_limit(tmp_path):
    from cycle_manager import CycleManager
    cm = CycleManager(tmp_path / "cycles", tmp_path / "archive")
    cycle = cm.open_cycle("prop_exchange",
                          Artifact(type="PROP", sender="SG", content="x", id="PROP-SG-001"),
                          ["SG", "OP"], primary_agent="SG")
    cm.append_turn(cycle, "anything", "OP")
    status = cm.check_context_usage(cycle, model_limit=None, warning_fraction=0.4)
    assert status["over"] is False


def test_executor_uses_check_context_usage():
    src = (Path(__file__).resolve().parent.parent / "executor.py").read_text()
    assert "self.cycles.check_context_usage(" in src
    # the old inline estimate/compress dance is gone from the executor
    assert "self.cycles.compress_early_turns(cycle)" not in src


# ─── NEW-3 WikiManager.read_log shared ───────────────────────────────────────

def test_wiki_read_log(tmp_path):
    from wiki_manager import WikiManager
    agents = tmp_path / "agents"
    (agents / "SG" / "wiki").mkdir(parents=True)
    log = agents / "SG" / "wiki" / "log.md"
    log.write_text(
        "## [2026-06-01 10:00] SG-S001 (m) | first\n"
        "## [2026-06-02 11:00] SG-S001 (m) | second\n"
        "## [2026-06-03 12:00] SG-S001 (m) | third\n")
    wm = WikiManager(agents_dir=agents, universal_dir=tmp_path / "universal",
                     state_dir=tmp_path / "state",
                     instance_lookup=lambda c: "SG-S001", model_lookup=lambda c: "m")
    out = wm.read_log("SG", 2)
    assert "second" in out and "third" in out and "first" not in out
    assert wm.read_log("NOPE", 5) == ""


def test_read_log_shared_by_both_interfaces():
    mcp = (Path(__file__).resolve().parent.parent / "mcp_server.py").read_text()
    tg = (Path(__file__).resolve().parent.parent / "telegram_bot.py").read_text()
    assert "self.wiki.read_log(code, n)" in mcp
    assert "self.wiki.read_log(code, n)" in tg
