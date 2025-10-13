# frontend/components/reconciliation_ui.py

import streamlit as st
import pandas as pd
import io
from backend.reconciliation import reconcile_service, exporter
from backend.reconciliation.bank_normalizer import normalize_transactions, BANK_PRESETS
from backend.utils.logger import logger
from backend.reconciliation.exporter import export_excel_bytes
from backend.reconciliation.gst_calculator import calculate_gst, GST_CATEGORY_OPTIONS, calculate_gst_value
from backend.reconciliation.session_manager import session_manager

# Cache heavy processing for speed
@st.cache_data(show_spinner=False)
def process_files_cached(file_entries):
    return reconcile_service.process_files(file_entries)

def get_excel_bytes(df_total, monthly_summary):
    return exporter.export_excel_bytes(df_total, monthly_summary)

def save_current_session():
    """Save current session state to disk."""
    if st.session_state.get("current_session_id") and st.session_state.get("logged_in"):
        username = st.session_state.user.get("username", "default_user")
        
        # Save pending changes frequently
        if st.session_state.get("reconciliation_results") is not None:
            session_manager.save_pending_changes_only(
                username,
                st.session_state.current_session_id,
                st.session_state.get("pending_changes", {}),
                st.session_state.get("updated_pages", set()),
                st.session_state.get("page_number", 1)
            )

def load_session(session_id: str):
    """Load a session and populate all state."""
    username = st.session_state.user.get("username", "default_user")
    session_data = session_manager.load_session_data(username, session_id)
    
    if session_data:
        # Load accounts metadata (store as metadata, not as full accounts with files)
        st.session_state.accounts_metadata = session_data.get("accounts", [])
        st.session_state.accounts = []  # Clear current accounts
        st.session_state.loaded_files_data = session_data.get("files_data", {})
        
        # Load results
        if session_data.get("results") is not None:
            st.session_state.reconciliation_results = session_data["results"]
            st.session_state.edited_df_cache = session_data["results"].copy()
            st.session_state.gst_calculated = True
        
        # Load state
        st.session_state.pending_changes = session_data.get("pending_changes", {})
        st.session_state.updated_pages = session_data.get("updated_pages", set())
        st.session_state.page_number = session_data.get("page_number", 1)
        st.session_state.current_session_id = session_id
        st.session_state.selected_rows = set()
        
        return True
    return False

