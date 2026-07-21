import logging
from django.core.mail import send_mail
from django.conf import settings

logger = logging.getLogger(__name__)

def send_otp_email(recipient_email, otp_code):
    """
    Sends a 6-digit OTP verification code to the recipient's email address.
    Uses Django's send_mail configured via settings/environment variables.
    """
    subject = f"{otp_code} is your EventLens verification code"
    
    body = (
        f"Hello,\n\n"
        f"Your guest verification code for EventLens is: {otp_code}\n\n"
        f"This code is valid for 10 minutes.\n"
        f"If you did not request this code, please ignore this email.\n\n"
        f"Best regards,\n"
        f"EventLens Team"
    )

    from_email = getattr(settings, 'DEFAULT_FROM_EMAIL', None) or getattr(settings, 'EMAIL_HOST_USER', 'no-reply@eventlens.local')

    try:
        send_mail(
            subject=subject,
            message=body,
            from_email=from_email,
            recipient_list=[recipient_email],
            fail_silently=False,
        )
        logger.info(f"OTP email sent successfully to {recipient_email}")
        return True, f"OTP verification code sent to {recipient_email}."
    except Exception as e:
        err_msg = str(e)
        logger.error(f"Failed to send OTP email to {recipient_email}: {err_msg}")
        return False, f"Failed to send email ({err_msg}). Dev OTP: {otp_code}"
