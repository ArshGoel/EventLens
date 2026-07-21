import os
from pathlib import Path
from django.core.management.base import BaseCommand
from django.core.management import call_command
from django.conf import settings

class Command(BaseCommand):
    help = 'Completely resets the database, local media files, and Cloudinary storage for production & development.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--no-input',
            action='store_true',
            help='Do NOT prompt for confirmation before resetting.'
        )

    def handle(self, *args, **options):
        no_input = options.get('no_input')
        
        if not no_input:
            self.stdout.write(self.style.WARNING(
                "\nWARNING: This will PERMANENTLY delete all database records, local media files, and Cloudinary assets!"
            ))
            confirm = input("Type 'yes' to confirm complete project reset: ")
            if confirm.lower() != 'yes':
                self.stdout.write(self.style.NOTICE("Project reset cancelled."))
                return

        # 1. Cloudinary Purge
        self.stdout.write(self.style.NOTICE("\n1. Purging Cloudinary storage..."))
        if settings.CLOUDINARY_CLOUD_NAME and settings.CLOUDINARY_API_KEY:
            try:
                import cloudinary
                import cloudinary.api
                cloudinary.config(
                    cloud_name=settings.CLOUDINARY_CLOUD_NAME,
                    api_key=settings.CLOUDINARY_API_KEY,
                    api_secret=settings.CLOUDINARY_API_SECRET,
                    secure=True
                )
                cloudinary.api.delete_resources_by_prefix('eventlens/')
                self.stdout.write(self.style.SUCCESS("  [OK] Cloudinary resources under 'eventlens/' purged."))
            except Exception as e:
                self.stdout.write(self.style.WARNING(f"  [WARN] Cloudinary purge notice: {str(e)}"))
        else:
            self.stdout.write(self.style.NOTICE("  - Cloudinary credentials not configured. Skipping."))

        # 2. Database Flush
        self.stdout.write(self.style.NOTICE("\n2. Flushing database tables..."))
        try:
            call_command('flush', interactive=False)
            self.stdout.write(self.style.SUCCESS("  [OK] Database tables flushed successfully."))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"  [ERR] Database flush error: {str(e)}"))

        # 3. Media Directory Clean
        self.stdout.write(self.style.NOTICE("\n3. Clearing local media directories..."))
        media_root = Path(settings.MEDIA_ROOT)
        for subfolder in ['photos', 'selfies', 'logos']:
            folder = media_root / subfolder
            if folder.exists():
                for file_item in folder.iterdir():
                    if file_item.is_file():
                        try:
                            file_item.unlink()
                        except Exception:
                            pass
        self.stdout.write(self.style.SUCCESS("  [OK] Local media files cleared."))

        self.stdout.write(self.style.SUCCESS("\n[SUCCESS] Complete project reset finished successfully!\n"))
