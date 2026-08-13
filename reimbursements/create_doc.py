import os
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = [
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/drive",
]

CREDENTIALS_FILE = "google_apps.json"
TOKEN_FILE = "token.json"

BLANK_ROWS = 5  # number of empty data rows in each table

TABLES = [
    {"heading": "Meals", "headers": ["Date", "Meal", "Cost", "Cost (USD)"]},
    {"heading": "Transportation", "headers": ["Date", "Provider", "Cost", "Cost (USD)"]},
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


def bold_header_requests(table_el, headers):
    """Return batchUpdate requests that bold the first row of a table."""
    requests = []
    header_row = table_el["tableRows"][0]
    for c_idx, cell in reversed(list(enumerate(header_row["tableCells"]))):
        cell_index = cell["content"][0]["startIndex"]
        text = headers[c_idx]
        requests.append(
            {"insertText": {"location": {"index": cell_index}, "text": text}}
        )
        requests.append(
            {
                "updateTextStyle": {
                    "range": {
                        "startIndex": cell_index,
                        "endIndex": cell_index + len(text),
                    },
                    "textStyle": {"bold": True},
                    "fields": "bold",
                }
            }
        )
    return requests


def create_template():
    creds = get_credentials()
    docs = build("docs", "v1", credentials=creds)

    doc = docs.documents().create(body={"title": "Receipts Template"}).execute()
    doc_id = doc["documentId"]
    print(f"Created document: https://docs.google.com/document/d/{doc_id}/edit")

    # Insert headings and tables for each section.
    # We build from the end of the document backwards so indices stay stable,
    # but the Docs API inserts at the given index so we go in order and
    # re-fetch after each batchUpdate to get fresh indices.
    for table_def in TABLES:
        doc = docs.documents().get(documentId=doc_id).execute()
        body = doc["body"]["content"]
        # Insert at the end — find the index of the final newline
        end_index = body[-1]["endIndex"] - 1

        heading_text = table_def["heading"] + "\n"
        setup_requests = [
            {
                "insertText": {
                    "location": {"index": end_index},
                    "text": heading_text,
                }
            },
            {
                "updateParagraphStyle": {
                    "range": {
                        "startIndex": end_index,
                        "endIndex": end_index + len(heading_text),
                    },
                    "paragraphStyle": {"namedStyleType": "HEADING_2"},
                    "fields": "namedStyleType",
                }
            },
            {
                "insertTable": {
                    "rows": BLANK_ROWS + 1,  # +1 for header row
                    "columns": len(table_def["headers"]),
                    "location": {"index": end_index + len(heading_text)},
                }
            },
        ]
        docs.documents().batchUpdate(
            documentId=doc_id, body={"requests": setup_requests}
        ).execute()

        # Re-fetch to get the table's cell indices, then fill the header row
        doc = docs.documents().get(documentId=doc_id).execute()
        tables_in_doc = [
            el["table"] for el in doc["body"]["content"] if "table" in el
        ]
        # The table we just inserted is the last one
        new_table = tables_in_doc[-1]
        fill_requests = bold_header_requests(new_table, table_def["headers"])
        docs.documents().batchUpdate(
            documentId=doc_id, body={"requests": fill_requests}
        ).execute()

    print("Template created successfully.")
    return doc_id


if __name__ == "__main__":
    create_template()
