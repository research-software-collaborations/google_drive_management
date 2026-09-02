"""
Generate an invitation letter from a Google Docs template.

Reads the template, prompts interactively for each <PLACEHOLDER> field,
derives what it can automatically, then copies the template into Drive and
exports a local PDF.

Auto-filled:
  <today>        — today's date
  <X year>       — year extracted from the answer given for <X date>
  <Last name>    — last word of the answer given for <Name>
  <First name>   — first word of the answer given for <Name>

Usage:
    python generate_invitation.py
    python generate_invitation.py --list-fields   # inspect template only
"""

import argparse
import json
import os
import re
from datetime import date
from googleapiclient.discovery import build

from auth import get_credentials

SIGNATURE_PNG    = os.path.join(os.path.dirname(__file__), "signature.png")
DEFAULTS_FILE    = os.path.join(os.path.dirname(__file__), "saved", "invitation_defaults.json")
TEMPLATE_DOC_ID  = "1b0MzoHB6JoEM3O6Rb2olgdO9nNepXckE5IU53JNbUp8"
TARGET_FOLDER_ID = "16jvwyNUzuZ805XI2n2br9Pq5CRoL_m1_"


def load_defaults():
    if os.path.exists(DEFAULTS_FILE):
        with open(DEFAULTS_FILE) as f:
            return json.load(f)
    return {}


def save_defaults(fields):
    os.makedirs(os.path.dirname(DEFAULTS_FILE), exist_ok=True)
    with open(DEFAULTS_FILE, "w") as f:
        json.dump(fields, f, indent=2)


def extract_text(doc):
    parts = []
    for el in doc["body"]["content"]:
        if "paragraph" in el:
            for e in el["paragraph"].get("elements", []):
                parts.append(e.get("textRun", {}).get("content", ""))
        elif "table" in el:
            for row in el["table"]["tableRows"]:
                for cell in row["tableCells"]:
                    for p in cell["content"]:
                        for e in p.get("paragraph", {}).get("elements", []):
                            parts.append(e.get("textRun", {}).get("content", ""))
    return "".join(parts)


def find_placeholders(doc):
    """Return list of (tag, key, default) in document order, unique by tag.

    Supports both <Key> and <Key: default value> syntax.
    tag is the full original string (e.g. '<Event name: HSF-India workshop>').
    Each distinct tag gets its own entry so replaceAllText covers all forms.
    The default is taken from whichever occurrence of a key first supplies one.
    """
    text = extract_text(doc)
    seen_tags = set()
    key_to_default = {}
    result = []
    for m in re.finditer(r"<([^<>]+)>", text):
        content = m.group(1)
        tag = m.group(0)
        if tag in seen_tags:
            continue
        seen_tags.add(tag)
        if ":" in content:
            key, _, default = content.partition(":")
            key, default = key.strip(), default.strip()
        else:
            key, default = content.strip(), None
        if key not in key_to_default:
            key_to_default[key] = default
        result.append((tag, key, key_to_default[key]))
    return result


def extract_year(date_str):
    """Pull a 4-digit year out of a date string, e.g. 'August 10-15, 2026' -> '2026'."""
    m = re.search(r"\b(20\d{2})\b", date_str)
    return m.group(1) if m else None


def compute_derivations(placeholders, filled):
    """Return {key: value} for fields derivable from already-filled values.

    Rules:
      - 'today'      -> today's date
      - 'Last name'  -> last word of filled['Name']
      - 'First name' -> first word of filled['Name']
      - 'X year'     -> year extracted from filled['X date']
    """
    derived = {}
    today_str = date.today().strftime("%-d %B %Y")

    for _, key, _ in placeholders:
        if key in filled:
            continue
        lower = key.lower()

        if lower == "today":
            derived[key] = today_str

        elif lower == "last name" and "Name" in filled:
            derived[key] = filled["Name"].split()[-1]

        elif lower == "first name" and "Name" in filled:
            derived[key] = filled["Name"].split()[0]

        else:
            m = re.fullmatch(r"(.+)\s+year", key, re.IGNORECASE)
            if m:
                prefix = m.group(1)
                for k, v in filled.items():
                    if k.lower() == f"{prefix.lower()} date":
                        year = extract_year(v)
                        if year:
                            derived[key] = year
                        break

    return derived


def collect_fields(placeholders, saved=None):
    """Prompt interactively for each placeholder; skip auto-derived ones.

    saved: dict of previously entered values used as defaults.
    """
    if saved is None:
        saved = {}
    filled = {}

    auto = compute_derivations(placeholders, filled)
    filled.update(auto)

    for _, key, template_default in placeholders:
        if key in filled:
            continue

        # Saved value takes priority over template default
        default = saved.get(key, template_default)

        if default is not None:
            prompt = f"  {key} [{default}]: "
        else:
            prompt = f"  {key}: "

        value = input(prompt).strip()
        if not value and default is not None:
            value = default
        filled[key] = value

        newly_derived = compute_derivations(placeholders, filled)
        for dk, dv in newly_derived.items():
            if dk not in filled:
                filled[dk] = dv
                print(f"  {dk} (derived): {dv}")

    return filled


