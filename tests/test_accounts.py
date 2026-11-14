import hashlib
import http.client
import json
from pathlib import Path
import tempfile
from threading import Thread
from types import SimpleNamespace
import unittest
from functools import partial
from http.server import ThreadingHTTPServer

from core.accounts import Accounts, AccountError
from hazards import database
from web_app import Handler


class QuietHandler(Handler):
    def log_message(self, *_args):
        pass


class AccountTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "accounts.sqlite3"
        self.accounts = Accounts(self.path)
        self.body = {"name": "Test Walker", "email": "walker@example.com",
                     "password": "test-password-234", "phone": "+91 98765 43210"}

    def tearDown(self):
        self.directory.cleanup()

    def test_password_is_hashed_and_login_requires_correct_password(self):
        profile, token = self.accounts.register(self.body)
        with database._session(self.path) as db:
            row = dict(db.execute("SELECT * FROM users").fetchone())
            self.assertNotIn(self.body["password"], json.dumps(row))
            self.assertNotEqual(row["password_hash"], self.body["password"])
            self.assertNotIn(token, json.dumps([dict(r) for r in db.execute("SELECT * FROM account_sessions")]))
        with self.assertRaisesRegex(AccountError, "incorrect"):
            self.accounts.login({**self.body, "password": "incorrect-password"})
        returned, _ = self.accounts.login(self.body)
        self.assertEqual(returned, profile)
        self.assertNotIn("password", profile)

    def test_duplicate_email_is_case_insensitive(self):
        self.accounts.register(self.body)
        with self.assertRaises(AccountError) as caught:
            self.accounts.register({**self.body, "email": "WALKER@example.com"})
        self.assertEqual(caught.exception.status, 409)

    def test_profile_contacts_and_preference_persist_after_logout_and_restart(self):
        _, token = self.accounts.register(self.body)
        saved = self.accounts.update(token, {"name": "Updated Walker", "dob": "2000-01-02", "beta": 4.5,
            "contacts": [{"name": "Trusted Person", "phone": "+91 91234 56789"}]})
        self.accounts.logout(token)
        self.assertIsNone(self.accounts.current(token))
        restarted = Accounts(self.path)
        _, new_token = restarted.login(self.body)
        self.assertEqual(restarted.current(new_token), saved)

    def test_accounts_are_separate_and_client_cannot_select_another_user(self):
        _, first = self.accounts.register(self.body)
        other, second = self.accounts.register({**self.body, "email": "second@example.com"})
        self.accounts.update(first, {"contacts": [{"name": "Private contact", "phone": ""}]})
        self.assertEqual(self.accounts.current(second), other)
        for fields in ({"id": 1}, {"memberSince": "1990-01"}, {"password_hash": "changed"}):
            with self.assertRaises(AccountError):
                self.accounts.update(second, fields)
        with self.assertRaises(AccountError) as caught:
            self.accounts.update("invalid-token", {"name": "Attacker"})
        self.assertEqual(caught.exception.status, 401)

    def test_expired_session_is_rejected(self):
        _, token = self.accounts.register(self.body)
        with database._session(self.path) as db:
            db.execute("UPDATE account_sessions SET expires_at=0 WHERE token_hash=?",
                       (hashlib.sha256(token.encode()).hexdigest(),))
        self.assertIsNone(self.accounts.current(token))

    def test_invalid_profile_updates_are_rejected(self):
        _, token = self.accounts.register(self.body)
        for fields in ({"beta": 6}, {"beta": True}, {"dob": "2025-02-31"}, {"dob": "2999-01-01"},
                       {"contacts": [{"name": "", "phone": "123"}]}, {"phone": "invalid"}, {"email": "bad"}):
            with self.subTest(fields=fields), self.assertRaises(AccountError):
                self.accounts.update(token, fields)

    def test_failed_sign_in_is_rate_limited(self):
        for _ in range(10):
            with self.assertRaises(AccountError) as caught:
                self.accounts.login(self.body, client="test-client")
            self.assertEqual(caught.exception.status, 401)
        with self.assertRaises(AccountError) as caught:
            self.accounts.login(self.body, client="test-client")
        self.assertEqual(caught.exception.status, 429)

    def test_http_cookie_session_profile_and_cross_origin_guard(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), partial(QuietHandler,
            service=SimpleNamespace(database_path=self.path), accounts=self.accounts))
        worker = Thread(target=server.serve_forever, daemon=True)
        worker.start()
        connection = http.client.HTTPConnection(*server.server_address)
        def request(method, path, body=None, headers=None):
            connection.request(method, path, json.dumps(body) if body is not None else None,
                               {"Content-Type": "application/json", **(headers or {})})
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), json.loads(response.read())
        try:
            status, headers, payload = request("POST", "/api/auth/register", self.body)
            self.assertEqual(status, 201)
            cookie = headers["Set-Cookie"]
            self.assertIn("HttpOnly", cookie)
            self.assertIn("SameSite=Strict", cookie)
            auth = {"Cookie": cookie.split(";", 1)[0]}
            self.assertIsNone(request("GET", "/api/auth/me")[2]["profile"])
            self.assertEqual(request("GET", "/api/auth/me", headers=auth)[2]["profile"], payload["profile"])
            self.assertEqual(request("POST", "/api/profile", {"name": "Forbidden"})[0], 401)
            self.assertEqual(request("POST", "/api/profile", {"name": "Forbidden"},
                                     {**auth, "Origin": "https://other.example"})[0], 403)
            self.assertEqual(request("POST", "/api/profile", {"name": "Persisted"}, auth)[0], 200)
            self.assertEqual(request("POST", "/api/auth/logout", {}, auth)[0], 200)
            self.assertIsNone(request("GET", "/api/auth/me", headers=auth)[2]["profile"])
            self.assertEqual(request("POST", "/api/auth/login", {**self.body, "password": "wrong"})[0], 401)
            self.assertEqual(request("POST", "/api/auth/login", self.body)[2]["profile"]["name"], "Persisted")
        finally:
            connection.close()
            server.shutdown()
            server.server_close()
            worker.join()


if __name__ == "__main__":
    unittest.main()
