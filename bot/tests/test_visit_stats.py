import hashlib
import hmac
import json
import time
import unittest
from urllib.parse import urlencode

from visit_stats import verify_init_data

TOKEN = "123456:TEST-TOKEN"


def build_init_data(token, auth_date, user):
    pairs = {"auth_date": str(auth_date), "user": json.dumps(user)}
    check = "\n".join(f"{k}={pairs[k]}" for k in sorted(pairs))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    pairs["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(pairs)


class VerifyInitDataTests(unittest.TestCase):
    USER = {"id": 42, "first_name": "A"}

    def test_valid(self):
        data = build_init_data(TOKEN, int(time.time()), self.USER)
        self.assertEqual(verify_init_data(data, TOKEN)["id"], 42)

    def test_wrong_token(self):
        data = build_init_data("other:token", int(time.time()), self.USER)
        self.assertIsNone(verify_init_data(data, TOKEN))

    def test_tampered_user(self):
        data = build_init_data(TOKEN, int(time.time()), self.USER)
        self.assertIsNone(verify_init_data(data.replace("%3A+42", "%3A+43"), TOKEN))
        self.assertIn("%3A+42", data)

    def test_expired(self):
        data = build_init_data(TOKEN, int(time.time()) - 3 * 86400, self.USER)
        self.assertIsNone(verify_init_data(data, TOKEN))

    def test_empty(self):
        self.assertIsNone(verify_init_data("", TOKEN))
        self.assertIsNone(verify_init_data("a=b", TOKEN))


if __name__ == "__main__":
    unittest.main()
