import os
import cv2
import numpy as np
from celery import shared_task
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

from django.contrib.auth.models import User
from Accounts.models import UserProfile
from Photos.models import Photo
from Events.models import Event
from .models import DetectedFace, GuestMatch

import logging

logger = logging.getLogger(__name__)

# Lazy loader for InsightFace FaceAnalysis to avoid overhead on Celery load
_face_app = None

def get_face_app():
    global _face_app
    if _face_app is None:
        import insightface
        from insightface.app import FaceAnalysis
        from django.conf import settings
        import onnxruntime as ort
        
        # Check if environment variables are explicitly set
        env_model = os.environ.get('INSIGHTFACE_MODEL_NAME')
        env_ctx_id = os.environ.get('FACE_ENGINE_CTX_ID')
        env_det_size = os.environ.get('FACE_DETECTION_SIZE')

        # Detect GPU availability in ONNX Runtime
        providers = ort.get_available_providers()
        has_gpu = 'CUDAExecutionProvider' in providers

        if has_gpu:
            logger.info("NVIDIA GPU detected in ONNX Runtime providers: %s", providers)
        else:
            logger.info("No GPU detected in ONNX Runtime providers: %s. Using CPU configuration.", providers)

        # 1. Model Name Selection (Always default to buffalo_l for maximum accuracy)
        if env_model:
            model_name = env_model
        else:
            model_name = 'buffalo_l'

        # 2. Context ID (GPU vs CPU)
        if env_ctx_id:
            ctx_id = int(env_ctx_id)
        else:
            ctx_id = 0 if has_gpu else -1

        # 3. Detection Size
        if env_det_size:
            det_size_val = int(env_det_size)
        else:
            det_size_val = 640 if has_gpu else 480

        logger.info(f"InsightFace configured with Model: {model_name}, Ctx_ID: {ctx_id}, Det_Size: {det_size_val}")
        
        _face_app = FaceAnalysis(name=model_name)
        _face_app.prepare(ctx_id=ctx_id, det_size=(det_size_val, det_size_val))
    return _face_app


@shared_task
def process_photo_faces_task(photo_id):
    """
    Detects faces in an uploaded photo, extracts 512-D embeddings, and saves them.
    """
    try:
        photo = Photo.objects.get(id=photo_id)
    except Photo.DoesNotExist:
        return f"Photo {photo_id} not found."

    img = None
    # 1. Try loading from local path if exists
    try:
        if photo.image and hasattr(photo.image, 'path') and os.path.exists(photo.image.path):
            img = cv2.imread(photo.image.path)
    except Exception:
        pass

    # 2. Try loading from remote URL (Cloudinary)
    if img is None and photo.image_url:
        try:
            import urllib.request
            resp = urllib.request.urlopen(photo.image_url)
            image_data = np.asarray(bytearray(resp.read()), dtype="uint8")
            img = cv2.imdecode(image_data, cv2.IMREAD_COLOR)
        except Exception as url_err:
            return f"Failed to load remote image from URL {photo.image_url}: {str(url_err)}"

    if img is None:
        return f"Failed to load image from either local path or URL for photo {photo_id}"

    app = get_face_app()
    faces = app.get(img)

    created_count = 0
    for face in faces:
        # Bounding box [x_min, y_min, x_max, y_max]
        bbox = face.bbox.tolist()
        
        # 512-D embedding
        embedding = face.normed_embedding.tolist()

        # Save to DB
        DetectedFace.objects.create(
            photo=photo,
            bbox=bbox,
            embedding=embedding
        )
        created_count += 1

    return f"Processed photo {photo_id}. Found and saved {created_count} faces."


