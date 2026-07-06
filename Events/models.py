from django.db import models
from django.contrib.auth.models import User
from django.utils.text import slugify

class Event(models.Model):
    photographer = models.ForeignKey(User, on_delete=models.CASCADE, related_name='events')
    name = models.CharField(max_length=200)
    date = models.DateField()
    passcode = models.CharField(max_length=50, blank=True, null=True)
    slug = models.SlugField(unique=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(f"{self.name}-{self.date}")
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name
