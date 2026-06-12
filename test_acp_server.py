import asyncio
import json
import subprocess
import sys

async def test_server():
    # Start the server as a subprocess
    process = await asyncio.create_subprocess_exec(
        ".venv/bin/python", "main.py",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )

    async def send_request(method, params, request_id):
        request = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
            "id": request_id
        }
        process.stdin.write(json.dumps(request).encode() + b"\n")
        await process.stdin.drain()

    async def read_response():
        while True:
            line = await process.stdout.readline()
            if not line:
                return None
            line_str = line.decode().strip()
            if not line_str:
                continue
            try:
                return json.loads(line_str)
            except json.JSONDecodeError as e:
                print(f"Failed to decode JSON: {repr(line_str)}")
                raise e

    try:
        # 1. Initialize
        print("Sending initialize...")
        await send_request("initialize", {
            "protocolVersion": 1,
            "clientInfo": {"name": "test-client", "version": "0.1.0"}
        }, 1)
        resp = await read_response()
        print(f"Initialize response: {resp}")

        # 2. New Session
        print("\nSending session/new...")
        await send_request("session/new", {
            "cwd": "/tmp",
            "mcpServers": []
        }, 2)
        resp = await read_response()
        print(f"New session response: {resp}")
        session_id = resp["result"]["sessionId"]

        # 3. Prompt
        print(f"\nSending prompt to session {session_id}...")
        await send_request("session/prompt", {
            "sessionId": session_id,
            "prompt": [{"type": "text", "text": "hello"}]
        }, 3)
        resp = await read_response()
        print(f"Prompt response: {resp}")

        # Wait a bit for potential updates
        print("\nWaiting for updates (5s)...")
        try:
            while True:
                update = await asyncio.wait_for(read_response(), timeout=5.0)
                print(f"Update: {update}")
        except asyncio.TimeoutError:
            print("Done waiting.")

    finally:
        process.terminate()
        await process.wait()

if __name__ == "__main__":
    asyncio.run(test_server())
