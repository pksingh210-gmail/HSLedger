# frontend/components/reconciliation_ui.py

# frontend/components/reconciliation_ui.py

import streamlit as st
import pandas as pd
from backend.reconciliation import reconcile_service, exporter
from backend.reconciliation.bank_normalizer import normalize_transactions, BANK_PRESETS
from backend.utils.logger import logger
from backend.reconciliation.exporter import export_excel_bytes
from frontend.components.debug_utils import show_temp_result
from backend.reconciliation.gst_calculator import calculate_gst, GST_CATEGORY_OPTIONS, calculate_gst_value
from backend.reconciliation.gst_editor import edit_gst_category_inline

# Cache heavy processing for speed
@st.cache_data(show_spinner=False)
def process_files_cached(file_entries):
    return reconcile_service.process_files(file_entries)

# Removed caching to allow real-time updates
def get_excel_bytes(df_total, monthly_summary):
    return exporter.export_excel_bytes(df_total, monthly_summary)

def render():
    pd.set_option("styler.render.max_elements", 5000000)

    # Initialize session state
    if "accounts" not in st.session_state:
        st.session_state.accounts = []
    if "reconciliation_results" not in st.session_state:
        st.session_state.reconciliation_results = None
    if "file_uploader_key" not in st.session_state:
        st.session_state.file_uploader_key = 0
    if "page_number" not in st.session_state:
        st.session_state.page_number = 1
    if "page_size" not in st.session_state:
        st.session_state.page_size = 50
    if "show_gst" not in st.session_state:
        st.session_state.show_gst = True
    if "gst_calculated" not in st.session_state:
        st.session_state.gst_calculated = False
    if "edited_df_cache" not in st.session_state:
        st.session_state.edited_df_cache = None

    st.markdown("""
        <style>
            .block-container { padding-top: 2rem; }
        </style>
    """, unsafe_allow_html=True)

    st.markdown(
        "<h3 style='margin-top:0rem;margin-bottom:0rem'>🏦Bank Transactions Reconciliation</h3>",
        unsafe_allow_html=True,
    )

    col1, _, col2 = st.columns([5, 0.2, 5])

    # --- Add Bank Account Form ---
    with col1:
        st.markdown(
            "<h4 style='margin-top:0rem; margin-bottom:0rem; font-size:1.3rem;'>➕Add Bank Account & Upload Files</h4>",
            unsafe_allow_html=True,
        )
        form_key = f"add_account_form_{st.session_state.file_uploader_key}"
        with st.form(key=form_key):
            sorted_banks = [""] + sorted(BANK_PRESETS.keys())
            bank_name = st.selectbox(
                "Bank Name",
                options=sorted_banks,
                index=0,
                key=f"bank_name_input_{st.session_state.file_uploader_key}",
            )
            account_number = st.text_input(
                "Account Number", key=f"account_number_input_{st.session_state.file_uploader_key}"
            )
            uploaded_files = st.file_uploader(
                "Upload CSV(s) for this account",
                type=["csv"],
                accept_multiple_files=True,
                key=f"uploaded_files_input_{st.session_state.file_uploader_key}",
            )
            submitted = st.form_submit_button("Add Account")
            if submitted:
                if not bank_name or not account_number or not uploaded_files:
                    st.error("Please provide bank name, account number and at least one CSV file.")
                else:
                    st.session_state.accounts.append(
                        {
                            "bank_name": bank_name,
                            "account_number": account_number,
                            "files": uploaded_files,
                        }
                    )
                    st.session_state.file_uploader_key += 1
                    st.rerun()

    # --- Accounts Ready ---
    with col2:
        st.markdown(
            "<h4 style='margin-top:0;margin-bottom:2px;font-size:1.3rem;'>📋Accounts Ready</h4>",
            unsafe_allow_html=True,
        )
        if st.session_state.accounts:
            for i, acc in enumerate(st.session_state.accounts, start=1):
                st.write(f"**{i}. {acc['bank_name']} — {acc['account_number']}**")
                st.write("Files: " + ", ".join([f.name for f in acc["files"]]))
        else:
            st.info("No accounts added yet.")

    # --- Run Agent and Reset ---
    st.markdown("<hr style='margin-top:5px; margin-bottom:5px;'>", unsafe_allow_html=True)
    btn_col1, btn_col2, btn_col3, btn_col4 = st.columns([3, 2, 2, 1])

    with btn_col2:
        if st.button("🚀Run Agent", disabled=len(st.session_state.accounts) == 0):
            file_entries = [
                {"bank_name": acc["bank_name"], "account_number": acc["account_number"], "file": f}
                for acc in st.session_state.accounts
                for f in acc["files"]
            ]
            status_placeholder = st.empty()
            status_placeholder.info("Matching in progress ⏳")
            result_df = process_files_cached(file_entries)

            # Rename DataFrame columns to standardized names
            result_df = result_df.rename(columns={
                "date": "Date", "bank": "Bank", "account": "Account",
                "description": "Description", "debit": "Debit", "credit": "Credit",
                "tmp_idx": "TmpIdx", "classification": "Classification", "pairid": "PairID",
                "GL Account": "GL Account", "GST": "GST", "GST Category": "GST Category",
                "Who": "Who", "Month": "Month", "Year": "Year"
            })

            status_placeholder.empty()

            if result_df is None or result_df.empty:
                st.info("No transactions processed or all files invalid.")
                st.session_state.reconciliation_results = None
                st.session_state.gst_calculated = False
                st.session_state.edited_df_cache = None
            else:
                # Auto-calculate GST on initial load
                result_df = calculate_gst(result_df)
                st.session_state.reconciliation_results = result_df
                st.session_state.edited_df_cache = result_df.copy()
                st.session_state.page_number = 1
                st.session_state.gst_calculated = True
                #st.success("✅ GST calculated automatically!")

    with btn_col4:
        if st.button("🔄Reset", disabled=st.session_state.reconciliation_results is None):
            keys_to_clear = ["reconciliation_results", "page_number", "page_size", "accounts", 
                           "gst_calculated", "edited_df_cache", "original_gst_df"]
            for key in keys_to_clear:
                if key in st.session_state:
                    del st.session_state[key]
            st.rerun()

    # --- Display Results ---
    if st.session_state.reconciliation_results is not None:
        st.subheader("🔎Reconciliation Results")
        
        # Use cached edited dataframe if available
        if st.session_state.edited_df_cache is not None:
            df_total = st.session_state.edited_df_cache.copy()
        else:
            df_total = st.session_state.reconciliation_results.copy()

        if not st.session_state.show_gst and "GST" in df_total.columns:
            df_total = df_total.drop(columns=["GST"])

        # --- Monthly Summary ---
        monthly_summary = None
        summary_df = None
        if "Date" in df_total.columns and not df_total["Date"].isna().all():
            df_total["Date_dt"] = pd.to_datetime(df_total["Date"], errors="coerce", dayfirst=True)
            df_total["Month"] = df_total["Date_dt"].dt.month
            df_total["Year"] = df_total["Date_dt"].dt.year
            df_total["Date"] = df_total["Date_dt"].dt.strftime("%d/%m/%Y")

            monthly_summary = []
            for (year, month), group in df_total.groupby(["Year", "Month"]):
                internal_count = (group["Classification"] == "🟢Internal").sum()
                incoming_count = (group["Classification"] == "🔵Incoming").sum()
                outgoing_count = (group["Classification"] == "🟡Outgoing").sum()
                total_income = group.loc[group["Classification"] == "🔵Incoming", "Credit"].sum()
                total_expense = group.loc[group["Classification"] == "🟡Outgoing", "Debit"].sum()
                total_incoming_gst = group.loc[group["Classification"] == "🔵Incoming", "GST"].sum()
                total_outgoing_gst = group.loc[group["Classification"] == "🟡Outgoing", "GST"].sum()
                year_month_str = f"{year}/{month:02d}"

                monthly_summary.append([
                    year_month_str, internal_count, incoming_count, outgoing_count,
                    total_income, total_expense, total_incoming_gst, total_outgoing_gst,
                ])

            summary_df = pd.DataFrame(
                monthly_summary,
                columns=[
                    "Year/Month", "🟢Internal Transfers", "🔵Incoming Count", "🟡Outgoing Count",
                    "Total 🔵Incoming Income", "Total 🟡Outgoing Expense",
                    "Total 🔵Incoming GST", "Total 🟡Outgoing GST",
                ],
            )

            totals = pd.DataFrame([[
                "Grand Total",
                summary_df["🟢Internal Transfers"].sum(),
                summary_df["🔵Incoming Count"].sum(),
                summary_df["🟡Outgoing Count"].sum(),
                summary_df["Total 🔵Incoming Income"].sum(),
                summary_df["Total 🟡Outgoing Expense"].sum(),
                summary_df["Total 🔵Incoming GST"].sum(),
                summary_df["Total 🟡Outgoing GST"].sum(),
            ]], columns=summary_df.columns)

            summary_df = pd.concat([summary_df, totals], ignore_index=True)

            # Round decimals
            for col in ["Total 🔵Incoming Income", "Total 🟡Outgoing Expense", "Total 🔵Incoming GST", "Total 🟡Outgoing GST"]:
                summary_df[col] = summary_df[col].map(lambda x: f"{x:.2f}" if pd.notnull(x) else "")

            def highlight_total(row):
                return (
                    ["background-color: #fff3cd; font-weight: bold"] * len(row)
                    if row["Year/Month"] == "Grand Total"
                    else [""] * len(row)
                )

            with st.expander("📊Monthly Summary", expanded=False):
                st.dataframe(summary_df.style.apply(highlight_total, axis=1))

        # --- Detailed Table ---
        key_columns = [
            "Date", "Bank", "Account", "Description", "Debit", "Credit",
            "Classification", "PairID", "GL Account", "GST", "GST Category", "Who"
        ]

        df_display = df_total[[col for col in key_columns if col in df_total.columns]].copy()

        # Sort by PairID and Date
        if "PairID" in df_display.columns and df_display["PairID"].notna().any():
            df_display = df_display.sort_values(by=["PairID", "Date"], ascending=[True, True], na_position='last')
        else:
            df_display = df_display.sort_values(by=["Date"], ascending=True)

        # Pagination setup BEFORE editing
        total_rows = len(df_display)
        total_pages = (total_rows // st.session_state.page_size) + (
            1 if total_rows % st.session_state.page_size > 0 else 0
        )
        start_idx = (st.session_state.page_number - 1) * st.session_state.page_size
        end_idx = start_idx + st.session_state.page_size
        df_page = df_display.iloc[start_idx:end_idx].copy()

        # Prepare display with formatting for non-editable columns
        df_page_display = df_page.copy()
        for col in ["Debit", "Credit", "GST"]:
            if col in df_page_display.columns:
                df_page_display[col] = df_page_display[col].map(
                    lambda x: f"{x:.2f}" if pd.notnull(x) else ""
                )

        with st.expander("📄Transaction Details", expanded=True):
            #st.markdown("**💡 Tip:** Click on any GST Category cell to change it. GST will auto-recalculate!")
            
            # Configure data editor with GST Category dropdown
            column_config = {
                "GST Category": st.column_config.SelectboxColumn(
                    "GST Category",
                    help="Select to auto-recalculate GST",
                    width="medium",
                    options=GST_CATEGORY_OPTIONS,
                    required=True,
                ),
                "Date": st.column_config.TextColumn("Date", width="small"),
                "Bank": st.column_config.TextColumn("Bank", width="small"),
                "Account": st.column_config.TextColumn("Account", width="small"),
                "Description": st.column_config.TextColumn("Description", width="large"),
                "Debit": st.column_config.TextColumn("Debit", width="small"),
                "Credit": st.column_config.TextColumn("Credit", width="small"),
                "GST": st.column_config.TextColumn("GST", width="small"),
                "Classification": st.column_config.TextColumn("Classification", width="small"),
                "PairID": st.column_config.TextColumn("PairID", width="small"),
            }
            
            # Create editable dataframe
            edited_page = st.data_editor(
                df_page_display,
                column_config=column_config,
                use_container_width=True,
                hide_index=True,
                num_rows="fixed",
                disabled=[col for col in df_page_display.columns if col not in ["GST Category"]],
                key=f"gst_editor_page_{st.session_state.page_number}"
            )
            
            # Check for changes and recalculate GST
            if edited_page is not None:
                changes_made = False
                for display_idx, original_idx in enumerate(df_page.index):
                    original_category = df_page.at[original_idx, "GST Category"]
                    new_category = edited_page.iloc[display_idx]["GST Category"]
                    
                    if original_category != new_category:
                        changes_made = True
                        # Get numeric values from original df_page (before formatting)
                        debit = df_page.at[original_idx, "Debit"] if pd.notnull(df_page.at[original_idx, "Debit"]) else 0
                        credit = df_page.at[original_idx, "Credit"] if pd.notnull(df_page.at[original_idx, "Credit"]) else 0
                        
                        # Recalculate GST
                        new_gst = calculate_gst_value(debit, credit, new_category)
                        
                        # Update in main dataframe
                        df_display.at[original_idx, "GST Category"] = new_category
                        df_display.at[original_idx, "GST"] = new_gst
                
                # If changes were made, update cache and rerun
                if changes_made:
                    # Update the full dataframe in cache
                    st.session_state.edited_df_cache = df_display.copy()
                    st.session_state.reconciliation_results = df_display.copy()
                    st.rerun()
            
            # Apply color coding using CSS styles
            def get_row_style(row):
                cls = row.get("Classification", "")
                if cls == "Internal":
                    return "background-color: #d4f7dc"
                elif cls == "Incoming":
                    return "background-color: #e8f3ff"
                elif cls == "Outgoing":
                    return "background-color: #fff3cd"
                return ""
            
            # Pagination
            pag_col1, pag_col2, pag_col3 = st.columns([1, 1, 1])
            with pag_col1:
                if st.button("⬅ Previous", key="prev_page") and st.session_state.page_number > 1:
                    st.session_state.page_number -= 1
                    st.rerun()
            with pag_col3:
                if st.button("Next ➡", key="next_page") and st.session_state.page_number < total_pages:
                    st.session_state.page_number += 1
                    st.rerun()
            with pag_col2:
                st.markdown(f"Page {st.session_state.page_number} of {total_pages}")

        # Export with updated GST values
        excel_bytes = get_excel_bytes(df_display, summary_df)
        st.download_button(
            label="📥 Download Full Excel",
            data=excel_bytes,
            file_name="reconciliation_results.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

