#!/usr/bin/env python3
"""
Unified Payment Analysis Script
Combines CSV merging and checkout item analysis in one workflow.

Usage:
    1. Edit the CONFIG section below to set your input/output files
    2. Run: python process_payments.py
       or: python process_payments.py clean-sales-report [input_csv] [output_csv]

The script will:
    1. Merge two CSV files on ID column (inner join)
    2. Analyze checkout items from the merged data
    3. Produce item_summary.csv with aggregated statistics
"""

import pandas as pd
import re
import sys
from pathlib import Path
from collections import defaultdict


# ============================================================================
# CONFIGURATION - Edit these values to process different files
# ============================================================================
path = "Raportit/Maaliskuu/"

CONFIG = {
    "input_files": {
        "transfers": path
        + "transfers (4).csv",  # First CSV file (e.g., transfers export)
        "payments": path
        + "unified_payments (2).csv",  # Second CSV file (e.g., payments export)
        "id_col_transfers": "ID",  # ID column name in transfers file
        "id_col_payments": "id",  # ID column name in payments file
    },
    "output_files": {
        "merged": path
        + f"merged_payments_{path.split('/')[-2]}.csv",  # Intermediate merged file
        "summary": path
        + f"item_summary_{path.split('/')[-2]}.csv",  # Final analysis output
        "save_merged": False,  # Set to False to skip saving merged CSV
    },
}
# ============================================================================


def european_to_float(value):
    """Convert European number format (e.g., '119,00') to float."""
    if pd.isna(value):
        return 0.0
    raw = str(value).strip().replace("\xa0", "").replace("€", "")
    if raw == "":
        return 0.0
    cleaned = re.sub(r"[^0-9,.\-]", "", raw)
    if cleaned == "":
        return 0.0
    if "," in cleaned:
        cleaned = cleaned.replace(".", "").replace(",", ".")
    return float(cleaned)


def format_quantity(value):
    """Format quantity cleanly: integer if whole number, else 2 decimals."""
    as_float = float(value)
    if as_float.is_integer():
        return str(int(as_float))
    return f"{as_float:.2f}"


