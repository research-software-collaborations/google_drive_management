import re
import pdfplumber
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

TOKEN_FILE = "token.json"
DOC_ID = "1h3_XEKz2V17iZWIQLF8LCDDXt24GhgfaoXHT2VIkJDY"

OTHER_RAW = [
    ("June 24", "Swiss",           "36221 INR"),
    ("June 24", "Virgin Atlantic", "755.29 GBP"),
    ("June 17", "Visa app",        "120 USD"),
]

RATE_PDFS = {
    ("INR", "GBP"): "inr_to_gbp.pdf",
    ("INR", "USD"): "inr_to_usd.pdf",
    ("GBP", "USD"): "gbp_to_usd.pdf",
    # GBP->USD is also used inverted to get USD->GBP
}

MONTHS = {
    "January": 1, "February": 2, "March": 3, "April": 4,
    "May": 5, "June": 6, "July": 7, "August": 8,
    "September": 9, "October": 10, "November": 11, "December": 12,
}


def parse_rates(pdf_path):
    rates = {}
    pattern = re.compile(r"\w+ (\d{1,2}) (\w+) \d{4}\s+1 \w+ = ([0-9.]+) \w+")
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            for m in pattern.finditer(text):
                day, month_name, rate = int(m.group(1)), m.group(2), float(m.group(3))
                month = MONTHS.get(month_name)
                if month:
                    rates[(month, day)] = rate
    return rates


def parse_cost(cost_str):
    parts = cost_str.strip().split()
    return float(parts[0]), parts[1]


def parse_date(date_str):
    parts = date_str.strip().split()
    return MONTHS[parts[0]], int(parts[1])


def get_rate(currency, target, all_rates, month, day):
    if currency == target:
        return 1.0
    # Direct lookup
    key = (currency, target)
    if key in all_rates:
        r = all_rates[key].get((month, day))
        if r is not None:
            return r
    # Invert GBP->USD to get USD->GBP
    inv_key = (target, currency)
    if inv_key in all_rates:
        r = all_rates[inv_key].get((month, day))
        if r is not None:
            return 1.0 / r
    return None


def build_rows(raw_rows, all_rates, target):
    rows = []
    total = 0.0
    for date_str, description, cost_str in raw_rows:
        amount, currency = parse_cost(cost_str)
        month, day = parse_date(date_str)
        rate = get_rate(currency, target, all_rates, month, day)
        if rate is not None:
            converted = amount * rate
            total += converted
            rate_str = f"{rate:.6g}"
            converted_str = f"{converted:.2f}"
        else:
            rate_str = ""
            converted_str = ""
            print(f"  Warning: no rate for {currency}->{target} on {date_str}")
        rows.append((date_str, description, cost_str, rate_str, converted_str))
    rows.append(("Total", "", "", "", f"{total:.2f}"))
    return rows


def fill_empty_cells(table_el, rows_data):
    """Fill only the Rate and Cost cells (cols 3 & 4) in existing data rows, right-to-left."""
    requests = []
    # rows_data excludes header; table rows include header at index 0
    for r_idx, row_data in reversed(list(enumerate(rows_data))):
        table_row = table_el["tableRows"][r_idx + 1]  # +1 to skip header
        for c_idx in reversed([3, 4]):
            text = str(row_data[c_idx])
            if not text:
                continue
            cell_index = table_row["tableCells"][c_idx]["content"][0]["startIndex"]
            requests.append(
                {"insertText": {"location": {"index": cell_index}, "text": text}}
            )
    return requests


def insert_total_row(doc_id, docs, table_el, table_start_index, total_str):
    """Append a Total row to the existing table, then fill it."""
    docs.documents().batchUpdate(
        documentId=doc_id,
        body={"requests": [{"insertTableRow": {"tableCellLocation": {
            "tableStartLocation": {"index": table_start_index},
            "rowIndex": len(table_el["tableRows"]) - 1,
            "columnIndex": 0,
        }, "insertBelow": True}}]},
    ).execute()

    # Re-fetch to get new row indices
    doc = docs.documents().get(documentId=doc_id).execute()
    updated_table = next(
        el["table"] for el in doc["body"]["content"]
        if "table" in el and el["startIndex"] == table_start_index
    )

    new_row = updated_table["tableRows"][-1]
    requests = []
    for c_idx, text in reversed([(0, "Total"), (4, total_str)]):
        cell_index = new_row["tableCells"][c_idx]["content"][0]["startIndex"]
        requests.append({"insertText": {"location": {"index": cell_index}, "text": text}})
    docs.documents().batchUpdate(documentId=doc_id, body={"requests": requests}).execute()
    return updated_table


