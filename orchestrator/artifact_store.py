"""
Artifact storage: inbox, outbox, archive.

Per Spec §7. The archive is IMMUTABLE — once an artifact is written, it is never
modified. Routing operates on outbox→archive→inbox copies, not on shared references.
"""

from __future__ import annotations

import os
import re
import shutil
import time
from pathlib import Path

from models import Artifact, PRIORITY_ORDER
from state_manager import LOCKS, atomic_write


_FILE_MARKER_RE = re.compile(r"(?m)^###[ \t]+FILE:[ \t]*(.+?)[ \t]*$")
_APP_NOTES_RE = re.compile(r"(?m)^###[ \t]+APPLICATION_NOTES[ \t]*$")


def parse_file_sections(content: str) -> dict[str, str]:
    """Parse a DOC body's `### FILE: <name>` sections into {filename: file_content}
    (Spec sc4 §4.3). Each file's content runs until the next `### FILE:` marker or
    the `### APPLICATION_NOTES` marker (SE/TE's report — not a file, excluded) or
    end of body. Filenames may be bracketed (`### FILE: [doc.md]`). Returns {} if
    there are no FILE markers."""
    notes = _APP_NOTES_RE.search(content)
    limit = notes.start() if notes else len(content)
    matches = [m for m in _FILE_MARKER_RE.finditer(content) if m.start() < limit]
    out: dict[str, str] = {}
    for i, m in enumerate(matches):
        name = m.group(1).strip().strip("[]").strip()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else limit
        body = content[start:end].strip("\n")
        if name:
            out[name] = body
    return out


def _read_text(path: str | Path) -> str:
    with open(path) as f:
        return f.read()


def _quarantine_corrupt(path: Path, err: Exception) -> None:
    """Move a corrupt artifact file out of the active set so it doesn't
    poison repeated scans (defensive enhancement, not spec-mandated). The corrupt copy is preserved with
    a `.corrupt-<unix-ms>` suffix for forensic review."""
    if not path.exists():
        return
    quarantine = path.with_name(f"{path.name}.corrupt-{int(time.time() * 1000)}")
    try:
        os.replace(path, quarantine)
        print(f"[artifact_store] quarantined corrupt {path} → {quarantine.name} "
              f"({type(err).__name__}: {err})", flush=True)
    except OSError as move_err:
        print(f"[artifact_store] could not quarantine {path}: {move_err}",
              flush=True)


class Inbox:
    """Per-agent inbox. Files have status: unprocessed | processed."""

    def __init__(self, agent_dir: str | Path) -> None:
        self.dir = Path(agent_dir) / "inbox"
        self.dir.mkdir(parents=True, exist_ok=True)

    def _list_files(self) -> list[Path]:
        return [p for p in sorted(self.dir.glob("*.md"))]

    def _load(self, path: Path) -> Artifact:
        return Artifact.from_markdown(_read_text(path), filename=path.name)

    def has_unprocessed(self) -> bool:
        for path in self._list_files():
            try:
                if self._load(path).status == "unprocessed":
                    return True
            except Exception as e:
                # defensive enhancement (not spec-mandated) — quarantine corrupt inbox files; they'd otherwise
                # be re-scanned every tick and swallowed silently by the bare
                # `continue`.
                _quarantine_corrupt(path, e)
                continue
        return False

    def peek(self) -> Artifact | None:
        """First unprocessed artifact (no sort), used for cycle lookup."""
        items = self.get_unprocessed()
        return items[0] if items else None

    def get_unprocessed(self) -> list[Artifact]:
        """All unprocessed artifacts, sorted by (priority, timestamp)."""
        artifacts: list[Artifact] = []
        for path in self._list_files():
            try:
                a = self._load(path)
            except Exception as e:
                _quarantine_corrupt(path, e)   # defensive enhancement (not spec-mandated)
                continue
            if a.status == "unprocessed":
                artifacts.append(a)
        return sorted(
            artifacts,
            key=lambda a: (PRIORITY_ORDER.get(a.priority or "P2", 2), a.timestamp),
        )

    def mark_processed(self, artifacts: list[Artifact]) -> None:
        """Rewrite each artifact with status=processed."""
        for a in artifacts:
            if not a.filename:
                continue
            a.status = "processed"
            atomic_write(self.dir / a.filename, a.to_markdown())

    def deliver(self, artifact: Artifact) -> Path:
        """Drop an artifact into the inbox. Filename is the artifact id."""
        artifact.status = "unprocessed"
        filename = f"{artifact.id}.md"
        path = self.dir / filename
        atomic_write(path, artifact.to_markdown())
        artifact.filename = filename
        return path


