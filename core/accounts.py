"""SQLite accounts, password verification, and revocable browser sessions."""
import hashlib
import hmac
import json
import math
import re
import secrets
import sqlite3
import time
from datetime import date
from threading import Lock

from hazards import database

SESSION_SECONDS = 7 * 24 * 60 * 60
GENDERS = ("Female", "Male", "Non-binary", "Prefer not to say")


class AccountError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def text(value, label, limit, required=False):
    if not isinstance(value, str):
        raise AccountError("{} must be text.".format(label))
    value = value.strip()
    if (required and not value) or len(value) > limit:
        raise AccountError("Enter {} ({} characters maximum).".format(label, limit))
    return value


def validate(fields):
    if not isinstance(fields, dict) or set(fields) - {"name", "email", "phone", "gender", "dob", "contacts", "beta"}:
        raise AccountError("Unknown profile fields.")
    result = {}
    for key, value in fields.items():
        if key in ("name", "email", "phone", "gender", "dob"):
            value = text(value, key, {"name": 60, "email": 254, "phone": 32, "gender": 30, "dob": 10}[key], key in ("name", "email", "phone"))
        if key == "email":
            value = value.lower()
            if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
                raise AccountError("Enter a valid email address.")
        if key == "phone" and (not re.fullmatch(r"\+?[\d\s()-]+", value) or not 7 <= len(re.sub(r"\D", "", value)) <= 15):
            raise AccountError("Enter a valid phone number.")
        if key == "gender" and value and value not in GENDERS:
            raise AccountError("Choose a valid gender option.")
        if key == "dob" and value:
            try:
                birthday = date.fromisoformat(value)
            except ValueError:
                raise AccountError("Enter a valid date of birth.") from None
            if not date(1900, 1, 1) <= birthday <= date.today():
                raise AccountError("Date of birth must be between 1900 and today.")
            value = birthday.isoformat()
        if key == "beta":
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 5:
                raise AccountError("Safety preference must be between zero and five.")
        if key == "contacts":
            if not isinstance(value, list) or len(value) > 10:
                raise AccountError("Save up to ten trusted contacts.")
            contacts = []
            for contact in value:
                if not isinstance(contact, dict) or set(contact) - {"name", "phone"}:
                    raise AccountError("Enter a valid trusted contact.")
                name = text(contact.get("name", ""), "contact name", 60, True)
                phone = text(contact.get("phone", ""), "contact phone", 32)
                if phone:
                    validate({"phone": phone})
                contacts.append({"name": name, "phone": phone})
            value = contacts
        result[key] = value
    return result


def password_hash(password, salt):
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), 600000).hex()


class Accounts:
    def __init__(self, database_path=None):
        self.database_path = database_path
        self._failures = {}
        self._lock = Lock()
        with database._session(database_path) as db:
            db.execute("""CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY, email TEXT NOT NULL UNIQUE,
                salt TEXT NOT NULL, password_hash TEXT NOT NULL, profile TEXT NOT NULL)""")
            db.execute("""CREATE TABLE IF NOT EXISTS account_sessions (
                token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                expires_at INTEGER NOT NULL)""")

    def _session(self, db, user_id):
        token = secrets.token_urlsafe(32)
        db.execute("DELETE FROM account_sessions WHERE expires_at <= ?", (int(time.time()),))
        db.execute("INSERT INTO account_sessions VALUES (?, ?, ?)",
                   (hashlib.sha256(token.encode()).hexdigest(), user_id, int(time.time()) + SESSION_SECONDS))
        return token

    def register(self, body):
        password = body.get("password", "")
        if not isinstance(password, str) or not 8 <= len(password) <= 128:
            raise AccountError("Use a password with 8 to 128 characters.")
        fields = validate({key: body.get(key, "") for key in ("name", "email", "phone", "gender", "dob")})
        profile = {**fields, "contacts": [], "beta": 2, "memberSince": date.today().isoformat()[:7]}
        salt = secrets.token_hex(16)
        digest = password_hash(password, salt)
        try:
            with database._session(self.database_path) as db:
                user_id = db.execute("INSERT INTO users(email,salt,password_hash,profile) VALUES (?,?,?,?)",
                                     (fields["email"], salt, digest, json.dumps(profile))).lastrowid
                token = self._session(db, user_id)
        except sqlite3.IntegrityError:
            raise AccountError("An account with this email already exists. Please sign in.", 409) from None
        return profile, token

    def login(self, body, client="local"):
        now = time.time()
        with self._lock:
            self._failures = {key: values for key, times in self._failures.items()
                              if (values := [t for t in times if t > now - 900])}
            if len(self._failures.get(client, [])) >= 10:
                raise AccountError("Too many unsuccessful sign-in attempts. Try again in 15 minutes.", 429)
        email = text(body.get("email", ""), "email", 254).lower()
        password = body.get("password", "")
        if not isinstance(password, str) or not 1 <= len(password) <= 128:
            raise AccountError("Enter your password.")
        with database._session(self.database_path) as db:
            row = db.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
            digest = password_hash(password, row["salt"] if row else "00" * 16)
            if not row or not hmac.compare_digest(digest, row["password_hash"]):
                with self._lock:
                    self._failures.setdefault(client, []).append(now)
                raise AccountError("Email or password is incorrect.", 401)
            token = self._session(db, row["id"])
            profile = json.loads(row["profile"])
        with self._lock:
            self._failures.pop(client, None)
        return profile, token

    def _user(self, db, token):
        if not isinstance(token, str) or not token or len(token) > 128:
            return None
        return db.execute("""SELECT users.* FROM users JOIN account_sessions ON users.id = account_sessions.user_id
            WHERE token_hash = ? AND expires_at > ?""",
            (hashlib.sha256(token.encode()).hexdigest(), int(time.time()))).fetchone()

    def current(self, token):
        with database._session(self.database_path) as db:
            row = self._user(db, token)
            return json.loads(row["profile"]) if row else None

    def update(self, token, fields):
        fields = validate(fields)
        try:
            with database._session(self.database_path) as db:
                row = self._user(db, token)
                if not row:
                    raise AccountError("Please sign in again.", 401)
                profile = {**json.loads(row["profile"]), **fields}
                db.execute("UPDATE users SET email = ?, profile = ? WHERE id = ?",
                           (profile["email"], json.dumps(profile), row["id"]))
                return profile
        except sqlite3.IntegrityError:
            raise AccountError("This email is already used by another account.", 409) from None

    def logout(self, token):
        with database._session(self.database_path) as db:
            db.execute("DELETE FROM account_sessions WHERE token_hash = ?",
                       (hashlib.sha256(token.encode()).hexdigest(),))
