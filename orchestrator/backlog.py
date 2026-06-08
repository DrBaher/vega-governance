"""
Backlog — pending / in_progress / resolved.

Spec v5 §10.1: ONE generic Backlog class, instantiated twice — `op_backlog`
(PROP items for OP, scope decisions) and `admin_backlog` (GOV items + role
requests for Admin OP, governance). Each artifact lives as a file; the directory
it's in encodes the state. AUTH is constructed by the bot when OP responds.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Optional

from models import Artifact, utcnow_iso
from state_manager import atomic_append, atomic_write


class Backlog:

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.pending = self.root / "pending"
        self.in_progress = self.root / "in_progress"
        self.resolved = self.root / "resolved"
        for d in (self.pending, self.in_progress, self.resolved):
            d.mkdir(parents=True, exist_ok=True)

    def add(self, artifact: Artifact) -> Path:
        if not artifact.id:
            raise ValueError("Cannot enqueue artifact without id")
        path = self.pending / f"{artifact.id}.md"
        atomic_write(path, artifact.to_markdown())
        return path

    def list_pending(self) -> list[Artifact]:
        out: list[Artifact] = []
        for p in sorted(self.pending.glob("*.md")):
            try:
                a = Artifact.from_markdown(p.read_text(), filename=p.name)
                out.append(a)
            except Exception:
                continue
        return out

    def list_in_progress(self) -> list[Artifact]:
        out: list[Artifact] = []
        for p in sorted(self.in_progress.glob("*.md")):
            try:
                out.append(Artifact.from_markdown(p.read_text(), filename=p.name))
            except Exception:
                continue
        return out

    def get_in_progress(self) -> Optional[Artifact]:
        items = self.list_in_progress()
        return items[0] if items else None

    def start_exchange(self, artifact_id: str) -> Path:
        src = self.pending / f"{artifact_id}.md"
        dst = self.in_progress / f"{artifact_id}.md"
        if not src.exists():
            raise FileNotFoundError(f"No pending OP item: {artifact_id}")
        shutil.move(src, dst)
        return dst

    def resolve(self, artifact_id: str, resolution: Optional[str] = None) -> Path:
        """Mark in-progress → resolved. Caller is responsible for emitting AUTH.

        Spec v5 §10.1 (lines 1295-1302) / audit P1-1: when `resolution` text is
        provided (e.g. the action note from `/resolve <GOV> <action>`), append it
        to the resolved file so there is a durable record of *why* the item was
        closed. SYS reads this in the cycle archive at its next audit.
        """
        for src_dir in (self.in_progress, self.pending):
            src = src_dir / f"{artifact_id}.md"
            if src.exists():
                dst = self.resolved / f"{artifact_id}.md"
                shutil.move(src, dst)
                if isinstance(resolution, str) and resolution:
                    atomic_append(dst, f"\n---\nResolution: {resolution}\n")
                return dst
        raise FileNotFoundError(f"OP item not in pending or in_progress: {artifact_id}")

    def find(self, artifact_id: str) -> Optional[Path]:
        for d in (self.pending, self.in_progress, self.resolved):
            p = d / f"{artifact_id}.md"
            if p.exists():
                return p
        return None


# Back-compat alias — the class was `OPBacklog` before the v5 rename to a generic
# two-instance Backlog (Spec §10.1). Kept so older imports keep working.
OPBacklog = Backlog


def make_auth(prop_id: str, disposition: str, reason: str = "") -> Artifact:
    """Construct an AUTH artifact in response to a PROP.

    Per Spec §7.2, AUTH is immutable once issued. SG produces SUM separately.
    Disposition is strictly `approve | reject` (Spec sc4 / Tier 3): `/modify` no
    longer mints an AUTH — it sends a directive into the open exchange cycle and
    SG produces a revised PROP, which OP then approves or rejects.
    """
    if disposition not in {"approve", "reject"}:
        raise ValueError(f"Invalid disposition: {disposition} (approve|reject only)")
    body = f"OP disposition: {disposition}\n"
    if reason:
        body += f"\nReason: {reason}\n"

    return Artifact(
        type="AUTH",
        sender="OP",
        recipient="SG",
        content=body,
        references=[prop_id],
        disposition=disposition,
        timestamp=utcnow_iso(),
    )
