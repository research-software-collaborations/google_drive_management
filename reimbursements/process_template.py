"""
Read a filled HSF-India reimbursement template Google Doc, compute USD conversions,
fill in guest_reimbursement_request.pdf, and save everything to a Google Drive folder.

Usage:
    python process_template.py DOC_ID
"""

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
import os
from datetime import date
import requests
from bs4 import BeautifulSoup
from pypdf import PdfReader, PdfWriter
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google.oauth2.credentials import Credentials
from playwright.sync_api import sync_playwright

TOKEN_FILE = "token.json"
TEMPLATE_PDF = "guest_reimbursement_request.pdf"

MONTHS = {
    "January": 1, "February": 2, "March": 3, "April": 4,
    "May": 5, "June": 6, "July": 7, "August": 8,
    "September": 9, "October": 10, "November": 11, "December": 12,
}

CURRENCY_NAMES = {
    "AUD": "Australian Dollar",
    "CAD": "Canadian Dollar",
    "CHF": "Swiss Franc",
    "CNY": "Chinese Yuan Renminbi",
    "CZK": "Czech Koruna",
    "DKK": "Danish Krone",
    "EUR": "Euro",
    "GBP": "British Pound",
    "HKD": "Hong Kong Dollar",
    "HUF": "Hungarian Forint",
    "INR": "Indian Rupee",
    "JPY": "Japanese Yen",
    "MXN": "Mexican Peso",
    "MYR": "Malaysian Ringgit",
    "NOK": "Norwegian Krone",
    "NZD": "New Zealand Dollar",
    "PLN": "Polish Zloty",
    "RON": "Romanian New Leu",
    "SEK": "Swedish Krona",
    "SGD": "Singapore Dollar",
    "THB": "Thai Baht",
    "TRY": "Turkish Lira",
    "TWD": "Taiwan New Dollar",
    "USD": "US Dollar",
    "ZAR": "South African Rand",
}

SECTION_HEADINGS = {
    "Personal information",
    "Trip information",
    "Flights, visas, etc",
    "Meals",
    "Local Transportation",
}


# ---------------------------------------------------------------------------
# Date helpers
# ---------------------------------------------------------------------------

def infer_year(month, day):
    today = date.today()
    candidate = date(today.year, month, min(day, 28))
    return today.year if candidate <= today else today.year - 1


def parse_date(date_str):
    """Parse 'June 2', 'June 2, 2026', or '1/4' (month/day) -> (month, day, year)."""
    s = date_str.strip()
    # Numeric M/D or M/D/YY or M/D/YYYY
    m = re.match(r"(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?$", s)
    if m:
        month, day = int(m.group(1)), int(m.group(2))
        if m.group(3):
            y = int(m.group(3))
            year = y + 2000 if y < 100 else y
        else:
            year = infer_year(month, day)
        return month, day, year
    # Word month: 'June 2' or 'June 2, 2026'
    m = re.match(r"(\w+)\s+(\d+),?\s*(\d{4})?", s)
    if not m:
        raise ValueError(f"Cannot parse date: {date_str!r}")
    month_name, day = m.group(1), int(m.group(2))
    month = MONTHS.get(month_name)
    if not month:
        raise ValueError(f"Unknown month: {month_name!r}")
    year = int(m.group(3)) if m.group(3) else infer_year(month, day)
    return month, day, year


def to_iso(month, day, year):
    return f"{year:04d}-{month:02d}-{day:02d}"


def to_form_date(month, day, year):
    return f"{month:02d}/{day:02d}/{year % 100:02d}"


# ---------------------------------------------------------------------------
# Exchange rates
# ---------------------------------------------------------------------------

def fetch_usd_rates(date_iso):
    """Return {currency_name: units_per_usd} for the given date."""
    url = f"https://www.x-rates.com/historical/?from=USD&amount=1&date={date_iso}"
    r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    table = soup.find("table", class_="ratesTable")
    if not table:
        raise ValueError(f"No rates table found for {date_iso}")
    rates = {}
    for row in table.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) >= 2:
            name = cells[0].get_text(strip=True)
            try:
                rates[name] = float(cells[1].get_text(strip=True).replace(",", ""))
            except ValueError:
                pass
    return rates


