from django.contrib import admin

from Photos.models import Photo

@admin.register(Photo)
class PhotoAdmin(admin.ModelAdmin):
    list_display = ('id', 'event', 'collection', 'uploaded_at')
    list_filter = ('event', 'collection')

