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

# Lazy loader for InsightFace FaceAnalysis to avoid overhead on Celery load
_face_app = None

def get_face_app():
    global _face_app
    if _face_app is None:
        import insightface
        from insightface.app import FaceAnalysis
        _face_app = FaceAnalysis(name='buffalo_l')
        # ctx_id=-1 forces CPU execution. If you have CUDA setup, change to 0
        _face_app.prepare(ctx_id=-1, det_size=(640, 640))
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

    img_path = photo.image.path
    if not os.path.exists(img_path):
        return f"Image file not found at {img_path}"

    img = cv2.imread(img_path)
    if img is None:
        return f"Failed to read image at {img_path}"

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
