from django.contrib import admin

from Events.models import Event, Collection

@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = ('id', 'name', 'photographer', 'date', 'created_at')
    search_fields = ('name', 'photographer__username')

@admin.register(Collection)
class CollectionAdmin(admin.ModelAdmin):
    list_display = ('id', 'name', 'event', 'created_at')
    list_filter = ('event',)
    search_fields = ('name', 'event__name')

