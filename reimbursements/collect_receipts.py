"""
Collect uploaded files from the shared HSF-India upload form for a specific guest.

Filters responses by the pre-filled name and event fields, moves uploaded files
into the guest's Drive folder, and creates a Google Doc listing the receipt files.

Usage:
    python collect_receipts.py --event "HSF-India 2026" --name "Jane Smith"
"""

import argparse
import os
import sys
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

TOKEN_FILE       = "token.json"
CREDENTIALS_FILE = "google_apps.json"
ROOT_FOLDER_ID   = "11MV67UxEjRDSf8ecl-5xKtwyAKGQu1Y3CUEV9JgMOkIiVVy32pjHDXxI2PW3p6OMoE824-aX"

# Shared upload form
TEMPLATE_FORM_ID = "1virimgEaohXGFdaraQ81EjCUjBmpn50JU9LRSEESYAk"
NAME_QUESTION_ID  = "4d16f245"
EVENT_QUESTION_ID = "736d15ce"

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

def find_expenses_doc(drive, folder_id):
    """Return (doc_id, doc_name) for the Expenses doc in the folder, or (None, None)."""
    files = drive.files().list(
        q=f"'{folder_id}' in parents and mimeType='application/vnd.google-apps.document' and name='Expenses' and trashed=false",
        fields="files(id, name)",
    ).execute().get("files", [])
    return (files[0]["id"], files[0]["name"]) if files else (None, None)


def find_folder(drive, name, parent_id):
    q = (
        f"name='{name}' and mimeType='application/vnd.google-apps.folder'"
        f" and '{parent_id}' in parents and trashed=false"
    )
    files = drive.files().list(q=q, fields="files(id)").execute().get("files", [])
    return files[0]["id"] if files else None


def move_file(drive, file_id, dest_folder_id):
    f = drive.files().get(fileId=file_id, fields="parents").execute()
    old_parents = ",".join(f.get("parents", []))
    drive.files().update(
        fileId=file_id,
        addParents=dest_folder_id,
        removeParents=old_parents,
        fields="id",
    ).execute()


# ---------------------------------------------------------------------------
# Forms helpers
# ---------------------------------------------------------------------------

def text_answer(response, question_id):
    """Extract the text value for a question from a response, or ''."""
    answer = response.get("answers", {}).get(question_id)
    if not answer:
        return ""
    answers = answer.get("textAnswers", {}).get("answers", [])
    return answers[0]["value"] if answers else ""


def get_receipts_question_id(forms, form_id):
    """Return questionId for the receipts file-upload question."""
    form = forms.forms().get(formId=form_id).execute()
    for item in form.get("items", []):
        if "questionItem" not in item:
            continue
        q = item["questionItem"]["question"]
        if "fileUploadQuestion" not in q:
            continue
        if "receipt" in item.get("title", "").lower():
            return q["questionId"]
    return None


def collect_files_for_guest(forms, form_id, name, event, receipts_q_id):
    """
    Return (all_files, receipt_files) for responses matching name + event.
    Each entry is (file_id, file_name).
    """
    resp = forms.forms().responses().list(formId=form_id).execute()
    all_files     = []
    receipt_files = []
    seen = set()

    for response in resp.get("responses", []):
        if text_answer(response, NAME_QUESTION_ID)  != name:
            continue
        if text_answer(response, EVENT_QUESTION_ID) != event:
            continue

        for q_id, answer in response.get("answers", {}).items():
            if "fileUploadAnswers" not in answer:
                continue
            for fa in answer["fileUploadAnswers"]["answers"]:
                file_id   = fa["fileId"]
                file_name = fa.get("fileName", file_id)
                if file_id in seen:
                    continue
                seen.add(file_id)
                all_files.append((file_id, file_name))
                if q_id == receipts_q_id:
                    receipt_files.append((file_id, file_name))

    return all_files, receipt_files


# ---------------------------------------------------------------------------
# Receipts doc
# ---------------------------------------------------------------------------

