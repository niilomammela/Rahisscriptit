#!/usr/bin/env python3
"""
Fetch Stripe payout data for a given month and produce revenue/fee reports.

WHY PAYOUT-DATE MATCHING MATTERS
---------------------------------
A Stripe payout that arrives in your bank on e.g. March 15 typically contains
charges processed March 1–13. The manual workflow works around this by merging
the transfers CSV (payout-level) with the unified_payments CSV (charge-level)
on the payout ID, so each charge is linked to the payout it belongs to.

This script does the same thing via the API:
  1. List all payouts whose arrival_date falls in the target month.
  2. For each payout, call BalanceTransaction.list(payout=po_xxx) to get
     every constituent charge/refund — this is the API equivalent of the
     transfers + unified_payments merge.

Outputs (saved to --output-dir, or Raportit/Stripe/<MonthFI>/ by default):
  stripe_payout_summary-<month>.csv     one row per payout
  stripe_transactions-<month>.csv       one row per charge/refund in those payouts
  stripe_item_summary-<month>.csv       aggregated by item description (matches
                                        the format of item_summary_*.csv)

Usage:
    python stripe_fetch.py 2026-03
    python stripe_fetch.py 2026-03 --output-dir Raportit/Stripe/Maaliskuu/

Auth:
    STRIPE_SECRET_KEY   your Stripe secret key (sk_live_... or sk_test_...)
    Set in .env or as an environment variable.
"""

import argparse
import calendar
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import stripe
import pandas as pd

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

MONTH_NAMES_FI = {
    1: "tammikuu", 2: "helmikuu", 3: "maaliskuu", 4: "huhtikuu",
    5: "toukokuu", 6: "kesäkuu", 7: "heinäkuu", 8: "elokuu",
    9: "syyskuu", 10: "lokakuu", 11: "marraskuu", 12: "joulukuu",
}


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def configure_stripe() -> None:
    key = os.environ.get("STRIPE_SECRET_KEY", "").strip()
    if not key:
        sys.exit("Error: set STRIPE_SECRET_KEY in your .env file.")
    stripe.api_key = key


# ---------------------------------------------------------------------------
# Date helpers
# ---------------------------------------------------------------------------

def month_unix_range(year: int, month: int) -> tuple[int, int]:
    last_day = calendar.monthrange(year, month)[1]
    start = int(datetime(year, month, 1, 0, 0, 0, tzinfo=timezone.utc).timestamp())
    end = int(datetime(year, month, last_day, 23, 59, 59, tzinfo=timezone.utc).timestamp())
    return start, end