@shared_task
def match_guest_selfie_task(user_id, event_id):
    """
    Processes a guest's selfie, extracts its embedding, and matches it
    against all detected faces within the specified event.
    """
    channel_layer = get_channel_layer()
    group_name = f"user_{user_id}"

    def send_socket_status(status, title="Matching Status", message=""):
        async_to_sync(channel_layer.group_send)(
            group_name,
            {
                "type": "send_notification",
                "title": title,
                "message": f"STATUS: {status}. {message}"
            }
        )

    try:
        user = User.objects.get(id=user_id)
        profile = UserProfile.objects.get(user=user)
        event = Event.objects.get(id=event_id)
    except (User.DoesNotExist, UserProfile.DoesNotExist, Event.DoesNotExist) as e:
        return f"Failed to fetch models: {str(e)}"

    if not profile.selfie:
        send_socket_status("ERROR", "No Selfie Uploaded", "Please upload a reference selfie first.")
        return "No selfie uploaded."

    send_socket_status("PROCESSING", "Selfie Analysis", "Extracting face embedding from your selfie...")

    # Step 1: Compute selfie embedding if not already present
    if not profile.selfie_embedding:
        selfie_path = profile.selfie.path
        if not os.path.exists(selfie_path):
            send_socket_status("ERROR", "File Not Found", "Selfie file does not exist on disk.")
            return "Selfie file not found."

        img = cv2.imread(selfie_path)
        if img is None:
            send_socket_status("ERROR", "Read Failure", "Could not read the selfie image file.")
            return "Failed to read selfie."

        app = get_face_app()
        faces = app.get(img)

        if not faces:
            send_socket_status("ERROR", "No Face Detected", "Could not detect a clear face in your selfie. Please try another photo.")
            return "No face found in selfie."

        # Take the largest/first face detected in the selfie
        selfie_face = faces[0]
        profile.selfie_embedding = selfie_face.normed_embedding.tolist()
        profile.save()

    guest_embedding = np.array(profile.selfie_embedding)

    send_socket_status("MATCHING", "Running AI Search", "Comparing your face with all photos in this wedding...")

    # Step 2: Compare against all photos inside the Event
    photos = Photo.objects.filter(event=event)
    detected_faces = DetectedFace.objects.filter(photo__in=photos)

    matches_found = 0
    for det_face in detected_faces:
        face_emb = np.array(det_face.embedding)
        
        # Cosine similarity (dot product since vectors are normalized)
        similarity = float(np.dot(guest_embedding, face_emb))

        # Threshold: 0.45-0.5 is standard for matching using ArcFace
        if similarity >= 0.48:
            GuestMatch.objects.get_or_create(
                guest=user,
                photo=det_face.photo,
                defaults={"similarity": similarity}
            )
            matches_found += 1

    send_socket_status("SUCCESS", "Scan Complete!", f"Matched {matches_found} photos! Refreshing your gallery...")
    return f"Completed matching for user {user_id} in event {event_id}. Found {matches_found} matches."

