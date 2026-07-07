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
def send_hd_requests_email_task(user_id, event_id):
    """
    Finds all PENDING HD requests for this user in this event,
    and sends them an email listing the Google Drive links.
    """
    from django.core.mail import send_mail
    from django.contrib.auth.models import User
    from Events.models import Event
    from .models import HDRequest
    
    try:
        user = User.objects.get(id=user_id)
        event = Event.objects.get(id=event_id)
        
        # Get all pending requests
        requests = HDRequest.objects.filter(guest=user, event=event, status='PENDING')
        if not requests.exists():
            return "No pending HD requests found."
            
        # Build the email body
        email_body = f"Hello {user.username},\n\n"
        email_body += f"Here are the download links for the high-resolution photos you requested from the event '{event.name}':\n\n"
        
        count = 1
        for req in requests:
            photo = req.photo
            if photo.google_drive_file_id:
                link = f"https://drive.google.com/uc?export=download&id={photo.google_drive_file_id}"
                email_body += f"Photo {count}: {link}\n"
                count += 1
                
        email_body += "\nEnjoy your photos!\nBest regards,\nEventLens AI Delivery Team\n"
        
        # Send Email
        send_mail(
            subject=f"Your HD Photos from {event.name}",
            message=email_body,
            from_email="no-reply@eventlens.local",
            recipient_list=[user.email],
            fail_silently=False,
        )
        
        # Mark as SENT
        requests.update(status='SENT')
        return f"Successfully sent HD email containing {count-1} links to {user.email}."
        
    except Exception as e:
        return f"Failed to send HD email: {str(e)}"
