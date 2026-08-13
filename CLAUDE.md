# HSF India Receipts — Project Notes

## Purpose
Generate Google Docs with expense tables (Meals and Transportation) for guest reimbursement,
with exchange rate conversions to both USD and GBP sourced from PDF rate tables.

## Conda environment
```
conda activate hsfindia
```
Packages: `google-api-python-client`, `google-auth-oauthlib`, `pdfplumber`, `pypdf`,
`beautifulsoup4`, `requests`, `playwright` (+ Chromium via `playwright install chromium`)

## Google API credentials
- `google_apps.json` — OAuth client credentials (do not commit)
- `token.json` — generated on first run, cached thereafter (do not commit)
- Scopes: Google Docs + Google Drive

## Scripts

### `create_doc.py`
Creates a blank **template** Google Doc with empty Meals and Transportation tables.
Edit `BLANK_ROWS` at the top to control how many empty rows each table gets.

### `fill_receipts.py`
Creates a full receipt doc for one guest with four tables:
- Meals (USD) and Transportation (USD)
- Meals (GBP) and Transportation (GBP)

**To use for a new guest**, edit the top of the file:
```python
NAME = "Guest Name"

MEALS_RAW = [
    ("Month DD", "Description", "amount CUR"),
    ...
]

TRANSPORT_RAW = [
    ("Month DD", "Provider", "amount CUR"),
    ...
]
```
Supported currencies: `GBP`, `INR`. Cost strings must be `"amount CUR"` e.g. `"12 GBP"` or `"1600 INR"`.

### `get_rates.py`
Fetches historical exchange rates from [x-rates.com](https://www.x-rates.com) for a given
base currency and date. Saves the page as a PDF for documentation and prints rates as JSON
to stdout (warnings go to stderr so the output is pipeable).

```bash
python get_rates.py --from USD --date 2026-07-14 --to INR GBP
# PDF saved: ./USD_rates_2026-07-14.pdf
# {"date": "2026-07-14", "base": "USD", "rates": {"INR": 94.32, "GBP": 0.747}}
```

Flags:
- `--from` — base currency code (e.g. `USD`, `GBP`)
- `--date` — date as `YYYY-MM-DD`
- `--to` — one or more target currency codes
- `--no-pdf` — skip PDF generation (useful in pipelines)
- `--pdf-dir` — directory to save PDF (default: current directory)

To consume the output in another Python script:
```python
import json, subprocess
result = json.loads(subprocess.check_output([
    "conda", "run", "-n", "hsfindia",
    "python", "get_rates.py",
    "--from", "USD", "--date", "2026-07-14",
    "--to", "INR", "GBP", "--no-pdf"
]))
rate_inr = result["rates"]["INR"]
```

### `append_other.py`
Fills in an "Other conversions" table that was manually added to an existing doc,
then appends a corresponding USD table. Edit `DOC_ID` and `OTHER_RAW` at the top.

## Exchange rates
Rates are fetched from [x-rates.com](https://www.x-rates.com) via `get_rates.py`, which
also saves a PDF for documentation. No manual downloads needed.

Legacy PDFs (`gbp_to_usd.pdf`, `inr_to_usd.pdf`, `inr_to_gbp.pdf`) were sourced from
[exchangerates.org.uk](https://www.exchangerates.org.uk) and used by `fill_receipts.py`
and `append_other.py`. Each covers a 180-day window — refresh if expenses fall outside it.

USD → GBP is computed automatically as `1 / GBP→USD rate` (no separate PDF needed).

## Key implementation notes
- **Cell fill order**: always fill table cells right-to-left, bottom-to-top within a single
  `batchUpdate` call, otherwise earlier inserts shift the indices of later cells.
- **Table heading style**: `HEADING_2`
- **Total row**: placed in column 0 (not column 1)
- The `parse_rates()` regex matches exchangerates.org.uk PDF format:
  `Weekday DD Month YYYY  1 CUR = X.XXXX CUR`
