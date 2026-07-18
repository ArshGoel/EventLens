import json
from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.models import User
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, Http404
from django.contrib import messages
from django.utils.text import slugify

from Accounts.models import UserProfile
from Events.models import Event
from Photos.models import Photo
from FaceEngine.models import GuestMatch
from FaceEngine.tasks import process_photo_faces_task, match_guest_selfie_task

def get_object_or_death(klass, *args, **kwargs):
    # Safe custom helper to avoid using get_object_or_404 if not imported
    try:
        return klass.objects.get(*args, **kwargs)
    except klass.DoesNotExist:
        raise Http404("Object not found.")


def home(request):
    if request.user.is_authenticated:
        try:
            profile = request.user.profile
            if profile.is_photographer:
                return redirect('photographer_dashboard')
        except UserProfile.DoesNotExist:
            pass
    return render(request, 'home.html')


def register_view(request):
    next_url = request.GET.get('next') or request.POST.get('next') or ''
    if request.method == 'POST':
        username = request.POST['username']
        email = request.POST['email']
        password = request.POST['password']
        role = request.POST.get('role', 'guest') # 'photographer' or 'guest'

        if User.objects.filter(username=username).exists():
            messages.error(request, "Username already exists.")
            return render(request, 'register.html', {'next': next_url})

        user = User.objects.create_user(username=username, email=email, password=password)
        is_photographer = (role == 'photographer')
        
        # Create UserProfile
        UserProfile.objects.create(
            user=user,
            is_photographer=is_photographer,
            is_guest=not is_photographer
        )

        login(request, user)
        if next_url:
            return redirect(next_url)
        if is_photographer:
            return redirect('photographer_dashboard')
        return redirect('home')

    return render(request, 'register.html', {'next': next_url})


def login_view(request):
    next_url = request.GET.get('next') or request.POST.get('next') or ''
    if request.method == 'POST':
        username = request.POST['username']
        ctx_pass = request.POST['password']
        user = authenticate(request, username=username, password=ctx_pass)
        if user is not None:
            login(request, user)
            if next_url:
                return redirect(next_url)
            try:
                if user.profile.is_photographer:
                    return redirect('photographer_dashboard')
            except UserProfile.DoesNotExist:
                pass
            return redirect('home')
        else:
            messages.error(request, "Invalid username or password.")
    return render(request, 'login.html', {'next': next_url})


def logout_view(request):
    logout(request)
    return redirect('home')


@login_required
def photographer_dashboard(request):
    try:
        profile = request.user.profile
        if not profile.is_photographer:
            return redirect('home')
    except UserProfile.DoesNotExist:
        return redirect('home')

    if request.method == 'POST':
        name = request.POST['name']
        date = request.POST['date']
        passcode = request.POST.get('passcode', '')

        # Create Event
        Event.objects.create(
            photographer=request.user,
            name=name,
            date=date,
            passcode=passcode
        )
        messages.success(request, f"Event '{name}' created successfully!")
        return redirect('photographer_dashboard')

    from Accounts.models import GoogleDriveCredential
    google_drive_connected = GoogleDriveCredential.objects.filter(user=request.user).exists()
    events = Event.objects.filter(photographer=request.user).order_by('-created_at')
    return render(request, 'photographer_dashboard.html', {
        'events': events,
        'google_drive_connected': google_drive_connected,
        'profile': profile
    })


