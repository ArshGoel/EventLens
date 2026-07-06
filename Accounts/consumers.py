import json
from channels.generic.websocket import AsyncWebsocketConsumer

class NotificationConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.user = self.scope.get("user")
        self.groups_list = ["broadcast"]
        
        if self.user and self.user.is_authenticated:
            self.groups_list.append(f"user_{self.user.id}")
            
        for group in self.groups_list:
            await self.channel_layer.group_add(
                group,
                self.channel_name
            )
        await self.accept()

    async def disconnect(self, close_code):
        for group in self.groups_list:
            await self.channel_layer.group_discard(
                group,
                self.channel_name
            )

    # Receive message from WebSocket (from client)
    async def receive(self, text_data):
        pass

    # Receive message from group and send to client
    async def send_notification(self, event):
        message = event["message"]
        title = event.get("title", "New Notification")
        await self.send(text_data=json.dumps({
            "title": title,
            "message": message
        }))
