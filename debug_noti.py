#!/usr/bin/env python3
"""
Simple test script to demonstrate the notification system
"""

import asyncio
import websockets
import json
from datetime import datetime


async def test_websocket_connection():
    """Test the WebSocket connection and receive notifications"""
    uri = "ws://172.18.7.89:5656/api/v1/simple-notification/ws/notifications"

    try:
        async with websockets.connect(uri) as websocket:
            print("Connected to notification WebSocket")

            # Listen for notifications
            while True:
                try:
                    message = await websocket.recv()
                    data = json.loads(message)
                    print(f"Received notification: {json.dumps(data, indent=2)}")

                    # If it's an initial notification, mark one as read
                    if data.get("type") == "initial_notifications" and data.get("notifications"):
                        notification_id = data["notifications"][0]["id"]
                        mark_read_message = {
                            "type": "mark_read",
                            "notification_id": notification_id,
                            "user_id": "test_user"
                        }
                        await websocket.send(json.dumps(mark_read_message))
                        print(f"Marked notification {notification_id} as read")

                except websockets.exceptions.ConnectionClosed:
                    print("WebSocket connection closed")
                    break
                except Exception as e:
                    print(f"Error receiving message: {e}")
                    break

    except Exception as e:
        print(f"Error connecting to WebSocket: {e}")


async def test_rest_endpoints():
    """Test the REST endpoints"""
    import aiohttp

    async with aiohttp.ClientSession() as session:
        # Get unread notifications
        async with session.get("http://172.18.7.89:5656/api/v1/simple-notification/unread") as response:
            if response.status == 200:
                data = await response.json()
                print(f"Unread notifications: {json.dumps(data, indent=2)}")
            else:
                print(f"Error getting unread notifications: {response.status}")


if __name__ == "__main__":
    print("Testing Simple Notification System")
    print("=" * 40)

    # Test REST endpoints
    print("\n1. Testing REST endpoints...")
    asyncio.run(test_rest_endpoints())

    # Test WebSocket connection
    print("\n2. Testing WebSocket connection...")
    print("Press Ctrl+C to stop the WebSocket test")
    try:
        asyncio.run(test_websocket_connection())
    except KeyboardInterrupt:
        print("\nWebSocket test stopped by user")

    print("\nTest completed!")