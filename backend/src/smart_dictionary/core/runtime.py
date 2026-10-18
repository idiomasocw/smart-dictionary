"""Event loop choice for psycopg's async mode.

psycopg cannot run on Windows' ProactorEventLoop, which uvicorn uses there by
default. Production (Linux) keeps uvicorn's default loop; this only keeps local
development on Windows working.
"""

import asyncio
import selectors
import sys

SELECTOR_LOOP = "smart_dictionary.core.runtime:selector_event_loop"


def selector_event_loop() -> asyncio.AbstractEventLoop:
    return asyncio.SelectorEventLoop(selectors.SelectSelector())


def uvicorn_loop() -> str:
    """The value for uvicorn's `loop` setting on this platform."""
    return SELECTOR_LOOP if sys.platform == "win32" else "auto"