def unix_to_date(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat()


# ---------------------------------------------------------------------------
# Stripe API helpers
# ---------------------------------------------------------------------------

def fetch_payouts(year: int, month: int) -> list:
    start_ts, end_ts = month_unix_range(year, month)
    return list(
        stripe.Payout.list(
            arrival_date={"gte": start_ts, "lte": end_ts},
            limit=100,
        ).auto_paging_iter()
    )


def fetch_balance_transactions(payout_id: str) -> list:
    """
    All balance transactions (charges, refunds, fees, etc.) that belong to
    the given payout. This is the authoritative way to see which individual
    payments make up a payout.
    """
    return list(
        stripe.BalanceTransaction.list(
            payout=payout_id,
            limit=100,
        ).auto_paging_iter()
    )


def _get(obj, key, default=None):
    """
    dict-style .get() that works for both plain dicts and Stripe SDK objects
    (StripeObject no longer subclasses dict / supports .get() as of stripe>=11).
    """
    try:
        return obj[key]
    except (KeyError, TypeError):
        return default


def resolve_description(bt) -> str:
    """
    Try to extract a meaningful item description from a balance transaction.
    Cascade: description field → charge description → checkout line items.
    """
    desc = (_get(bt, "description") or "").strip()
    if desc and desc.lower() != "stripe fee":
        return desc

    source_id = _get(bt, "source")
    if not source_id or not isinstance(source_id, str):
        return desc or _get(bt, "id", "")

    # Fetch the charge object for richer description / checkout line items
    if source_id.startswith("ch_") or source_id.startswith("py_"):
        try:
            charge = stripe.Charge.retrieve(source_id, expand=["payment_intent"])
            charge_desc = (_get(charge, "description") or "").strip()

            # Attempt checkout session line items
            pi = _get(charge, "payment_intent")
            if isinstance(pi, dict):
                pi_id = _get(pi, "id", "")
            else:
                pi_id = pi or ""

            if pi_id:
                sessions = stripe.checkout.Session.list(payment_intent=pi_id, limit=1)
                session_list = _get(sessions, "data", [])
                if session_list:
                    session = session_list[0]
                    line_items = stripe.checkout.Session.list_line_items(
                        session["id"], limit=100
                    )
                    names = [
                        _get(li, "description") or _get(_get(li, "price", {}) or {}, "nickname", "")
                        for li in _get(line_items, "data", [])
                        if _get(li, "description") or _get(_get(li, "price", {}) or {}, "nickname")
                    ]
                    if names:
                        return "; ".join(names)

            return charge_desc or desc or source_id
        except stripe.error.StripeError:
            pass

    return desc or source_id


# ---------------------------------------------------------------------------
# Report builders
# ---------------------------------------------------------------------------

def build_item_summary(transaction_rows: list[dict]) -> list[dict]:
    """
    Aggregate transaction rows by description — mirrors the item_summary_*.csv
    format produced by process_payments.py's analyze_checkout_items().
    """
    stats: dict[str, dict] = defaultdict(lambda: {
        "qty_sold": 0,
        "qty_refunded": 0,
        "revenue_regular": 0.0,
        "revenue_refunded": 0.0,
        "total_fees": 0.0,
    })

    for row in transaction_rows:
        name = row["Description"] or "Unknown"
        gross = float(row["Gross (EUR)"])
        fee = float(row["Fee (EUR)"])
        is_refund = row["Type"] in ("refund", "payment_refund")

        if is_refund:
            stats[name]["qty_refunded"] += 1
            stats[name]["revenue_refunded"] += gross  # already negative
        else:
            stats[name]["qty_sold"] += 1
            stats[name]["revenue_regular"] += gross
            stats[name]["total_fees"] += fee

    output: list[dict] = []
    total_refunds_qty = 0
    total_refunds_revenue = 0.0

    for name in sorted(stats):
        s = stats[name]
        total_rev = s["revenue_regular"] + s["revenue_refunded"]
        unit_price = s["revenue_regular"] / s["qty_sold"] if s["qty_sold"] > 0 else 0.0
        output.append({
            "Item Name": name,
            "Total Quantity": int(s["qty_sold"]),
            "Price per Item": f"{unit_price:.2f}",
            "Total Revenue": f"{total_rev:.2f}",
            "Total Fees": f"{s['total_fees']:.2f}",
        })
        total_refunds_qty += int(s["qty_refunded"])
        total_refunds_revenue += s["revenue_refunded"]

    if total_refunds_qty > 0:
        avg_refund = total_refunds_revenue / total_refunds_qty if total_refunds_qty else 0.0
        output.append({
            "Item Name": "--- REFUNDS (Total) ---",
            "Total Quantity": total_refunds_qty,
            "Price per Item": f"{avg_refund:.2f}",
            "Total Revenue": f"{total_refunds_revenue:.2f}",
            "Total Fees": "0.00",
        })

    all_qty = sum(s["qty_sold"] for s in stats.values())
    all_rev = sum(s["revenue_regular"] + s["revenue_refunded"] for s in stats.values())
    all_fees = sum(s["total_fees"] for s in stats.values())
    reg_rev = sum(s["revenue_regular"] for s in stats.values())

    output.append({
        "Item Name": "=== TOTAL (All Transactions) ===",
        "Total Quantity": int(all_qty) + total_refunds_qty,
        "Price per Item": "",
        "Total Revenue": f"{all_rev:.2f}",
        "Total Fees": f"{all_fees:.2f}",
    })
    output.append({
        "Item Name": "=== TOTAL (Excluding Refunds) ===",
        "Total Quantity": int(all_qty),
        "Price per Item": "",
        "Total Revenue": f"{reg_rev:.2f}",
        "Total Fees": f"{all_fees:.2f}",
    })

    return output


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Fetch Stripe payouts that arrived in a given month and produce "
            "transaction + item summary CSVs."
        )
    )
    parser.add_argument("month", help="Month in YYYY-MM format, e.g. 2026-03")
    parser.add_argument(
        "--output-dir",
        help="Directory for output files (default: Raportit/Stripe/<MonthFI>/)",
    )
    args = parser.parse_args()

    try:
        dt = datetime.strptime(args.month, "%Y-%m")
    except ValueError:
        sys.exit("Error: month must be YYYY-MM format, e.g. 2026-03")

    year, month = dt.year, dt.month
    month_name_fi = MONTH_NAMES_FI[month]
    month_str = f"{year}-{month:02d}"

    if args.output_dir:
        out_dir = Path(args.output_dir)
    else:
        out_dir = Path(__file__).parent / "Raportit" / "Stripe" / month_name_fi.capitalize()
    out_dir.mkdir(parents=True, exist_ok=True)

    configure_stripe()

    print("=" * 70)
    print(f"Stripe Payout Fetch — {month_name_fi.capitalize()} {year}")
    print("=" * 70)

    print(f"\nFetching payouts that arrived in {month_name_fi.capitalize()} {year} ...")
    payouts = fetch_payouts(year, month)
    print(f"Found {len(payouts)} payout(s)\n")

    if not payouts:
        print("No payouts found for this period.")
        sys.exit(0)

    payout_summary_rows: list[dict] = []
    all_transaction_rows: list[dict] = []

    for payout in payouts:
        payout_id = payout["id"]
        arrival_date = unix_to_date(payout["arrival_date"])
        currency = payout["currency"].upper()
        payout_amount = payout["amount"] / 100

        print(f"  Payout {payout_id}  arrived {arrival_date}  {currency} {payout_amount:.2f}")
        print(f"    Fetching constituent balance transactions ...")

        balance_txns = fetch_balance_transactions(payout_id)
        print(f"    Found {len(balance_txns)} balance transaction(s)")

        gross = fees = net = 0.0
        charge_count = refund_count = 0

        for bt in balance_txns:
            bt_type = bt["type"]

            # Skip Stripe's own fee line — it's already reflected in the net
            if bt_type == "stripe_fee":
                continue

            bt_gross = bt["amount"] / 100
            bt_fee = bt["fee"] / 100
            bt_net = bt["net"] / 100
            bt_date = unix_to_date(bt["created"])
            desc = resolve_description(bt)

            if bt_type in ("charge", "payment"):
                gross += bt_gross
                fees += bt_fee
                net += bt_net
                charge_count += 1
            elif bt_type in ("refund", "payment_refund"):
                gross += bt_gross   # negative amount
                fees += bt_fee      # may be negative (fee reversal)
                net += bt_net
                refund_count += 1

            all_transaction_rows.append({
                "Payout ID": payout_id,
                "Payout Arrival Date": arrival_date,
                "Transaction ID": bt["id"],
                "Transaction Date": bt_date,
                "Type": bt_type,
                "Description": desc,
                "Gross (EUR)": f"{bt_gross:.2f}",
                "Fee (EUR)": f"{bt_fee:.2f}",
                "Net (EUR)": f"{bt_net:.2f}",
                "Currency": currency,
            })

        payout_summary_rows.append({
            "Payout ID": payout_id,
            "Arrival Date": arrival_date,
            "Status": payout["status"],
            "Currency": currency,
            "Payout Amount (EUR)": f"{payout_amount:.2f}",
            "Gross Revenue (EUR)": f"{gross:.2f}",
            "Total Fees (EUR)": f"{fees:.2f}",
            "Net Revenue (EUR)": f"{net:.2f}",
            "Charges": charge_count,
            "Refunds": refund_count,
        })

    # Save CSVs
    summary_csv = out_dir / f"stripe_payout_summary-{month_str}.csv"
    txn_csv = out_dir / f"stripe_transactions-{month_str}.csv"
    item_csv = out_dir / f"stripe_item_summary-{month_str}.csv"

    pd.DataFrame(payout_summary_rows).to_csv(summary_csv, index=False)
    pd.DataFrame(all_transaction_rows).to_csv(txn_csv, index=False)

    # Only charge/refund rows feed into item summary (skip payout rows etc.)
    item_rows_source = [
        r for r in all_transaction_rows
        if r["Type"] in ("charge", "payment", "refund", "payment_refund")
    ]
    pd.DataFrame(build_item_summary(item_rows_source)).to_csv(item_csv, index=False)

    # Print totals
    total_gross = sum(float(r["Gross Revenue (EUR)"]) for r in payout_summary_rows)
    total_fees = sum(float(r["Total Fees (EUR)"]) for r in payout_summary_rows)
    total_net = sum(float(r["Net Revenue (EUR)"]) for r in payout_summary_rows)

    print(f"\n{'=' * 70}")
    print(f"Summary — {month_name_fi.capitalize()} {year}")
    print(f"  Payouts:         {len(payouts)}")
    print(f"  Gross revenue:   EUR {total_gross:.2f}")
    print(f"  Total fees:      EUR {total_fees:.2f}")
    print(f"  Net revenue:     EUR {total_net:.2f}")
    print(f"\nOutput files:")
    print(f"  {summary_csv}")
    print(f"  {txn_csv}")
    print(f"  {item_csv}")
    print(f"{'=' * 70}")


if __name__ == "__main__":
    main()
