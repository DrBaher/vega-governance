"""
Tier 2 (spec sc4 §4.2/§4.3) — scope/test file commit on propagation.

SE/TE emit modified files inside a DOC (`### FILE:` markers); the Guardian
validates and emits PRO-SCOPE/PRO-TEST; the router's commit_propagation_files
writes the files to scope/ or test_models/ (the ONLY scope-write path), using
write-ahead staging, BEFORE delivering the propagation signal downstream.
"""

from __future__ import annotations

import types
from pathlib import Path

import pytest

from artifact_store import ArtifactStore, parse_edit_ops, parse_file_sections
from models import Artifact
from router import Router
from sequence_manager import SequenceManager


# ─── parse_file_sections ─────────────────────────────────────────────────────

def test_parse_file_sections_basic():
    content = (
        "preamble ignored\n"
        "### FILE: alpha.md\n# Alpha\nbody A\n\n"
        "### FILE: beta.md\n# Beta\nbody B\n\n"
        "### APPLICATION_NOTES\nall 2 applied, checklist PASS\n"
    )
    files = parse_file_sections(content)
    assert set(files) == {"alpha.md", "beta.md"}
    assert "body A" in files["alpha.md"] and "# Alpha" in files["alpha.md"]
    assert "body B" in files["beta.md"]
    # APPLICATION_NOTES is NOT a file
    assert "APPLICATION_NOTES" not in files
    assert "checklist PASS" not in files["beta.md"]


def test_parse_file_sections_bracketed_names_and_empty():
    files = parse_file_sections("### FILE: [doc_1.md]\ncontent\n")
    assert "doc_1.md" in files
    assert parse_file_sections("just prose, no markers") == {}


# ─── parse_edit_ops (surgical patch mode, §4.3 / B) ──────────────────────────

EDIT_DOC = (
    "### EDIT: Big_Doc.md\n"
    "<<<<<<< FIND\n**Version:** 1.0 | March 2026\n=======\n"
    "**Version:** 1.1 | June 2026\n>>>>>>> REPLACE\n\n"
    "### EDIT: Other.md\n"
    "<<<<<<< FIND\n| SCN-SG-001 | 41 items |\n=======\n"
    "| SCN-SG-001 | 43 items |\n>>>>>>> REPLACE\n\n"
    "### APPLICATION_NOTES\nboth edits applied\n"
)


def test_parse_edit_ops_basic():
    ops = parse_edit_ops(EDIT_DOC)
    assert len(ops) == 2
    assert ops[0] == ("Big_Doc.md", "**Version:** 1.0 | March 2026",
                      "**Version:** 1.1 | June 2026")
    assert ops[1][0] == "Other.md"
    # APPLICATION_NOTES excluded
    assert all("applied" not in r for _, _, r in ops)


def test_parse_edit_ops_none():
    assert parse_edit_ops("### FILE: x.md\nfull content\n") == []


# ─── commit_propagation_files ────────────────────────────────────────────────

def _cfg(tmp_path):
    return types.SimpleNamespace(
        SCOPE_DIR=str(tmp_path / "scope"),
        TEST_MODELS_FULL_DIR=str(tmp_path / "test_models" / "full"),
        TEST_MODELS_BUILD_DIR=str(tmp_path / "test_models" / "build"),
    )


def _router_with_doc(tmp_path, doc_id="DOC-SE-001", doc_content="", agents=()):
    agents_dir = tmp_path / "agents"
    archive_dir = tmp_path / "artifacts" / "archive"
    state_dir = tmp_path / "state"
    for d in (agents_dir, archive_dir, state_dir):
        d.mkdir(parents=True, exist_ok=True)
    for code in agents:
        (agents_dir / code / "inbox").mkdir(parents=True, exist_ok=True)
        (agents_dir / code / "outbox").mkdir(parents=True, exist_ok=True)
    store = ArtifactStore(agents_dir, archive_dir)
    if doc_content:
        store.archive_artifact(Artifact(type="DOC", sender="SE", id=doc_id,
                                        content=doc_content))
    router = Router(store=store, state_dir=state_dir, config=_cfg(tmp_path))
    return router, store


@pytest.mark.asyncio
async def test_commit_pro_scope_writes_files_atomically(tmp_path):
    doc = ("### FILE: Scope_v7.2.md\n# Scope\nnew validated content\n\n"
           "### APPLICATION_NOTES\nitem 01 applied\n")
    router, _ = _router_with_doc(tmp_path, "DOC-SE-001", doc)
    pro = Artifact(type="PRO-SCOPE", sender="SG", id="PRO-SCOPE-001",
                   references=["DOC-SE-001"])
    await router.commit_propagation_files(pro)
    scope = Path(tmp_path / "scope")
    assert (scope / "Scope_v7.2.md").read_text().rstrip() == "# Scope\nnew validated content"
    # write-ahead staging cleaned up
    assert not list(scope.glob("*.incoming"))


