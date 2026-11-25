import asyncio
import inspect
import os
import platform

import pytest

ASYNC_TEST_TIMEOUT_S = float(os.environ.get("PHANTOMWATTS_TEST_TIMEOUT_S", "30"))

# tests/windows drives the real Bluetooth stack of the machine it runs on. It is only collected
# where that is asked for, on Windows.
WINDOWS_E2E = platform.system() == "Windows" and os.environ.get("PHANTOMWATTS_WINDOWS_E2E") == "1"
collect_ignore_glob = [] if WINDOWS_E2E else ["windows/*"]


@pytest.hookimpl(tryfirst=True)
def pytest_pyfunc_call(pyfuncitem: pytest.Function) -> bool | None:
    """Run `async def` tests on a fresh event loop each, without an asyncio plugin."""
    if not inspect.iscoroutinefunction(pyfuncitem.obj):
        return None
    wanted = inspect.signature(pyfuncitem.obj).parameters
    arguments = {name: pyfuncitem.funcargs[name] for name in wanted}
    asyncio.run(asyncio.wait_for(pyfuncitem.obj(**arguments), ASYNC_TEST_TIMEOUT_S))
    return True
