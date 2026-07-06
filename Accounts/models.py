from django.db import models
from django.contrib.auth.models import User

class UserProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    is_photographer = models.BooleanField(default=False)
    is_guest = models.BooleanField(default=True)
    selfie = models.ImageField(upload_to='selfies/', null=True, blank=True)
    selfie_embedding = models.JSONField(null=True, blank=True) # 512 float list

    def __str__(self):
        return f"{self.user.username}'s Profile"


class GoogleDriveCredential(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='google_drive_credential')
    token = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Google Drive Credential for {self.user.username}"

