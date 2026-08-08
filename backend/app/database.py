import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

DEFAULT_DB = Path(__file__).resolve().parents[1] / "contextprobe.db"
DB_PATH = Path(os.getenv("CONTEXTPROBE_DB_PATH", DEFAULT_DB))

SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS assets (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  asset_type TEXT NOT NULL,
  description TEXT,
  owner TEXT,
  certified INTEGER NOT NULL DEFAULT 0,
  deprecated INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS columns (
  asset_id TEXT NOT NULL REFERENCES assets(id),
  name TEXT NOT NULL,
  data_type TEXT NOT NULL,
  description TEXT,
  PRIMARY KEY (asset_id, name)
);
CREATE TABLE IF NOT EXISTS lineage_edges (
  upstream_id TEXT NOT NULL REFERENCES assets(id),
  downstream_id TEXT NOT NULL REFERENCES assets(id),
  PRIMARY KEY (upstream_id, downstream_id)
);
CREATE TABLE IF NOT EXISTS probes (
  id TEXT PRIMARY KEY,
  asset_id TEXT NOT NULL REFERENCES assets(id),
  column_name TEXT,
  question TEXT NOT NULL,
  required_terms_json TEXT NOT NULL,
  expected_answer TEXT NOT NULL,
  wrong_answer TEXT NOT NULL,
  correct_markers_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS probe_results (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  probe_id TEXT NOT NULL REFERENCES probes(id),
  asset_id TEXT NOT NULL REFERENCES assets(id),
  engine TEXT NOT NULL,
  outcome TEXT NOT NULL,
  answer TEXT NOT NULL,
  context_seen TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS probe_results_latest
  ON probe_results (probe_id, created_at DESC);
"""


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    try:
        yield db
        db.commit()
    finally:
        db.close()


# id, name, type, description, owner, certified, deprecated
ASSETS = [
    ("raw_orders", "raw_orders", "source", "Raw order events landed from the storefront.", "Commerce Platform", 0, 0),
    ("raw_payments", "raw_payments", "source", "Raw payment gateway events.", "Payments Platform", 0, 0),
    ("raw_customers", "raw_customers", "source", "Raw customer profile records.", "Customer Platform", 0, 0),
    ("stg_orders", "stg_orders", "model", "Typed and deduplicated orders.", "Commerce Analytics", 0, 0),
    ("stg_payments", "stg_payments", "model", "Typed payment records with currency normalized to USD.", "Payments Analytics", 0, 0),
    ("stg_customers", "stg_customers", "model", "Cleaned customer records, one row per customer.", "Customer Data", 0, 0),
    ("fct_revenue", "fct_revenue", "model", "Revenue table.", "Finance Analytics", 1, 0),
    ("dim_customer", "dim_customer", "model", "Customer dimension.", "Customer Data", 0, 0),
    ("exec_revenue_dashboard", "executive_revenue_dashboard", "dashboard", "Executive revenue reporting.", "Finance Analytics", 1, 0),
    ("finance_monthly_report", "finance_monthly_report", "dashboard", "Monthly finance close reporting.", "Finance Analytics", 0, 0),
    ("legacy_customers", "legacy_customers", "source", "Deprecated customer table. Unverified, see owner before use.", "Customer Data", 0, 1),
    ("sandbox_experiments", "sandbox_experiments", "model", None, None, 0, 0),
]

EDGES = [
    ("raw_orders", "stg_orders"),
    ("raw_payments", "stg_payments"),
    ("raw_customers", "stg_customers"),
    ("stg_orders", "fct_revenue"),
    ("stg_payments", "fct_revenue"),
    ("stg_customers", "dim_customer"),
    ("dim_customer", "fct_revenue"),
    ("fct_revenue", "exec_revenue_dashboard"),
    ("fct_revenue", "finance_monthly_report"),
]

# asset_id, column, data_type, description
COLUMNS = [
    ("fct_revenue", "order_id", "string", "Order identifier, unique per row in this table."),
    ("fct_revenue", "net_revenue", "decimal", "Net revenue."),
    ("fct_revenue", "gross_revenue", "decimal",
     "Gross revenue in USD before returns, refunds and tax; recognized at order date."),
    ("fct_revenue", "recognized_at", "timestamp", None),
    ("stg_payments", "amount_usd", "decimal",
     "Payment amount converted to USD at the transaction-date rate, excluding tax and gateway fees."),
    ("stg_payments", "payment_status", "string", "Status."),
    ("stg_orders", "order_date", "timestamp",
     "Timestamp when the customer placed the order, in UTC."),
    ("stg_customers", "customer_key", "string",
     "Surrogate customer key generated in staging; not the source system identifier."),
    ("dim_customer", "region", "string", "Region."),
    ("legacy_customers", "email", "string",
     "Email address. Unverified legacy field, see owner before use."),
    ("sandbox_experiments", "value", "decimal", None),
]


# id, asset, column, question, required_terms, expected_answer, wrong_answer, correct_markers
PROBES = [
    # Vague description. Paired one-for-one with gross_revenue below.
    ("P_NET_TAX", "fct_revenue", "net_revenue", "Does net_revenue include tax?", ["tax"],
     "No. Tax is excluded from net_revenue.", "Yes, net_revenue includes tax.", ["exclud", "no"]),
    ("P_NET_REFUND", "fct_revenue", "net_revenue", "Are refunds deducted from net_revenue?", ["refund"],
     "Yes. Returns and refunds are deducted.", "No, refunds are not deducted from net_revenue.", ["yes"]),
    ("P_NET_CCY", "fct_revenue", "net_revenue", "What currency is net_revenue reported in?", ["usd"],
     "USD.", "It is reported in the customer's local currency.", ["usd"]),
    ("P_NET_RECOG", "fct_revenue", "net_revenue", "At what point is net_revenue recognized?", ["ship"],
     "At ship date.", "At the order date.", ["ship"]),
    # Qualified description covering the same four facts.
    ("P_GROSS_TAX", "fct_revenue", "gross_revenue", "Does gross_revenue include tax?", ["tax"],
     "No. Gross revenue is before tax.", "Yes, gross_revenue includes tax.", ["before", "no", "exclud"]),
    ("P_GROSS_REFUND", "fct_revenue", "gross_revenue", "Are refunds deducted from gross_revenue?", ["refund"],
     "No. It is before returns and refunds.", "Yes, refunds are already deducted.", ["before", "no"]),
    ("P_GROSS_CCY", "fct_revenue", "gross_revenue", "What currency is gross_revenue reported in?", ["usd"],
     "USD.", "It is reported in the customer's local currency.", ["usd"]),
    ("P_GROSS_RECOG", "fct_revenue", "gross_revenue", "At what point is gross_revenue recognized?", ["order date"],
     "At order date.", "At the ship date.", ["order date"]),
    # Missing description entirely: the safe-failure case.
    ("P_RECOG_MEAN", "fct_revenue", "recognized_at", "Is recognized_at the order date or the ship date?", ["ship"],
     "The ship date.", "It is the order date.", ["ship"]),
    ("P_REV_GRAIN", "fct_revenue", None, "What is one row in fct_revenue?", ["per order"],
     "One row per order.", "One row per customer.", ["per order"]),
    # Well-described column: should score clean.
    ("P_AMT_TAX", "stg_payments", "amount_usd", "Does amount_usd include tax?", ["tax"],
     "No. Tax is excluded.", "Yes, amount_usd includes tax.", ["exclud", "no"]),
    ("P_AMT_FX", "stg_payments", "amount_usd", "Which FX rate converts amount_usd?", ["transaction-date"],
     "The transaction-date rate.", "The month-end rate.", ["transaction-date"]),
    # Single-word description on a column feeding a certified dashboard.
    ("P_STATUS_VALUES", "stg_payments", "payment_status", "What are the allowed values of payment_status?", ["settled"],
     "settled, pending or reversed.", "Any string value is allowed.", ["settled"]),
    ("P_REGION_SCHEME", "dim_customer", "region", "Is region a billing region or a shipping region?", ["shipping"],
     "The shipping region.", "It is the billing region.", ["shipping"]),
    # Clear description: expect correct.
    ("P_ORDER_TZ", "stg_orders", "order_date", "What timezone is order_date stored in?", ["utc"],
     "UTC.", "It is stored in local time.", ["utc"]),
    ("P_CUSTKEY_SRC", "stg_customers", "customer_key", "Is customer_key the source system identifier?", ["not the source"],
     "No. It is a staging surrogate key.", "Yes, it is the source system identifier.", ["no", "surrogate"]),
    # Documented but self-declared unverified: should abstain, so risk stays zero.
    ("P_LEGACY_USE", "legacy_customers", "email", "Can I use legacy_customers.email for a new dashboard?",
     ["approved for reporting"],
     "No. It is deprecated and unverified.", "Yes, it is safe to use.", ["deprecat", "unverified"]),
    # Undocumented sandbox asset with no consumers.
    ("P_SANDBOX", "sandbox_experiments", "value", "What does value measure?", ["experiment"],
     "An experiment output value.", "It measures revenue.", ["experiment"]),
]


def initialize() -> None:
    with connect() as db:
        db.executescript(SCHEMA)
        db.executemany(
            """INSERT INTO assets VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                 name = excluded.name, asset_type = excluded.asset_type,
                 owner = excluded.owner, certified = excluded.certified,
                 deprecated = excluded.deprecated""",
            ASSETS,
        )
        db.executemany("INSERT OR IGNORE INTO lineage_edges VALUES (?, ?)", EDGES)
        db.executemany(
            """INSERT INTO columns VALUES (?, ?, ?, ?)
               ON CONFLICT(asset_id, name) DO UPDATE SET
                 data_type = excluded.data_type""",
            COLUMNS,
        )
        # Probe definitions are upserted so corrections to the suite always take
        # effect, instead of silently grading against a stale seeded row.
        db.executemany(
            """INSERT INTO probes
               (id, asset_id, column_name, question, required_terms_json,
                expected_answer, wrong_answer, correct_markers_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                 asset_id = excluded.asset_id, column_name = excluded.column_name,
                 question = excluded.question,
                 required_terms_json = excluded.required_terms_json,
                 expected_answer = excluded.expected_answer,
                 wrong_answer = excluded.wrong_answer,
                 correct_markers_json = excluded.correct_markers_json""",
            [
                (probe[0], probe[1], probe[2], probe[3], json.dumps(probe[4]),
                 probe[5], probe[6], json.dumps(probe[7]))
                for probe in PROBES
            ],
        )


def reset_fixture() -> None:
    """Restore seeded descriptions and clear probe history."""
    with connect() as db:
        db.execute("DELETE FROM probe_results")
        db.executemany(
            "UPDATE assets SET description = ? WHERE id = ?",
            [(asset[3], asset[0]) for asset in ASSETS],
        )
        db.executemany(
            "UPDATE columns SET description = ? WHERE asset_id = ? AND name = ?",
            [(column[3], column[0], column[1]) for column in COLUMNS],
        )