@login_required
def update_profile_view(request):
    try:
        profile = request.user.profile
        if not profile.is_photographer:
            return redirect('home')
    except UserProfile.DoesNotExist:
        return redirect('home')

    if request.method == 'POST':
        business_name = request.POST.get('business_name', '').strip()
        whatsapp = request.POST.get('whatsapp', '').strip()
        instagram = request.POST.get('instagram', '').strip()
        logo_file = request.FILES.get('logo')

        profile.business_name = business_name
        profile.whatsapp = whatsapp
        profile.instagram = instagram

        if logo_file:
            import io
            import uuid
            from PIL import Image
            from django.core.files.base import ContentFile
            from django.conf import settings

            try:
                original_bytes = logo_file.read()
                img_pil = Image.open(io.BytesIO(original_bytes))
                if img_pil.mode in ("RGBA", "P"):
                    img_pil = img_pil.convert("RGBA")
                img_pil.thumbnail((400, 400))
                
                out_io = io.BytesIO()
                img_pil.save(out_io, format='PNG')
                compressed_bytes = out_io.getvalue()
            except Exception as compress_err:
                messages.error(request, f"Failed to process logo: {str(compress_err)}")
                return redirect('photographer_dashboard')

            cloudinary_url = None
            if settings.CLOUDINARY_CLOUD_NAME and settings.CLOUDINARY_API_KEY:
                try:
                    import cloudinary
                    import cloudinary.uploader
                    cloudinary.config(
                        cloud_name=settings.CLOUDINARY_CLOUD_NAME,
                        api_key=settings.CLOUDINARY_API_KEY,
                        api_secret=settings.CLOUDINARY_API_SECRET,
                        secure=True
                    )
                    
                    if profile.logo_url and 'res.cloudinary.com' in profile.logo_url:
                        try:
                            old_public_id = profile.logo_url.split('/upload/')[1].split('/', 1)[1].rsplit('.', 1)[0]
                            cloudinary.uploader.destroy(old_public_id)
                        except Exception:
                            pass
                    
                    file_uuid = uuid.uuid4().hex
                    upload_res = cloudinary.uploader.upload(
                        io.BytesIO(compressed_bytes),
                        folder="eventlens/logos",
                        public_id=f"logo_{request.user.id}_{file_uuid}"
                    )
                    cloudinary_url = upload_res.get('secure_url')
                except Exception as cloud_err:
                    pass

            if cloudinary_url:
                profile.logo_url = cloudinary_url
                if profile.logo:
                    try:
                        profile.logo.delete(save=False)
                    except Exception:
                        pass
                profile.logo = None
            else:
                file_name = f"logo_{request.user.id}.png"
                profile.logo.save(file_name, ContentFile(compressed_bytes), save=False)
                profile.logo_url = None

        profile.save()
        messages.success(request, "Branding and profile settings updated successfully!")
        
    return redirect('photographer_dashboard')



@login_required
def upload_photos(request, event_id):
    """
    Handles bulk photos upload via AJAX / Dropzone.
    """
    event = get_object_or_death(Event, id=event_id, photographer=request.user)

    files = []
    for key in request.FILES:
        files.extend(request.FILES.getlist(key))

    if request.method == 'POST' and files:
        import io
        import uuid
        from PIL import Image
        from django.core.files.base import ContentFile
        from django.conf import settings

        photo_ids = []
        for file in files:
            try:
                # Read original bytes
                original_bytes = file.read()
                
                # Compress & downscale image to 1200px at 80% quality in-memory
                img_pil = Image.open(io.BytesIO(original_bytes))
                if img_pil.mode in ("RGBA", "P"):
                    img_pil = img_pil.convert("RGB")
                img_pil.thumbnail((1200, 1200))
                
                out_io = io.BytesIO()
                img_pil.save(out_io, format='JPEG', quality=80)
                compressed_bytes = out_io.getvalue()
            except Exception as compress_err:
                # If compression fails, skip this file or handle it
                continue

            # Handle Cloudinary upload if configured
            cloudinary_url = None
            if settings.CLOUDINARY_CLOUD_NAME and settings.CLOUDINARY_API_KEY:
                try:
                    import cloudinary
                    import cloudinary.uploader
                    cloudinary.config(
                        cloud_name=settings.CLOUDINARY_CLOUD_NAME,
                        api_key=settings.CLOUDINARY_API_KEY,
                        api_secret=settings.CLOUDINARY_API_SECRET,
                        secure=True
                    )
                    
                    file_uuid = uuid.uuid4().hex
                    upload_res = cloudinary.uploader.upload(
                        io.BytesIO(compressed_bytes),
                        folder=f"eventlens/event_{event.id}",
                        public_id=f"photo_{file_uuid}"
                    )
                    cloudinary_url = upload_res.get('secure_url')
                except Exception as cloud_err:
                    pass

            photo = Photo(event=event)

            if cloudinary_url:
                photo.image_url = cloudinary_url
                photo.save()
            else:
                # Fallback to local storage (only if Cloudinary is not configured or fails)
                file_uuid = uuid.uuid4().hex
                file_name = f"photo_{file_uuid}.jpg"
                photo.image.save(file_name, ContentFile(compressed_bytes), save=False)
                photo.image_url = photo.image.url
                photo.save()

            # Queue face detection background job
            process_photo_faces_task.delay(photo.id)
            photo_ids.append(photo.id)

        return JsonResponse({
            'status': 'success',
            'message': f'{len(photo_ids)} photos uploaded and queueing face detection.'
        })

    return JsonResponse({'status': 'error', 'message': 'Invalid request.'}, status=400)


@login_required
def guest_portal(request, slug):
    event = get_object_or_death(Event, slug=slug)
    
    # Passcode validation
    session_key = f"passcode_verified_{event.id}"
    if event.passcode and not request.session.get(session_key):
        if request.method == 'POST' and 'passcode' in request.POST:
            entered = request.POST['passcode']
            if entered == event.passcode:
                request.session[session_key] = True
                return redirect('guest_portal', slug=slug)
            else:
                messages.error(request, "Incorrect passcode.")
        return render(request, 'guest_passcode.html', {'event': event})

    profile = request.user.profile
    
    # Get matched photos
    matches = GuestMatch.objects.filter(guest=request.user, photo__event=event).select_related('photo')
    matched_photos = [match.photo for match in matches]

    # Check if they have uploaded a selfie
    has_selfie = bool(profile.selfie or profile.selfie_url)
    selfie_url = profile.selfie_url if profile.selfie_url else (profile.selfie.url if profile.selfie else None)

    return render(request, 'guest_event_portal.html', {
        'event': event,
        'has_selfie': has_selfie,
        'selfie_url': selfie_url,
        'matched_photos': matched_photos,
    })


