#!/usr/bin/env python3
"""
Dedicated script for creating a cleaned myyntiraportti summary CSV.

Usage:
    python myyntiraportti_summary.py [input_csv] [output_csv]
"""

import sys
from pathlib import Path

from process_payments import generate_clean_sales_report


def main():
    script_dir = Path(__file__).parent

    input_csv = (
        Path(sys.argv[1])
        if len(sys.argv) > 1
        else script_dir / "myyntiraportti-2026-01-01_2026-01-30.csv"
    )
    output_csv = (
        Path(sys.argv[2])
        if len(sys.argv) > 2
        else script_dir / "myyntiraportti-cleaned-summary-tammikuu.csv"
    )

    if not input_csv.is_absolute():
        input_csv = script_dir / input_csv
    if not output_csv.is_absolute():
        output_csv = script_dir / output_csv

    generate_clean_sales_report(input_csv, output_csv)


if __name__ == "__main__":
    main()
