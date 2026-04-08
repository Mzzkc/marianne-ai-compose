import asyncio
import json
import os
from pathlib import Path

import pytest


@pytest.mark.skip(reason="Live daemon integration script — requires running conductor, not a unit test")
async def test_stream():
    socket_path = Path("/tmp/marianne.sock")
    if not socket_path.exists():
        print(f"Socket not found at {socket_path}")
        return

    print(f"Connecting to {socket_path}...")
    reader, writer = await asyncio.open_unix_connection(str(socket_path))
    
    # Send daemon.monitor.stream request
    request = {
        "jsonrpc": "2.0",
        "method": "daemon.monitor.stream",
        "params": {},
        "id": 1
    }
    writer.write(json.dumps(request).encode() + b"\n")
    await writer.drain()
    
    print("Waiting for events (Ctrl+C to stop)...")
    try:
        while True:
            line = await reader.readline()
            if not line:
                print("Connection closed by server")
                break
            msg = json.loads(line)
            if "method" in msg and msg["method"] == "monitor.event":
                event = msg["params"]
                print(f"Event: {event.get('event')} @ {event.get('timestamp')}")
                if event.get("event") == "monitor.snapshot":
                    data = event.get("data", {})
                    print(f"  Snapshot: Jobs={data.get('running_jobs')} Mem={data.get('system_memory_used_mb'):.0f}MB")
            else:
                print(f"RPC Response: {msg}")
    except KeyboardInterrupt:
        print("Stopping...")
    finally:
        writer.close()
        await writer.wait_closed()

if __name__ == "__main__":
    asyncio.run(test_stream())
