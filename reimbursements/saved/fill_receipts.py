import os
import re
import pdfplumber
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

NAME = "Denis Martynov"

# Existing doc to append GBP tables to
EXISTING_DOC_ID = "1DIgpOmvlUNqRzywmJ-4x40mI_cRqKkNkG6gfZIkqHdM"

# Format: (Date "Month DD", Description, Cost "amount CUR")
MEALS_RAW = [
    ("July 14", "Lunch",  "19 GBP"),
    ("July 14", "Dinner", "13 GBP"),
    ("July 18", "Lunch",  "34 GBP"),
    ("July 15", "Dinner", "1600 INR"),
]

TRANSPORT_RAW = [
    ("July 14", "Uber", "36.11 GBP"),
    ("July 14", "UK train", "35.00 GBP"),
    ("July 15", "Uber", "1500 INR"),
    ("July 18", "Uber", "15 GBP"),
    ("July 18", "UK train", "36.11 GBP"),
]

RATE_PDFS_USD = {
    "GBP": "gbp_to_usd.pdf",
    "INR": "inr_to_usd.pdf",
}

RATE_PDFS_GBP = {
    "INR": "inr_to_gbp.pdf",
    # GBP -> GBP is always 1.0, handled in build_rows
}

MONTHS = {
    "January": 1, "February": 2, "March": 3, "April": 4,
    "May": 5, "June": 6, "July": 7, "August": 8,
    "September": 9, "October": 10, "November": 11, "December": 12,
}


def parse_rates(pdf_path):
    """Return {(month, day): rate} from an exchangerates.org.uk PDF."""
    rates = {}
    # Matches lines like: "Tuesday 14 July 2026 1 GBP = 1.3391 USD ..."
    pattern = re.compile(
        r"\w+ (\d{1,2}) (\w+) \d{4}\s+1 \w+ = ([0-9.]+) \w+"
    )
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            for match in pattern.finditer(text):
                day = int(match.group(1))
                month = MONTHS.get(match.group(2))
                rate = float(match.group(3))
                if month:
                    rates[(month, day)] = rate
    return rates


def parse_cost(cost_str):
    """Parse '12 GBP' -> (12.0, 'GBP') or '1600 INR' -> (1600.0, 'INR')."""
    parts = cost_str.strip().split()
    return float(parts[0]), parts[1]


def parse_date(date_str):
    """Parse 'July 14' -> (7, 14)."""
    parts = date_str.strip().split()
    return MONTHS[parts[0]], int(parts[1])


def build_rows(raw_rows, all_rates, target_currency):
    """Add Rate and Cost (target_currency) columns, plus a Total row."""
    rows = []
    total = 0.0
    for date_str, description, cost_str in raw_rows:
        amount, currency = parse_cost(cost_str)
        month, day = parse_date(date_str)

        if currency == target_currency:
            rate = 1.0
        else:
            rate = all_rates.get(currency, {}).get((month, day))

        if rate is not None:
            converted = amount * rate
            total += converted
            converted_str = f"{converted:.2f}"
            rate_str = str(rate)
        else:
            converted_str = ""
            rate_str = ""
            print(f"  Warning: no rate found for {currency} on {date_str}")
        rows.append((date_str, description, cost_str, rate_str, converted_str))
    rows.append(("Total", "", "", "", f"{total:.2f}"))
    return rows


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


def fill_table_requests(table_el, headers, rows_data):
    """Fill all cells right-to-left, bottom-to-top so inserts don't shift later indices."""
    requests = []
    all_rows = [headers] + [list(row) for row in rows_data]

    for r_idx in reversed(range(len(table_el["tableRows"]))):
        if r_idx >= len(all_rows):
            continue
        row_data = all_rows[r_idx]
        table_row = table_el["tableRows"][r_idx]

        for c_idx in reversed(range(len(table_row["tableCells"]))):
            text = str(row_data[c_idx]) if c_idx < len(row_data) else ""
            if not text:
                continue
            cell_index = table_row["tableCells"][c_idx]["content"][0]["startIndex"]
            requests.append(
                {"insertText": {"location": {"index": cell_index}, "text": text}}
            )
            if r_idx == 0:
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


def append_tables(doc_id, docs, tables):
    """Append a list of table definitions to an existing document."""
    for table_def in tables:
        n_rows = 1 + len(table_def["rows"])

        doc = docs.documents().get(documentId=doc_id).execute()
        end_index = doc["body"]["content"][-1]["endIndex"] - 1

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
                    "rows": n_rows,
                    "columns": len(table_def["headers"]),
                    "location": {"index": end_index + len(heading_text)},
                }
            },
        ]
        docs.documents().batchUpdate(
            documentId=doc_id, body={"requests": setup_requests}
        ).execute()

        doc = docs.documents().get(documentId=doc_id).execute()
        tables_in_doc = [el["table"] for el in doc["body"]["content"] if "table" in el]
        new_table = tables_in_doc[-1]

        fill_requests = fill_table_requests(
            new_table, table_def["headers"], table_def["rows"]
        )
        docs.documents().batchUpdate(
            documentId=doc_id, body={"requests": fill_requests}
        ).execute()

        print(f"  {table_def['heading']} table done.")


if __name__ == "__main__":
    print("Parsing exchange rates from PDFs...")
    rates_usd = {cur: parse_rates(path) for cur, path in RATE_PDFS_USD.items()}
    rates_gbp = {cur: parse_rates(path) for cur, path in RATE_PDFS_GBP.items()}

    tables_usd = [
        {
            "heading": "Meals",
            "headers": ["Date", "Meal", "Cost", "Rate", "Cost (USD)"],
            "rows": build_rows(MEALS_RAW, rates_usd, "USD"),
        },
        {
            "heading": "Transportation",
            "headers": ["Date", "Provider", "Cost", "Rate", "Cost (USD)"],
            "rows": build_rows(TRANSPORT_RAW, rates_usd, "USD"),
        },
    ]

    tables_gbp = [
        {
            "heading": "Meals (GBP)",
            "headers": ["Date", "Meal", "Cost", "Rate", "Cost (GBP)"],
            "rows": build_rows(MEALS_RAW, rates_gbp, "GBP"),
        },
        {
            "heading": "Transportation (GBP)",
            "headers": ["Date", "Provider", "Cost", "Rate", "Cost (GBP)"],
            "rows": build_rows(TRANSPORT_RAW, rates_gbp, "GBP"),
        },
    ]

    creds = get_credentials()
    docs = build("docs", "v1", credentials=creds)

    # Create new doc with USD tables
    doc = docs.documents().create(body={"title": NAME}).execute()
    doc_id = doc["documentId"]
    print(f"Created document: https://docs.google.com/document/d/{doc_id}/edit")
    append_tables(doc_id, docs, tables_usd)

    # Append GBP tables to the same doc
    print("Appending GBP tables...")
    append_tables(doc_id, docs, tables_gbp)

    print("Done.")
