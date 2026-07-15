#!/usr/bin/env python3
"""
Fetch SumUp transaction data for a given month via the SumUp API and produce
the same CSV reports as the manual CSV-export workflow.

Outputs (saved to --output-dir, or Raportit/Sumup/<MonthFI>/ by default):
  myyntiraportti-<start>_<end>.csv          raw sales rows (same schema as manual export)
  myyntiraportti-cleaned-summary-<month>.csv  product summary by category

Usage:
    python sumup_fetch.py 2026-03
    python sumup_fetch.py 2026-03 --output-dir Raportit/Sumup/Maaliskuu/

Auth — set one of the following in .env or as environment variables:
    SUMUP_ACCESS_TOKEN                         personal access token (simplest)
    SUMUP_CLIENT_ID + SUMUP_CLIENT_SECRET      OAuth client credentials
"""

import argparse
import calendar
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests
import pandas as pd

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from process_payments import generate_clean_sales_report

API_BASE = "https://api.sumup.com"
CATEGORIES_FILE = Path(__file__).parent / "sumup_categories.json"

MONTH_NAMES_FI = {
    1: "tammikuu", 2: "helmikuu", 3: "maaliskuu", 4: "huhtikuu",
    5: "toukokuu", 6: "kesäkuu", 7: "heinäkuu", 8: "elokuu",
    9: "syyskuu", 10: "lokakuu", 11: "marraskuu", 12: "joulukuu",
}


