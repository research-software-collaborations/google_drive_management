"""
Merge receipt PDFs listed in a guest's Receipts Google Doc into a single merged.pdf,
then upload it to their Drive folder.

Usage:
    python merge_receipts.py --event "HSF-India Summer 2026" --name "Jane Smith"
"""

import argparse
import io
import os
import re
import sys
import tempfile
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload
from pypdf import PdfWriter

TOKEN_FILE       = "token.json"
CREDENTIALS_FILE = "google_apps.json"
ROOT_FOLDER_ID   = "11MV67UxEjRDSf8ecl-5xKtwyAKGQu1Y3CUEV9JgMOkIiVVy32pjHDXxI2PW3p6OMoE824-aX"

SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/documents",
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


def find_folder(drive, name, parent_id):
    q = (
        f"name='{name}' and mimeType='application/vnd.google-apps.folder'"
        f" and '{parent_id}' in parents and trashed=false"
    )
    files = drive.files().list(q=q, fields="files(id)").execute().get("files", [])
    return files[0]["id"] if files else None


def find_doc(drive, folder_id, name):
    files = drive.files().list(
        q=f"'{folder_id}' in parents and mimeType='application/vnd.google-apps.document'"
          f" and name='{name}' and trashed=false",
        fields="files(id)",
    ).execute().get("files", [])
    return files[0]["id"] if files else None


def extract_file_ids_from_doc(docs, doc_id):
    """Return list of Drive file IDs linked in the Receipts doc, in order."""
    doc = docs.documents().get(documentId=doc_id).execute()
    file_ids = []
    seen = set()
    for el in doc["body"]["content"]:
        if "paragraph" not in el:
            continue
        for run in el["paragraph"].get("elements", []):
            url = run.get("textRun", {}).get("textStyle", {}).get("link", {}).get("url", "")
            m = re.search(r"/file/d/([^/]+)/", url)
            if m:
                fid = m.group(1)
                if fid not in seen:
                    seen.add(fid)
                    file_ids.append(fid)
    return file_ids


def download_file(drive, file_id):
    """Download a Drive file and return its contents as a BytesIO buffer."""
    request = drive.files().get_media(fileId=file_id)
    buf = io.BytesIO()
    downloader = MediaIoBaseDownload(buf, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    buf.seek(0)
    return buf


def upload_pdf(drive, local_path, folder_id, name):
    # Remove existing merged.pdf if present
    existing = drive.files().list(
        q=f"'{folder_id}' in parents and name='{name}' and trashed=false",
        fields="files(id)",
    ).execute().get("files", [])
    for f in existing:
        drive.files().delete(fileId=f["id"]).execute()

    drive.files().create(
        body={"name": name, "parents": [folder_id]},
        media_body=MediaFileUpload(local_path, mimetype="application/pdf"),
        fields="id",
    ).execute()


def main():
    parser = argparse.ArgumentParser(description="Merge receipt PDFs for a guest")
    parser.add_argument("--event", required=True, help="Event name")
    parser.add_argument("--name",  required=True, help="Guest full name")
    args = parser.parse_args()

    creds = get_credentials()
    drive = build("drive", "v3", credentials=creds)
    docs  = build("docs",  "v1", credentials=creds)

    # Locate the person's folder
    event_folder_id = find_folder(drive, args.event, ROOT_FOLDER_ID)
    if not event_folder_id:
        print(f"Event folder '{args.event}' not found.", file=sys.stderr)
        sys.exit(1)

    person_folder_id = find_folder(drive, args.name, event_folder_id)
    if not person_folder_id:
        print(f"Person folder '{args.name}' not found.", file=sys.stderr)
        sys.exit(1)

    # Find the Receipts doc
    receipts_doc_id = find_doc(drive, person_folder_id, "Receipts")
    if not receipts_doc_id:
        print("'Receipts' doc not found in person's folder.", file=sys.stderr)
        sys.exit(1)

    # Extract file IDs from links in the doc
    file_ids = extract_file_ids_from_doc(docs, receipts_doc_id)
    if not file_ids:
        print("No file links found in Receipts doc.", file=sys.stderr)
        sys.exit(1)
    print(f"Found {len(file_ids)} file(s) to merge.")

    # Download and merge
    writer = PdfWriter()
    for file_id in file_ids:
        meta = drive.files().get(fileId=file_id, fields="name, mimeType").execute()
        name = meta.get("name", file_id)
        mime = meta.get("mimeType", "")
        if "pdf" not in mime.lower() and not name.lower().endswith(".pdf"):
            print(f"  Skipping non-PDF: {name}")
            continue
        print(f"  Adding: {name}")
        buf = download_file(drive, file_id)
        writer.append(buf)

    if len(writer.pages) == 0:
        print("No PDF pages found — nothing to merge.", file=sys.stderr)
        sys.exit(1)

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp_path = tmp.name
        writer.write(tmp)

    try:
        print("Uploading merged.pdf...")
        upload_pdf(drive, tmp_path, person_folder_id, "merged.pdf")
        print(f"Done. https://drive.google.com/drive/folders/{person_folder_id}")
    finally:
        os.unlink(tmp_path)


if __name__ == "__main__":
    main()
