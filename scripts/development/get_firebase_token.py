#!/usr/bin/env python3
"""
CLI Tool: Generate and retrieve a valid Firebase ID token for development.

Usage:
    python scripts/development/get_firebase_token.py --user alex
    python scripts/development/get_firebase_token.py --user tony
    python scripts/development/get_firebase_token.py --user dev
    python scripts/development/get_firebase_token.py --email custom@example.com
"""

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from dotenv import load_dotenv

load_dotenv()

import firebase_admin
from firebase_admin import auth, credentials

PRESET_USERS = {
    "alex": {
        "email": "alex@example.com",
        "display_name": "Alex",
        "fallback_emails": ["alexnguyenit911@gmail.com", "alex.nguyen@nutreeai.com"],
    },
    "tony": {
        "email": "tony@example.com",
        "display_name": "Tony",
        "fallback_emails": ["tonytran07it@gmail.com"],
    },
    "dev": {
        "email": "dev@example.com",
        "display_name": "Dev User",
        "fallback_emails": ["nutreeaidev@gmail.com"],
    },
}

DEFAULT_WEB_API_KEY = "AIzaSyATZE30kROrvgn723Tnpf5QHXOM4iGAFOc"  # nutree-ai web/android API key


def init_firebase_admin():
    sa_env = os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON")
    if not sa_env:
        print("ERROR: FIREBASE_SERVICE_ACCOUNT_JSON environment variable not set in .env", file=sys.stderr)
        sys.exit(1)

    try:
        service_account_info = json.loads(sa_env)
        cred = credentials.Certificate(service_account_info)
        return firebase_admin.initialize_app(cred)
    except ValueError:
        # Already initialized
        return firebase_admin.get_app()
    except Exception as e:
        print(f"ERROR: Failed to initialize Firebase Admin SDK: {e}", file=sys.stderr)
        sys.exit(1)


def get_or_create_user(email: str, display_name: str | None = None, fallback_emails: list[str] | None = None):
    # Try primary email first
    try:
        return auth.get_user_by_email(email)
    except auth.UserNotFoundError:
        pass

    # Try fallback emails if primary not found
    if fallback_emails:
        for fb_email in fallback_emails:
            try:
                user = auth.get_user_by_email(fb_email)
                print(f"[Info] Found existing account with fallback email: {fb_email} (UID: {user.uid})")
                return user
            except auth.UserNotFoundError:
                continue

    # If neither exists, create account with primary email
    print(f"[Info] User {email} not found. Creating test user in Firebase...")
    user = auth.create_user(
        email=email,
        password="nutree_test",
        display_name=display_name or email.split("@")[0].capitalize(),
        email_verified=True,
    )
    print(f"[Info] Created new Firebase user: {user.email} (UID: {user.uid})")
    return user


def exchange_custom_token_for_id_token(custom_token: str, api_key: str = DEFAULT_WEB_API_KEY) -> dict:
    url = f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithCustomToken?key={api_key}"
    data = json.dumps({"token": custom_token, "returnSecureToken": True}).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})

    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        err_msg = e.read().decode("utf-8")
        print(f"ERROR: Failed to exchange custom token: {err_msg}", file=sys.stderr)
        sys.exit(1)


def copy_to_clipboard(text: str) -> bool:
    if sys.platform == "darwin":
        try:
            p = subprocess.Popen(["pbcopy"], stdin=subprocess.PIPE)
            p.communicate(input=text.encode("utf-8"))
            return p.returncode == 0
        except Exception:
            return False
    return False


def main():
    parser = argparse.ArgumentParser(description="Get Firebase ID Token for Alex, Tony, Dev, or custom email.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--user", choices=["alex", "tony", "dev"], help="Preset user shortcut (alex, tony, dev)")
    group.add_argument("--email", help="Custom user email")
    parser.add_argument("--raw", action="store_true", help="Print only the ID token (useful for scripting)")

    args = parser.parse_args()

    init_firebase_admin()

    if args.user:
        preset = PRESET_USERS[args.user]
        email = preset["email"]
        display_name = preset["display_name"]
        fallback_emails = preset["fallback_emails"]
    else:
        email = args.email.strip().lower()
        display_name = email.split("@")[0].capitalize()
        fallback_emails = None

    user = get_or_create_user(email, display_name, fallback_emails)

    custom_token = auth.create_custom_token(user.uid)
    if isinstance(custom_token, bytes):
        custom_token = custom_token.decode("utf-8")

    auth_res = exchange_custom_token_for_id_token(custom_token)
    id_token = auth_res.get("idToken")

    if args.raw:
        print(id_token)
        return

    copied = copy_to_clipboard(id_token)

    print("\n" + "=" * 60)
    print(f"  🔥 FIREBASE AUTH TOKEN: {user.display_name or user.email}")
    print("=" * 60)
    print(f"Email:        {user.email}")
    print(f"Firebase UID: {user.uid}")
    print(f"Token Length: {len(id_token)} chars")
    print(f"Expires In:   {auth_res.get('expiresIn')}s (1 hour)")
    if copied:
        print("Clipboard:    ✅ Copied ID token to clipboard!")
    print("-" * 60)
    print("Firebase ID Token:")
    print(id_token)
    print("-" * 60)
    print("Example cURL command:")
    print(f'curl -H "Authorization: Bearer {id_token}" http://localhost:8000/v1/users/me')
    print("=" * 60 + "\n")


if __name__ == "__main__":
    main()
