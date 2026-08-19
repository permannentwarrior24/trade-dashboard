"""Async subprocess cleanup helpers."""

import asyncio


async def stop_process(
    process: asyncio.subprocess.Process | None, grace_seconds: float = 2.0
) -> None:
    """Terminate a child process and escalate to kill if it does not exit."""
    if process is None or process.returncode is not None:
        return
    try:
        process.terminate()
    except ProcessLookupError:
        return
    try:
        await asyncio.wait_for(process.wait(), timeout=grace_seconds)
    except asyncio.TimeoutError:
        try:
            process.kill()
        except ProcessLookupError:
            return
        await process.wait()
