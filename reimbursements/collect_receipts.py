"""
Collect uploaded files from the shared HSF-India upload form for a specific guest.

Filters responses by the pre-filled name and event fields, moves uploaded files
into the guest's Drive folder, and creates a Google Doc listing the receipt files.

Usage:
    python collect_receipts.py --event "HSF-India 2026" --name "Jane Smith"
"""

import argparse
import httplib2
import os
import sys
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google_auth_httplib2 import AuthorizedHttp
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
    current_parents = f.get("parents", [])
    if dest_folder_id in current_parents:
        return  # already in the right place
    drive.files().update(
        fileId=file_id,
        addParents=dest_folder_id,
        removeParents=",".join(current_parents),
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


def get_all_responses(forms, form_id):
    """Return all form responses (cached in a single call)."""
    return forms.forms().responses().list(formId=form_id).execute().get("responses", [])


def get_event_folder_ids(drive, root_folder_id):
    """Return IDs of all direct subfolder children of the root (the event folders)."""
    files = drive.files().list(
        q=(f"'{root_folder_id}' in parents"
           f" and mimeType='application/vnd.google-apps.folder'"
           f" and trashed=false"),
        fields="files(id)",
    ).execute().get("files", [])
    return {f["id"] for f in files}


def discover_submissions(responses):
    """
    Return a dict {event: {name: [file_id, ...]}} for responses with file uploads.
    """
    result = {}
    for response in responses:
        event = text_answer(response, EVENT_QUESTION_ID)
        name  = text_answer(response, NAME_QUESTION_ID)
        if not event or not name:
            continue
        for ans in response.get("answers", {}).values():
            if "fileUploadAnswers" not in ans:
                continue
            for fa in ans["fileUploadAnswers"]["answers"]:
                result.setdefault(event, {}).setdefault(name, [])
                fid = fa["fileId"]
                if fid not in result[event][name]:
                    result[event][name].append(fid)
    return result


def has_unprocessed_files(drive, file_ids, event_folder_ids):
    """
    Return True if at least one file has NOT yet been moved into the
    event folder hierarchy (i.e. its parent's parent is not an event folder).
    """
    for fid in file_ids:
        try:
            parents = drive.files().get(fileId=fid, fields="parents").execute().get("parents", [])
            for person_folder_id in parents:
                grandparents = drive.files().get(
                    fileId=person_folder_id, fields="parents"
                ).execute().get("parents", [])
                if not set(grandparents) & event_folder_ids:
                    return True  # parent's parent is not an event folder → not yet processed
        except Exception:
            pass
    return False


def interactive_select(drive, responses):
    """Prompt the user to pick an event and name from available submissions."""
    all_submissions = discover_submissions(responses)
    event_folder_ids = get_event_folder_ids(drive, ROOT_FOLDER_ID)

    print(f"  (Found {sum(len(g) for g in all_submissions.values())} total submission(s))", file=sys.stderr)

    # Filter to guests whose files haven't yet been moved into the event hierarchy
    submissions = {}
    for event, guests in all_submissions.items():
        for name, file_ids in guests.items():
            unprocessed = has_unprocessed_files(drive, file_ids, event_folder_ids)
            print(f"  {name} / {event}: {'unprocessed' if unprocessed else 'already moved'}", file=sys.stderr)
            if unprocessed:
                submissions.setdefault(event, []).append(name)

    if not submissions:
        if all_submissions:
            print("All submissions appear to have already been processed (files moved out of upload folders).")
        else:
            print("No file submissions found in the form.")
        sys.exit(0)

    pairs = sorted(
        (ev, nm)
        for ev, names in submissions.items()
        for nm in names
    )
    print("\nPending submissions:")
    for i, (ev, nm) in enumerate(pairs, 1):
        print(f"  {i}. {nm} ({ev})")
    while True:
        raw = input("\nSelect number: ").strip()
        if raw.isdigit() and 1 <= int(raw) <= len(pairs):
            return pairs[int(raw) - 1]
        print("  Invalid choice, try again.")


def collect_files_for_guest(forms, form_id, name, event, receipts_q_id, responses=None):
    """
    Return (all_files, receipt_files) for responses matching name + event.
    Each entry is (file_id, file_name).
    """
    if responses is None:
        responses = get_all_responses(forms, form_id)
    all_files     = []
    receipt_files = []
    seen = set()

    for response in responses:
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
# Expenses doc reader
# ---------------------------------------------------------------------------

def _cell_text(cell):
    """Extract plain text from a Google Docs table cell."""
    parts = []
    for content in cell.get("content", []):
        for elem in content.get("paragraph", {}).get("elements", []):
            parts.append(elem.get("textRun", {}).get("content", ""))
    return "".join(parts).strip()


def print_expenses_tables(docs, doc_id):
    """Read the Expenses doc and print all tables (with their headings) to stdout."""
    doc = docs.documents().get(documentId=doc_id).execute()
    body = doc["body"]["content"]

    heading = None
    found_any = False
    for element in body:
        if "paragraph" in element:
            para = element["paragraph"]
            style = para.get("paragraphStyle", {}).get("namedStyleType", "")
            if style in ("HEADING_1", "HEADING_2", "HEADING_3"):
                texts = [e.get("textRun", {}).get("content", "")
                         for e in para.get("elements", [])]
                heading = "".join(texts).strip()
        elif "table" in element:
            found_any = True
            rows = [
                [_cell_text(c) for c in row.get("tableCells", [])]
                for row in element["table"].get("tableRows", [])
            ]
            if not rows:
                continue
            ncols = max(len(r) for r in rows)
            col_widths = [
                max((len(r[c]) if c < len(r) else 0) for r in rows)
                for c in range(ncols)
            ]
            if heading:
                print(f"\n{heading}")
                print("=" * len(heading))
                heading = None
            for i, row in enumerate(rows):
                cells = [
                    (row[c] if c < len(row) else "").ljust(col_widths[c])
                    for c in range(ncols)
                ]
                print("  " + "  ".join(cells).rstrip())
                if i == 0:
                    print("  " + "  ".join("-" * w for w in col_widths))

    if not found_any:
        print("  (no tables found in Expenses doc)")


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
    parser.add_argument("--event", help="Event name (omit to pick interactively)")
    parser.add_argument("--name",  help="Guest full name (omit to pick interactively)")
    parser.add_argument("--debug", action="store_true", help="Print raw form responses and exit")
    args = parser.parse_args()

    creds = get_credentials()
    _http = AuthorizedHttp(creds, http=httplib2.Http(timeout=120))
    drive = build("drive", "v3", http=_http)
    forms = build("forms", "v1", http=_http)
    docs  = build("docs",  "v1", http=_http)

    responses = get_all_responses(forms, TEMPLATE_FORM_ID)

    if args.debug:
        print(f"Total responses in form: {len(responses)}")
        for i, r in enumerate(responses):
            print(f"\nResponse {i+1}:")
            for q_id, ans in r.get("answers", {}).items():
                if "textAnswers" in ans:
                    val = ans["textAnswers"]["answers"][0]["value"]
                    print(f"  [{q_id}] text: {val!r}")
                elif "fileUploadAnswers" in ans:
                    files = ans["fileUploadAnswers"]["answers"]
                    print(f"  [{q_id}] files: {[f.get('fileName') for f in files]}")
        return

    if args.event and args.name:
        event, name = args.event, args.name
    else:
        event, name = interactive_select(drive, responses)
        print(f"\nSelected: {name} / {event}")

    # Locate the person's Drive folder
    event_folder_id = find_folder(drive, event, ROOT_FOLDER_ID)
    if not event_folder_id:
        print(f"Event folder '{event}' not found.", file=sys.stderr)
        sys.exit(1)

    person_folder_id = find_folder(drive, name, event_folder_id)
    if not person_folder_id:
        print(f"Person folder '{name}' not found.", file=sys.stderr)
        sys.exit(1)

    # Identify the receipts question in the shared form
    receipts_q_id = get_receipts_question_id(forms, TEMPLATE_FORM_ID)
    if not receipts_q_id:
        print("Warning: could not identify receipts question.", file=sys.stderr)

    # Collect matching responses
    all_files, receipt_files = collect_files_for_guest(
        forms, TEMPLATE_FORM_ID, name, event, receipts_q_id, responses=responses
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
    doc_id = create_receipts_doc(docs, drive, person_folder_id, name, all_files, receipt_files)
    print(f"  https://docs.google.com/document/d/{doc_id}/edit")

    doc_id, _ = find_expenses_doc(drive, person_folder_id)
    if doc_id:
        print(f"\nExpenses doc: https://docs.google.com/document/d/{doc_id}/edit")
        print_expenses_tables(docs, doc_id)
        print(f"\nTo process:")
        print(f"  python process_template.py {doc_id}")
    else:
        print("\nWarning: 'Expenses' doc not found in person's folder.", file=sys.stderr)

    print("Done.")


if __name__ == "__main__":
    main()