@pytest.mark.asyncio
async def test_commit_edit_surgical_preserves_rest_of_big_file(tmp_path):
    # A large existing scope doc; SE changes ONE line via ### EDIT: (not re-emitted)
    big = "# DB Spec\n" + ("filler line\n" * 5000) + "**Version:** 1.0 | March 2026\n" + ("more\n" * 5000)
    scope = Path(tmp_path / "scope"); scope.mkdir(parents=True)
    (scope / "Big.md").write_text(big)
    doc = ("### EDIT: Big.md\n<<<<<<< FIND\n**Version:** 1.0 | March 2026\n=======\n"
           "**Version:** 1.1 | June 2026\n>>>>>>> REPLACE\n\n### APPLICATION_NOTES\nok\n")
    router, _ = _router_with_doc(tmp_path, "DOC-SE-010", doc)
    await router.commit_propagation_files(
        Artifact(type="PRO-SCOPE", sender="SG", id="PRO-SCOPE-010", references=["DOC-SE-010"]))
    result = (scope / "Big.md").read_text()
    assert "**Version:** 1.1 | June 2026" in result
    assert "**Version:** 1.0 | March 2026" not in result
    assert result.count("filler line") == 5000 and result.count("more") == 5000  # rest intact
    assert not list(scope.glob("*.incoming"))


@pytest.mark.asyncio
async def test_commit_edit_anchor_not_found_rejects_no_write(tmp_path):
    scope = Path(tmp_path / "scope"); scope.mkdir(parents=True)
    (scope / "Doc.md").write_text("original content unchanged\n")
    doc = ("### EDIT: Doc.md\n<<<<<<< FIND\nTEXT THAT DOES NOT EXIST\n=======\n"
           "replacement\n>>>>>>> REPLACE\n")
    router, _ = _router_with_doc(tmp_path, "DOC-SE-011", doc)
    await router.commit_propagation_files(
        Artifact(type="PRO-SCOPE", sender="SG", id="PRO-SCOPE-011", references=["DOC-SE-011"]))
    assert (scope / "Doc.md").read_text() == "original content unchanged\n"  # untouched
    assert not list(scope.glob("*.incoming"))


@pytest.mark.asyncio
async def test_commit_edit_ambiguous_anchor_rejects(tmp_path):
    scope = Path(tmp_path / "scope"); scope.mkdir(parents=True)
    (scope / "Doc.md").write_text("dup\ndup\nkeep\n")   # 'dup' appears twice
    doc = ("### EDIT: Doc.md\n<<<<<<< FIND\ndup\n=======\nchanged\n>>>>>>> REPLACE\n")
    router, _ = _router_with_doc(tmp_path, "DOC-SE-012", doc)
    await router.commit_propagation_files(
        Artifact(type="PRO-SCOPE", sender="SG", id="PRO-SCOPE-012", references=["DOC-SE-012"]))
    assert (scope / "Doc.md").read_text() == "dup\ndup\nkeep\n"  # untouched (ambiguous)


@pytest.mark.asyncio
async def test_commit_pro_test_full_and_build_targets(tmp_path):
    router, store = _router_with_doc(tmp_path)
    store.archive_artifact(Artifact(type="DOC", sender="TE", id="DOC-TE-001",
                                    content="### FILE: tm_full.md\nfull model\n"))
    store.archive_artifact(Artifact(type="DOC", sender="TE", id="DOC-TE-002",
                                    content="### FILE: tm_build.md\nbuild model\n"))
    await router.commit_propagation_files(
        Artifact(type="PRO-TEST-FULL", sender="TG", id="PRO-TEST-FULL-001",
                 references=["DOC-TE-001"]))
    await router.commit_propagation_files(
        Artifact(type="PRO-TEST-BUILD", sender="TG", id="PRO-TEST-BUILD-001",
                 references=["DOC-TE-002"]))
    assert (tmp_path / "test_models" / "full" / "tm_full.md").exists()
    assert (tmp_path / "test_models" / "build" / "tm_build.md").exists()


@pytest.mark.asyncio
async def test_commit_noop_for_non_propagation(tmp_path):
    router, _ = _router_with_doc(tmp_path)
    # An ordinary artifact must not touch scope/
    await router.commit_propagation_files(
        Artifact(type="AUTH", sender="OP", id="AUTH-OP-001", references=["PROP-SG-001"]))
    assert not (tmp_path / "scope").exists() or not list((tmp_path / "scope").glob("*.md"))


