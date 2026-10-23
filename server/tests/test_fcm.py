import base64
import json

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from portal.services import fcm


def _account():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
    return key, {"project_id": "demo", "client_email": "svc@demo.iam.gserviceaccount.com", "private_key": pem}


def test_assertion_is_a_valid_rs256_jwt():
    key, acc = _account()
    head, claims, sig = fcm._assertion(acc).split(".")
    pad = lambda s: s + "=" * (-len(s) % 4)  # noqa: E731
    assert json.loads(base64.urlsafe_b64decode(pad(head)))["alg"] == "RS256"
    c = json.loads(base64.urlsafe_b64decode(pad(claims)))
    assert c["iss"] == acc["client_email"] and c["scope"] == fcm.SCOPE and c["exp"] > c["iat"]
    key.public_key().verify(base64.urlsafe_b64decode(pad(sig)), f"{head}.{claims}".encode(), padding.PKCS1v15(), hashes.SHA256())


def test_message_carries_url_and_tag():
    m = fcm.message("tok", {"title": "SU: Havørn", "body": "Skagen", "url": "/#/traad/1", "tag": "t1"})["message"]
    assert m["token"] == "tok" and m["notification"]["title"] == "SU: Havørn"
    assert m["data"] == {"url": "/#/traad/1", "tag": "t1"}
    assert m["android"]["priority"] == "HIGH" and m["android"]["notification"]["tag"] == "t1"