@shared_task
def send_hd_requests_email_task(user_id, event_id, base_url=None):
    """
    Finds all PENDING HD requests for this user in this event,
    downloads the original high-res files from Google Drive,
    compiles them in-memory into a ZIP archive, and emails it as an attachment or download link.
    """
    import io
    import zipfile
    from django.core.mail import EmailMessage
    from django.contrib.auth.models import User
    from django.conf import settings
    from Events.models import Event
    from Accounts.models import GoogleDriveCredential
    from .models import HDRequest
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaIoBaseDownload
    
    try:
        user = User.objects.get(id=user_id)
        event = Event.objects.get(id=event_id)
        
        # Get all pending requests
        requests = HDRequest.objects.filter(guest=user, event=event, status='PENDING')
        if not requests.exists():
            return "No pending HD requests found."
            
        # 1. Retrieve the photographer's Google Drive credentials
        photographer_cred = GoogleDriveCredential.objects.filter(user=event.photographer).first()
        if not photographer_cred:
            logger.error(f"Photographer {event.photographer.username} Google Drive credential missing. Cannot process HD requests.")
            return "Photographer Google Drive credentials missing."

        creds = Credentials.from_authorized_user_info(photographer_cred.token)
        service = build('drive', 'v3', credentials=creds)

        # 2. Download files in memory and build ZIP archive
        zip_buffer = io.BytesIO()
        compiled_count = 0
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
            for req in requests:
                photo = req.photo
                if not photo.google_drive_file_id:
                    continue
                
                # Fetch file metadata to get name
                try:
                    file_info = service.files().get(fileId=photo.google_drive_file_id, fields='name').execute()
                    file_name = file_info.get('name', f"photo_{photo.id}.jpg")
                except Exception:
                    file_name = f"photo_{photo.id}.jpg"
                
                # Download file contents
                try:
                    request_media = service.files().get_media(fileId=photo.google_drive_file_id)
                    fh = io.BytesIO()
                    downloader = MediaIoBaseDownload(fh, request_media)
                    done = False
                    while done is False:
                        _, done = downloader.next_chunk()
                    
                    # Write to ZIP
                    zip_file.writestr(file_name, fh.getvalue())
                    compiled_count += 1
                except Exception as dl_err:
                    logger.error(f"Error downloading HD file {photo.google_drive_file_id}: {str(dl_err)}")

        # Check if ZIP has files
        zip_buffer.seek(0)
        zip_data = zip_buffer.getvalue()
        if not zip_data or compiled_count == 0:
            logger.error("No HD images successfully compiled into the ZIP.")
            return "No images compiled into the ZIP."

        # 3. Construct and send EmailMessage based on attachment size
        subject = f"Your HD Photos from {event.name}"
        zip_filename = f"{event.name.replace(' ', '_')}_HD_Photos.zip"
        
        # 25GB threshold for email attachments
        threshold = 25 * 1024 * 1024 * 1024
        
        if len(zip_data) < threshold:
            body = (
                f"Hello {user.username},\n\n"
                f"Attached is the high-resolution ZIP archive containing the {compiled_count} HD wedding photos "
                f"you requested from the event '{event.name}'.\n\n"
                f"Enjoy your memories!\n\n"
                f"Best regards,\n"
                f"EventLens AI Delivery Team\n"
            )
            email = EmailMessage(
                subject=subject,
                body=body,
                from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', 'no-reply@eventlens.local'),
                to=[user.email]
            )
            email.attach(zip_filename, zip_data, 'application/zip')
            email.send(fail_silently=False)
        else:
            import uuid
            
            # Make sure directory exists
            downloads_dir = os.path.join(settings.MEDIA_ROOT, 'hd_downloads')
            os.makedirs(downloads_dir, exist_ok=True)
            
            # Generate a secure unique filename
            unique_id = uuid.uuid4().hex
            safe_event_name = event.name.replace(' ', '_')
            unique_filename = f"{safe_event_name}_{unique_id}.zip"
            file_path = os.path.join(downloads_dir, unique_filename)
            
            # Write zip file
            with open(file_path, 'wb') as f:
                f.write(zip_data)
                
            # Construct download URL
            if not base_url:
                base_url = "http://localhost:8000" # Fallback
            download_url = f"{base_url}{settings.MEDIA_URL}hd_downloads/{unique_filename}"
            
            body = (
                f"Hello {user.username},\n\n"
                f"The ZIP archive containing the {compiled_count} HD wedding photos you requested from the event '{event.name}' "
                f"exceeded email attachment size limits.\n\n"
                f"You can download your high-resolution photos using the following secure link:\n"
                f"{download_url}\n\n"
                f"Enjoy your memories!\n\n"
                f"Best regards,\n"
                f"EventLens AI Delivery Team\n"
            )
            email = EmailMessage(
                subject=subject,
                body=body,
                from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', 'no-reply@eventlens.local'),
                to=[user.email]
            )
            email.send(fail_silently=False)
            
        # Mark all as SENT
        requests.update(status='SENT')
        return f"Successfully sent HD ZIP archive containing {compiled_count} photos to {user.email}."
        
    except Exception as e:
        logger.error(f"Failed to execute send_hd_requests_email_task: {str(e)}")
        return f"Failed to send HD email: {str(e)}"