def generate_clean_sales_report(input_path, output_path):
    """
    Create a cleaned product sales report from myyntiraportti CSV.
    Output contains card/non-cash rows first and Käteinen rows in a separate section.
    """
    print("\n" + "=" * 80)
    print("CLEAN SALES REPORT (myyntiraportti)")
    print("=" * 80)
    print(f"Input:  {input_path}")
    print(f"Output: {output_path}")

    try:
        df = pd.read_csv(input_path)
    except FileNotFoundError:
        print(f"✗ Error: File not found - {input_path}", file=sys.stderr)
        sys.exit(1)
    except pd.errors.EmptyDataError:
        print(f"✗ Error: Input CSV is empty - {input_path}", file=sys.stderr)
        sys.exit(1)

    required_cols = ["Tyyppi", "Maksutapa", "Määrä", "Kuvaus", "Kategoria", "Hinta (brutto)"]
    missing_cols = [col for col in required_cols if col not in df.columns]
    if missing_cols:
        print(f"✗ Error: Missing required columns: {missing_cols}", file=sys.stderr)
        sys.exit(1)

    sales_df = df[df["Tyyppi"].astype(str).str.strip().str.casefold() == "myynti"].copy()
    if sales_df.empty:
        print("✗ Error: No 'Myynti' rows found in input CSV", file=sys.stderr)
        sys.exit(1)

    sales_df["product"] = sales_df["Kuvaus"].astype(str).str.strip()
    sales_df = sales_df[sales_df["product"] != ""]
    sales_df["category"] = sales_df["Kategoria"].fillna("").astype(str).str.strip()
    sales_df.loc[sales_df["category"] == "", "category"] = "Uncategorized"

    sales_df["quantity"] = sales_df["Määrä"].apply(european_to_float)
    sales_df["gross_total"] = sales_df["Hinta (brutto)"].apply(european_to_float)
    sales_df["is_cash"] = (
        sales_df["Maksutapa"].astype(str).str.casefold().str.contains("käteinen", na=False)
    )

    grouped = (
        sales_df.groupby(["is_cash", "category", "product"], as_index=False)
        .agg(
            quantity_sold=("quantity", "sum"),
            total_sold=("gross_total", "sum"),
        )
        .sort_values(by=["is_cash", "category", "product"], ascending=[True, True, True])
    )

    grouped["unit_price"] = grouped.apply(
        lambda row: (row["total_sold"] / row["quantity_sold"])
        if row["quantity_sold"] > 0
        else 0.0,
        axis=1,
    )

    card_rows = grouped[grouped["is_cash"] == False]
    cash_rows = grouped[grouped["is_cash"] == True]

    output_rows = []
    output_cols = ["Section", "Category", "Product", "Quantity Sold", "Unit Price", "Total Sold"]

    output_rows.append(
        {
            "Section": "=== CARD / NON-CASH SALES ===",
            "Category": "",
            "Product": "",
            "Quantity Sold": "",
            "Unit Price": "",
            "Total Sold": "",
        }
    )
    for _, row in card_rows.iterrows():
        output_rows.append(
            {
                "Section": "Card/Non-cash",
                "Category": row["category"],
                "Product": row["product"],
                "Quantity Sold": format_quantity(row["quantity_sold"]),
                "Unit Price": f"{row['unit_price']:.2f}",
                "Total Sold": f"{row['total_sold']:.2f}",
            }
        )
    output_rows.append(
        {
            "Section": "Card/Non-cash",
            "Category": "",
            "Product": "TOTAL",
            "Quantity Sold": "",
            "Unit Price": "",
            "Total Sold": f"{card_rows['total_sold'].sum():.2f}",
        }
    )

    output_rows.append(
        {
            "Section": "",
            "Category": "",
            "Product": "",
            "Quantity Sold": "",
            "Unit Price": "",
            "Total Sold": "",
        }
    )
    output_rows.append(
        {
            "Section": "=== KÄTEINEN (CASH) SALES ===",
            "Category": "",
            "Product": "",
            "Quantity Sold": "",
            "Unit Price": "",
            "Total Sold": "",
        }
    )
    for _, row in cash_rows.iterrows():
        output_rows.append(
            {
                "Section": "Käteinen",
                "Category": row["category"],
                "Product": row["product"],
                "Quantity Sold": format_quantity(row["quantity_sold"]),
                "Unit Price": f"{row['unit_price']:.2f}",
                "Total Sold": f"{row['total_sold']:.2f}",
            }
        )
    output_rows.append(
        {
            "Section": "Käteinen",
            "Category": "",
            "Product": "TOTAL",
            "Quantity Sold": "",
            "Unit Price": "",
            "Total Sold": f"{cash_rows['total_sold'].sum():.2f}",
        }
    )

    card_category_summary = (
        sales_df[sales_df["is_cash"] == False]
        .groupby("category", as_index=False)
        .agg(total_sold=("gross_total", "sum"))
        .sort_values(by="category", ascending=True)
    )
    output_rows.append(
        {
            "Section": "",
            "Category": "",
            "Product": "",
            "Quantity Sold": "",
            "Unit Price": "",
            "Total Sold": "",
        }
    )
    output_rows.append(
        {
            "Section": "=== CARD CATEGORY TOTALS (END SUMMARY) ===",
            "Category": "",
            "Product": "",
            "Quantity Sold": "",
            "Unit Price": "",
            "Total Sold": "",
        }
    )
    for _, row in card_category_summary.iterrows():
        output_rows.append(
            {
                "Section": "Card category summary",
                "Category": row["category"],
                "Product": "",
                "Quantity Sold": "",
                "Unit Price": "",
                "Total Sold": f"{row['total_sold']:.2f}",
            }
        )
    output_rows.append(
        {
            "Section": "Card category summary",
            "Category": "",
            "Product": "ALL CARD CATEGORIES TOTAL",
            "Quantity Sold": "",
            "Unit Price": "",
            "Total Sold": f"{card_category_summary['total_sold'].sum():.2f}",
        }
    )

    output_df = pd.DataFrame(output_rows, columns=output_cols)
    output_df.to_csv(output_path, index=False)

    print(f"  - Myynti rows processed: {len(sales_df)}")
    print(f"  - Card/non-cash products: {len(card_rows)}")
    print(f"  - Käteinen products: {len(cash_rows)}")
    print(f"✓ Clean report saved to {output_path}")


