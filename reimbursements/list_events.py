"""
List events created by previous runs of the onboarding script, with the
onboarded guests under each event and their workflow status.

Status columns per guest:
  receipts — "Receipts" Google Doc exists (collect_receipts.py has been run)
  reimb    — "reimbursement.pdf" exists (process_template.py has been run)

Usage:
    python list_events.py
    python list_events.py --limit 25
    python list_events.py --all

Events are printed most-recently-modified first.
"""

import argparse
import os
from datetime import datetime

from googleapiclient.discovery import build

from auth import get_credentials

ROOT_FOLDER_ID = "11MV67UxEjRDSf8ecl-5xKtwyAKGQu1Y3CUEV9JgMOkIiVVy32pjHDXxI2PW3p6OMoE824-aX"

DEFAULT_LIMIT = 5
CHECK = "✓"
CROSS = "✗"


def list_event_folders(drive, limit):
    """Return event folders under ROOT_FOLDER_ID, most-recently-modified first."""
    q = (
        f"'{ROOT_FOLDER_ID}' in parents"
        " and mimeType='application/vnd.google-apps.folder'"
        " and trashed=false"
    )
    folders = []
    page_token = None
    while True:
        kwargs = dict(
            q=q,
            orderBy="modifiedTime desc",
            fields="nextPageToken, files(id, name, modifiedTime)",
            pageSize=min(limit or 1000, 1000),
        )
        if page_token:
            kwargs["pageToken"] = page_token
        resp = drive.files().list(**kwargs).execute()
        folders.extend(resp.get("files", []))
        page_token = resp.get("nextPageToken")
        if not page_token or (limit and len(folders) >= limit):
            break
    return folders[:limit] if limit else folders


def list_person_folders(drive, event_folder_id):
    """Return person subfolders inside an event folder, sorted by name."""
    files = drive.files().list(
        q=(
            f"'{event_folder_id}' in parents"
            " and mimeType='application/vnd.google-apps.folder'"
            " and trashed=false"
        ),
        orderBy="name",
        fields="files(id, name)",
    ).execute().get("files", [])
    return files


def get_person_status(drive, person_folder_id):
    """
    Return (has_receipts, has_reimbursement) for a person's folder.
    Both checks are done in a single API call.
    """
    files = drive.files().list(
        q=(
            f"'{person_folder_id}' in parents"
            " and (name='Receipts' or name='reimbursement.pdf')"
            " and trashed=false"
        ),
        fields="files(name, mimeType)",
    ).execute().get("files", [])

    has_receipts = any(
        f["name"] == "Receipts"
        and f["mimeType"] == "application/vnd.google-apps.document"
        for f in files
    )
    has_reimbursement = any(f["name"] == "reimbursement.pdf" for f in files)
    return has_receipts, has_reimbursement


def format_modified(iso_str):
    dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
    return dt.astimezone().strftime("%Y-%m-%d %H:%M")


def status_symbol(flag):
    return CHECK if flag else CROSS


def main():
    parser = argparse.ArgumentParser(
        description="List onboarded events and guest status from Google Drive"
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--limit", type=int, default=DEFAULT_LIMIT,
        help=f"Maximum number of events to show (default {DEFAULT_LIMIT})",
    )
    group.add_argument("--all", action="store_true", help="Show all events")
    args = parser.parse_args()

    limit = None if args.all else args.limit

    creds = get_credentials()
    drive = build("drive", "v3", credentials=creds)

    events = list_event_folders(drive, limit)
    if not events:
        print("No events found.")
        return

    label = "all" if args.all else f"top {limit}"
    print(f"Events ({label}, most recent first):\n")

    for event in events:
        modified = format_modified(event["modifiedTime"])
        event_url = f"https://drive.google.com/drive/folders/{event['id']}"
        print(f"  {event['name']}   {modified}   {event_url}")

        persons = list_person_folders(drive, event["id"])
        if not persons:
            print("    (no guests onboarded)")
        else:
            name_width = max(len(p["name"]) for p in persons)
            for person in persons:
                has_receipts, has_reimb = get_person_status(drive, person["id"])
                receipts_col = f"receipts {status_symbol(has_receipts)}"
                reimb_col    = f"reimb {status_symbol(has_reimb)}"
                person_url   = f"https://drive.google.com/drive/folders/{person['id']}"
                print(
                    f"    {person['name']:<{name_width}}"
                    f"  {receipts_col}  {reimb_col}"
                    f"  {person_url}"
                )
        print()


if __name__ == "__main__":
    main()
