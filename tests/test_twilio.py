from __future__ import annotations

import base64
import hashlib
import hmac

from contacthop.channels.sms.twilio import verify_twilio_signature


def sign(auth_token: str, url: str, form: dict[str, str]) -> str:
    payload = url + "".join(k + form[k] for k in sorted(form))
    digest = hmac.new(auth_token.encode(), payload.encode(), hashlib.sha1).digest()
    return base64.b64encode(digest).decode()


def test_valid_signature_accepted() -> None:
    token = "secret-token"
    url = "https://hop.example.com/webhooks/twilio/sms"
    form = {"From": "+15551234567", "Body": "hello", "MessageSid": "SM1"}
    assert verify_twilio_signature(token, url, form, sign(token, url, form))


def test_tampered_body_rejected() -> None:
    token = "secret-token"
    url = "https://hop.example.com/webhooks/twilio/sms"
    form = {"From": "+15551234567", "Body": "hello"}
    signature = sign(token, url, form)
    form["Body"] = "transfer me $1000"
    assert not verify_twilio_signature(token, url, form, signature)