def load_category_lookup() -> dict[str, str]:
    """
    Product name -> category, loaded from sumup_categories.json.
    The SumUp REST API doesn't expose the merchant's product catalog
    (categories only exist in the dashboard CSV export), so known mappings
    are maintained locally instead. Unknown products fall back to
    "Uncategorized".
    """
    if not CATEGORIES_FILE.exists():
        return {}
    with open(CATEGORIES_FILE, encoding="utf-8") as f:
        data = json.load(f)
    return {k: v for k, v in data.items() if not k.startswith("_")}


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def get_token() -> str:
    token = os.environ.get("SUMUP_ACCESS_TOKEN", "").strip()
    if token:
        return token

    client_id = os.environ.get("SUMUP_CLIENT_ID", "").strip()
    client_secret = os.environ.get("SUMUP_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        sys.exit(
            "Error: set SUMUP_ACCESS_TOKEN or both SUMUP_CLIENT_ID and "
            "SUMUP_CLIENT_SECRET in your .env file."
        )

    resp = requests.post(
        f"{API_BASE}/token",
        data={
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


# ---------------------------------------------------------------------------
# API calls
# ---------------------------------------------------------------------------

def fetch_transactions(token: str, start: datetime, end: datetime) -> list[dict]:
    """Fetch all successful/refunded transactions in the date range (paginated)."""
    headers = {"Authorization": f"Bearer {token}"}
    params = {
        "oldest_time": start.strftime("%Y-%m-%dT00:00:00.000Z"),
        "newest_time": end.strftime("%Y-%m-%dT23:59:59.999Z"),
        "limit": 100,
        "statuses[]": ["SUCCESSFUL", "REFUNDED"],
    }
    history_endpoint = f"{API_BASE}/v0.1/me/transactions/history"
    url = history_endpoint
    transactions: list[dict] = []

    while url:
        resp = requests.get(url, headers=headers, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        batch = data.get("items", [])
        transactions.extend(batch)
        print(f"  fetched {len(batch)} transactions (running total: {len(transactions)})")

        next_href = next(
            (link["href"] for link in data.get("links", []) if link.get("rel") == "next"),
            None,
        )
        if next_href and not next_href.startswith("http"):
            # SumUp returns just the query string for "next", not a full URL
            next_href = f"{history_endpoint}?{next_href}"
        url = next_href
        params = {}  # next URL already encodes all params

    return transactions


def fetch_transaction_detail(token: str, tx_id: str) -> dict:
    """Fetch a single transaction's full detail (includes products array)."""
    headers = {"Authorization": f"Bearer {token}"}
    resp = requests.get(
        f"{API_BASE}/v0.1/me/transactions",
        headers=headers,
        params={"id": tx_id},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Data transformation
# ---------------------------------------------------------------------------

def _product_category(product: dict) -> str:
    cat = product.get("category")
    if isinstance(cat, dict):
        return cat.get("name", "") or ""
    if isinstance(cat, str):
        return cat
    return ""


def build_myyntiraportti_rows(transactions: list[dict], token: str) -> list[dict]:
    """
    Convert API transactions into rows that match the Finnish myyntiraportti
    CSV schema expected by generate_clean_sales_report().

    Fetches full transaction detail for any transaction that lacks products.
    """
    rows: list[dict] = []
    total = len(transactions)
    category_lookup = load_category_lookup()

    for i, tx in enumerate(transactions, 1):
        status = (tx.get("status") or "").upper()
        payment_type = (tx.get("payment_type") or "").upper()
        tx_type = "Myynti" if status == "SUCCESSFUL" else "Palautus"
        maksutapa = "Käteinen" if payment_type == "CASH" else payment_type.capitalize()
        amount = float(tx.get("amount", 0))

        products = tx.get("products") or []
        if not products:
            try:
                detail = fetch_transaction_detail(token, tx["id"])
                products = detail.get("products") or []
            except Exception:
                pass

        if products:
            for product in products:
                name = product.get("name", "Unknown")
                rows.append({
                    "Tyyppi": tx_type,
                    "Maksutapa": maksutapa,
                    "Määrä": product.get("quantity", 1),
                    "Kuvaus": name,
                    "Kategoria": _product_category(product) or category_lookup.get(name, "Uncategorized"),
                    "Hinta (brutto)": product.get("total_price", amount),
                })
        else:
            # No itemized product detail available — fall back to the
            # transaction's product_summary (human-readable name set at
            # checkout), not the opaque transaction_code/id.
            name = tx.get("product_summary") or tx.get("transaction_code") or tx.get("id") or "Unknown"
            rows.append({
                "Tyyppi": tx_type,
                "Maksutapa": maksutapa,
                "Määrä": 1,
                "Kuvaus": name,
                "Kategoria": category_lookup.get(name, "Uncategorized"),
                "Hinta (brutto)": amount,
            })

        if i % 20 == 0 or i == total:
            print(f"  processed {i}/{total} transactions")

    return rows


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def parse_month(s: str) -> tuple[int, int]:
    """Accepts 'M/YYYY', 'MM/YYYY' (e.g. 4/2026) or 'YYYY-MM' (e.g. 2026-04)."""
    for fmt in ("%m/%Y", "%Y-%m"):
        try:
            dt = datetime.strptime(s, fmt)
            return dt.year, dt.month
        except ValueError:
            continue
    sys.exit(f"Error: month must be M/YYYY (e.g. 4/2026) or YYYY-MM (e.g. 2026-04), got {s!r}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch SumUp transactions for a month and produce report CSVs"
    )
    parser.add_argument("month", help="Month as M/YYYY (e.g. 4/2026) or YYYY-MM (e.g. 2026-04)")
    parser.add_argument(
        "--output-dir",
        help="Directory for output files (default: Raportit/Sumup/<MonthFI>/)",
    )
    args = parser.parse_args()

    year, month = parse_month(args.month)
    last_day = calendar.monthrange(year, month)[1]
    start = datetime(year, month, 1, tzinfo=timezone.utc)
    end = datetime(year, month, last_day, tzinfo=timezone.utc)
    month_name_fi = MONTH_NAMES_FI[month]

    if args.output_dir:
        out_dir = Path(args.output_dir)
    else:
        out_dir = Path(__file__).parent / "Raportit" / "Sumup" / month_name_fi.capitalize()
    out_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 70)
    print(f"SumUp Fetch — {month_name_fi.capitalize()} {year}")
    print("=" * 70)

    token = get_token()

    print(f"\nFetching transactions {start.date()} → {end.date()} ...")
    transactions = fetch_transactions(token, start, end)
    print(f"Total transactions: {len(transactions)}\n")

    if not transactions:
        print("No transactions found for this period.")
        sys.exit(0)

    # Build raw myyntiraportti CSV (same schema as manual SumUp export)
    myynti_path = out_dir / f"myyntiraportti-{start.strftime('%Y-%m-%d')}_{end.strftime('%Y-%m-%d')}.csv"
    print("Building myyntiraportti rows ...")
    rows = build_myyntiraportti_rows(transactions, token)
    pd.DataFrame(rows).to_csv(myynti_path, index=False)
    print(f"Saved: {myynti_path}\n")

    # Produce cleaned summary using the existing analysis function
    cleaned_path = out_dir / f"myyntiraportti-cleaned-summary-{month_name_fi}.csv"
    generate_clean_sales_report(myynti_path, cleaned_path)

    print("\n" + "=" * 70)
    print("Done.")
    print("=" * 70)


if __name__ == "__main__":
    main()
