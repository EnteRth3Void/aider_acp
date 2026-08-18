import asyncio
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

async def test_server():
    # Start the server as a subprocess
    process = await asyncio.create_subprocess_exec(
        str(ROOT / ".venv/bin/python"),
        str(ROOT / "main.py"),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(ROOT),
    )

    pending_requests = {}

    async def read_raw_line():
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

    async def send_request(method, params, request_id):
        future = asyncio.get_running_loop().create_future()
        pending_requests[request_id] = future
        request = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
            "id": request_id
        }
        process.stdin.write(json.dumps(request).encode() + b"\n")
        await process.stdin.drain()
        return await future

    async def receiver_loop():
        try:
            while True:
                msg = await read_raw_line()
                if msg is None:
                    break
                
                if "id" in msg and "method" not in msg:
                    # Response to our request
                    req_id = msg["id"]
                    if req_id in pending_requests:
                        future = pending_requests.pop(req_id)
                        future.set_result(msg)
                elif "method" in msg:
                    # Incoming request or notification
                    method = msg["method"]
                    if "id" in msg:
                        # Request
                        if method == "session/request_permission":
                            tool_call = msg["params"].get("toolCall", {})
                            title = tool_call.get("title", "unknown")
                            content = tool_call.get("content") or []
                            if content:
                                block = content[0].get("content", {})
                                title = block.get("text", title)
                            print(f"\n[CLIENT] Received permission request for: {title}")
                            response = {
                                "jsonrpc": "2.0",
                                "id": msg["id"],
                                "result": {
                                    "outcome": {
                                        "outcome": "selected",
                                        "optionId": "approve"
                                    }
                                }
                            }
                            process.stdin.write(json.dumps(response).encode() + b"\n")
                            await process.stdin.drain()
                            print("[CLIENT] Sent approval.")
                        elif method == "fs/write_text_file":
                            params = msg["params"]
                            print(
                                f"\n[CLIENT] fs/write_text_file path={params.get('path')} "
                                f"bytes={len(params.get('content') or '')}"
                            )
                            response = {
                                "jsonrpc": "2.0",
                                "id": msg["id"],
                                "result": {},
                            }
                            process.stdin.write(json.dumps(response).encode() + b"\n")
                            await process.stdin.drain()
                        elif method == "fs/read_text_file":
                            params = msg["params"]
                            print(f"\n[CLIENT] fs/read_text_file path={params.get('path')}")
                            response = {
                                "jsonrpc": "2.0",
                                "id": msg["id"],
                                "error": {
                                    "code": -32000,
                                    "message": "not implemented in test client",
                                },
                            }
                            process.stdin.write(json.dumps(response).encode() + b"\n")
                            await process.stdin.drain()
                        else:
                            print(f"\n[CLIENT] Received unknown request: {method}")
                    else:
                        # Notification
                        if method == "session/update":
                            update = msg["params"].get("update", {})
                            kind = update.get("sessionUpdate", "unknown")
                            if kind == "tool_call":
                                print(f"Update ({kind}): {update.get('title', '')}")
                            elif kind == "tool_call_update":
                                print(f"Update ({kind}): status={update.get('status', '')}")
                            else:
                                content = update.get("content", {})
                                text = content.get("text", "")
                                print(f"Update ({kind}): {text[:100]}...")
                        else:
                            print(f"\n[CLIENT] Received notification: {method}")
        except Exception as e:
            print(f"Receiver loop error: {e}")

    receiver_task = asyncio.create_task(receiver_loop())

    try:
        # 1. Initialize
        print("Sending initialize...")
        resp = await send_request("initialize", {
            "protocolVersion": 1,
            "clientInfo": {"name": "test-client", "version": "0.1.0"},
            "clientCapabilities": {
                "fs": {"readTextFile": True, "writeTextFile": True}
            }
        }, 1)
        print(f"Initialize response: {resp}")

        # 2. New Session
        print("\nSending session/new...")
        resp = await send_request("session/new", {
            "cwd": "/tmp",
            "mcpServers": []
        }, 2)
        print(f"New session response: {resp}")
        session_id = resp["result"]["sessionId"]

        # 3. Prompt
        print(f"\nSending prompt to session {session_id}...")
        resp = await send_request("session/prompt", {
            "sessionId": session_id,
            "prompt": [{"type": "text", "text": "Hello!"}]
        }, 3)
        print(f"Prompt response: {resp}")

        # Wait a bit for updates
        await asyncio.sleep(2)

        # 4. List Sessions
        print("\nSending session/list...")
        resp = await send_request("session/list", {}, 4)
        print(f"List sessions response: {resp}")

        # 5. Close Session
        print(f"\nSending session/close for {session_id}...")
        resp = await send_request("session/close", {"sessionId": session_id}, 5)
        print(f"Close session response: {resp}")

        # 6. List Sessions again
        print("\nSending session/list again...")
        resp = await send_request("session/list", {}, 6)
        print(f"List sessions (after close) response: {resp}")

        await asyncio.sleep(1)

    finally:
        receiver_task.cancel()
        process.terminate()
        await process.wait()

if __name__ == "__main__":
    asyncio.run(test_server())
