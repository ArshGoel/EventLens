import os
import re
import logging
from django.conf import settings

logger = logging.getLogger(__name__)

def format_phone_number(phone):
    """
    Formats raw phone string into E.164 format (+1234567890).
    """
    cleaned = re.sub(r'[^\d+]', '', phone.strip())
    if not cleaned:
        return cleaned
    if not cleaned.startswith('+'):
        if len(cleaned) == 10:
            cleaned = '+91' + cleaned
        else:
            cleaned = '+' + cleaned
    return cleaned

def send_otp_sms(phone_number, otp_code):
    """
    Sends OTP code to recipient phone number using Twilio REST API.
    Falls back gracefully if Twilio credentials are missing or SMS fails.
    """
    account_sid = os.environ.get('TWILIO_ACCOUNT_SID') or getattr(settings, 'TWILIO_ACCOUNT_SID', '')
    auth_token = os.environ.get('TWILIO_AUTH_TOKEN') or getattr(settings, 'TWILIO_AUTH_TOKEN', '')
    sender = os.environ.get('TWILIO_PHONE_NUMBER') or getattr(settings, 'TWILIO_PHONE_NUMBER', '')

    target_phone = format_phone_number(phone_number)
    body_text = f"Your EventLens guest login verification code is: {otp_code}. Valid for 10 minutes."

    if account_sid and auth_token and sender:
        try:
            from twilio.rest import Client
            client = Client(account_sid, auth_token)
            message = client.messages.create(
                to=target_phone,
                from_=sender,
                body=body_text
            )
            logger.info(f"Twilio SMS sent to {target_phone}. Message SID: {message.sid}")
            return True, f"OTP sent via SMS to {target_phone}."
        except Exception as e:
            err_str = str(e)
            logger.error(f"Twilio SMS failed for {target_phone}: {err_str}")
            if "unverified" in err_str.lower() or "21608" in err_str:
                return False, f"Twilio Trial Notice: Recipient {target_phone} is unverified in your Twilio Console. Dev Code: {otp_code}"
            return False, f"Twilio SMS Error: {err_str}. Dev Code: {otp_code}"
    else:
        logger.warning(f"Twilio credentials not configured. Dev OTP for {target_phone} is {otp_code}")
        return True, f"Twilio credentials not set. Dev OTP is {otp_code}"

