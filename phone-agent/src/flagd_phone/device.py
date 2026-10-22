from __future__ import annotations

import asyncio
import json
from typing import Any

from .protocol import PortalConnection, PortalError
from .runner import Journal


GLOBAL_ACTION_HOME = 2
GLOBAL_ACTION_LOCK_SCREEN = 8


async def park_phone(
    portal: PortalConnection, journal: Journal, *, timeout: float = 5,
) -> dict[str, Any]:
    """Leave the foreground app and switch off the display, without inference.

    Return Home before disabling the wake watchdog: releasing its wake lock
    can immediately put a previously idle display to sleep. Disable it before
    locking so it cannot undo the screen-lock action.
    These are Portal's documented equivalents of Home and locking the display;
    no app packages, UI trees, shell commands or ADB are used.
    """
    journal.record("screen_park_intent")
    deadline = asyncio.get_running_loop().time() + timeout

    async def status() -> dict[str, Any]:
        value = await portal.call("screen/keepAwake/status")
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except ValueError as error:
                raise PortalError("invalid screen-awake status") from error
        if not isinstance(value, dict):
            raise PortalError("invalid screen-awake status")
        journal.record("screen_park_status", status=value)
        return value

    current = await status()
    home_dispatched = False
    if current.get("interactive") is True:
        journal.record("device_action_intent", action="home")
        accepted = await portal.call("global", {"action": GLOBAL_ACTION_HOME}, mutation=True)
        if accepted is False:
            raise PortalError("device rejected home")
        home_dispatched = True
        journal.record("device_action_acknowledged", action="home")
    await portal.call("screen/keepAwake/set", {"enabled": False}, mutation=True)
    journal.record("screen_awake_acknowledged", enabled=False)
    current = await status()
    while current.get("enabled") is not False:
        if asyncio.get_running_loop().time() >= deadline:
            raise PortalError("screen-awake watchdog did not disable")
        await asyncio.sleep(0.2)
        current = await status()
    if current.get("interactive") is False:
        result = {"screen_off": True, "home_dispatched": home_dispatched, "already_off": not home_dispatched}
        journal.record("screen_park_finished", **result)
        return result
    journal.record("device_action_intent", action="lock_screen")
    accepted = await portal.call("global", {"action": GLOBAL_ACTION_LOCK_SCREEN}, mutation=True)
    if accepted is False:
        raise PortalError("device rejected lock_screen")
    journal.record("device_action_acknowledged", action="lock_screen")
    current = await status()
    while current.get("interactive") is not False:
        if asyncio.get_running_loop().time() >= deadline:
            raise PortalError("display remained interactive after locking")
        await asyncio.sleep(0.2)
        current = await status()
    if current.get("enabled") is not False:
        raise PortalError("screen-awake watchdog re-enabled during cleanup")
    result = {"screen_off": True, "home_dispatched": home_dispatched, "already_off": False}
    journal.record("screen_park_finished", **result)
    return result


async def ensure_screen_awake(
    portal: PortalConnection, journal: Journal, *, timeout: float = 10,
) -> dict[str, Any]:
    """Enable Portal's persistent watchdog and verify live display readiness.

    The set response describes the requested target, not the live service state.
    The setting stays enabled until the operator disables it in Portal.
    """
    journal.record("screen_awake_intent", enabled=True)
    await portal.call("screen/keepAwake/set", {"enabled": True}, mutation=True)
    journal.record("screen_awake_acknowledged", enabled=True)
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        status = await portal.call("screen/keepAwake/status")
        if isinstance(status, str):
            try:
                status = json.loads(status)
            except ValueError as error:
                raise PortalError("invalid screen-awake status") from error
        if not isinstance(status, dict):
            raise PortalError("invalid screen-awake status")
        journal.record("screen_awake_status", status=status)
        if status.get("deviceLocked") is True:
            raise PortalError("screen remains securely locked; operator unlock required")
        if status.get("enabled") is True and status.get("interactive") is True:
            return status
        if asyncio.get_running_loop().time() >= deadline:
            raise PortalError("screen-awake watchdog did not wake the display")
        await asyncio.sleep(0.2)