def to_usd(amount, currency, date_iso, cache):
    """Return (usd_amount, rate) where rate is 1 currency = rate USD."""
    if currency == "USD":
        return amount, 1.0
    name = CURRENCY_NAMES.get(currency)
    if name is None:
        raise ValueError(
            f"Unknown currency code '{currency}'. "
            f"Add it to CURRENCY_NAMES in process_template.py."
        )
    if date_iso not in cache:
        print(f"  Fetching rates for {date_iso}...", file=sys.stderr)
        cache[date_iso] = fetch_usd_rates(date_iso)
    units_per_usd = cache[date_iso].get(name)
    if units_per_usd is None:
        raise ValueError(f"No rate for {currency} ({name}) on {date_iso} — x-rates.com may not list this currency")
    rate = 1.0 / units_per_usd
    return amount * rate, rate


def save_rate_pdf(date_iso, output_path):
    """Save x-rates.com historical page as PDF for documentation."""
    url = f"https://www.x-rates.com/historical/?from=USD&amount=1&date={date_iso}"
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(url, wait_until="networkidle")
        page.pdf(path=output_path, format="A4")
        browser.close()


# ---------------------------------------------------------------------------
# Google Doc parsing
# ---------------------------------------------------------------------------

def cell_text(cell):
    return "".join(
        e.get("textRun", {}).get("content", "").strip()
        for p in cell["content"]
        for e in p.get("paragraph", {}).get("elements", [])
    )


def parse_doc(doc_id):
    creds = Credentials.from_authorized_user_file(TOKEN_FILE)
    docs = build("docs", "v1", credentials=creds)
    doc = docs.documents().get(documentId=doc_id).execute()

    sections = {}
    current = None
    for el in doc["body"]["content"]:
        if "paragraph" in el:
            text = "".join(
                e.get("textRun", {}).get("content", "")
                for e in el["paragraph"].get("elements", [])
            ).strip()
            if text in SECTION_HEADINGS:
                current = text
        elif "table" in el and current:
            sections[current] = el["table"]
            current = None

    return sections


def parse_kv(table):
    return {
        cell_text(row["tableCells"][0]): cell_text(row["tableCells"][1])
        for row in table["tableRows"]
        if len(row["tableCells"]) >= 2
    }


def parse_expenses(table):
    rows = []
    for row in table["tableRows"][1:]:  # skip header
        cells = [cell_text(c) for c in row["tableCells"]]
        date_s, description, cost_s, currency = cells
        if not date_s and not description:
            continue
        rows.append({
            "date":        date_s.strip(),
            "description": description.strip(),
            "amount":      float(cost_s.strip()) if cost_s.strip() else 0.0,
            "currency":    currency.strip().upper(),
        })
    return rows


# ---------------------------------------------------------------------------
# PDF filling
# ---------------------------------------------------------------------------

