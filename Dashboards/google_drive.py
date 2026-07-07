import os
import json
from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, HttpResponseBadRequest
from django.conf import settings
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from Accounts.models import GoogleDriveCredential
from Events.models import Event
from Photos.tasks import import_photos_from_drive_task

SCOPES = ['https://www.googleapis.com/auth/drive.readonly']

def get_google_client_config():
    if not settings.GOOGLE_DRIVE_CLIENT_ID or not settings.GOOGLE_DRIVE_CLIENT_SECRET:
        raise ValueError("Google Drive Client ID or Secret is not configured in settings/env.")
    
    return {
        "web": {
            "client_id": settings.GOOGLE_DRIVE_CLIENT_ID,
            "client_secret": settings.GOOGLE_DRIVE_CLIENT_SECRET,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
            "redirect_uris": [settings.GOOGLE_DRIVE_REDIRECT_URI],
        }
    }

@login_required
def google_drive_auth_init(request):
    try:
        client_config = get_google_client_config()
    except ValueError as e:
        return HttpResponseBadRequest(str(e))
        
    flow = Flow.from_client_config(
        client_config,
        scopes=SCOPES,
        redirect_uri=settings.GOOGLE_DRIVE_REDIRECT_URI
    )
    
    # Enable offline access and prompt consent to receive a refresh token
    authorization_url, state = flow.authorization_url(
        access_type='offline',
        include_granted_scopes='true',
        prompt='consent'
    )
    
    request.session['google_oauth_state'] = state
    request.session['google_oauth_code_verifier'] = flow.code_verifier
    return redirect(authorization_url)

@login_required
def google_drive_auth_callback(request):
    state = request.session.get('google_oauth_state')
    if not state or state != request.GET.get('state'):
        return HttpResponseBadRequest("State mismatch or session expired.")
    
    try:
        client_config = get_google_client_config()
    except ValueError as e:
        return HttpResponseBadRequest(str(e))

    flow = Flow.from_client_config(
        client_config,
        scopes=SCOPES,
        redirect_uri=settings.GOOGLE_DRIVE_REDIRECT_URI,
        state=state
    )
    
    # Restore the PKCE code verifier from session
    flow.code_verifier = request.session.get('google_oauth_code_verifier')
    
    # Clean session state
    if 'google_oauth_state' in request.session:
        del request.session['google_oauth_state']
    if 'google_oauth_code_verifier' in request.session:
        del request.session['google_oauth_code_verifier']
        
    # Build full redirect path (including parameters)
    authorization_response = request.build_absolute_uri()
    
    # google-auth-oauthlib expects https. If local/HTTP, we instruct it via env variable
    if not request.is_secure():
        os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'

    flow.fetch_token(authorization_response=authorization_response)
    credentials = flow.credentials
    
    # Save/Update credentials in database
    token_data = {
        'token': credentials.token,
        'refresh_token': credentials.refresh_token,
        'token_uri': credentials.token_uri,
        'client_id': credentials.client_id,
        'client_secret': credentials.client_secret,
        'scopes': credentials.scopes
    }
    
    GoogleDriveCredential.objects.update_or_create(
        user=request.user,
        defaults={'token': token_data}
    )
    
    return redirect('photographer_dashboard')

@login_required
def google_drive_disconnect(request):
    if request.method == 'POST':
        GoogleDriveCredential.objects.filter(user=request.user).delete()
        return redirect('photographer_dashboard')
    return HttpResponseBadRequest("Invalid request method.")

@login_required
def google_drive_list_folders(request):
    try:
        cred_obj = GoogleDriveCredential.objects.get(user=request.user)
    except GoogleDriveCredential.DoesNotExist:
        return JsonResponse({'status': 'error', 'message': 'Google Drive not connected.'}, status=401)
        
    try:
        creds = Credentials(
            token=cred_obj.token.get('token'),
            refresh_token=cred_obj.token.get('refresh_token'),
            token_uri=cred_obj.token.get('token_uri'),
            client_id=cred_obj.token.get('client_id'),
            client_secret=cred_obj.token.get('client_secret'),
            scopes=cred_obj.token.get('scopes')
        )
        
        # Build Drive Service
        service = build('drive', 'v3', credentials=creds)
        
        # List folders (not trashed)
        results = service.files().list(
            q="mimeType='application/vnd.google-apps.folder' and trashed=false",
            spaces='drive',
            fields="nextPageToken, files(id, name, parents)",
            pageSize=100
        ).execute()
        
        folders = results.get('files', [])
        return JsonResponse({'status': 'success', 'folders': folders})
    except Exception as e:
        # Check if the token was refreshed and update it if so
        try:
            if creds and creds.valid is False and creds.refresh_token:
                from google.auth.transport.requests import Request
                creds.refresh(Request())
                cred_obj.token['token'] = creds.token
                cred_obj.save()
        except Exception:
            pass
        return JsonResponse({'status': 'error', 'message': str(e)}, status=500)

@login_required
def google_drive_import_photos(request):
    if request.method != 'POST':
        return JsonResponse({'status': 'error', 'message': 'Invalid request method.'}, status=405)
        
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'status': 'error', 'message': 'Invalid JSON body.'}, status=400)
        
    event_id = data.get('event_id')
    folder_id = data.get('folder_id')
    
    if not event_id or not folder_id:
        return JsonResponse({'status': 'error', 'message': 'Missing event_id or folder_id.'}, status=400)
        
    # Check that event exists and belongs to the photographer
    try:
        event = Event.objects.get(id=event_id, photographer=request.user)
    except Event.DoesNotExist:
        return JsonResponse({'status': 'error', 'message': 'Event not found.'}, status=404)
        
    # Check that credentials exist
    if not GoogleDriveCredential.objects.filter(user=request.user).exists():
        return JsonResponse({'status': 'error', 'message': 'Google Drive not connected.'}, status=401)
        
    # Queue Celery task
    import_photos_from_drive_task.delay(request.user.id, event.id, folder_id)
    
    return JsonResponse({
        'status': 'success',
        'message': 'Google Drive photos import task successfully queued in the background!'
    })
