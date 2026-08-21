import base64
import json
import os
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path

from cryptography.hazmat.primitives import serialization

from tools.license_server import license_admin


def request_code(device_id: str, nonce: str) -> str:
    payload = {
        "version": 1,
        "deviceId": device_id,
        "installPublicKey": "install-public-key",
        "nonce": nonce,
        "appVersion": "1.4.0",
        "requestedAt": "2026-08-14T10:00:00Z",
    }
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    encoded = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    return license_admin.REQUEST_PREFIX + encoded


class LicenseAdminTest(unittest.TestCase):
    def test_issue_generates_license_id_when_omitted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            key_path = root / "issuer.pem"
            password_path = root / "password.txt"
            ledger_path = root / "ledger.json"
            password = b"test-password"
            license_admin.generate_key(key_path, password)
            password_path.write_text(password.decode(), encoding="utf-8")

            args = Namespace(
                request=request_code("device-auto", "nonce-auto"),
                days=7,
                customer="customer-auto",
                license="",
                key=key_path,
                ledger=ledger_path,
                password_file=password_path,
            )
            license_admin.command_issue(args)
            licenses = license_admin.load_ledger(ledger_path)["licenses"]
            self.assertEqual(len(licenses), 1)
            self.assertTrue(next(iter(licenses)).startswith("AUTO-"))

    def test_issue_signature_and_single_device_ledger(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            key_path = root / "issuer.pem"
            password_path = root / "password.txt"
            ledger_path = root / "ledger.json"
            password = b"test-password"
            public_text = license_admin.generate_key(key_path, password)
            password_path.write_text(password.decode(), encoding="utf-8")
            os.environ.pop("LAOGU_LICENSE_KEY_PASSWORD", None)

            private_key = license_admin.load_private_key(key_path, password)
            code, payload = license_admin.issue_activation(
                request_code("device-a", "nonce-a"),
                private_key,
                "LICENSE-001",
                "customer-a",
                7,
            )
            public_key = private_key.public_key()
            encoded_payload, encoded_signature = code.removeprefix(license_admin.ACTIVATION_PREFIX).split(".")
            raw = license_admin.b64url_decode(encoded_payload)
            public_key.verify(license_admin.b64url_decode(encoded_signature), raw)
            self.assertEqual(payload["deviceId"], "device-a")
            self.assertEqual(
                public_text,
                license_admin.b64std_raw(
                    public_key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
                ),
            )

            args = Namespace(
                request=request_code("device-a", "nonce-1"),
                days=7,
                customer="customer-a",
                license="LICENSE-001",
                key=key_path,
                ledger=ledger_path,
                password_file=password_path,
            )
            license_admin.command_issue(args)
            args.request = request_code("device-b", "nonce-2")
            with self.assertRaises(ValueError):
                license_admin.command_issue(args)

            license_admin.command_unbind(Namespace(ledger=ledger_path, license="LICENSE-001"))
            license_admin.command_issue(args)
            ledger = license_admin.load_ledger(ledger_path)
            self.assertEqual(ledger["licenses"]["LICENSE-001"]["deviceId"], "device-b")


if __name__ == "__main__":
    unittest.main()
