import pandas as pd
import re
from backend.utils.date_utils import parsedate
from backend.utils.logger import logger


# ------------------------
# Bank Preset Mappings
# ------------------------
BANK_PRESETS = {
    "CBA": {
        "date": "Date",
        "description": "Description",
        "amount": "Amount",
        "balance": "Balance"
    },
    "ANZ": {
        "date": "Transaction Date",
        "description": "Transaction Details",
        "amount": "Amount ($)",
        "balance": "Balance ($)"
    },
    "Westpac": {
        "date": "Date",
        "description": "Transaction Description",
        "debit": "Debit",
        "credit": "Credit",
        "balance": "Balance"
    },
    "NAB": {
        "date": "Date",
        "description": "Description",
        "debit": "Debit",
        "credit": "Credit",
        "balance": "Balance"
    },
    "Macquarie": {
        "date": "Date",
        "description": "Transaction Details",
        "amount": "Amount",
        "balance": "Balance"
    },
    "HSBC": {
        "date": "Date",
        "description": "Transaction Details",
        "debit": "Money Out",
        "credit": "Money In",
        "balance": "Balance"
    },
    "BOQ": {
        "date": "Transaction Date",
        "description": "Description",
        "amount": "Transaction Amount",
        "balance": "Balance"
    },
    "ING": {
        "date": "Date",
        "description": "Transaction Description",
        "amount": "Amount",
        "balance": "Balance"
    },
    "Bendigo": {
        "date": "Transaction Date",
        "description": "Particulars",
        "debit": "Withdrawal",
        "credit": "Deposit",
        "balance": "Balance"
    },
    "Suncorp": {
        "date": "Date",
        "description": "Transaction Description",
        "amount": "Transaction Amount",
        "balance": "Balance"
    },
    "AMP": {
        "date": "Date",
        "description": "Description",
        "debit": "Debit",
        "credit": "Credit",
        "balance": "Balance"
    },
    "ME": {
        "date": "Transaction Date",
        "description": "Description",
        "amount": "Amount",
        "balance": "Balance"
    },
}


# ------------------------
# Helpers
# ------------------------
def _clean_columns(df):
    """Strip whitespace and lowercase all column headers for safe matching."""
    return {c.strip().lower(): c for c in df.columns}


def _find_column(df, keywords):
    """Heuristic finder with case-insensitive + space-tolerant match."""
    cols = _clean_columns(df)
    for k in keywords:
        k = k.strip().lower()
        if k in cols:
            return cols[k]
        for c_lower, c_orig in cols.items():
            if k in c_lower:
                return c_orig
    return None


def _match_column_case_insensitive(df, col_name):
    """Match preset column to actual DataFrame col (case-insensitive + space-tolerant)."""
    if not col_name:
        return None
    cols = _clean_columns(df)
    return cols.get(col_name.strip().lower(), None)


# ------------------------
# Amount Cleaning Helper
# ------------------------
def clean_amount(value):
    """
    Cleans and converts amount strings to floats.
    Handles $, parentheses, commas, and $(...) formats.
    Examples:
      '$123.45'   -> 123.45
      '($123.45)' -> -123.45
      '$(123.45)' -> -123.45
      '(123.45)'  -> -123.45
      '123.45'    -> 123.45
    """
    if pd.isna(value):
        return 0.0

    val = str(value).strip()
    if val == "" or val.lower() in ["na", "null", "none"]:
        return 0.0

    # Check if value should be negative
    is_negative = "(" in val and ")" in val

    # Remove $, commas, parentheses, and spaces
    val = re.sub(r"[^0-9.\-]", "", val)

    try:
        num = float(val)
        return -num if is_negative else num
    except ValueError:
        return 0.0


# ------------------------
# Detect Debit/Credit Helper
# ------------------------
def detect_debit_credit(df):
    """
    Detects which numeric column is debit and which is credit.
    If one column has both positive and negative values, it is treated as both.
    """
    numeric_cols = df.select_dtypes(include=['number']).columns.tolist()
    debit_col = None
    credit_col = None

    for col in numeric_cols:
        sample = df[col].dropna().head(10)
        if sample.empty:
            continue
        if (sample < 0).any() and (sample > 0).any():
            debit_col = col
            credit_col = col
            break
        elif (sample < 0).any():
            debit_col = col
        elif (sample > 0).any():
            credit_col = col

    return debit_col, credit_col