def merge_csv_files(file1_path, file2_path, id_col1, id_col2):
    """
    Merge two CSV files on their ID columns using an inner join.

    Returns:
        merged_df: Pandas DataFrame with merged data
    """
    print("=" * 80)
    print("STEP 1: Merging CSV Files")
    print("=" * 80)

    try:
        # Read both CSV files
        print(f"\nReading {file1_path}...")
        df1 = pd.read_csv(file1_path)
        print(f"  - Loaded {len(df1)} rows with {len(df1.columns)} columns")

        print(f"Reading {file2_path}...")
        df2 = pd.read_csv(file2_path)
        print(f"  - Loaded {len(df2)} rows with {len(df2.columns)} columns")

        # Normalize ID column names for the merge
        if id_col1 in df1.columns:
            df1_renamed = df1.rename(columns={id_col1: id_col2})
        else:
            raise ValueError(f"Column '{id_col1}' not found in {file1_path}")

        if id_col2 not in df2.columns:
            raise ValueError(f"Column '{id_col2}' not found in {file2_path}")

        # Perform inner join on the ID column
        print(f"\nMerging on '{id_col2}' column (inner join)...")
        merged_df = pd.merge(
            df1_renamed,
            df2,
            on=id_col2,
            how="inner",
            suffixes=("_transfers", "_payments"),
        )

        print(
            f"  - Merged result: {len(merged_df)} rows with {len(merged_df.columns)} columns"
        )
        print(f"  - Rows from file1 not matched: {len(df1) - len(merged_df)}")
        print(f"  - Rows from file2 not matched: {len(df2) - len(merged_df)}")
        print(f"\n✓ Merge completed successfully")

        return merged_df

    except FileNotFoundError as e:
        print(f"✗ Error: File not found - {e}", file=sys.stderr)
        sys.exit(1)
    except pd.errors.EmptyDataError:
        print(f"✗ Error: One of the CSV files is empty", file=sys.stderr)
        sys.exit(1)
    except ValueError as e:
        print(f"✗ Error: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"✗ Unexpected error during merge: {e}", file=sys.stderr)
        sys.exit(1)


def parse_checkout_items(summary):
    """
    Parse checkout line item summary and return the full combination name.
    Keep combined items together (don't split by semicolon).
    """
    if pd.isna(summary) or summary == "":
        return None
    return summary


def analyze_checkout_items(df, output_path):
    """
    Analyze checkout items from merged payments data and generate summary.

    Args:
        df: Merged DataFrame with payment data
        output_path: Path for output item_summary.csv
    """
    print("\n" + "=" * 80)
    print("STEP 2: Analyzing Checkout Items")
    print("=" * 80)

    # Validate required columns
    required_cols = [
        "Checkout Line Item Summary",
        "Amount_transfers",
        "Amount Refunded",
        "Fee",
    ]
    missing_cols = [col for col in required_cols if col not in df.columns]
    if missing_cols:
        print(f"✗ Error: Missing required columns: {missing_cols}", file=sys.stderr)
        sys.exit(1)

    # Initialize aggregation dictionaries
    item_stats = defaultdict(
        lambda: {
            "qty_sold": 0,
            "qty_refunded": 0,
            "revenue_regular": 0.0,
            "revenue_refunded": 0.0,
            "total_fees": 0.0,
        }
    )

    # Process each transaction
    print(f"\nProcessing {len(df)} transactions...")
    for idx, row in df.iterrows():
        # Parse checkout items
        summary = row["Checkout Line Item Summary"]
        item_name = parse_checkout_items(summary)

        if not item_name:
            continue

        # Get transaction details
        amount = european_to_float(row["Amount_transfers"])
        amount_refunded = european_to_float(row["Amount Refunded"])
        fee = european_to_float(row["Fee"])

        # Determine if this is a refund (negative amount in transfers)
        is_refund = amount < 0

        # Update statistics for this combined item
        if is_refund:
            item_stats[item_name]["qty_refunded"] += 1
            item_stats[item_name]["revenue_refunded"] += amount
        else:
            item_stats[item_name]["qty_sold"] += 1
            item_stats[item_name]["revenue_regular"] += amount
            item_stats[item_name]["total_fees"] += fee

    print(f"  - Found {len(item_stats)} unique item combinations")

    # Convert to DataFrame for output
    print("\nGenerating summary...")
    output_data = []

    # Track refund totals for summary row
    total_refunds_qty = 0
    total_refunds_revenue = 0.0

    for item_name in sorted(item_stats.keys()):
        stats = item_stats[item_name]
        total_quantity = stats["qty_sold"]
        total_revenue = stats["revenue_regular"] + stats["revenue_refunded"]

        # Calculate price per item (average price)
        price_per_item = (
            stats["revenue_regular"] / stats["qty_sold"]
            if stats["qty_sold"] > 0
            else 0.0
        )

        output_data.append(
            {
                "Item Name": item_name,
                "Total Quantity": total_quantity,
                "Price per Item": f"{price_per_item:.2f}",
                "Total Revenue": f"{total_revenue:.2f}",
                "Total Fees": f"{stats['total_fees']:.2f}",
            }
        )

        # Accumulate refund totals
        total_refunds_qty += stats["qty_refunded"]
        total_refunds_revenue += stats["revenue_refunded"]

    # Add a summary row for all refunds
    if total_refunds_qty > 0:
        avg_refund_price = total_refunds_revenue / total_refunds_qty
        output_data.append(
            {
                "Item Name": "--- REFUNDS (Total) ---",
                "Total Quantity": total_refunds_qty,
                "Price per Item": f"{avg_refund_price:.2f}",
                "Total Revenue": f"{total_refunds_revenue:.2f}",
                "Total Fees": "0.00",
            }
        )

    # Calculate totals for summary rows
    total_qty_sold = sum(stats["qty_sold"] for stats in item_stats.values())
    total_qty_refunded = sum(stats["qty_refunded"] for stats in item_stats.values())
    total_revenue_regular = sum(
        stats["revenue_regular"] for stats in item_stats.values()
    )
    total_revenue_refunded = sum(
        stats["revenue_refunded"] for stats in item_stats.values()
    )
    total_fees_sum = sum(stats["total_fees"] for stats in item_stats.values())

    # Add summary row: Net total across all transactions (refunds remain negative)
    total_all_transactions = total_qty_sold + total_qty_refunded
    total_revenue_all_transactions = total_revenue_regular + total_revenue_refunded
    output_data.append(
        {
            "Item Name": "=== TOTAL (All Transactions) ===",
            "Total Quantity": total_all_transactions,
            "Price per Item": "",
            "Total Revenue": f"{total_revenue_all_transactions:.2f}",
            "Total Fees": f"{total_fees_sum:.2f}",
        }
    )

    # Add summary row: Total with refund transactions removed
    total_revenue_excluding_refunds = total_revenue_regular
    output_data.append(
        {
            "Item Name": "=== TOTAL (Excluding Refunds) ===",
            "Total Quantity": total_qty_sold,
            "Price per Item": "",
            "Total Revenue": f"{total_revenue_excluding_refunds:.2f}",
            "Total Fees": f"{total_fees_sum:.2f}",
        }
    )

    output_df = pd.DataFrame(output_data)

    # Save to CSV
    print(f"Saving results to {output_path}...")
    output_df.to_csv(output_path, index=False)

    # Print summary statistics
    print("\n" + "=" * 80)
    print("Summary Statistics:")
    print("=" * 80)

    total_qty = sum(stats["qty_sold"] for stats in item_stats.values())
    total_qty_refunded = sum(stats["qty_refunded"] for stats in item_stats.values())
    total_revenue = sum(
        stats["revenue_regular"] + stats["revenue_refunded"]
        for stats in item_stats.values()
    )
    total_fees = sum(stats["total_fees"] for stats in item_stats.values())

    print(
        f"Total Items: {total_qty} ({total_qty_refunded} refunds not included in count)"
    )
    print(f"Total Revenue: €{total_revenue:.2f}")
    print(f"Total Fees Collected: €{total_fees:.2f}")

    print("\n" + "=" * 80)
    print("Top 5 Items by Quantity:")
    print("=" * 80)

    sorted_items = sorted(
        item_stats.items(), key=lambda x: x[1]["qty_sold"], reverse=True
    )

    for i, (name, stats) in enumerate(sorted_items[:5], 1):
        total_qty = stats["qty_sold"]
        total_rev = stats["revenue_regular"] + stats["revenue_refunded"]
        print(f"{i}. {name}: {total_qty} total, €{total_rev:.2f} revenue")

    print("\n✓ Analysis completed successfully!")
    print(f"✓ Results saved to {output_path}")


def main():
    """Main function to orchestrate the entire workflow."""
    script_dir = Path(__file__).parent

    # Get file paths from configuration
    file1 = script_dir / CONFIG["input_files"]["transfers"]
    file2 = script_dir / CONFIG["input_files"]["payments"]
    merged_output = script_dir / CONFIG["output_files"]["merged"]
    summary_output = script_dir / CONFIG["output_files"]["summary"]

    id_col1 = CONFIG["input_files"]["id_col_transfers"]
    id_col2 = CONFIG["input_files"]["id_col_payments"]

    print("\n" + "=" * 80)
    print("UNIFIED PAYMENT ANALYSIS SCRIPT")
    print("=" * 80)
    print(f"\nInput Files:")
    print(f"  - Transfers: {file1.name}")
    print(f"  - Payments: {file2.name}")
    print(f"\nOutput Files:")
    print(f"  - Item Summary: {summary_output.name}")
    if CONFIG["output_files"]["save_merged"]:
        print(f"  - Merged Data: {merged_output.name}")

    # Step 1: Merge CSV files
    merged_df = merge_csv_files(file1, file2, id_col1, id_col2)

    # Optionally save merged CSV
    if CONFIG["output_files"]["save_merged"]:
        print(f"\nSaving merged data to {merged_output}...")
        merged_df.to_csv(merged_output, index=False)
        print(f"✓ Saved merged data")

    # Step 2: Analyze checkout items
    analyze_checkout_items(merged_df, summary_output)

    print("\n" + "=" * 80)
    print("WORKFLOW COMPLETED SUCCESSFULLY!")
    print("=" * 80)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "clean-sales-report":
        script_dir = Path(__file__).parent
        input_csv = (
            Path(sys.argv[2])
            if len(sys.argv) > 2
            else script_dir / "myyntiraportti-2026-01-31_2026-02-27.csv"
        )
        output_csv = (
            Path(sys.argv[3])
            if len(sys.argv) > 3
            else script_dir / "myyntiraportti-cleaned-summary.csv"
        )
        if not input_csv.is_absolute():
            input_csv = script_dir / input_csv
        if not output_csv.is_absolute():
            output_csv = script_dir / output_csv
        generate_clean_sales_report(input_csv, output_csv)
    else:
        main()