def stamp_signature(pdf_path, sig_png, output_path):
    """Place sig_png above the 'David J. Lange' line in the PDF."""
    import fitz  # pymupdf

    doc = fitz.open(pdf_path)
    page = doc[0]

    # Find the name text to anchor the signature position
    hits = page.search_for("David J. Lange")
    if not hits:
        print("  Warning: 'David J. Lange' not found in PDF — skipping signature stamp")
        doc.close()
        import shutil; shutil.copy(pdf_path, output_path)
        return

    name_rect = hits[0]
    sig_img = fitz.open(sig_png)
    sig_w, sig_h = sig_img[0].rect.width, sig_img[0].rect.height
    sig_img.close()

    # Scale signature to roughly 2.5 cm wide (≈71 pts) keeping aspect ratio
    target_w = 95.0
    target_h = target_w * (sig_h / sig_w)
    gap = 4  # points between sig bottom and name top

    sig_rect = fitz.Rect(
        name_rect.x0,
        name_rect.y0 - gap - target_h,
        name_rect.x0 + target_w,
        name_rect.y0 - gap,
    )
    page.insert_image(sig_rect, filename=sig_png)
    doc.save(output_path)
    doc.close()


def replace_requests(placeholders, fields):
    return [
        {
            "replaceAllText": {
                "containsText": {"text": tag, "matchCase": True},
                "replaceText": fields[key],
            }
        }
        for tag, key, _ in placeholders
        if key in fields
    ]


def main():
    parser = argparse.ArgumentParser(description="Generate an invitation letter from template")
    parser.add_argument(
        "--list-fields", action="store_true",
        help="List the <PLACEHOLDER> fields in the template and exit",
    )
    args = parser.parse_args()

    creds = get_credentials()
    docs  = build("docs",  "v1", credentials=creds)
    drive = build("drive", "v3", credentials=creds)

    print(f"Reading template...")
    template = docs.documents().get(documentId=TEMPLATE_DOC_ID).execute()
    placeholders = find_placeholders(template)

    if args.list_fields:
        print("Placeholders found in template:")
        for tag, key, default in placeholders:
            if default is not None:
                print(f"  {tag}  (default: {default!r})")
            else:
                print(f"  {tag}")
        return

    print("Fill in the invitation letter fields.")
    print("(Fields that can be derived automatically will be shown but not prompted.)\n")

    auto_preview = compute_derivations(placeholders, {})
    for key, val in auto_preview.items():
        print(f"  {key} (auto): {val}")
    if auto_preview:
        print()

    saved = load_defaults()
    fields = collect_fields(placeholders, saved)

    name_for_file = fields.get("Name", "guest").replace(" ", "_")
    default_pdf  = f"invitation_{name_for_file}.pdf"
    default_doc  = f"Invitation Letter — {fields.get('Name', 'Guest')}"

    print()
    pdf_out  = input(f"  Local PDF filename [{default_pdf}]: ").strip() or default_pdf
    doc_name = input(f"  Google Doc name [{default_doc}]: ").strip() or default_doc

    print("\nReady to create:")
    for _, key, _ in placeholders:
        print(f"  <{key}> -> {fields.get(key, '(empty)')!r}")
    print(f"  Doc name: {doc_name!r}")
    print(f"  Local PDF: {pdf_out!r}")
    confirm = input("\nProceed? [Y/n]: ").strip().lower()
    if confirm == "n":
        print("Aborted.")
        return

    # Copy template into target folder
    print(f"\nCopying template to Drive...")
    copied = drive.files().copy(
        fileId=TEMPLATE_DOC_ID,
        body={"name": doc_name, "parents": [TARGET_FOLDER_ID]},
        fields="id",
    ).execute()
    new_doc_id = copied["id"]
    print(f"  Created: https://docs.google.com/document/d/{new_doc_id}/edit")

    # Fill placeholders
    print("Filling placeholders...")
    docs.documents().batchUpdate(
        documentId=new_doc_id,
        body={"requests": replace_requests(placeholders, fields)},
    ).execute()

    # Export PDF locally
    print(f"Exporting PDF...")
    pdf_bytes = drive.files().export(
        fileId=new_doc_id,
        mimeType="application/pdf",
    ).execute()
    unsigned_path = pdf_out + ".unsigned.pdf"
    with open(unsigned_path, "wb") as f:
        f.write(pdf_bytes)

    # Stamp signature
    if os.path.exists(SIGNATURE_PNG):
        print("Stamping signature...")
        stamp_signature(unsigned_path, SIGNATURE_PNG, pdf_out)
        os.remove(unsigned_path)
    else:
        print(f"  No signature.png found — saving unsigned PDF")
        os.rename(unsigned_path, pdf_out)

    save_defaults(fields)

    print(f"\nDone.")
    print(f"  Google Doc:   https://docs.google.com/document/d/{new_doc_id}/edit")
    print(f"  Drive folder: https://drive.google.com/drive/folders/{TARGET_FOLDER_ID}")
    print(f"  Local PDF:    {os.path.abspath(pdf_out)}")


if __name__ == "__main__":
    main()
