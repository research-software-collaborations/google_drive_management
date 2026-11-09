"""
Fetch historical exchange rates from x-rates.com.

Usage:
    python get_rates.py --from USD --date 2026-07-14 --to INR GBP

Stdout (JSON, for use by other scripts):
    {"date": "2026-07-14", "base": "USD", "rates": {"INR": 94.32, "GBP": 0.747}}

Also saves a PDF:  USD_rates_2026-07-14.pdf
Use --no-pdf to skip PDF generation.
"""

import argparse
import json
import sys
import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

CURRENCY_NAMES = {
    "AED": "UAE Dirham",
    "AUD": "Australian Dollar",
    "CAD": "Canadian Dollar",
    "CHF": "Swiss Franc",
    "CNY": "Chinese Yuan Renminbi",
    "EUR": "Euro",
    "GBP": "British Pound",
    "HKD": "Hong Kong Dollar",
    "INR": "Indian Rupee",
    "JPY": "Japanese Yen",
    "MXN": "Mexican Peso",
    "NOK": "Norwegian Krone",
    "NZD": "New Zealand Dollar",
    "SEK": "Swedish Krona",
    "SGD": "Singapore Dollar",
    "THB": "Thai Baht",
    "TRY": "Turkish Lira",
    "USD": "US Dollar",
    "ZAR": "South African Rand",
}


def fetch_rates(base, date):
    """Return (url, {currency_name: rate}) for 1 unit of base currency."""
    url = f"https://www.x-rates.com/historical/?from={base}&amount=1&date={date}"
    r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    table = soup.find("table", class_="ratesTable")
    if not table:
        raise ValueError("Rates table not found on page — check the URL or date.")
    rates = {}
    for row in table.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) >= 2:
            name = cells[0].get_text(strip=True)
            try:
                rates[name] = float(cells[1].get_text(strip=True).replace(",", ""))
            except ValueError:
                pass
    return url, rates


def save_pdf(url, base, date, pdf_dir="."):
    path = f"{pdf_dir}/{base}_rates_{date}.pdf"
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(url, wait_until="networkidle")
        page.emulate_media(media="screen")  # avoid print CSS hiding the rates table
        page.pdf(path=path, format="Letter", print_background=True)
        browser.close()
    return path


def main():
    parser = argparse.ArgumentParser(description="Fetch historical exchange rates from x-rates.com")
    parser.add_argument("--from", dest="base", required=True, help="Base currency (e.g. USD)")
    parser.add_argument("--date", required=True, help="Date as YYYY-MM-DD")
    parser.add_argument("--to", nargs="+", required=True, help="Target currency codes (e.g. INR GBP)")
    parser.add_argument("--no-pdf", action="store_true", help="Skip saving PDF")
    parser.add_argument("--pdf-dir", default=".", help="Directory to save PDF (default: current dir)")
    args = parser.parse_args()

    url, all_rates = fetch_rates(args.base, args.date)

    result = {}
    for code in args.to:
        name = CURRENCY_NAMES.get(code.upper())
        if not name:
            print(f"Warning: unknown currency code '{code}'", file=sys.stderr)
            continue
        rate = all_rates.get(name)
        if rate is None:
            print(f"Warning: no rate found for {name} ({code})", file=sys.stderr)
        else:
            result[code.upper()] = rate

    if not args.no_pdf:
        path = save_pdf(url, args.base, args.date, args.pdf_dir)
        print(f"PDF saved: {path}", file=sys.stderr)

    print(json.dumps({"date": args.date, "base": args.base, "rates": result}))


if __name__ == "__main__":
    main()
