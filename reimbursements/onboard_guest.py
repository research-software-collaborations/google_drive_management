"""
Onboard one or more guests for HSF-India reimbursement.

Usage (single guest):
    python onboard_guest.py --event "HSF-India 2026" --name "Jane Smith" --email jane@example.com

Usage (bulk, from CSV file):
    python onboard_guest.py --event "HSF-India 2026" --file guests.csv

The CSV file should have two columns (name, email), with or without a header row.

Creates <root>/<event>/<name>/ in Drive for each guest, copies the expense template
into it, and opens a draft invitation email in Mail.app with a pre-filled upload form link.

Note: file upload questions in Google Forms require respondents to be signed
into a Google account.
"""

import argparse
import csv
import os
import subprocess
from urllib.parse import urlencode
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

TOKEN_FILE       = "token.json"
CREDENTIALS_FILE = "google_apps.json"
TEMPLATE_DOC_ID  = "15SfU0jNphjcIkWqLQYXAtJF5kYnDut4VLYGNXhAseXQ"
ROOT_FOLDER_ID   = "11MV67UxEjRDSf8ecl-5xKtwyAKGQu1Y3CUEV9JgMOkIiVVy32pjHDXxI2PW3p6OMoE824-aX"

# Shared upload form — pre-filled with name and event so responses can be matched later
FORM_RESPONDER_URL = "https://docs.google.com/forms/d/e/1FAIpQLSdRXl7IkgxmtTrj72lxeWz3g0HUKUbKoHzyseT2cmu5rZ7fYg/viewform"
NAME_ENTRY_ID      = "1293349445"
EVENT_ENTRY_ID     = "1936528846"

SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/forms.body",
    "https://www.googleapis.com/auth/forms.responses.readonly",
]


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


# ---------------------------------------------------------------------------
# Drive helpers
# ---------------------------------------------------------------------------

def find_folder(drive, name, parent_id):
    q = (
        f"name='{name}' and mimeType='application/vnd.google-apps.folder'"
        f" and '{parent_id}' in parents and trashed=false"
    )
    files = drive.files().list(q=q, fields="files(id)").execute().get("files", [])
    return files[0]["id"] if files else None


def create_folder(drive, name, parent_id):
    f = drive.files().create(
        body={"name": name, "mimeType": "application/vnd.google-apps.folder",
              "parents": [parent_id]},
        fields="id",
    ).execute()
    return f["id"]


def get_or_create_folder(drive, name, parent_id):
    folder_id = find_folder(drive, name, parent_id)
    if folder_id:
        print(f"  Existing folder: {name}")
    else:
        folder_id = create_folder(drive, name, parent_id)
        print(f"  Created folder:  {name}")
    return folder_id


def archive_existing(drive, folder_id):
    """Move any existing files/folders in folder_id into an 'old' subfolder."""
    files = drive.files().list(
        q=f"'{folder_id}' in parents and trashed=false",
        fields="files(id, name)",
    ).execute().get("files", [])
    if not files:
        return
    old_id = create_folder(drive, "old", folder_id)
    for f in files:
        drive.files().update(
            fileId=f["id"],
            addParents=old_id,
            removeParents=folder_id,
            fields="id",
        ).execute()
    print(f"  Archived {len(files)} existing item(s) to 'old/'")


def copy_file(drive, template_id, folder_id, name):
    f = drive.files().copy(
        fileId=template_id,
        body={"name": name, "parents": [folder_id]},
        fields="id",
    ).execute()
    return f["id"]


def make_world_editable(drive, file_id):
    drive.permissions().create(
        fileId=file_id,
        body={"type": "anyone", "role": "writer"},
    ).execute()


# ---------------------------------------------------------------------------
# Form URL
# ---------------------------------------------------------------------------

def make_form_url(name, event):
    params = urlencode({
        f"entry.{NAME_ENTRY_ID}":  name,
        f"entry.{EVENT_ENTRY_ID}": event,
    })
    return f"{FORM_RESPONDER_URL}?usp=pp_url&{params}"


# ---------------------------------------------------------------------------
# Email draft
# ---------------------------------------------------------------------------

