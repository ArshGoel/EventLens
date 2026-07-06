from django.db import models
from Photos.models import Photo
from django.contrib.auth.models import User

class DetectedFace(models.Model):
    photo = models.ForeignKey(Photo, on_delete=models.CASCADE, related_name='detected_faces')
    bbox = models.JSONField() # [x1, y1, x2, y2]
    embedding = models.JSONField() # 512-dimension float list

    def __str__(self):
        return f"Face in Photo {self.photo.id}"

class GuestMatch(models.Model):
    guest = models.ForeignKey(User, on_delete=models.CASCADE, related_name='matches')
    photo = models.ForeignKey(Photo, on_delete=models.CASCADE, related_name='matches')
    similarity = models.FloatField()
    matched_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('guest', 'photo')
