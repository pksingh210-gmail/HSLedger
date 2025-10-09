import pandas as pd
import itertools
import streamlit as st

# Australian GST rate
GST_RATE = 0.10

def classify_transactions(df: pd.DataFrame, show_progress=True) -> pd.DataFrame:
    df.columns = df.columns.str.strip().str.lower()
    df = df.reset_index(drop=True)

    # Ensure numeric with zero fallback
    df["debit"] = pd.to_numeric(df.get("debit", 0), errors="coerce").fillna(0)
    df["credit"] = pd.to_numeric(df.get("credit", 0), errors="coerce").fillna(0)

    # --- Do NOT modify date --- keep as received from normalizer
    # if "date" in df.columns:
    #     df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.date

    # Description-based adjustment
    def desc_based_adjust(row):
        desc = str(row.get("description", "")).lower()
        amount = row.get("debit") - row.get("credit")
        if amount != 0:
            return row
        if any(keyword in desc for keyword in ["transfer to", "payment", "card", "bill", "invoice"]):
            row["debit"] = abs(amount) if amount <= 0 else row["debit"]
            row["credit"] = 0
        elif any(keyword in desc for keyword in ["direct credit", "salary", "refund", "fast transfer from"]):
            row["credit"] = abs(amount) if amount >= 0 else row["credit"]
            row["debit"] = 0
        return row

    df = df.apply(desc_based_adjust, axis=1)

    df["tmp_idx"] = df.index

    # Include all non-zero debits/credits
    debit_df = df[df["debit"] != 0][["tmp_idx", "date", "debit", "bank", "account"]].copy()
    credit_df = df[df["credit"] != 0][["tmp_idx", "date", "credit", "bank", "account"]].copy()

    debit_df = debit_df.rename(columns={
        "tmp_idx": "tmp_idx_debit", "date": "date_debit", "debit": "amount",
        "bank": "bank_debit", "account": "account_debit"
    })
    credit_df = credit_df.rename(columns={
        "tmp_idx": "tmp_idx_credit", "date": "date_credit", "credit": "amount",
        "bank": "bank_credit", "account": "account_credit"
    })

    # Outer merge on amount to find possible internal transfers
    merged = pd.merge(
        debit_df, credit_df,
        on="amount", how="outer", indicator=True
    )

    # --- Keep original dates from normalizer ---
    # merged["date_debit"] = pd.to_datetime(merged.get("date_debit"), errors="coerce").dt.date
    # merged["date_credit"] = pd.to_datetime(merged.get("date_credit"), errors="coerce").dt.date

    df["classification"] = None
    df["pairid"] = None
    df["GL Account"] = None
    df["GST"] = 0.0
    df["Who"] = None

    pair_id_counter = itertools.count(1)
    matched_debits, matched_credits = set(), set()

    progress_bar = st.progress(0, text="Matching internal transfers...") if show_progress else None
    total = len(merged)

    for i, row in enumerate(merged.itertuples(index=False), 1):
        d_idx = getattr(row, "tmp_idx_debit", None)
        c_idx = getattr(row, "tmp_idx_credit", None)
        if d_idx in matched_debits or c_idx in matched_credits:
            continue
        if pd.isna(d_idx) or pd.isna(c_idx):
            continue

        # --- Only allow internal transfer if accounts are different ---
        account_debit = getattr(row, "account_debit", None)
        account_credit = getattr(row, "account_credit", None)
        if account_debit == account_credit:
            continue  # skip internal transfer within the same account

        # Treat as internal transfer across accounts
        pid = f"PAIR{next(pair_id_counter):05d}"
        df.loc[int(d_idx), ["classification", "pairid"]] = ["Internal", pid]
        df.loc[int(c_idx), ["classification", "pairid"]] = ["Internal", pid]
        matched_debits.add(int(d_idx))
        matched_credits.add(int(c_idx))

        if progress_bar and i % 50 == 0:
            progress_bar.progress(min(i / total, 1.0), text="Matching internal transfers...")

    if progress_bar:
        progress_bar.progress(1.0, text="Matching complete ✅")

    # External classification
    mask_unclassified = df["classification"].isna()
    df.loc[mask_unclassified & (df["debit"] > 0), "classification"] = "Outgoing"
    df.loc[mask_unclassified & (df["credit"] > 0), "classification"] = "Incoming"
    df["classification"] = df["classification"].fillna("Unclassified")

    # GST calculation for external transactions (GST-inclusive)
    def calculate_gst_inclusive(row):
        if row["classification"] in ["Incoming", "Outgoing"]:
            if row["classification"] == "Outgoing":
                return round(row["debit"] * GST_RATE / (1 + GST_RATE), 2)
            if row["classification"] == "Incoming":
                return round(row["credit"] * GST_RATE / (1 + GST_RATE), 2)
        return 0.0

    df["GST"] = df.apply(calculate_gst_inclusive, axis=1)

    # --- Pull month/year straight from normalizer date ---
    if "date" in df.columns:
        df["Month"] = df["date"].apply(lambda x: x.month if hasattr(x, "month") else None)
        df["Year"] = df["date"].apply(lambda x: x.year if hasattr(x, "year") else None)

    required_final = [
        "date", "bank", "account", "description", "debit", "credit",
        "classification", "pairid", "GL Account", "GST", "Who"
    ]
    for col in required_final:
        if col not in df.columns:
            df[col] = None
    df = df[required_final]

    rename_map = {
        "date": "Date",
        "description": "Description",
        "bank": "Bank",
        "account": "Account",
        "debit": "Debit",
        "credit": "Credit",
        "classification": "Classification",
        "pairid": "PairID",
        "GL Account": "GL Account",
        "GST": "GST",
        "Who": "Who"
    }
    df = df.rename(columns=rename_map)

    return df