@pytest.mark.asyncio
async def test_commit_rejects_path_traversal(tmp_path):
    doc = "### FILE: ../escape.md\nmalicious\n\n### FILE: ok.md\nfine\n"
    router, _ = _router_with_doc(tmp_path, "DOC-SE-009", doc)
    await router.commit_propagation_files(
        Artifact(type="PRO-SCOPE", sender="SG", id="PRO-SCOPE-009",
                 references=["DOC-SE-009"]))
    scope = Path(tmp_path / "scope")
    assert (scope / "ok.md").exists()                    # safe file committed
    assert not (tmp_path / "escape.md").exists()         # traversal blocked
    assert not (scope.parent / "escape.md").exists()


@pytest.mark.asyncio
async def test_commit_finds_doc_not_at_references_0(tmp_path):
    # PRO-SCOPE often lists VAL/SCN before the DOC (and agents may emit a
    # placeholder like VAL-SG-NNN as references[0]). Commit must find the DOC.
    doc = "### FILE: Scope_v7.2.md\nvalidated body\n\n### APPLICATION_NOTES\nok\n"
    router, _ = _router_with_doc(tmp_path, "DOC-SE-002", doc)
    pro = Artifact(type="PRO-SCOPE", sender="SG", id="PRO-SCOPE-002",
                   references=["VAL-SG-NNN", "SCN-SG-005", "DOC-SE-002"])  # DOC is last
    await router.commit_propagation_files(pro)
    assert (tmp_path / "scope" / "Scope_v7.2.md").read_text().strip() == "validated body"


@pytest.mark.asyncio
async def test_commit_missing_doc_is_safe(tmp_path):
    router, _ = _router_with_doc(tmp_path)   # no DOC archived
    await router.commit_propagation_files(
        Artifact(type="PRO-SCOPE", sender="SG", id="PRO-SCOPE-404",
                 references=["DOC-SE-404"]))   # must not raise
    assert not list((tmp_path / "scope").glob("*.incoming"))


# ─── route() wiring — commit BEFORE downstream delivery ──────────────────────

@pytest.mark.asyncio
async def test_route_commits_then_delivers_downstream(tmp_path):
    doc = "### FILE: Scope_v7.2.md\nvalidated v7.2\n\n### APPLICATION_NOTES\nok\n"
    router, store = _router_with_doc(tmp_path, "DOC-SE-003", doc,
                                     agents=("SA", "BR", "TG", "TA"))
    pro = Artifact(type="PRO-SCOPE", sender="SG", id="PRO-SCOPE-003",
                   references=["DOC-SE-003"])
    await router.route(pro)
    # scope file written
    assert (tmp_path / "scope" / "Scope_v7.2.md").read_text().strip() == "validated v7.2"
    # downstream agents received the PRO-SCOPE signal
    for code in ("SA", "BR", "TG", "TA"):
        assert (tmp_path / "agents" / code / "inbox" / "PRO-SCOPE-003.md").exists()
    # PRO-SCOPE itself archived
    assert (tmp_path / "artifacts" / "archive" / "PRO-SCOPE-003.md").exists()


# ─── load_from_archive standardized name ─────────────────────────────────────

def test_load_from_archive(tmp_path):
    store = ArtifactStore(tmp_path / "agents", tmp_path / "archive")
    store.archive_artifact(Artifact(type="DOC", sender="SE", id="DOC-SE-001",
                                    content="hello"))
    got = store.load_from_archive("DOC-SE-001")
    assert got is not None and got.id == "DOC-SE-001"
    assert store.load_from_archive("nope") is None


# ─── SE/TE system prompt carries the DOC ### FILE: format ────────────────────

FW_FIXTURE = """\
# Framework
## 5.3 Scope Editor
SE role body.
## 5.6 Tests Editor
TE role body.
## 5.1 Scope Guardian
SG role body.
"""


def test_editor_doc_format_in_se_te_prompts():
    from framework_parser import compose_system_prompt, AGENT_TO_SECTION
    # only run for agents whose section the fixture provides
    se = compose_system_prompt(FW_FIXTURE, "SE")
    assert "### EDIT:" in se and "### FILE:" in se and "APPLICATION_NOTES" in se
    assert "FIND" in se and "REPLACE" in se   # surgical format documented
    sg = compose_system_prompt(FW_FIXTURE, "SG")
    assert "### EDIT:" not in sg and "### FILE:" not in sg  # guardians don't apply files


def test_clean_propagation_staging_wired():
    src = (Path(__file__).resolve().parent.parent / "main.py").read_text()
    assert "_clean_propagation_staging" in src
    assert "*.incoming" in src
