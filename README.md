# Payment Data Analysis Scripts

This repository contains scripts for processing and analyzing payment data from Stripe exports.

## Quick Start (Recommended)

### Unified Script (`process_payments.py`)
**Single command to run the entire workflow** - merges CSV files and analyzes checkout items.

**Configuration:**
Edit the `CONFIG` section at the top of `process_payments.py`:
```python
CONFIG = {
    'input_files': {
        'transfers': 'transfers (3).csv',      # First CSV file
        'payments': 'unified_payments (2).csv', # Second CSV file
        'id_col_transfers': 'ID',               # ID column name in transfers
        'id_col_payments': 'id'                 # ID column name in payments
    },
    'output_files': {
        'merged': 'merged_payments.csv',        # Intermediate merged file
        'summary': 'item_summary.csv',          # Final analysis output
        'save_merged': True                     # Save merged CSV?
    }
}
```

**Usage:**
```bash
source venv/bin/activate
python process_payments.py
```

**Split scripts (original `process_payments.py` kept as-is):**
```bash
source venv/bin/activate
python item_summary.py
```

### Cleaned myyntiraportti product report
Generate a cleaned product summary from `myyntiraportti` with:
- quantity-aware aggregation (`Määrä`)
- card/non-cash products first
- separate `Käteinen` section
- product quantity, unit price (`Hinta (brutto)`), and total sold amount

```bash
source venv/bin/activate
python myyntiraportti_summary.py
# optional: python myyntiraportti_summary.py <input_csv> <output_csv>
```

**What It Does:**
1. Merges two CSV files on ID column (inner join)
2. Analyzes checkout items from merged data
3. Generates `item_summary.csv` with aggregated statistics
4. Optionally saves `merged_payments.csv`

**Results:**
- 152 transactions merged from 215 transfers + 362 payments
- 17 unique item combinations identified
- 150 successful purchases + 2 refunds
- €14,297.00 revenue from successful purchases
- €14,118.00 net total revenue (refunds included as negative)
- €253.84 total fees collected

---

## Individual Scripts (Optional)

You can also run the individual scripts separately if needed:

### 1. CSV Merge Script (`merge_csvs.py`)
Merges two CSV files based on their shared ID column using an inner join.

**Files Generated:**
- `merged_payments.csv` - The merged payment data

**What It Does:**
- Reads `transfers (3).csv` (215 rows)
- Reads `unified_payments (2).csv` (362 rows)
- Merges them on the ID column (inner join)
- Only keeps rows where IDs match in both files
- Adds suffixes `_transfers` and `_payments` to duplicate column names
- Outputs `merged_payments.csv` (152 matched rows with 98 columns)

### 2. Item Analysis Script (`analyze_items.py`)
Analyzes checkout items from payment data and generates an aggregated summary.

**Files Generated:**
- `item_summary.csv` - Aggregated item statistics

**What It Does:**
- Reads `merged_payments.csv` (152 transactions)
- Parses "Checkout Line Item Summary" column
- **Keeps combined items together** - items purchased together in the same transaction (separated by semicolons) remain as a single entry
  - Example: "Opiskelijat (1); Kyllä (1)" stays as one combined item, not split into separate items
- Uses `Amount_transfers` column for pricing (handles refunds as negative values)
- Extracts and sums quantities for each unique item combination
- Calculates per-item statistics:
  - Total quantity (successful purchases only)
  - Price per item (average: Total Revenue / Total Quantity)
  - Total revenue (from Amount_transfers, includes negative refunds)
  - Total fees collected (from successful purchases only)

## Setup

### First time setup:
```bash
# Create virtual environment and install dependencies
python3 -m venv venv
source venv/bin/activate
pip install pandas
```

## Usage

### Recommended: Unified workflow
```bash
source venv/bin/activate
python process_payments.py
```

### Alternative: Run scripts separately
```bash
# Step 1: Merge CSV files
source venv/bin/activate
python merge_csvs.py

# Step 2: Analyze items
python analyze_items.py
```

## Output Files

1. **merged_payments.csv** - 152 payment transactions with complete data
2. **item_summary.csv** - Summary with columns:
   - Item Name
   - Total Quantity
   - Price per Item (average price: Total Revenue / Total Quantity)
   - Total Revenue
   - Total Fees
   
   **Output Structure:**
   - 17 rows: Individual item combinations with their statistics
   - Row 18: `--- REFUNDS (Total) ---` - Summary of all refunds (2 transactions, -€179 total)
   - Row 19: `=== TOTAL (All Transactions) ===` - Net total across all transactions (152 transactions, €14,118)
   - Row 20: `=== TOTAL (Excluding Refunds) ===` - Total excluding refunds (150 transactions, €14,297)

## Processing Different Files

To process different CSV files:

1. **Using the unified script** (recommended):
   - Open `process_payments.py`
   - Edit the `CONFIG` dictionary at the top (lines 23-35)
   - Change the filenames in `input_files` section
   - Run: `python process_payments.py`

2. **Using individual scripts**:
   - Open `merge_csvs.py` and edit lines 82-84
   - Open `analyze_items.py` and edit lines 176-177

## Notes

- All scripts handle European number format ("119,00")
- Refunds are automatically detected (negative values in Amount_transfers)
- **Item combinations are preserved** - items purchased together stay together in the output
- Scripts include error handling and progress reporting
- All totals are cross-validated against source data
- The unified script provides clear visual feedback with section headers and summaries
