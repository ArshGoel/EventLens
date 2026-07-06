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
    if request.method == 'POST':
        username = request.POST['username']
        email = request.POST['email']
        password = request.POST['password']
        role = request.POST.get('role', 'guest') # 'photographer' or 'guest'

        if User.objects.filter(username=username).exists():
            messages.error(request, "Username already exists.")
            return render(request, 'register.html')

        user = User.objects.create_user(username=username, email=email, password=password)
        is_photographer = (role == 'photographer')
        
        # Create UserProfile
        UserProfile.objects.create(
            user=user,
            is_photographer=is_photographer,
            is_guest=not is_photographer
        )

        login(request, user)
        if is_photographer:
            return redirect('photographer_dashboard')
        return redirect('home')

    return render(request, 'register.html')


def login_view(request):
    if request.method == 'POST':
        username = request.POST['username']
        ctx_pass = request.POST['password']
        user = authenticate(request, username=username, password=ctx_pass)
        if user is not None:
            login(request, user)
            try:
                if user.profile.is_photographer:
                    return redirect('photographer_dashboard')
            except UserProfile.DoesNotExist:
                pass
            return redirect('home')
        else:
            messages.error(request, "Invalid username or password.")
    return render(request, 'login.html')


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
        'google_drive_connected': google_drive_connected
    })



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
        photo_ids = []
        for file in files:
            photo = Photo.objects.create(event=event, image=file)
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
    has_selfie = bool(profile.selfie)

    return render(request, 'guest_event_portal.html', {
        'event': event,
        'has_selfie': has_selfie,
        'selfie_url': profile.selfie.url if has_selfie else None,
        'matched_photos': matched_photos,
    })


@login_required
def upload_selfie(request, slug):
    event = get_object_or_death(Event, slug=slug)
    if request.method == 'POST' and request.FILES.get('selfie'):
        profile = request.user.profile
        profile.selfie = request.FILES['selfie']
        profile.selfie_embedding = None # Reset old embedding so it gets re-calculated
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
