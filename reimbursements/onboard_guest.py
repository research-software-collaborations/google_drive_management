"""
Onboard a guest for HSF-India reimbursement.

Usage:
    python onboard_guest.py --event "HSF-India 2026" --name "Jane Smith" --email jane@example.com

Creates <root>/<event>/<name>/ in Drive, copies the expense template into it,
and prints a draft invitation email with a pre-filled upload form link.

Note: file upload questions in Google Forms require respondents to be signed
into a Google account.
"""

import argparse
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
        f"Thank you for participating in {event}. To process your reimbursement\n"
        f"please complete the two steps below.\n\n"
        f"1. Fill in your expense details here:\n"
        f"   {doc_url}\n\n"
        f"   Enter all amounts in the original currency (USD, GBP, INR, etc.) — we will handle the conversion.\n\n"
        f"2. Upload your receipts and any on-boarding documents here:\n"
        f"   {form_url}\n\n"
        f"   (You will need to be signed into a Google account to upload files.)\n\n"
        f"Once both are complete, please let us know and we will process your reimbursement.\n\n"
        f"Many thanks,\nDavid\n"
    )
    open_mail_draft(email, subject, body)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Onboard a guest for HSF-India reimbursement")
    parser.add_argument("--event", required=True, help="Event name, e.g. 'HSF-India Summer 2026'")
    parser.add_argument("--name",  required=True, help="Guest full name, e.g. 'Jane Smith'")
    parser.add_argument("--email", required=True, help="Guest email address")
    args = parser.parse_args()

    creds = get_credentials()
    drive = build("drive", "v3", credentials=creds)

    print(f"Onboarding {args.name} for {args.event}...")

    event_folder_id  = get_or_create_folder(drive, args.event, ROOT_FOLDER_ID)
    person_folder_id = get_or_create_folder(drive, args.name, event_folder_id)

    archive_existing(drive, person_folder_id)

    print("  Copying expense template...")
    doc_id = copy_file(drive, TEMPLATE_DOC_ID, person_folder_id, "Expenses")
    make_world_editable(drive, doc_id)

    doc_url  = f"https://docs.google.com/document/d/{doc_id}/edit"
    form_url = make_form_url(args.name, args.event)

    print(f"\n  Folder: https://drive.google.com/drive/folders/{person_folder_id}")
    print(f"  Doc:    {doc_url}")
    print(f"  Form:   {form_url}")

    compose_onboard_email(args.name, args.email, args.event, doc_url, form_url)


if __name__ == "__main__":
    main()