def create_receipts_doc(docs, drive, folder_id, name, all_files, receipt_files):
    file_meta = drive.files().create(
        body={"name": "Receipts", "mimeType": "application/vnd.google-apps.document",
              "parents": [folder_id]},
        fields="id",
    ).execute()
    doc_id = file_meta["id"]

    receipt_ids = {fid for fid, _ in receipt_files}
    other_files = [(fid, fname) for fid, fname in all_files if fid not in receipt_ids]

    def section_text(heading, files):
        return heading + "\n" + "".join(f"{fname}\n" for _, fname in files)

    parts = []
    if receipt_files:
        parts.append((f"Receipts — {name}\n", receipt_files, "HEADING_1"))
    if other_files:
        parts.append(("Other uploaded documents\n", other_files, "HEADING_2"))

    if not parts:
        docs.documents().batchUpdate(documentId=doc_id, body={"requests": [
            {"insertText": {"location": {"index": 1},
                            "text": f"No files uploaded by {name}.\n"}},
        ]}).execute()
        return doc_id

    full_text = "".join(
        heading + "".join(f"{fname}\n" for _, fname in files)
        for heading, files, _ in parts
    )

    requests = [{"insertText": {"location": {"index": 1}, "text": full_text}}]

    idx = 1
    for heading, files, style in parts:
        requests.append({"updateParagraphStyle": {
            "range": {"startIndex": idx, "endIndex": idx + len(heading)},
            "paragraphStyle": {"namedStyleType": style},
            "fields": "namedStyleType",
        }})
        idx += len(heading)
        for file_id, fname in files:
            requests.append({"updateTextStyle": {
                "range": {"startIndex": idx, "endIndex": idx + len(fname)},
                "textStyle": {"link": {"url": f"https://drive.google.com/file/d/{file_id}/view"}},
                "fields": "link",
            }})
            idx += len(fname) + 1  # +1 for \n

    docs.documents().batchUpdate(documentId=doc_id, body={"requests": requests}).execute()
    return doc_id


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Collect uploaded files for a guest")
    parser.add_argument("--event", required=True, help="Event name")
    parser.add_argument("--name",  required=True, help="Guest full name")
    parser.add_argument("--debug", action="store_true", help="Print raw form responses and exit")
    args = parser.parse_args()

    creds = get_credentials()
    drive = build("drive", "v3", credentials=creds)
    forms = build("forms", "v1", credentials=creds)
    docs  = build("docs",  "v1", credentials=creds)

    # Locate the person's Drive folder
    event_folder_id = find_folder(drive, args.event, ROOT_FOLDER_ID)
    if not event_folder_id:
        print(f"Event folder '{args.event}' not found.", file=sys.stderr)
        sys.exit(1)

    person_folder_id = find_folder(drive, args.name, event_folder_id)
    if not person_folder_id:
        print(f"Person folder '{args.name}' not found.", file=sys.stderr)
        sys.exit(1)

    # Identify the receipts question in the shared form
    receipts_q_id = get_receipts_question_id(forms, TEMPLATE_FORM_ID)
    if not receipts_q_id:
        print("Warning: could not identify receipts question.", file=sys.stderr)

    if args.debug:
        resp = forms.forms().responses().list(formId=TEMPLATE_FORM_ID).execute()
        print(f"Total responses in form: {len(resp.get('responses', []))}")
        for i, r in enumerate(resp.get("responses", [])):
            print(f"\nResponse {i+1}:")
            for q_id, ans in r.get("answers", {}).items():
                if "textAnswers" in ans:
                    val = ans["textAnswers"]["answers"][0]["value"]
                    print(f"  [{q_id}] text: {val!r}")
                elif "fileUploadAnswers" in ans:
                    files = ans["fileUploadAnswers"]["answers"]
                    print(f"  [{q_id}] files: {[f.get('fileName') for f in files]}")
        return

    # Collect matching responses
    all_files, receipt_files = collect_files_for_guest(
        forms, TEMPLATE_FORM_ID, args.name, args.event, receipts_q_id
    )
    print(f"Found {len(all_files)} uploaded file(s), {len(receipt_files)} receipt(s).")

    if not all_files:
        print("No files to move.")
        return

    print("Moving files...")
    for file_id, file_name in all_files:
        move_file(drive, file_id, person_folder_id)
        print(f"  {file_name}")

    print("Creating Receipts doc...")
    doc_id = create_receipts_doc(docs, drive, person_folder_id, args.name, all_files, receipt_files)
    print(f"  https://docs.google.com/document/d/{doc_id}/edit")

    doc_id, _ = find_expenses_doc(drive, person_folder_id)
    if doc_id:
        print(f"\nExpenses doc ready. To process:")
        print(f"  python process_template.py {doc_id}")
    else:
        print("\nWarning: 'Expenses' doc not found in person's folder.", file=sys.stderr)

    print("Done.")


if __name__ == "__main__":
    main()