def append_table(doc_id, docs, heading, headers, rows):
    doc = docs.documents().get(documentId=doc_id).execute()
    end_index = doc["body"]["content"][-1]["endIndex"] - 1
    heading_text = heading + "\n"
    n_rows = 1 + len(rows)

    docs.documents().batchUpdate(documentId=doc_id, body={"requests": [
        {"insertText": {"location": {"index": end_index}, "text": heading_text}},
        {"updateParagraphStyle": {
            "range": {"startIndex": end_index, "endIndex": end_index + len(heading_text)},
            "paragraphStyle": {"namedStyleType": "HEADING_2"},
            "fields": "namedStyleType",
        }},
        {"insertTable": {
            "rows": n_rows, "columns": len(headers),
            "location": {"index": end_index + len(heading_text)},
        }},
    ]}).execute()

    doc = docs.documents().get(documentId=doc_id).execute()
    new_table = [el["table"] for el in doc["body"]["content"] if "table" in el][-1]

    all_rows = [headers] + [list(r) for r in rows]
    requests = []
    for r_idx in reversed(range(len(new_table["tableRows"]))):
        if r_idx >= len(all_rows):
            continue
        row_data = all_rows[r_idx]
        table_row = new_table["tableRows"][r_idx]
        for c_idx in reversed(range(len(table_row["tableCells"]))):
            text = str(row_data[c_idx]) if c_idx < len(row_data) else ""
            if not text:
                continue
            cell_index = table_row["tableCells"][c_idx]["content"][0]["startIndex"]
            requests.append({"insertText": {"location": {"index": cell_index}, "text": text}})
            if r_idx == 0:
                requests.append({"updateTextStyle": {
                    "range": {"startIndex": cell_index, "endIndex": cell_index + len(text)},
                    "textStyle": {"bold": True}, "fields": "bold",
                }})
    docs.documents().batchUpdate(documentId=doc_id, body={"requests": requests}).execute()


if __name__ == "__main__":
    print("Parsing rates...")
    all_rates = {k: parse_rates(v) for k, v in RATE_PDFS.items()}

    creds = Credentials.from_authorized_user_file(TOKEN_FILE)
    docs = build("docs", "v1", credentials=creds)

    # Find the existing "Other conversions" table (last table in doc)
    doc = docs.documents().get(documentId=DOC_ID).execute()
    tables_in_doc = [el for el in doc["body"]["content"] if "table" in el]
    other_table_parent = tables_in_doc[-1]
    other_table_el = other_table_parent["table"]
    other_table_start = other_table_parent["startIndex"]

    gbp_rows = build_rows(OTHER_RAW, all_rates, "GBP")
    data_rows = gbp_rows[:-1]   # exclude Total — added separately
    total_str = gbp_rows[-1][4]

    print("Filling existing Other conversions (GBP) table...")
    fill_reqs = fill_empty_cells(other_table_el, data_rows)
    docs.documents().batchUpdate(documentId=DOC_ID, body={"requests": fill_reqs}).execute()

    # Re-fetch table after fills, then add Total row
    doc = docs.documents().get(documentId=DOC_ID).execute()
    other_table_el = next(
        el["table"] for el in doc["body"]["content"]
        if "table" in el and el["startIndex"] == other_table_start
    )
    insert_total_row(DOC_ID, docs, other_table_el, other_table_start, total_str)

    print("Appending Other conversions (USD) table...")
    usd_rows = build_rows(OTHER_RAW, all_rates, "USD")
    append_table(
        DOC_ID, docs,
        "Other conversions (USD)",
        ["Date", "Provider", "Cost", "Rate", "Cost (USD)"],
        usd_rows,
    )

    print("Done.")
