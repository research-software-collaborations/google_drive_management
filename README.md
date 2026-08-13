# HSF-India Guest Reimbursement

Scripts for managing HSF-India guest reimbursements: setting up Google Drive folders,
processing expense forms, and filling the Princeton reimbursement PDF.

## Setup

### Conda environment

```bash
conda create -n hsfindia python=3.11
conda activate hsfindia
pip install google-api-python-client google-auth-oauthlib pdfplumber pypdf \
            beautifulsoup4 requests playwright
playwright install chromium
```

### Google API credentials

1. Create a project in [Google Cloud Console](https://console.cloud.google.com)
2. Enable the **Google Docs API**, **Google Drive API**, and **Google Forms API**
3. Create an OAuth 2.0 client ID (Desktop app) and download it as `google_apps.json`
4. Add any non-owner Google accounts as test users under APIs & Services → OAuth consent screen

On first run, a browser window will open for authentication. The token is cached in
`token.json` thereafter. Neither file should be committed to version control.

---

## Workflow

### 1. Onboard a guest

Creates the Drive folder structure, copies the expense template, generates a pre-filled
upload form link, and opens a draft email in Mail.app.

```bash
python onboard_guest.py --event "HSF-India Summer 2026" \
                        --name "Jane Smith" \
                        --email jane@example.com
```

The guest receives:
- A link to fill in their expense details (Google Doc)
- A link to upload receipts and on-boarding documents (Google Form)

Drive structure created: `<root>/<event>/<name>/`

---

### 2. Collect uploaded receipts

Once the guest has submitted the form, moves their uploaded files into their Drive folder
and creates a **Receipts** Google Doc listing them.

```bash
python collect_receipts.py --event "HSF-India Summer 2026" --name "Jane Smith"
```

At the end, prints the command to run next:

```
Expenses doc ready. To process:
  python process_template.py <doc_id>
```

Use `--debug` to inspect raw form responses without moving any files.

---

### 3. Process the expense template

Reads the filled Google Doc, converts all amounts to USD, fills in
`guest_reimbursement_request.pdf`, uploads everything to the guest's Drive folder,
saves a local `reimbursement.pdf`, and opens a signature-request email in Mail.app.

```bash
python process_template.py <doc_id>
```

Files uploaded to Drive:
- `reimbursement.pdf` — filled Princeton reimbursement form
- `MealsTransportation_breakout` — Google Doc with USD-converted expense tables
- `MealsTransportation_breakout.pdf`
- `rates_USD_YYYY-MM-DD.pdf` — exchange rate documentation (one per non-USD date)

---

## Other scripts

### `get_rates.py`

Fetches historical exchange rates from [x-rates.com](https://www.x-rates.com) and
optionally saves the page as a PDF for documentation.

```bash
python get_rates.py --from USD --date 2026-07-14 --to INR GBP
# PDF saved: ./USD_rates_2026-07-14.pdf
# {"date": "2026-07-14", "base": "USD", "rates": {"INR": 94.32, "GBP": 0.747}}
```

Use `--no-pdf` to skip PDF generation (useful in pipelines).

### `create_doc.py`

Creates a blank expense template Google Doc with empty Meals and Transportation tables.
Useful for setting up a new template. Edit `BLANK_ROWS` at the top to control row count.

```bash
python create_doc.py
```

### `fill_receipts.py`

Legacy script for creating a receipt doc by editing the `NAME`, `MEALS_RAW`, and
`TRANSPORT_RAW` constants directly in the file. Superseded by `process_template.py`.

### `append_other.py`

Fills an "Other conversions" table in an existing Google Doc and appends a USD version.
Edit `DOC_ID` and `OTHER_RAW` at the top of the file before running.

---

## Expense template format

The Google Doc template has five sections read by `process_template.py`:

| Section | Fields |
|---|---|
| Personal information | First name, Last name, Street address, City, State/Province, Country, ZIP, Email address, Business phone number |
| Trip information | Name of HSF-India event, Departure date, Return date, Destination (City, Country) |
| Flights, visas, etc | Date, Description, Amount, Currency |
| Meals | Date, Description, Amount, Currency |
| Local Transportation | Date, Description, Amount, Currency |

Dates accept `June 2`, `June 2, 2026`, or `1/4` (month/day) format.
Supported currencies: AUD, CAD, CHF, CNY, CZK, DKK, EUR, GBP, HKD, HUF, INR, JPY,
MXN, MYR, NOK, NZD, PLN, RON, SEK, SGD, THB, TRY, TWD, USD, ZAR.
