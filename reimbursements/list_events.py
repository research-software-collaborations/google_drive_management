"""
List events created by previous runs of the onboarding script.

Usage:
    python list_events.py
    python list_events.py --limit 25
    python list_events.py --all

Events are printed most-recently-modified first.
"""

import argparse
import os
from datetime import datetime, timezone

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

TOKEN_FILE       = "token.json"
CREDENTIALS_FILE = "google_apps.json"
ROOT_FOLDER_ID   = "11MV67UxEjRDSf8ecl-5xKtwyAKGQu1Y3CUEV9JgMOkIiVVy32pjHDXxI2PW3p6OMoE824-aX"

SCOPES = [
    "https://www.googleapis.com/auth/drive.readonly",
]

DEFAULT_LIMIT = 10


def get_credentials():
    creds = None
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN_FILE, "w") as f:
            f.write(creds.to_json())
    return creds


def list_event_folders(drive, limit):
    """Return event folders under ROOT_FOLDER_ID, most-recently-modified first."""
    q = (
        f"'{ROOT_FOLDER_ID}' in parents"
        " and mimeType='application/vnd.google-apps.folder'"
        " and trashed=false"
    )
    fields = "nextPageToken, files(id, name, modifiedTime)"

    folders = []
    page_token = None
    while True:
        kwargs = dict(
            q=q,
            orderBy="modifiedTime desc",
            fields=fields,
            pageSize=min(limit or 1000, 1000),
        )
        if page_token:
            kwargs["pageToken"] = page_token

        resp = drive.files().list(**kwargs).execute()
        folders.extend(resp.get("files", []))

        page_token = resp.get("nextPageToken")
        if not page_token:
            break
        if limit and len(folders) >= limit:
            break

    return folders[:limit] if limit else folders


def format_modified(iso_str):
    dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
    local_dt = dt.astimezone()
    return local_dt.strftime("%Y-%m-%d %H:%M")


def main():
    parser = argparse.ArgumentParser(description="List onboarded events from Google Drive")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--limit", type=int, default=DEFAULT_LIMIT,
                       help=f"Maximum number of events to show (default {DEFAULT_LIMIT})")
    group.add_argument("--all", action="store_true", help="Show all events")
    args = parser.parse_args()

    limit = None if args.all else args.limit

    creds = get_credentials()
    drive = build("drive", "v3", credentials=creds)

    folders = list_event_folders(drive, limit)

    if not folders:
        print("No events found.")
        return

    label = "all" if args.all else f"top {limit}"
    print(f"Events ({label}, most recent first):\n")

    name_width = max(len(f["name"]) for f in folders)
    for f in folders:
        modified = format_modified(f["modifiedTime"])
        url = f"https://drive.google.com/drive/folders/{f['id']}"
        print(f"  {f['name']:<{name_width}}  {modified}  {url}")


if __name__ == "__main__":
    main()
