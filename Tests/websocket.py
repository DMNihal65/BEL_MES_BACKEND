import websockets
import asyncio
import json

async def test_websocket():
    uri = "ws://172.18.7.89:4470/api/v1/production_monitoring/ws/machine-status"
    try:
        async with websockets.connect(uri) as websocket:
            print("Connected to WebSocket")
            while True:
                try:
                    message = await websocket.recv()
                    data = json.loads(message)
                    print("Received:", data)
                except Exception as e:
                    print(f"Error receiving message: {e}")
                    break
    except Exception as e:
        print(f"Connection error: {e}")

asyncio.run(test_websocket())