import io
import os
import re
import urllib.request
import logging
from PIL import Image, ImageOps, ImageDraw, ImageFont
from django.conf import settings

logger = logging.getLogger(__name__)

def create_text_branding_badge(brand_name, size=220):
    """
    Generates a 1:1 square branding badge containing the photographer's brand name or initials.
    """
    img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    pad = 8
    draw.rounded_rectangle(
        [pad, pad, size - pad, size - pad],
        radius=36,
        fill=(15, 23, 42, 210), # Dark glassmorphic background
        outline=(99, 102, 241, 240), # Glowing indigo border
        width=5
    )

    name = (brand_name or "EVENTLENS").strip()
    words = name.split()
    if len(words) >= 2:
        display_text = (words[0][0] + words[1][0]).upper()
    else:
        display_text = name[:6].upper()

    try:
        font = ImageFont.truetype("arial.ttf", int(size * 0.36))
    except Exception:
        font = ImageFont.load_default()

    bbox = draw.textbbox((0, 0), display_text, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    x = (size - tw) / 2
    y = (size - th) / 2 - bbox[1]

    draw.text((x, y), display_text, fill=(255, 255, 255, 245), font=font)
    return img


def get_photographer_logo_image(photographer_profile):
    """
    Safely retrieves or generates a 1:1 branding logo for the photographer.
    """
    if not photographer_profile:
        return None

    # 1. Try local FieldFile profile.logo
    if photographer_profile.logo and hasattr(photographer_profile.logo, 'name') and bool(photographer_profile.logo.name):
        try:
            if hasattr(photographer_profile.logo, 'path') and os.path.exists(photographer_profile.logo.path):
                return Image.open(photographer_profile.logo.path)
            elif hasattr(photographer_profile.logo, 'storage'):
                with photographer_profile.logo.storage.open(photographer_profile.logo.name, 'rb') as f:
                    return Image.open(io.BytesIO(f.read()))
        except Exception as e:
            logger.warning(f"Error reading profile.logo: {str(e)}")

    # 2. Try profile.logo_url
    logo_url = photographer_profile.logo_url
    if logo_url:
        if logo_url.startswith('http://') or logo_url.startswith('https://'):
            try:
                req = urllib.request.Request(logo_url, headers={'User-Agent': 'Mozilla/5.0'})
                with urllib.request.urlopen(req, timeout=6) as resp:
                    return Image.open(io.BytesIO(resp.read()))
            except Exception as url_err:
                logger.warning(f"Error downloading logo_url ({logo_url}): {str(url_err)}")
        else:
            clean_rel = logo_url.lstrip('/')
            if clean_rel.startswith('media/'):
                clean_rel = clean_rel[6:]
            local_path = os.path.join(settings.MEDIA_ROOT, clean_rel)
            if os.path.exists(local_path):
                try:
                    return Image.open(local_path)
                except Exception:
                    pass

    # 3. Fallback: Generate 1:1 branding badge using photographer's business name or username
    brand_text = photographer_profile.business_name or photographer_profile.user.username or "EventLens"
    return create_text_branding_badge(brand_text)


def apply_branding_logo(img_pil, photographer_profile, logo_scale=0.08, margin_px=18):
    """
    Applies the photographer's 1:1 ratio branding logo or badge to the bottom-right corner.
    Logo scale reduced by 25% for a subtle, elegant watermark.
    """
    if not img_pil or not photographer_profile:
        return img_pil

    try:
        logo_image = get_photographer_logo_image(photographer_profile)
        if not logo_image:
            return img_pil

        main_w, main_h = img_pil.size

        min_dim = min(main_w, main_h)
        logo_side = int(min_dim * logo_scale)
        logo_side = max(35, min(logo_side, 180))

        # Crop / fit logo into exact 1:1 square ratio
        logo_square = ImageOps.fit(logo_image, (logo_side, logo_side), Image.Resampling.LANCZOS)
        if logo_square.mode != 'RGBA':
            logo_square = logo_square.convert('RGBA')

        base = img_pil.convert('RGBA')

        offset_x = main_w - logo_side - margin_px
        offset_y = main_h - logo_side - margin_px

        if offset_x < 0:
            offset_x = 0
        if offset_y < 0:
            offset_y = 0

        # Composite logo at bottom-right corner using alpha channel
        base.paste(logo_square, (offset_x, offset_y), logo_square)

        return base.convert('RGB')

    except Exception as err:
        logger.error(f"Error applying branding logo watermark: {str(err)}")
        return img_pil


def reprocess_photo_watermark(photo):
    """
    Re-applies 1:1 photographer branding watermark to an existing Photo object
    and updates its saved file & image_url.
    """
    if not photo or not photo.event:
        return False

    try:
        profile = photo.event.photographer.profile
    except Exception:
        return False

    try:
        from django.core.files.base import ContentFile
        
        image_bytes = None
        if photo.image and hasattr(photo.image, 'path') and os.path.exists(photo.image.path):
            try:
                with open(photo.image.path, 'rb') as f:
                    image_bytes = f.read()
            except Exception:
                pass

        if not image_bytes and photo.image and hasattr(photo.image, 'storage') and photo.image.name:
            try:
                with photo.image.storage.open(photo.image.name, 'rb') as f:
                    image_bytes = f.read()
            except Exception:
                pass

        if not image_bytes and photo.image_url:
            try:
                req = urllib.request.Request(photo.image_url, headers={'User-Agent': 'Mozilla/5.0'})
                with urllib.request.urlopen(req, timeout=6) as response:
                    image_bytes = response.read()
            except Exception:
                pass

        if not image_bytes:
            return False

        img_pil = Image.open(io.BytesIO(image_bytes))
        watermarked_pil = apply_branding_logo(img_pil, profile)

        out_io = io.BytesIO()
        watermarked_pil.save(out_io, format='JPEG', quality=80)
        new_bytes = out_io.getvalue()

        file_name = photo.image.name.split('/')[-1] if (photo.image and photo.image.name) else f"photo_{photo.id}.jpg"
        photo.image.save(file_name, ContentFile(new_bytes), save=False)
        photo.image_url = photo.image.url
        photo.save()
        return True
    except Exception as e:
        logger.error(f"Error reprocessing watermark for photo {photo.id}: {str(e)}")

    return False
