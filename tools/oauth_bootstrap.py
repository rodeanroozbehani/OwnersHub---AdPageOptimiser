"""One-shot OAuth2 flow to generate a Google Ads refresh_token.

Run this ONCE on any machine with a browser (it can be your Windows box, not
the Beelink — the Beelink doesn't need a browser). It will:
  1. Open Google's OAuth consent screen
  2. You log in with the Google account that owns the Ads account
  3. Paste the authorisation code back here
  4. Print the refresh_token to copy into google-ads.yaml

Prerequisites
-------------
  pip install google-auth-oauthlib

The script only needs `google-auth-oauthlib`, which is already installed as a
dependency of `google-ads`. No other setup required.

Usage
-----
  python tools/oauth_bootstrap.py --client-secrets path/to/client_secret.json

Where to get client_secret.json
--------------------------------
  1. Go to https://console.cloud.google.com/
  2. Enable the "Google Ads API" in APIs & Services → Library
  3. Go to APIs & Services → Credentials → + Create Credentials → OAuth 2.0 Client ID
  4. Application type: "Desktop app"  (NOT Web application)
  5. Download the JSON file and save it somewhere safe
  6. Pass its path to this script

DO NOT commit client_secret.json or the generated google-ads.yaml to git.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


SCOPE = "https://www.googleapis.com/auth/adwords"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate a Google Ads OAuth2 refresh_token"
    )
    # Option A: JSON file
    parser.add_argument(
        "--client-secrets",
        default=None,
        help="Path to the client_secret_*.json file downloaded from Google Cloud Console",
    )
    # Option B: pass values directly (no JSON file needed)
    parser.add_argument(
        "--client-id",
        default=None,
        help="OAuth2 client_id (alternative to --client-secrets)",
    )
    parser.add_argument(
        "--client-secret",
        default=None,
        help="OAuth2 client_secret (alternative to --client-secrets)",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Write result directly to this path (e.g. ../google-ads.yaml)",
    )
    args = parser.parse_args(argv)

    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        sys.stderr.write(
            "google-auth-oauthlib not found.\n"
            "Run:  pip install google-auth-oauthlib\n"
        )
        return 1

    # Resolve credentials from either source
    if args.client_id and args.client_secret:
        client_id = args.client_id.strip()
        client_secret = args.client_secret.strip()
        # Build an in-memory secrets dict that InstalledAppFlow accepts
        secrets_data = {
            "installed": {
                "client_id": client_id,
                "client_secret": client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": ["urn:ietf:wg:oauth:2.0:oob", "http://localhost"],
            }
        }
        flow = InstalledAppFlow.from_client_config(secrets_data, scopes=[SCOPE])
    elif args.client_secrets:
        secrets_path = Path(args.client_secrets)
        if not secrets_path.exists():
            sys.stderr.write(f"Error: {secrets_path} not found\n")
            return 1
        with secrets_path.open(encoding="utf-8") as fh:
            secrets_data = json.load(fh)
        client_info = secrets_data.get("installed") or secrets_data.get("web") or {}
        client_id = client_info.get("client_id", "")
        client_secret = client_info.get("client_secret", "")
        if not client_id or not client_secret:
            sys.stderr.write(
                "Could not extract client_id / client_secret from the JSON.\n"
                "Make sure you downloaded an 'OAuth 2.0 Client ID' (Desktop app) file.\n"
            )
            return 1
        flow = InstalledAppFlow.from_client_secrets_file(str(secrets_path), scopes=[SCOPE])
    else:
        sys.stderr.write(
            "Provide either --client-secrets <file> OR both --client-id and --client-secret\n"
        )
        return 2

    print("\n=== OwnersHub Google Ads OAuth2 bootstrap ===\n")
    print(
        "A browser window will open (or a URL will be printed).\n"
        "Sign in with the Google account that OWNS the Ads account.\n"
        "Grant access to Google Ads.\n"
        "You will be given a code — paste it back here.\n"
    )

    # run_console() prints the URL and waits for a code — works on headless servers
    # run_local_server() opens a browser automatically on desktop
    try:
        credentials = flow.run_local_server(port=0, open_browser=True)
        print("Browser flow completed successfully.\n")
    except Exception:
        print("Browser flow unavailable; falling back to console (URL + paste).\n")
        credentials = flow.run_console()

    refresh_token = credentials.refresh_token
    if not refresh_token:
        sys.stderr.write(
            "\nError: no refresh_token was returned.\n"
            "This usually means the OAuth consent screen is missing 'access_type=offline'.\n"
            "Delete the token and try again; make sure you click 'Allow' on the consent screen.\n"
        )
        return 1

    print("\n=== SUCCESS ===\n")
    print(f"  client_id:     {client_id}")
    print(f"  client_secret: {client_secret}")
    print(f"  refresh_token: {refresh_token}")

    yaml_content = (
        "# google-ads.yaml — KEEP SECRET, chmod 600, never commit to git\n"
        "#\n"
        "# developer_token: get this from your Google Ads Manager Account\n"
        "#   https://developers.google.com/google-ads/api/docs/first-call/dev-token\n"
        "#\n"
        f"developer_token: \"YOUR_DEVELOPER_TOKEN_HERE\"\n"
        f"client_id: \"{client_id}\"\n"
        f"client_secret: \"{client_secret}\"\n"
        f"refresh_token: \"{refresh_token}\"\n"
        "# login_customer_id: \"1234567890\"   # MCC/manager account id (no dashes) — optional\n"
        "use_proto_plus: true\n"
    )

    if args.output:
        out_path = Path(args.output)
        out_path.write_text(yaml_content, encoding="utf-8")
        print(f"\ngoogle-ads.yaml written to: {out_path}")
        print("Remember to: chmod 600", out_path)
    else:
        print("\n--- Copy the following into google-ads.yaml ---\n")
        print(yaml_content)
        print("--- Then fill in your developer_token ---\n")
        print("Remember: chmod 600 google-ads.yaml  (on Linux)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
