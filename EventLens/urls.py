"""
URL configuration for EventLens project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/4.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path
from Accounts import views as ac_views
from Dashboards import views as dash_views
from Dashboards import google_drive as gd_views
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    path('admin/', admin.site.urls),
    path('notifications-test/', ac_views.notifications_test, name='notifications_test'),
    path('trigger-task/', ac_views.trigger_notification_task, name='trigger_notification_task'),

    # Platform routes
    path('', dash_views.home, name='home'),
    path('login/', dash_views.login_view, name='login'),
    path('register/', dash_views.register_view, name='register'),
    path('logout/', dash_views.logout_view, name='logout'),
    path('dashboard/photographer/', dash_views.photographer_dashboard, name='photographer_dashboard'),
    path('dashboard/upload/<int:event_id>/', dash_views.upload_photos, name='upload_photos'),
    path('event/<slug:slug>/', dash_views.guest_portal, name='guest_portal'),
    path('event/<slug:slug>/upload-selfie/', dash_views.upload_selfie, name='upload_selfie'),

    # Google Drive Routes
    path('google-drive/connect/', gd_views.google_drive_auth_init, name='google_drive_connect'),
    path('google-drive/callback/', gd_views.google_drive_auth_callback, name='google_drive_callback'),
    path('google-drive/disconnect/', gd_views.google_drive_disconnect, name='google_drive_disconnect'),
    path('google-drive/folders/', gd_views.google_drive_list_folders, name='google_drive_folders'),
    path('google-drive/import/', gd_views.google_drive_import_photos, name='google_drive_import'),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

