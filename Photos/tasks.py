import io
import logging
from celery import shared_task
from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from google.oauth2.credentials import Credentials

from Accounts.models import GoogleDriveCredential
from Events.models import Event
from Photos.models import Photo
from FaceEngine.tasks import process_photo_faces_task

logger = logging.getLogger(__name__)

@shared_task
def import_photos_from_drive_task(user_id, event_id, folder_id):
    channel_layer = get_channel_layer()
    group_name = f"user_{user_id}"

    def send_socket_status(status, title="Google Drive Import", message=""):
        async_to_sync(channel_layer.group_send)(
            group_name,
            {
                "type": "send_notification",
                "title": title,
                "message": f"[{status}] {message}"
            }
        )

    try:
        user = User.objects.get(id=user_id)
        event = Event.objects.get(id=event_id, photographer=user)
        cred_obj = GoogleDriveCredential.objects.get(user=user)
    except (User.DoesNotExist, Event.DoesNotExist, GoogleDriveCredential.DoesNotExist) as e:
        logger.error(f"Import failed initialization: {str(e)}")
        return f"Failed to initialize import task: {str(e)}"

    send_socket_status("STARTED", "Google Drive Import", "Connecting to Google Drive...")

    try:
        # Reconstruct Credentials
        creds = Credentials(
            token=cred_obj.token.get('token'),
            refresh_token=cred_obj.token.get('refresh_token'),
            token_uri=cred_obj.token.get('token_uri'),
            client_id=cred_obj.token.get('client_id'),
            client_secret=cred_obj.token.get('client_secret'),
            scopes=cred_obj.token.get('scopes')
        )

        # Refresh token if expired
        if not creds.valid and creds.refresh_token:
            from google.auth.transport.requests import Request
            creds.refresh(Request())
            cred_obj.token['token'] = creds.token
            cred_obj.save()

        # Build drive v3 service
        service = build('drive', 'v3', credentials=creds)

        # Query files in selected folder (mimeType is image, and not trashed)
        query = f"'{folder_id}' in parents and mimeType contains 'image/' and trashed = false"
        
        results = service.files().list(
            q=query,
            fields="files(id, name, mimeType)",
            pageSize=100
        ).execute()

        files = results.get('files', [])
        total_files = len(files)

        if total_files == 0:
            send_socket_status("COMPLETED", "Google Drive Import", "No images found in the selected folder.")
            return "No images found."

        send_socket_status("PROGRESS", "Google Drive Import", f"Found {total_files} photos. Downloading...")

        imported_count = 0
        for idx, file_info in enumerate(files):
            file_id = file_info['id']
            file_name = file_info['name']

            try:
                # Download file contents
                request = service.files().get_media(fileId=file_id)
                fh = io.BytesIO()
                downloader = MediaIoBaseDownload(fh, request)
                done = False
                while done is False:
                    _, done = downloader.next_chunk()

                # Save file to Photo
                photo = Photo(event=event)
                photo.image.save(file_name, ContentFile(fh.getvalue()), save=True)

                # Trigger face engine processing
                process_photo_faces_task.delay(photo.id)

                imported_count += 1
                
                # Throttle socket updates to reduce overhead
                if imported_count % 3 == 0 or imported_count == total_files:
                    send_socket_status("PROGRESS", "Google Drive Import", f"Imported {imported_count}/{total_files} photos...")

            except Exception as file_err:
                logger.error(f"Failed to import file {file_name} ({file_id}): {str(file_err)}")
                send_socket_status("WARNING", "Google Drive Import", f"Failed to download {file_name}. Skipping...")

        send_socket_status("SUCCESS", "Google Drive Import", f"Successfully imported {imported_count} photos to '{event.name}'!")
        return f"Imported {imported_count}/{total_files} files."

    except Exception as e:
        logger.error(f"Error during Google Drive import task: {str(e)}")
        send_socket_status("ERROR", "Google Drive Import", f"Import failed: {str(e)}")
        return f"Import failed: {str(e)}"
