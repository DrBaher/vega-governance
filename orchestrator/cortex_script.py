"""
CORTEX add-on — placeholder.

Interface from CORTEX spec v0.2 BETA Appendix A. All methods raise
NotImplementedError. CORTEX is a navigation layer over the agent wiki (ranked
lists by concept) — useful only once wikis exceed CORTEX_SCAN_THRESHOLD entries.

To activate:
  1. Implement these methods per CORTEX spec §6–§17.
  2. Set CORTEX_ENABLED = True in config.py.

While CORTEX_ENABLED is False, the orchestrator does not call any of these
methods, so this file is inert. The audit (May 2026) flagged the absence of
this file as a startup-crash risk if the flag were ever toggled — this stub
removes that risk while preserving the option to fill in the implementation.

References:
  - CORTEX spec Appendix A (the interface this file mirrors)
  - Orchestrator spec §5.1 (post_execution hook point)
  - Orchestrator spec §13 (periodic_maintenance hook point)
"""

from __future__ import annotations

from typing import Any


_NOT_IMPL_MSG = (
    "CORTEX is not yet implemented. The orchestrator imports the interface "
    "stub from cortex_script.py. Set CORTEX_ENABLED=False (the default) until "
    "the methods are filled in per CORTEX spec v0.2 BETA §6–§17."
)


class CortexScript:
    """Stub implementation of the CORTEX script (per CORTEX spec Appendix A).

    All four public methods raise NotImplementedError. The orchestrator gates
    each call site on `config.CORTEX_ENABLED`, so this stub is never invoked
    during normal operation.
    """

    def __init__(self, config: Any) -> None:
        self.scan_threshold = getattr(config, "CORTEX_SCAN_THRESHOLD", 15)
        # The CORTEX spec stores its internal state under STATE_DIR/cortex.
        # When implementing, create the directory lazily.
        state_dir = getattr(config, "STATE_DIR", "")
        self.data_dir = (state_dir.rstrip("/") + "/cortex") if state_dir else None

    def post_execution(
        self,
        agent_code: str,
        log_entries: list[Any],
        consultation_record: Any,
        wiki_updates: list[Any],
    ) -> None:
        """Spec §5.1 hook point — called after every agent execution.
        Should update concept evidence, recompute strengths, regenerate
        concepts.md. See CORTEX spec §10, §17.3."""
        raise NotImplementedError(_NOT_IMPL_MSG)

    def periodic_maintenance(self, agent_code: str) -> dict[str, Any]:
        """Spec §13 hook point — called periodically (e.g., every N executions).
        Apply decay, generate self-lint report, regenerate concepts.md.
        See CORTEX spec §12, §16."""
        raise NotImplementedError(_NOT_IMPL_MSG)

    def sys_cross_agent(self) -> tuple[Any, Any]:
        """Called during SYS execution — generate UNIVERSAL/concept_graph.md.
        See CORTEX spec §14."""
        raise NotImplementedError(_NOT_IMPL_MSG)