def render():
    pd.set_option("styler.render.max_elements", 5000000)

    # Get username
    username = st.session_state.user.get("username", "default_user") if st.session_state.get("logged_in") else "default_user"

    # Initialize session state
    if "accounts" not in st.session_state:
        st.session_state.accounts = []
    if "accounts_metadata" not in st.session_state:
        st.session_state.accounts_metadata = []
    if "loaded_files_data" not in st.session_state:
        st.session_state.loaded_files_data = {}
    if "reconciliation_results" not in st.session_state:
        st.session_state.reconciliation_results = None
    if "file_uploader_key" not in st.session_state:
        st.session_state.file_uploader_key = 0
    if "page_number" not in st.session_state:
        st.session_state.page_number = 1
    if "page_size" not in st.session_state:
        st.session_state.page_size = 10
    if "show_gst" not in st.session_state:
        st.session_state.show_gst = True
    if "gst_calculated" not in st.session_state:
        st.session_state.gst_calculated = False
    if "edited_df_cache" not in st.session_state:
        st.session_state.edited_df_cache = None
    if "pending_changes" not in st.session_state:
        st.session_state.pending_changes = {}
    if "updated_pages" not in st.session_state:
        st.session_state.updated_pages = set()
    if "current_session_id" not in st.session_state:
        # Try to load latest session on first render
        latest_session = session_manager.get_latest_session(username)
        if latest_session:
            load_session(latest_session)
        else:
            st.session_state.current_session_id = None
    if "selected_rows" not in st.session_state:
        st.session_state.selected_rows = set()

    st.markdown("""
        <style>
            .block-container { padding-top: 2rem; }
        </style>
    """, unsafe_allow_html=True)

    st.markdown(
        "<h3 style='margin-top:0rem;margin-bottom:0rem'>🏦Bank Transactions Reconciliation</h3>",
        unsafe_allow_html=True,
    )

    col1, _, col2, _, col3 = st.columns([5, 0.1, 3.5, 0.1, 3])

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
        
        # Show loaded accounts from session
        display_accounts = st.session_state.accounts if st.session_state.accounts else st.session_state.accounts_metadata
        
        if display_accounts:
            for i, acc in enumerate(display_accounts, start=1):
                acc_col1, acc_col2 = st.columns([4, 1])
                with acc_col1:
                    st.write(f"**{i}. {acc['bank_name']} — {acc['account_number']}**")
                    
                    # Handle both file objects and string filenames
                    if 'files' in acc and acc['files']:
                        if isinstance(acc['files'][0], str):
                            # It's a list of filenames (from loaded session)
                            st.write("Files: " + ", ".join(acc['files']))
                        else:
                            # It's a list of file objects (from upload)
                            st.write("Files: " + ", ".join([f.name for f in acc["files"]]))
                    else:
                        st.write("Files: None")
                        
                with acc_col2:
                    if st.button("🗑️", key=f"remove_account_{i}", help="Remove this account"):
                        if st.session_state.accounts:
                            st.session_state.accounts.pop(i-1)
                        elif st.session_state.accounts_metadata:
                            st.session_state.accounts_metadata.pop(i-1)
                        st.rerun()
        else:
            st.info("No accounts added yet.")

    # --- Sessions List ---
    with col3:
        st.markdown(
            "<h4 style='margin-top:0;margin-bottom:2px;font-size:1.3rem;'>📂Sessions</h4>",
            unsafe_allow_html=True,
        )
        
        sessions = session_manager.get_all_sessions(username)
        
        if sessions:
            for session in sessions:
                session_col1, session_col2 = st.columns([4, 1])
                with session_col1:
                    is_current = session["session_id"] == st.session_state.current_session_id
                    label = f"{'🟢 ' if is_current else ''}**{session['display_name']}**"
                    if st.button(label, key=f"load_session_{session['session_id']}", use_container_width=True):
                        if load_session(session["session_id"]):
                            st.success(f"Session loaded: {session['display_name']}")
                            st.rerun()
                        else:
                            st.error("Failed to load session")
                with session_col2:
                    if st.button("🗑️", key=f"delete_session_{session['session_id']}", help="Delete session"):
                        if session_manager.delete_session(username, session["session_id"]):
                            if st.session_state.current_session_id == session["session_id"]:
                                st.session_state.current_session_id = None
                            st.success("Session deleted")
                            st.rerun()
        else:
            st.info("No sessions yet.")

    # --- Run Agent and Reset All ---
    st.markdown("<hr style='margin-top:5px; margin-bottom:5px;'>", unsafe_allow_html=True)
    btn_col1, btn_col2, btn_col3, btn_col4 = st.columns([3, 2, 2, 1])

    with btn_col2:
        if st.button("🚀Run Agent", disabled=len(st.session_state.accounts) == 0):
            # Create new session
            session_id = session_manager.create_session(username)
            st.session_state.current_session_id = session_id
            
            # Prepare file entries and save input data
            file_entries = []
            uploaded_files_data = {}
            
            for acc in st.session_state.accounts:
                for f in acc["files"]:
                    file_entries.append({
                        "bank_name": acc["bank_name"], 
                        "account_number": acc["account_number"], 
                        "file": f
                    })
                    # Store file content
                    f.seek(0)
                    uploaded_files_data[f.name] = f.read()
                    f.seek(0)
            
            # Save input data
            session_manager.save_input_data(username, session_id, st.session_state.accounts, uploaded_files_data)
            
            status_placeholder = st.empty()
            status_placeholder.info("Matching in progress ⏳")
            result_df = process_files_cached(file_entries)

            # Rename DataFrame columns
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
                st.session_state.pending_changes = {}
                st.session_state.updated_pages = set()
            else:
                # Auto-calculate GST
                result_df = calculate_gst(result_df)
                st.session_state.reconciliation_results = result_df
                st.session_state.edited_df_cache = result_df.copy()
                st.session_state.page_number = 1
                st.session_state.gst_calculated = True
                st.session_state.pending_changes = {}
                st.session_state.updated_pages = set()
                st.session_state.selected_rows = set()
                
                # Save output data
                session_manager.save_output_data(
                    username, session_id, result_df,
                    st.session_state.pending_changes,
                    st.session_state.updated_pages,
                    st.session_state.page_number
                )

    with btn_col4:
        if st.button("🔄Reset All", disabled=st.session_state.reconciliation_results is None):
            # Clear only Accounts Ready and Results screens
            # Keep Sessions intact (don't delete from disk)
            keys_to_clear = [
                "reconciliation_results", "page_number", "accounts", 
                "gst_calculated", "edited_df_cache", "pending_changes", 
                "updated_pages", "current_session_id", "accounts_metadata",
                "loaded_files_data", "selected_rows"
            ]
            for key in keys_to_clear:
                if key in st.session_state:
                    del st.session_state[key]
            
            # Increment file uploader key to reset the form
            st.session_state.file_uploader_key += 1
            st.rerun()

    # --- Display Results ---
    if st.session_state.reconciliation_results is not None:
        st.subheader("🔎Reconciliation Results")
        
        # Use cached edited dataframe
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

            for col in ["Total 🔵Incoming Income", "Total 🟡Outgoing Expense", "Total 🔵Incoming GST", "Total 🟡Outgoing GST"]:
                summary_df[col] = summary_df[col].map(lambda x: f"{x:.2f}" if pd.notnull(x) else "")

            def highlight_total(row):
                return (
                    ["background-color: #fff3cd; font-weight: bold"] * len(row)
                    if row["Year/Month"] == "Grand Total"
                    else [""] * len(row)
                )

            with st.expander("📊Monthly Summary", expanded=False):
                # Add CSS for monthly summary table
                st.markdown("""
                    <style>
                        div[data-testid="stDataFrame"] {
                            font-size: 11px !important;
                        }
                        div[data-testid="stDataFrame"] table {
                            font-size: 11px !important;
                        }
                    </style>
                """, unsafe_allow_html=True)
                st.dataframe(summary_df.style.apply(highlight_total, axis=1))

        # --- Detailed Table ---
        key_columns = [
            "Select", "Date", "Bank", "Account", "Description", "Debit", "Credit",
            "Classification", "PairID", "GL Account", "GST", "GST Category", "Who"
        ]

        df_display = df_total[[col for col in key_columns if col in df_total.columns and col != "Select"]].copy()
        
        # Add Select column
        df_display.insert(0, "Select", False)
        for idx in st.session_state.selected_rows:
            if idx in df_display.index:
                df_display.at[idx, "Select"] = True

        # Sort by PairID and Date
        if "PairID" in df_display.columns and df_display["PairID"].notna().any():
            df_display = df_display.sort_values(by=["PairID", "Date"], ascending=[True, True], na_position='last')
        else:
            df_display = df_display.sort_values(by=["Date"], ascending=True)

        # Pagination
        total_rows = len(df_display)
        total_pages = (total_rows // st.session_state.page_size) + (
            1 if total_rows % st.session_state.page_size > 0 else 0
        )
        start_idx = (st.session_state.page_number - 1) * st.session_state.page_size
        end_idx = start_idx + st.session_state.page_size
        df_page = df_display.iloc[start_idx:end_idx].copy()

        # Apply pending changes to current page
        for idx, new_category in st.session_state.pending_changes.items():
            if idx in df_page.index:
                df_page.at[idx, "GST Category"] = new_category

        # Prepare display with formatting
        df_page_display = df_page.copy()
        for col in ["Debit", "Credit", "GST"]:
            if col in df_page_display.columns:
                df_page_display[col] = df_page_display[col].map(
                    lambda x: f"{x:.2f}" if pd.notnull(x) else ""
                )

        with st.expander("📄Transaction Details", expanded=True):
            # Status bar
            pending_count = len(st.session_state.pending_changes)
            status_msg = f"**💡 Status:** {pending_count} pending change(s) | Pages updated: {len(st.session_state.updated_pages)}/{total_pages} | Session: {st.session_state.current_session_id or 'New'}"
            st.markdown(status_msg)
            
            # Delete selected rows button
            if len(st.session_state.selected_rows) > 0:
                if st.button(f"🗑️ Delete {len(st.session_state.selected_rows)} Selected Row(s)", type="primary"):
                    # Remove selected rows
                    df_display = df_display[~df_display.index.isin(st.session_state.selected_rows)]
                    
                    # Update main dataframes
                    st.session_state.edited_df_cache = df_display.drop(columns=["Select"])
                    st.session_state.reconciliation_results = df_display.drop(columns=["Select"])
                    
                    # Clear selection
                    st.session_state.selected_rows = set()
                    
                    # Save to session
                    if st.session_state.current_session_id:
                        session_manager.save_output_data(
                            username,
                            st.session_state.current_session_id,
                            st.session_state.reconciliation_results,
                            st.session_state.pending_changes,
                            st.session_state.updated_pages,
                            st.session_state.page_number
                        )
                    
                    st.success(f"Deleted {len(st.session_state.selected_rows)} row(s)")
                    st.rerun()
            
            # Add CSS for table styling
            st.markdown("""
                <style>
                    .table-header {
                        font-weight: bold;
                        background-color: #f0f2f6;
                        padding: 8px 4px;
                        border-bottom: 2px solid #ddd;
                        font-size: 11px;
                        text-align: center;
                    }
                    .table-cell {
                        font-size: 11px;
                        padding: 4px 2px;
                    }
                    div[data-testid="stText"] > div {
                        font-size: 11px !important;
                    }
                </style>
            """, unsafe_allow_html=True)
            
            # Display table header
            st.markdown("**Transaction Table:**")
            header_cols = st.columns([0.5, 1, 1, 1, 3, 1, 1, 1.5, 1, 1, 1, 1.5, 1])
            headers = ["☑", "Date", "Bank", "Account", "Description", "Debit", "Credit", 
                      "Classification", "PairID", "GL Account", "GST", "GST Category", "Who"]
            
            for col, header in zip(header_cols, headers):
                with col:
                    st.markdown(f"<div class='table-header'>{header}</div>", unsafe_allow_html=True)
            
            # Create a container for the table rows
            for display_idx, original_idx in enumerate(df_page.index):
                row_data = df_page_display.iloc[display_idx]
                
                # Create columns for each row
                cols = st.columns([0.5, 1, 1, 1, 3, 1, 1, 1.5, 1, 1, 1, 1.5, 1])
                
                # Select checkbox
                with cols[0]:
                    is_selected = st.checkbox(
                        "☑", 
                        value=original_idx in st.session_state.selected_rows,
                        key=f"select_{original_idx}_{st.session_state.page_number}",
                        label_visibility="collapsed"
                    )
                    if is_selected:
                        st.session_state.selected_rows.add(original_idx)
                    else:
                        st.session_state.selected_rows.discard(original_idx)
                
                # Display other columns as text with smaller font
                with cols[1]:
                    st.markdown(f"<div class='table-cell'>{str(row_data.get('Date', ''))}</div>", unsafe_allow_html=True)
                with cols[2]:
                    st.markdown(f"<div class='table-cell'>{str(row_data.get('Bank', ''))}</div>", unsafe_allow_html=True)
                with cols[3]:
                    st.markdown(f"<div class='table-cell'>{str(row_data.get('Account', ''))}</div>", unsafe_allow_html=True)
                with cols[4]:
                    desc = str(row_data.get("Description", ""))
                    desc_short = desc[:40] + "..." if len(desc) > 40 else desc
                    st.markdown(f"<div class='table-cell'>{desc_short}</div>", unsafe_allow_html=True)
                with cols[5]:
                    st.markdown(f"<div class='table-cell'>{str(row_data.get('Debit', ''))}</div>", unsafe_allow_html=True)
                with cols[6]:
                    st.markdown(f"<div class='table-cell'>{str(row_data.get('Credit', ''))}</div>", unsafe_allow_html=True)
                with cols[7]:
                    st.markdown(f"<div class='table-cell'>{str(row_data.get('Classification', ''))}</div>", unsafe_allow_html=True)
                with cols[8]:
                    st.markdown(f"<div class='table-cell'>{str(row_data.get('PairID', ''))}</div>", unsafe_allow_html=True)
                with cols[9]:
                    st.markdown(f"<div class='table-cell'>{str(row_data.get('GL Account', ''))}</div>", unsafe_allow_html=True)
                with cols[10]:
                    st.markdown(f"<div class='table-cell'>{str(row_data.get('GST', ''))}</div>", unsafe_allow_html=True)
                
                # GST Category selectbox - editable
                with cols[11]:
                    current_category = st.session_state.pending_changes.get(
                        original_idx,
                        df_page.at[original_idx, "GST Category"]
                    )
                    
                    new_category = st.selectbox(
                        "GST Cat",
                        options=GST_CATEGORY_OPTIONS,
                        index=GST_CATEGORY_OPTIONS.index(current_category) if current_category in GST_CATEGORY_OPTIONS else 0,
                        key=f"gst_cat_{original_idx}_{st.session_state.page_number}",
                        label_visibility="collapsed"
                    )
                    
                    # Track changes
                    original_from_cache = st.session_state.edited_df_cache.at[original_idx, "GST Category"]
                    if new_category != original_from_cache:
                        st.session_state.pending_changes[original_idx] = new_category
                    elif original_idx in st.session_state.pending_changes:
                        del st.session_state.pending_changes[original_idx]
                
                # Who column
                with cols[12]:
                    st.markdown(f"<div class='table-cell'>{str(row_data.get('Who', ''))}</div>", unsafe_allow_html=True)
            
            # Pagination and Submit button
            pag_col1, pag_col2, pag_col3, pag_col4 = st.columns([1, 1, 1, 1])
            
            with pag_col1:
                if st.button("⬅ Previous", key="prev_page") and st.session_state.page_number > 1:
                    st.session_state.page_number -= 1
                    # Save state before navigation
                    save_current_session()
                    st.rerun()
            
            with pag_col2:
                st.markdown(f"<div style='text-align: center; padding-top: 8px;'>Page {st.session_state.page_number} of {total_pages}</div>", unsafe_allow_html=True)
            
            with pag_col3:
                if st.button("Next ➡", key="next_page") and st.session_state.page_number < total_pages:
                    st.session_state.page_number += 1
                    # Save state before navigation
                    save_current_session()
                    st.rerun()
            
            with pag_col4:
                if st.button("✅ Submit", key="submit_changes", disabled=len(st.session_state.pending_changes) == 0):
                    # Apply all pending changes and recalculate GST
                    validation_errors = []
                    
                    for idx, new_category in st.session_state.pending_changes.items():
                        # Get original numeric values from edited_df_cache
                        debit = st.session_state.edited_df_cache.at[idx, "Debit"] if pd.notnull(st.session_state.edited_df_cache.at[idx, "Debit"]) else 0
                        credit = st.session_state.edited_df_cache.at[idx, "Credit"] if pd.notnull(st.session_state.edited_df_cache.at[idx, "Credit"]) else 0
                        
                        # Validation: GST on Sale requires non-zero credit
                        if new_category == "GST on Sale" and credit == 0:
                            validation_errors.append(f"Row index {idx}: GST on Sale requires non-zero Credit value")
                            continue
                        
                        # Validation: GST on Purchase requires non-zero debit
                        if new_category == "GST on Purchase" and debit == 0:
                            validation_errors.append(f"Row index {idx}: GST on Purchase requires non-zero Debit value")
                            continue
                        
                        # Recalculate GST
                        new_gst = calculate_gst_value(debit, credit, new_category)
                        
                        # Update in main dataframe
                        st.session_state.edited_df_cache.at[idx, "GST Category"] = new_category
                        st.session_state.edited_df_cache.at[idx, "GST"] = new_gst
                    
                    if validation_errors:
                        st.error("Validation Errors:\n\n" + "\n\n".join(validation_errors))
                    else:
                        # Update reconciliation results
                        st.session_state.reconciliation_results = st.session_state.edited_df_cache.copy()
                        st.session_state.updated_pages.add(st.session_state.page_number)
                        
                        # Save to session
                        if st.session_state.current_session_id:
                            session_manager.save_output_data(
                                username,
                                st.session_state.current_session_id,
                                st.session_state.reconciliation_results,
                                st.session_state.pending_changes,
                                st.session_state.updated_pages,
                                st.session_state.page_number
                            )
                        
                        # Clear pending changes
                        st.session_state.pending_changes = {}
                        
                        st.success(f"✅ Changes submitted! Page {st.session_state.page_number} updated.")
                        st.rerun()

        # Export with updated GST values
        # Remove Select column before export
        df_export = df_display.drop(columns=["Select"]) if "Select" in df_display.columns else df_display
        excel_bytes = get_excel_bytes(df_export, summary_df)
        st.download_button(
            label="📥 Download Full Excel",
            data=excel_bytes,
            file_name="reconciliation_results.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        
        # Auto-save on any interaction
        save_current_session()
