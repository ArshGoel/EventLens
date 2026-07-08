from django.db import models
from Events.models import Event

class Photo(models.Model):
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name='photos')
    image = models.ImageField(upload_to='photos/', blank=True, null=True)
    image_url = models.URLField(max_length=500, blank=True, null=True)
    google_drive_file_id = models.CharField(max_length=255, blank=True, null=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if self.image and not self.image_url:
            self.image_url = self.image.url
            super().save(update_fields=['image_url'])

    @property
    def preview_url(self):
        if self.image_url and 'res.cloudinary.com' in self.image_url:
            # Dynamically request downscaled and auto-compressed preview from Cloudinary
            return self.image_url.replace('/upload/', '/upload/w_1200,c_limit,q_auto/')
        return self.image_url

    @property
    def download_url(self):
        if self.image_url and 'res.cloudinary.com' in self.image_url:
            # Add fl_attachment to force download from Cloudinary
            return self.image_url.replace('/upload/', '/upload/fl_attachment/')
        return self.image_url

    def __str__(self):
        return f"Photo {self.id} - {self.event.name}"