class Outbox:
    """Per-agent outbox. Router drains it each loop iteration."""

    def __init__(self, agent_dir: str | Path) -> None:
        self.dir = Path(agent_dir) / "outbox"
        self.dir.mkdir(parents=True, exist_ok=True)

    def place(self, artifact: Artifact) -> Path:
        if not artifact.id:
            raise ValueError("Cannot place artifact without id in outbox")
        filename = f"{artifact.id}.md"
        path = self.dir / filename
        atomic_write(path, artifact.to_markdown())
        artifact.filename = filename
        return path

    def list_pending(self) -> list[Artifact]:
        out: list[Artifact] = []
        for path in sorted(self.dir.glob("*.md")):
            try:
                out.append(Artifact.from_markdown(_read_text(path), filename=path.name))
            except Exception as e:
                _quarantine_corrupt(path, e)   # defensive enhancement (not spec-mandated)
                continue
        return out

    def clear(self, artifact: Artifact) -> None:
        if not artifact.filename:
            return
        p = self.dir / artifact.filename
        if p.exists():
            p.unlink()


class Archive:
    """Immutable artifact archive. Never modified after write (Spec §7.4)."""

    def __init__(self, archive_dir: str | Path) -> None:
        self.dir = Path(archive_dir)
        self.dir.mkdir(parents=True, exist_ok=True)

    def write(self, artifact: Artifact) -> Path:
        if not artifact.id:
            raise ValueError("Cannot archive artifact without id")
        path = self.dir / f"{artifact.id}.md"
        # SC-4 / NEW-6 — the reasoning sidecar ({id}.thinking.md) is written by the
        # executor via write_thinking() at production time, not here (the router
        # archives without thinking context). See write_thinking/read_thinking.
        if path.exists():
            # Already archived (e.g., multi-recipient routing). Don't rewrite.
            # Spec §7.4 immutability rule: the FIRST write wins. If the new
            # artifact content differs from what's on disk, that's a SYS audit
            # signal — log it but keep the on-disk version (silent overwrite
            # would mask the divergence and break artifact provenance).
            try:
                existing = _read_text(path)
                new_content = artifact.to_markdown()
                if existing != new_content:
                    msg = (f"Archive collision for {artifact.id} — on-disk content "
                           f"differs from incoming write; on-disk version preserved "
                           f"per Spec §7.4 immutability rule.")
                    print(f"[artifact_store] WARNING | {msg} SYS should audit.",
                          flush=True)
                    # Persist to a SYS-discoverable governance log rather than only
                    # stdout (audit NEW-14) — a genuine collision (same id, different
                    # content) is an architectural signal SYS must be able to find.
                    self._log_collision(msg)
            except OSError:
                pass
            return path
        artifact.status = "archived"
        atomic_write(path, artifact.to_markdown())
        return path

    def write_thinking(self, artifact_id: str,
                       thinking_blocks: list[str] | None) -> Path | None:
        """Write the `{id}.thinking.md` reasoning sidecar (SC-4, §7.1). Immutable
        like the artifact: first write wins. Never raises — reasoning capture is
        best-effort and must not block the execution/archival path."""
        if not artifact_id or not thinking_blocks:
            return None
        path = self.dir / f"{artifact_id}.thinking.md"
        if path.exists():
            return path
        try:
            body = "\n\n---\n\n".join(b for b in thinking_blocks if b)
            atomic_write(path, f"# Reasoning — {artifact_id}\n\n{body}\n")
            return path
        except OSError:
            return None

    def read_thinking(self, artifact_id: str) -> str | None:
        """Return the sidecar reasoning for an artifact, or None if absent."""
        path = self.dir / f"{artifact_id}.thinking.md"
        if not path.exists():
            return None
        try:
            return _read_text(path)
        except OSError:
            return None

    def _log_collision(self, message: str) -> None:
        """Append an archive-collision record to artifacts/archive_collisions.log
        (alongside the immutable archive, never inside it) so SYS can surface it."""
        from datetime import datetime, timezone
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        log_path = self.dir.parent / "archive_collisions.log"
        try:
            with open(log_path, "a") as f:
                f.write(f"{ts} | GOVERNANCE | {message}\n")
        except OSError:
            pass

    def load(self, artifact_id: str) -> Artifact | None:
        path = self.dir / f"{artifact_id}.md"
        if not path.exists():
            return None
        return Artifact.from_markdown(_read_text(path), filename=path.name)

    def get_recent(self, since: str | None = None) -> list[Artifact]:
        """Return archived artifacts with timestamp > `since` (ISO 8601 string).
        If `since` is None, returns all archived artifacts.

        Spec §5.6 — SYS inbox composition consumes this to audit cross-agent
        artifact flow since its last run.
        """
        out: list[Artifact] = []
        for path in sorted(self.dir.glob("*.md")):
            try:
                a = Artifact.from_markdown(_read_text(path), filename=path.name)
            except Exception as e:
                _quarantine_corrupt(path, e)   # defensive enhancement (not spec-mandated)
                continue
            if since is None or a.timestamp > since:
                out.append(a)
        return sorted(out, key=lambda x: x.timestamp)