# ------------------------
# Normalizer Function
# ------------------------
def normalize_transactions(df: pd.DataFrame, bank_name: str, account_number: str) -> pd.DataFrame:
    # -----------------------
    # Preprocess unknown / tab-delimited files
    # -----------------------
    if df is not None and not df.empty:
        # If df has generic integer columns, assume no headers
        if all(isinstance(c, int) for c in df.columns):
            # Assign default headers that match normaliser preset
            default_cols = ["Date", "Amount", "Description", "Balance"]
            if df.shape[1] >= 4:
                df = df.iloc[:, :4]  # Only take first 4 columns
                df.columns = default_cols
            else:
                # Fill missing columns with None
                cols = list(df.columns)
                df.columns = cols + default_cols[len(cols):]
            logger.info(f"Preprocessed file: Added headers {df.columns.tolist()}")

        # Ensure numeric columns are properly typed
        for col in ["Amount", "Balance"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

    # -----------------------
    # Existing normalization logic
    # -----------------------
    if df is None or df.empty:
        return pd.DataFrame(columns=[
            "transactionid","date","bsb","accountnumber","description",
            "debit","credit","balance","type","reference","bank","accounttype"
        ])

    df_local = df.copy()
    df_local.columns = [c.strip() for c in df_local.columns]
    logger.info(f"Bank: {bank_name}, Available columns: {df_local.columns.tolist()}")

    preset = BANK_PRESETS.get(bank_name.strip().title()) or BANK_PRESETS.get(bank_name.strip().upper())

    date_col = _match_column_case_insensitive(df_local, preset.get("date") if preset else None)
    desc_col = _match_column_case_insensitive(df_local, preset.get("description") if preset else None)
    debit_col = _match_column_case_insensitive(df_local, preset.get("debit") if preset else None)
    credit_col = _match_column_case_insensitive(df_local, preset.get("credit") if preset else None)
    amount_col = _match_column_case_insensitive(df_local, preset.get("amount") if preset else None)
    balance_col = _match_column_case_insensitive(df_local, preset.get("balance") if preset else None)

    if not date_col:
        date_col = _find_column(df_local, ["date", "txn_date", "value_date"])
    if not desc_col:
        desc_col = _find_column(df_local, ["description", "details", "narrative", "memo"])
    if not debit_col or not credit_col:
        detected_debit, detected_credit = detect_debit_credit(df_local)
        debit_col = debit_col or detected_debit
        credit_col = credit_col or detected_credit
        if not debit_col:
            debit_col = _find_column(df_local, ["debit", "withdrawal", "money out"])
        if not credit_col:
            credit_col = _find_column(df_local, ["credit", "deposit", "money in"])
    if not amount_col and not (debit_col and credit_col):
        amount_col = _find_column(df_local, ["amount", "transaction amount", "value"])
    if not balance_col:
        balance_col = _find_column(df_local, ["balance", "running balance"])

    # Fallback: use any numeric column if all missing
    if not any([debit_col, credit_col, amount_col]):
        numeric_cols = df_local.select_dtypes(include=["number"]).columns
        if len(numeric_cols) == 1:
            amount_col = numeric_cols[0]

    df_out = pd.DataFrame()
    df_out["transactionid"] = df_local.index.astype(str)
    df_out["date"] = df_local[date_col].apply(parsedate) if date_col else None
    df_out["bsb"] = None
    df_out["accountnumber"] = account_number
    df_out["description"] = df_local[desc_col] if desc_col else None

    # Debit / Credit / Amount
    if debit_col and credit_col and debit_col != credit_col:
        df_out["debit"] = df_local[debit_col].apply(clean_amount)
        df_out["credit"] = df_local[credit_col].apply(clean_amount)
    elif amount_col:
        df_out["amount"] = df_local[amount_col].apply(clean_amount)
        df_out["debit"] = df_out["amount"].apply(lambda x: abs(x) if x < 0 else 0)
        df_out["credit"] = df_out["amount"].apply(lambda x: x if x > 0 else 0)
    elif debit_col and credit_col and debit_col == credit_col:
        df_out["debit"] = df_local[debit_col].apply(lambda x: abs(clean_amount(x)) if clean_amount(x) < 0 else 0)
        df_out["credit"] = df_local[credit_col].apply(lambda x: clean_amount(x) if clean_amount(x) > 0 else 0)
    else:
        df_out["debit"], df_out["credit"] = 0, 0

    df_out["balance"] = df_local[balance_col].apply(clean_amount) if balance_col else None
    df_out["type"] = None
    df_out["reference"] = None
    df_out["bank"] = bank_name
    df_out["accounttype"] = None

    return df_out


