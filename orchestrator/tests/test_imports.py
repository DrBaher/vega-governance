"""
Smoke test: every orchestrator module imports cleanly.

If a module fails to import (syntax error, circular import, missing dep),
this catches it before runtime.
"""


def test_all_modules_import():
    import models                  # noqa: F401
    import state_manager           # noqa: F401
    import sequence_manager        # noqa: F401
    import artifact_store          # noqa: F401
    import wiki_manager            # noqa: F401
    import router                  # noqa: F401
    import cycle_manager           # noqa: F401
    import backlog                 # noqa: F401
    import executor                # noqa: F401
    # telegram_bot pulls in python-telegram-bot (might be absent at test time)
    try:
        import telegram_bot       # noqa: F401
    except ImportError:
        pass
    # config and main require config.py to be filled in; skip in unit test