@login_required
def upload_selfie(request, slug):
    event = get_object_or_death(Event, slug=slug)
    if request.method == 'POST' and request.FILES.get('selfie'):
        import io
        import uuid
        from PIL import Image
        from django.core.files.base import ContentFile
        from django.conf import settings

        selfie_file = request.FILES['selfie']
        profile = request.user.profile

        try:
            # Read original bytes
            original_bytes = selfie_file.read()
            
            # Compress & downscale image to 1200px at 80% quality in-memory
            img_pil = Image.open(io.BytesIO(original_bytes))
            if img_pil.mode in ("RGBA", "P"):
                img_pil = img_pil.convert("RGB")
            img_pil.thumbnail((1200, 1200))
            
            out_io = io.BytesIO()
            img_pil.save(out_io, format='JPEG', quality=80)
            compressed_bytes = out_io.getvalue()
        except Exception as compress_err:
            return JsonResponse({'status': 'error', 'message': f'Failed to process image: {str(compress_err)}'}, status=400)

        # Handle Cloudinary upload if configured
        cloudinary_url = None
        if settings.CLOUDINARY_CLOUD_NAME and settings.CLOUDINARY_API_KEY:
            try:
                import cloudinary
                import cloudinary.uploader
                cloudinary.config(
                    cloud_name=settings.CLOUDINARY_CLOUD_NAME,
                    api_key=settings.CLOUDINARY_API_KEY,
                    api_secret=settings.CLOUDINARY_API_SECRET,
                    secure=True
                )
                
                # Delete old selfie from Cloudinary if it exists
                if profile.selfie_url and 'res.cloudinary.com' in profile.selfie_url:
                    try:
                        old_public_id = profile.selfie_url.split('/upload/')[1].split('/', 1)[1].rsplit('.', 1)[0]
                        cloudinary.uploader.destroy(old_public_id)
                    except Exception:
                        pass
                
                file_uuid = uuid.uuid4().hex
                upload_res = cloudinary.uploader.upload(
                    io.BytesIO(compressed_bytes),
                    folder="eventlens/selfies",
                    public_id=f"selfie_{request.user.id}_{file_uuid}"
                )
                cloudinary_url = upload_res.get('secure_url')
            except Exception as cloud_err:
                # If Cloudinary fails, log it and we'll fall back to local storage
                pass

        profile.selfie_embedding = None # Reset old embedding so it gets re-calculated

        if cloudinary_url:
            profile.selfie_url = cloudinary_url
            # Delete old local selfie file if it exists to save space
            if profile.selfie:
                try:
                    profile.selfie.delete(save=False)
                except Exception:
                    pass
            profile.selfie = None
        else:
            # Fallback to local storage (only if Cloudinary is not configured or fails)
            file_name = f"selfie_{request.user.id}.jpg"
            profile.selfie.save(file_name, ContentFile(compressed_bytes), save=False)
            profile.selfie_url = None

        profile.save()

        # Delete old matches for this event to run a fresh scan
        GuestMatch.objects.filter(guest=request.user, photo__event=event).delete()

        # Queue matching Celery task
        match_guest_selfie_task.delay(request.user.id, event.id)

        return JsonResponse({
            'status': 'success',
            'message': 'Selfie uploaded. Real-time matching task started.'
        })

    return JsonResponse({'status': 'error', 'message': 'Invalid request.'}, status=400)

@login_required
def request_hd_photo_bulk(request, slug):
    """
    Handles requesting HD download links for multiple photos.
    Saves HDRequest entries and runs email delivery task.
    """
    from FaceEngine.models import HDRequest
    from FaceEngine.tasks import send_hd_requests_email_task
    
    event = get_object_or_death(Event, slug=slug)
    
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            photo_ids = data.get('photo_ids', [])
        except json.JSONDecodeError:
            return JsonResponse({'status': 'error', 'message': 'Invalid JSON.'}, status=400)
            
        if not photo_ids:
            return JsonResponse({'status': 'error', 'message': 'No photos selected.'}, status=400)
            
        # Get valid photos in this event
        photos = Photo.objects.filter(id__in=photo_ids, event=event)
        
        requests_created = 0
        for photo in photos:
            # Save request as PENDING
            HDRequest.objects.update_or_create(
                event=event,
                guest=request.user,
                photo=photo,
                defaults={'status': 'PENDING'}
            )
            requests_created += 1
            
        if requests_created > 0:
            # Trigger celery background task to send the email!
            base_url = request.build_absolute_uri('/')[:-1]
            send_hd_requests_email_task.delay(request.user.id, event.id, base_url)
            
        return JsonResponse({
            'status': 'success',
            'message': f'Your request for {requests_created} HD photos has been received. Download links will be sent to your email ({request.user.email}) by end of day!'
        })
        
    return JsonResponse({'status': 'error', 'message': 'Invalid request method.'}, status=405)

