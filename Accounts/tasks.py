from celery import shared_task
import time
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

@shared_task
def debug_task(x, y):
    time.sleep(2)
    return f"Sum is {x + y}"

@shared_task
def send_notification_task(title, message):
    time.sleep(3) # Simulate some heavy background processing
    channel_layer = get_channel_layer()
    async_to_sync(channel_layer.group_send)(
        "broadcast",
        {
            "type": "send_notification",
            "title": title,
            "message": message
        }
    )