def fill_pdf(personal, trip, flights, meals_total, transport_total, output_path):
    reader = PdfReader(TEMPLATE_PDF)
    writer = PdfWriter()
    writer.append(reader)

    today = date.today().strftime("%m/%d/%y")
    dep   = trip.get("Departure date", "")
    ret   = trip.get("Return date", "")
    event = trip.get("Name of HSF-India event", "")

    fields = {}

    # Personal
    fields["First Name"]     = personal.get("First name", "")
    fields["Middle Initial"] = personal.get("Middle Initial", "")
    fields["Last Name"]      = personal.get("Last name", "")
    fields["Traveler Name"]  = f"{personal.get('First name', '')} {personal.get('Last name', '')}".strip()
    fields["StreetAddress"]  = personal.get("Street address", "")
    fields["Apt"]            = personal.get("Apt.", "")
    fields["City"]           = personal.get("City", "")
    fields["State"]          = personal.get("State/Province", "")
    fields["Country"]        = personal.get("Country", "")
    fields["Zip"]            = personal.get("ZIP", "")
    fields["EmailAddress"]   = personal.get("Email address", "")
    fields["BusPhone"]       = personal.get("Business phone number", "")
    fields["Today's Date"]   = today
    fields["date1"]          = today

    # Trip
    for form_field, key in [("departureDate", "Departure date"), ("returnDate", "Return date")]:
        try:
            fields[form_field] = to_form_date(*parse_date(trip.get(key, "")))
        except ValueError:
            fields[form_field] = trip.get(key, "")

    fields["destination"] = trip.get("Destination (City,Country)", "")
    fields["purpose"]     = f"To participate in {event} ({dep} to {ret})"

    # Expense rows
    expense_rows = list(flights)
    if meals_total is not None:
        expense_rows.append({
            "date": "", "description": "Meals (breakout attached)",
            "usd_amount": meals_total, "currency": None,
        })
    if transport_total is not None:
        expense_rows.append({
            "date": "", "description": "Local Transportation (breakout attached)",
            "usd_amount": transport_total, "currency": None,
        })

    if len(expense_rows) > 12:
        print(f"Warning: {len(expense_rows)} expense rows — truncating to 12", file=sys.stderr)

    total_usd = 0.0
    for i, row in enumerate(expense_rows[:12], 1):
        fields[f"expenseDate{i}"]        = row.get("date", "")
        fields[f"expenseDescription{i}"] = row["description"]
        usd = row["usd_amount"]
        total_usd += usd
        fields[f"expenseAdvance{i}"] = f"{usd:.2f}"
        if row.get("currency") and row["currency"] != "USD":
            fields[f"Amount{i}"]   = str(row["amount"])
            fields[f"currency{i}"] = row["currency"]
            fields[f"Exchange{i}"] = f"{row['rate']:.6f}"

    fields["totalAdvancePayments"] = f"{total_usd:.2f}"

    for page in writer.pages:
        writer.update_page_form_field_values(page, fields, auto_regenerate=False)

    with open(output_path, "wb") as f:
        writer.write(f)


# ---------------------------------------------------------------------------
# Breakout Google Doc (Meals + Transportation with USD conversion)
# ---------------------------------------------------------------------------

def fill_table_requests(table_el, headers, rows_data):
    """Fill cells right-to-left, bottom-to-top so inserts don't shift indices."""
    requests = []
    all_rows = [headers] + [list(r) for r in rows_data]
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
            requests.append({"insertText": {"location": {"index": cell_index}, "text": text}})
            if r_idx == 0:
                requests.append({"updateTextStyle": {
                    "range": {"startIndex": cell_index, "endIndex": cell_index + len(text)},
                    "textStyle": {"bold": True}, "fields": "bold",
                }})
    return requests


def build_breakout_rows(expenses, rates_cache):
    """Return table rows [(date, description, cost_str, rate_str, usd_str)] + Total."""
    rows = []
    total = 0.0
    for e in expenses:
        month, day, year = parse_date(e["date"])
        date_iso = to_iso(month, day, year)
        usd, rate = to_usd(e["amount"], e["currency"], date_iso, rates_cache)
        total += usd
        cost_str = f"{e['amount']} {e['currency']}"
        rate_str = "" if e["currency"] == "USD" else f"{rate:.6f}"
        rows.append((e["date"], e["description"], cost_str, rate_str, f"{usd:.2f}"))
    rows.append(("Total", "", "", "", f"{total:.2f}"))
    return rows, total


def append_table_to_doc(docs, doc_id, heading, headers, rows):
    """Append a heading + filled table to an existing Google Doc."""
    doc = docs.documents().get(documentId=doc_id).execute()
    end_index = doc["body"]["content"][-1]["endIndex"] - 1
    heading_text = heading + "\n"

    docs.documents().batchUpdate(documentId=doc_id, body={"requests": [
        {"insertText": {"location": {"index": end_index}, "text": heading_text}},
        {"updateParagraphStyle": {
            "range": {"startIndex": end_index, "endIndex": end_index + len(heading_text)},
            "paragraphStyle": {"namedStyleType": "HEADING_2"}, "fields": "namedStyleType",
        }},
        {"insertTable": {
            "rows": len(rows) + 1, "columns": len(headers),
            "location": {"index": end_index + len(heading_text)},
        }},
    ]}).execute()

    doc = docs.documents().get(documentId=doc_id).execute()
    new_table = [el["table"] for el in doc["body"]["content"] if "table" in el][-1]
    fill_reqs = fill_table_requests(new_table, headers, rows)
    docs.documents().batchUpdate(documentId=doc_id, body={"requests": fill_reqs}).execute()


