"""
CORTEX placeholder — the stub exists and refuses to do anything quietly.

Spec §2 lists cortex_script.py in the directory structure; spec §5.1 and §13
have hook points. Until CORTEX is implemented, the orchestrator keeps
CORTEX_ENABLED=False and never calls these methods. This test asserts the
stub is present (so toggling the flag doesn't crash on import) AND that
calling any method raises a clear NotImplementedError.
"""

import pytest

from cortex_script import CortexScript


class _StubConfig:
    CORTEX_SCAN_THRESHOLD = 15
    STATE_DIR = "/tmp/vega-test-state"


def test_cortex_script_imports():
    """The file exists and the class can be constructed without crashing."""
    cs = CortexScript(_StubConfig())
    assert cs.scan_threshold == 15
    assert cs.data_dir == "/tmp/vega-test-state/cortex"


def test_post_execution_raises_not_implemented():
    cs = CortexScript(_StubConfig())
    with pytest.raises(NotImplementedError) as exc:
        cs.post_execution("SG", [], None, [])
    assert "CORTEX is not yet implemented" in str(exc.value)
    assert "CORTEX_ENABLED" in str(exc.value)


def test_periodic_maintenance_raises_not_implemented():
    cs = CortexScript(_StubConfig())
    with pytest.raises(NotImplementedError):
        cs.periodic_maintenance("SG")


def test_sys_cross_agent_raises_not_implemented():
    cs = CortexScript(_StubConfig())
    with pytest.raises(NotImplementedError):
        cs.sys_cross_agent()