class ArtifactStore:
    """Facade combining per-agent Inbox/Outbox + the global Archive."""

    def __init__(self, agents_dir: str | Path, archive_dir: str | Path) -> None:
        self.agents_dir = Path(agents_dir)
        self.archive = Archive(archive_dir)
        self._inboxes: dict[str, Inbox] = {}
        self._outboxes: dict[str, Outbox] = {}

    def inbox(self, agent_code: str) -> Inbox:
        if agent_code not in self._inboxes:
            self._inboxes[agent_code] = Inbox(self.agents_dir / agent_code)
        return self._inboxes[agent_code]

    def outbox(self, agent_code: str) -> Outbox:
        if agent_code not in self._outboxes:
            self._outboxes[agent_code] = Outbox(self.agents_dir / agent_code)
        return self._outboxes[agent_code]

    def get_outbox(self, agent_code: str) -> list[Artifact]:
        return self.outbox(agent_code).list_pending()

    def archive_artifact(self, artifact: Artifact) -> Path:
        return self.archive.write(artifact)

    def load_from_archive(self, artifact_id: str) -> Artifact | None:
        """Canonical immutable-archive read (Spec sc4 — standardized name; use
        this everywhere instead of reaching into `.archive.load`)."""
        return self.archive.load(artifact_id)

    def write_thinking(self, artifact_id: str,
                       thinking_blocks: list[str] | None) -> Path | None:
        """Write a reasoning sidecar for an artifact (SC-4, §7.1)."""
        return self.archive.write_thinking(artifact_id, thinking_blocks)

    def read_thinking(self, artifact_id: str) -> str | None:
        return self.archive.read_thinking(artifact_id)

    def get_recent_artifacts(self, since: str | None = None) -> list[Artifact]:
        return self.archive.get_recent(since)

    def clear_from_outbox(self, agent_code: str, artifact: Artifact) -> None:
        self.outbox(agent_code).clear(artifact)