def create_breakout_doc(docs, drive, folder_id, meals_raw, transport_raw, rates_cache, name):
    """Create a Meals+Transportation breakout Google Doc in the Drive folder."""
    # Create the doc inside the folder via Drive API
    file_meta = drive.files().create(
        body={"name": name, "mimeType": "application/vnd.google-apps.document",
              "parents": [folder_id]},
        fields="id"
    ).execute()
    doc_id = file_meta["id"]

    headers = ["Date", "Description", "Cost", "Rate", "Cost (USD)"]

    if meals_raw:
        meals_rows, _ = build_breakout_rows(meals_raw, rates_cache)
        append_table_to_doc(docs, doc_id, "Meals", headers, meals_rows)

    if transport_raw:
        transport_rows, _ = build_breakout_rows(transport_raw, rates_cache)
        append_table_to_doc(docs, doc_id, "Local Transportation", headers, transport_rows)

    return doc_id


# ---------------------------------------------------------------------------
# Google Drive helpers
# ---------------------------------------------------------------------------


def find_drive_folder(drive, name):
    """Return the ID of an existing Drive folder with this name, or None."""
    results = drive.files().list(
        q=f"name='{name}' and mimeType='application/vnd.google-apps.folder' and trashed=false",
        fields="files(id, name)",
    ).execute()
    files = results.get("files", [])
    return files[0]["id"] if files else None


def create_drive_folder(drive, name, parent_id=None):
    body = {"name": name, "mimeType": "application/vnd.google-apps.folder"}
    if parent_id:
        body["parents"] = [parent_id]
    f = drive.files().create(body=body, fields="id").execute()
    return f["id"]


def list_folder_contents(drive, folder_id):
    """Return list of {id, name} for all non-trashed items in a folder."""
    results = drive.files().list(
        q=f"'{folder_id}' in parents and trashed=false",
        fields="files(id, name)",
    ).execute()
    return results.get("files", [])


def move_to_folder(drive, file_id, new_parent_id, old_parent_id):
    drive.files().update(
        fileId=file_id,
        addParents=new_parent_id,
        removeParents=old_parent_id,
        fields="id",
    ).execute()


def get_or_create_folder(drive, name):
    """Return folder_id, archiving existing contents to 'old/' if the folder exists."""
    folder_id = find_drive_folder(drive, name)
    if folder_id:
        print(f"  Folder '{name}' already exists — archiving contents to 'old/'...")
        contents = list_folder_contents(drive, folder_id)
        if contents:
            old_id = create_drive_folder(drive, "old", parent_id=folder_id)
            for item in contents:
                move_to_folder(drive, item["id"], old_id, folder_id)
            print(f"  Moved {len(contents)} item(s) to 'old/'")
    else:
        folder_id = create_drive_folder(drive, name)
        print(f"  Created new folder '{name}'")
    return folder_id


def copy_doc_to_folder(drive, doc_id, folder_id, name):
    drive.files().copy(
        fileId=doc_id,
        body={"name": name, "parents": [folder_id]}
    ).execute()


def upload_pdf(drive, local_path, folder_id, name):
    drive.files().create(
        body={"name": name, "parents": [folder_id]},
        media_body=MediaFileUpload(local_path, mimetype="application/pdf"),
        fields="id"
    ).execute()