@login_required
def delete_event(request, event_id):
    """
    Deletes the event and its associated photos.
    """
    from django.conf import settings
    event = get_object_or_death(Event, id=event_id, photographer=request.user)
    if request.method == 'POST':
        # Delete photos first to trigger cleanup
        photos = Photo.objects.filter(event=event)
        for photo in photos:
            # Delete local file if it exists
            if photo.image:
                try:
                    photo.image.delete(save=False)
                except Exception:
                    pass
            # If Cloudinary is connected and photo has image_url, delete it from Cloudinary
            if photo.image_url and 'res.cloudinary.com' in photo.image_url:
                try:
                    import cloudinary
                    import cloudinary.uploader
                    cloudinary.config(
                        cloud_name=settings.CLOUDINARY_CLOUD_NAME,
                        api_key=settings.CLOUDINARY_API_KEY,
                        api_secret=settings.CLOUDINARY_API_SECRET,
                        secure=True
                    )
                    # Extract public_id
                    public_id = photo.image_url.split('/upload/')[1].split('/', 1)[1].rsplit('.', 1)[0]
                    cloudinary.uploader.destroy(public_id)
                except Exception:
                    pass
        
        event.delete()
        return JsonResponse({'status': 'success', 'message': 'Event deleted successfully.'})
    return JsonResponse({'status': 'error', 'message': 'Invalid request method.'}, status=405)

@login_required
def edit_event(request, event_id):
    """
    Edits event name, date, and passcode.
    """
    event = get_object_or_death(Event, id=event_id, photographer=request.user)
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            name = data.get('name')
            date = data.get('date')
            passcode = data.get('passcode', '')
        except Exception:
            name = request.POST.get('name')
            date = request.POST.get('date')
            passcode = request.POST.get('passcode', '')

        if not name or not date:
            return JsonResponse({'status': 'error', 'message': 'Name and Date are required.'}, status=400)

        event.name = name
        event.date = date
        event.passcode = passcode
        event.save()
        return JsonResponse({
            'status': 'success', 
            'message': 'Event updated successfully.',
            'event': {
                'name': event.name,
                'date': str(event.date),
                'passcode': event.passcode
            }
        })
    return JsonResponse({'status': 'error', 'message': 'Invalid request method.'}, status=405)

@login_required
def list_event_photos(request, event_id):
    """
    Lists all photos associated with an event.
    """
    event = get_object_or_death(Event, id=event_id, photographer=request.user)
    photos = Photo.objects.filter(event=event).order_by('-uploaded_at')
    
    photo_list = []
    for photo in photos:
        photo_list.append({
            'id': photo.id,
            'preview_url': photo.preview_url,
            'uploaded_at': photo.uploaded_at.strftime('%Y-%m-%d %H:%M')
        })
        
    return JsonResponse({
        'status': 'success',
        'photos': photo_list
    })

@login_required
def delete_photo(request, photo_id):
    """
    Deletes a single photo.
    """
    from django.conf import settings
    photo = get_object_or_death(Photo, id=photo_id, event__photographer=request.user)
    if request.method == 'POST':
        # Delete local file
        if photo.image:
            try:
                photo.image.delete(save=False)
            except Exception:
                pass
        # Delete from Cloudinary if applicable
        if photo.image_url and 'res.cloudinary.com' in photo.image_url:
            try:
                import cloudinary
                import cloudinary.uploader
                cloudinary.config(
                    cloud_name=settings.CLOUDINARY_CLOUD_NAME,
                    api_key=settings.CLOUDINARY_API_KEY,
                    api_secret=settings.CLOUDINARY_API_SECRET,
                    secure=True
                )
                public_id = photo.image_url.split('/upload/')[1].split('/', 1)[1].rsplit('.', 1)[0]
                cloudinary.uploader.destroy(public_id)
            except Exception:
                pass
                
        photo.delete()
        return JsonResponse({'status': 'success', 'message': 'Photo deleted successfully.'})
    return JsonResponse({'status': 'error', 'message': 'Invalid request method.'}, status=405)


def scan_qr_view(request):
    """
    Renders the QR code scanner page for guests.
    """
    return render(request, 'scan_qr.html')

