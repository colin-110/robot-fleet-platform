from fastapi import WebSocket
from starlette.websockets import WebSocketDisconnect

class ConnectionManager:

    def __init__(self):

        self.active_connections = []

    async def connect(self, websocket: WebSocket):

        await websocket.accept()

        self.active_connections.append(websocket)

        print("WebSocket connected")

    def disconnect(self, websocket: WebSocket):

        if websocket in self.active_connections:

            self.active_connections.remove(websocket)

            print("WebSocket disconnected")

    async def broadcast(self, data):

        dead_connections = []

        for connection in self.active_connections:

            try:

                await connection.send_json(data)

            except WebSocketDisconnect:

                dead_connections.append(connection)

            except Exception as e:

                print("Broadcast error:", e)

                dead_connections.append(connection)

        for connection in dead_connections:

            self.disconnect(connection)

manager = ConnectionManager()