from django.db import models
from django.contrib.auth.models import User

class UserProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    is_photographer = models.BooleanField(default=False)
    is_guest = models.BooleanField(default=True)
    selfie = models.ImageField(upload_to='selfies/', null=True, blank=True)
    selfie_url = models.URLField(max_length=500, null=True, blank=True)
    selfie_embedding = models.JSONField(null=True, blank=True) # 512 float list

    # Photographer branding fields
    business_name = models.CharField(max_length=255, null=True, blank=True)
    logo = models.ImageField(upload_to='logos/', null=True, blank=True)
    logo_url = models.URLField(max_length=500, null=True, blank=True)
    whatsapp = models.CharField(max_length=20, null=True, blank=True)
    instagram = models.CharField(max_length=100, null=True, blank=True)

    def __str__(self):
        return f"{self.user.username}'s Profile"


class GoogleDriveCredential(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='google_drive_credential')
    token = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Google Drive Credential for {self.user.username}"

