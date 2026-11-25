import asyncio
import inspect
import os
import platform

import pytest

ASYNC_TEST_TIMEOUT_S = float(os.environ.get("PHANTOMWATTS_TEST_TIMEOUT_S", "30"))

# tests/windows and tests/linux drive the real Bluetooth stack of the machine they run on. They
# are only collected where that is asked for.
WINDOWS_E2E = platform.system() == "Windows" and os.environ.get("PHANTOMWATTS_WINDOWS_E2E") == "1"
LINUX_VHCI = platform.system() == "Linux" and os.environ.get("PHANTOMWATTS_LINUX_VHCI") == "1"
collect_ignore_glob = [
    tests for wanted, tests in ((WINDOWS_E2E, "windows/*"), (LINUX_VHCI, "linux/*")) if not wanted
]


@pytest.hookimpl(tryfirst=True)
def pytest_pyfunc_call(pyfuncitem: pytest.Function) -> bool | None:
    """Run `async def` tests on a fresh event loop each, without an asyncio plugin."""
    if not inspect.iscoroutinefunction(pyfuncitem.obj):
        return None
    wanted = inspect.signature(pyfuncitem.obj).parameters
    arguments = {name: pyfuncitem.funcargs[name] for name in wanted}
    asyncio.run(asyncio.wait_for(pyfuncitem.obj(**arguments), ASYNC_TEST_TIMEOUT_S))
    return True
