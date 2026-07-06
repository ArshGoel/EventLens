from django.shortcuts import render
from django.http import JsonResponse
from .tasks import send_notification_task

def notifications_test(request):
    return render(request, 'notifications_test.html')

def trigger_notification_task(request):
    title = request.GET.get('title', 'System Update')
    message = request.GET.get('message', 'A background job has completed successfully!')
    send_notification_task.delay(title, message)
    return JsonResponse({'status': 'Task queued'})
