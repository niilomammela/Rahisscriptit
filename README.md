# Tilitysraportit — Payment Accounting Scripts

Scripts for producing monthly sales reports for TiKH '26 from two payment
providers: **SumUp** (in-person card sales, `myyntiraportti`) and **Stripe**
(online payments/payouts).

Each provider has two ways to get data in:
- **API fetch (recommended)** — pulls directly from the provider for a given
  month, no manual export needed.
- **Manual CSV** — process a CSV you already exported from the provider's
  dashboard, for months before the API scripts existed or if the API is
  unavailable.

## Setup

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Copy `.env.example` to `.env` and fill in your credentials:

```bash
cp .env.example .env
```

- `SUMUP_ACCESS_TOKEN` — personal access token from https://developer.sumup.com
  (or `SUMUP_CLIENT_ID` + `SUMUP_CLIENT_SECRET` for OAuth client credentials)
- `STRIPE_SECRET_KEY` — from https://dashboard.stripe.com/apikeys. If using a
  restricted key, it needs **read** access to Balance, Charges, Payouts, and
  **Checkout Sessions** (the last one is required to resolve item names —
  without it, line items silently fall back to raw charge IDs).

`.env` is gitignored and never committed.

## Quick start: fetch a month from the APIs

Month can be given as `M/YYYY` (e.g. `4/2026`) or `YYYY-MM` (e.g. `2026-04`).

```bash
source venv/bin/activate

# SumUp — in-person card sales
python sumup_fetch.py 4/2026

# Stripe — online payments/payouts
python stripe_fetch.py 4/2026
```

Each writes into its own provider subfolder under `Raportit/<Provider>/<KuukausiFI>/`
by default (e.g. `Raportit/Sumup/Huhtikuu/`, `Raportit/Stripe/Huhtikuu/`), or
pass `--output-dir <path>` to override.

### `sumup_fetch.py`
Fetches all transactions in the month via SumUp's transaction history API,
maps them to the same schema as a manual `myyntiraportti` export, then runs
the same cleaning/aggregation as `myyntiraportti_summary.py`.

Outputs (in `Raportit/Sumup/<KuukausiFI>/`):
- `myyntiraportti-<start>_<end>.csv` — raw rows, one per line item
- `myyntiraportti-cleaned-summary-<month>.csv` — aggregated by product,
  split into Card/Non-cash and Käteinen (cash) sections

Product names come from SumUp's `product_summary` field. SumUp's REST API
does not expose the merchant's product catalog/categories at all (confirmed
against the official OpenAPI spec — only the dashboard CSV export has them),
so categories are resolved from a local lookup file, `sumup_categories.json`
(product name → category). Any product not yet in that file falls back to
"Uncategorized" — when a new product type appears, add it to
`sumup_categories.json` and it'll be categorized correctly from then on.

### `stripe_fetch.py`
Finds payouts that **arrived** in the given month, then fetches every balance
transaction that makes up each payout (mirrors the manual
transfers+unified_payments merge). Item names are resolved by walking
charge → payment_intent → checkout session → line items; this chain requires
the "Checkout Sessions Read" permission on the API key (see Setup above).

Outputs (in `Raportit/Stripe/<KuukausiFI>/`):
- `stripe_payout_summary-<month>.csv` — one row per payout with totals
- `stripe_transactions-<month>.csv` — every balance transaction
- `stripe_item_summary-<month>.csv` — aggregated by item name

Fetching item names is slow (up to 3 API calls per transaction) — a month
with ~200 transactions can take a couple of minutes. Consider running it in
the background for busy months.

## Manual CSV workflow (fallback / pre-API months)

### Stripe: `process_payments.py`
Merges a manually exported `transfers` CSV and `unified_payments` CSV from
the Stripe dashboard and produces an item summary — the same output as
`stripe_fetch.py`, without needing API access.

Edit the `CONFIG` block at the top of `process_payments.py` to point at your
files for the month you're processing:

```python
path = "Raportit/Huhtikuu/"

CONFIG = {
    "input_files": {
        "transfers": path + "transfers (4).csv",
        "payments": path + "unified_payments (2).csv",
        ...
    },
    ...
}
```

Then run:

```bash
source venv/bin/activate
python process_payments.py
# or, equivalently:
python item_summary.py
```

### SumUp: `myyntiraportti_summary.py`
Cleans and aggregates a manually exported `myyntiraportti` CSV (same output
as the cleaned-summary half of `sumup_fetch.py`):

```bash
source venv/bin/activate
python myyntiraportti_summary.py <input_csv> <output_csv>
# defaults to myyntiraportti-2026-01-01_2026-01-30.csv if no args given
```

## Notes

- All scripts handle European number format (`"119,00"`) where relevant.
- Refunds are detected automatically and shown as negative values.
- Item combinations purchased together in one Stripe checkout stay combined
  in the output rather than being split into separate line items.
- Finnish month names used in output paths: tammikuu, helmikuu, maaliskuu,
  huhtikuu, toukokuu, kesäkuu, heinäkuu, elokuu, syyskuu, lokakuu,
  marraskuu, joulukuu.