# ---------------------------------------------------------------------------
# Mail
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


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Process HSF-India reimbursement template")
    parser.add_argument("doc_id", help="Google Doc ID of the filled template")
    args = parser.parse_args()

    print("Reading template doc...")
    sections = parse_doc(args.doc_id)

    personal      = parse_kv(sections["Personal information"])
    trip          = parse_kv(sections["Trip information"])
    flights_raw   = parse_expenses(sections["Flights, visas, etc"])
    meals_raw     = parse_expenses(sections["Meals"])
    transport_raw = parse_expenses(sections["Local Transportation"])

    rates_cache = {}

    flights = []
    for e in flights_raw:
        month, day, year = parse_date(e["date"])
        date_iso  = to_iso(month, day, year)
        date_form = to_form_date(month, day, year)
        usd_amount, rate = to_usd(e["amount"], e["currency"], date_iso, rates_cache)
        flights.append({
            "date":        date_form,
            "description": e["description"],
            "amount":      e["amount"],
            "currency":    e["currency"],
            "usd_amount":  usd_amount,
            "rate":        rate,
        })

    def sum_usd(expenses):
        t = 0.0
        for e in expenses:
            month, day, year = parse_date(e["date"])
            usd, _ = to_usd(e["amount"], e["currency"], to_iso(month, day, year), rates_cache)
            t += usd
        return t

    meals_total     = sum_usd(meals_raw)     if meals_raw     else None
    transport_total = sum_usd(transport_raw) if transport_raw else None

    last = personal.get("Last name", "Guest")

    creds = Credentials.from_authorized_user_file(TOKEN_FILE)
    drive = build("drive", "v3", credentials=creds)
    docs  = build("docs",  "v1", credentials=creds)

    folder_id = drive.files().get(
        fileId=args.doc_id, fields="parents"
    ).execute()["parents"][0]
    print(f"\nUsing folder: https://drive.google.com/drive/folders/{folder_id}")

    with tempfile.TemporaryDirectory() as tmpdir:
        pdf_name = "reimbursement.pdf"
        pdf_path = os.path.join(tmpdir, pdf_name)

        print("Filling PDF...")
        fill_pdf(personal, trip, flights, meals_total, transport_total, pdf_path)

        print(f"Saving exchange rate PDFs for dates: {sorted(rates_cache)}")
        rate_pdfs = []
        for date_iso in sorted(rates_cache):
            rate_pdf_name = f"rates_USD_{date_iso}.pdf"
            rate_pdf_path = os.path.join(tmpdir, rate_pdf_name)
            save_rate_pdf(date_iso, rate_pdf_path)
            rate_pdfs.append((rate_pdf_path, rate_pdf_name))
            print(f"  Saved {rate_pdf_name}")

        print("Uploading filled PDF...")
        upload_pdf(drive, pdf_path, folder_id, pdf_name)

        for path, name in rate_pdfs:
            print(f"Uploading {name}...")
            upload_pdf(drive, path, folder_id, name)

        print("Creating breakout doc...")
        breakout_name = "MealsTransportation_breakout"
        breakout_doc_id = create_breakout_doc(
            docs, drive, folder_id,
            meals_raw, transport_raw, rates_cache, breakout_name
        )

        print("Exporting breakout doc as PDF...")
        pdf_bytes = drive.files().export(
            fileId=breakout_doc_id, mimeType="application/pdf"
        ).execute()
        breakout_pdf_path = os.path.join(tmpdir, f"{breakout_name}.pdf")
        with open(breakout_pdf_path, "wb") as f:
            f.write(pdf_bytes)
        upload_pdf(drive, breakout_pdf_path, folder_id, f"{breakout_name}.pdf")

        shutil.copy(pdf_path, pdf_name)
        print(f"Saved local copy: {pdf_name}")

    print(f"\nDone. https://drive.google.com/drive/folders/{folder_id}")

    first = personal.get("First name", "")
    guest_email = personal.get("Email address", "")
    event = trip.get("Name of HSF-India event", "")
    subject = "HSF-India reimbursement form — please sign and return"
    body = (
        f"Dear {first},\n\n"
        f"Please find attached your reimbursement form for {event}.\n\n"
        f"Could you sign it (in the \"Signature of guest\" section) and return\n"
        f"the signed copy to us? A digital signature is preferred, or you\n"
        f"can sign and scan.\n\n"
        f"Many thanks,\nDavid\n"
    )
    open_mail_draft(guest_email, subject, body, attachment=pdf_name)
    print(f"Opened draft email to {guest_email} in Mail.app")

    print(f"\nSummary:")
    for f in flights:
        print(f"  {f['date']}  {f['description']:<30}  {f['amount']} {f['currency']}  -> ${f['usd_amount']:.2f}")
    if meals_total is not None:
        print(f"  Meals total:          ${meals_total:.2f}")
    if transport_total is not None:
        print(f"  Transportation total: ${transport_total:.2f}")
    grand = sum(f["usd_amount"] for f in flights) + (meals_total or 0) + (transport_total or 0)
    print(f"  Grand total (USD):    ${grand:.2f}")


if __name__ == "__main__":
    main()