def open_mail_draft(to, subject, body, attachment=None):
    def esc(s):
        return s.replace("\\", "\\\\").replace('"', '\\"')

    body_as = " & return & ".join(f'"{esc(line)}"' for line in body.split("\n"))

    attachment_stmt = ""
    if attachment:
        abs_path = os.path.abspath(attachment)
        attachment_stmt = f'\n        make new attachment with properties {{file name:(("{esc(abs_path)}" as POSIX file) as alias)}}'

    script = f'''tell application "Mail"
    activate
    set myMessage to make new outgoing message with properties {{visible:true, subject:"{esc(subject)}", content:{body_as}}}
    tell myMessage
        make new to recipient at end of to recipients with properties {{address:"{esc(to)}"}}
        {attachment_stmt}
    end tell
end tell'''

    subprocess.run(["osascript", "-e", script])
    print(f"Opened draft email to {to} in Mail.app")


def compose_onboard_email(name, email, event, doc_url, form_url):
    first = name.split()[0]
    subject = f"HSF-India expense form — {event}"
    body = (
        f"Dear {first},\n\n"
        f"Thank you for participating in {event}. To process your reimbursement, please complete the two steps below.\n\n"
        f"1. Fill in your expense details here:\n"
        f"   {doc_url}\n\n"
        f"   Enter all amounts in the original currency (USD, GBP, INR, etc.) — we will handle the conversion.\n\n"
        f"2. Upload your receipts and any on-boarding documents (if previously requested) here:\n"
        f"   {form_url}\n\n"
        f"   (You will need to be signed into a Google account to upload files.)\n\n"
        f"A few notes on what we can reimburse: visas, transport, eSIMs, accommodation, and meals are all covered at actual cost. Any small incidentals can be listed under Meals — just describe them as a meal. Meal expenses should be under $50 per meal and $75 per day in total, though given the meals provided during the program this is unlikely to be an issue. For hotel charges, please include only the nightly room rate and taxes; any meals or transport charged to the room should be listed as separate entries. Finally, Princeton's finance office tends to query identical round-number entries appearing more than once.\n\n"
        f"Many thanks,\nDavid\n"
    )
    open_mail_draft(email, subject, body)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def onboard_one(drive, name, email, event):
    print(f"\nOnboarding {name} ({email}) for {event}...")

    event_folder_id  = get_or_create_folder(drive, event, ROOT_FOLDER_ID)
    person_folder_id = get_or_create_folder(drive, name, event_folder_id)

    archive_existing(drive, person_folder_id)

    print("  Copying expense template...")
    doc_id = copy_file(drive, TEMPLATE_DOC_ID, person_folder_id, "Expenses")
    make_world_editable(drive, doc_id)

    doc_url  = f"https://docs.google.com/document/d/{doc_id}/edit"
    form_url = make_form_url(name, event)

    print(f"  Folder: https://drive.google.com/drive/folders/{person_folder_id}")
    print(f"  Doc:    {doc_url}")
    print(f"  Form:   {form_url}")

    compose_onboard_email(name, email, event, doc_url, form_url)


def read_guests(path):
    """Read a CSV file with name and email columns (header optional)."""
    guests = []
    with open(path, newline="", encoding="utf-8") as f:
        sample = f.read(1024)
        f.seek(0)
        has_header = csv.Sniffer().has_header(sample)
        reader = csv.reader(f)
        if has_header:
            next(reader)
        for row in reader:
            if len(row) < 2:
                continue
            name, email = row[0].strip(), row[1].strip()
            if name and email:
                guests.append((name, email))
    return guests


def main():
    parser = argparse.ArgumentParser(description="Onboard guests for HSF-India reimbursement")
    parser.add_argument("--event", required=True, help="Event name, e.g. 'HSF-India Summer 2026'")

    who = parser.add_mutually_exclusive_group(required=True)
    who.add_argument("--name",  help="Single guest full name")
    who.add_argument("--file",  help="CSV file with columns: name, email (one guest per row)")

    parser.add_argument("--email", help="Guest email (required with --name)")
    args = parser.parse_args()

    if args.name and not args.email:
        parser.error("--email is required when --name is given")

    creds = get_credentials()
    drive = build("drive", "v3", credentials=creds)

    if args.file:
        guests = read_guests(args.file)
        print(f"Found {len(guests)} guest(s) in {args.file}")
        for name, email in guests:
            onboard_one(drive, name, email, args.event)
    else:
        onboard_one(drive, args.name, args.email, args.event)


if __name__ == "__main__":
    main()
