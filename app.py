# app.py
import os, re, tempfile, shutil
from datetime import datetime
import pandas as pd
import streamlit as st

# Optional (only if you use Camelot's lattice/stream flavors)
import camelot

st.set_page_config(page_title="PDF → CSV (Camelot)", layout="wide")

st.title("PDF → CSV (tables) — Camelot")
st.caption("Convert a folder of PDFs (or uploaded PDFs) into per-PDF CSVs + one master CSV.")

with st.expander("Options", expanded=True):
    mode = st.radio("Choose input method:", ["Use a local folder", "Upload PDFs"], horizontal=True)
    pages = st.text_input("Pages (Camelot)", value="all", help='e.g. "1", "1-3", "1,3,7", or "all"')
    flavor_order = st.multiselect(
        "Detection flavors to try (in order)",
        ["lattice", "stream"], default=["lattice", "stream"],
        help="lattice = gridlines; stream = whitespace/areas"
    )

def extract_date_from_filename(fname):
    # expects YYYY-MM-DD.pdf
    stem = os.path.splitext(os.path.basename(fname))[0]
    try:
        return datetime.strptime(stem, "%Y-%m-%d").date()
    except Exception:
        return None

def normalize(col):
    c = str(col).strip()
    c = re.sub(r"\s+", " ", c)
    c = c.replace("Prv.", "Prev").replace("Last Rate", "Last").replace("Open Rate", "Open")
    return c

def read_pdf_tables(pdf_path: str, pages: str, flavors: list):
    """Try flavors in order until we get some rows back."""
    last_exc = None
    for flv in flavors:
        try:
            tbls = camelot.read_pdf(pdf_path, pages=pages, flavor=flv, strip_text="\n")
            if tbls and sum(t.shape[0] for t in tbls) > 0:
                return tbls, flv
        except Exception as e:
            last_exc = e
            continue
    if last_exc:
        raise last_exc
    return [], None

def process_pdfs(pdf_dir: str, out_dir: str, pages: str, flavors: list):
    logs = []
    os.makedirs(out_dir, exist_ok=True)
    pdf_files = sorted([f for f in os.listdir(pdf_dir) if f.lower().endswith(".pdf")])
    if not pdf_files:
        logs.append(("warn", f"No PDFs found in {pdf_dir}"))
        return None, logs

    all_rows = []
    sample_preview = None

    for fname in pdf_files:
        fpath = os.path.join(pdf_dir, fname)
        date_val = extract_date_from_filename(fname)

        try:
            tables, used_flavor = read_pdf_tables(fpath, pages, flavors)
        except Exception as e:
            logs.append(("warn", f"{fname}: error reading ({e})"))
            continue

        if not tables or len(tables) == 0:
            logs.append(("warn", f"No tables found in {fname}"))
            continue

        pdf_df = pd.concat([t.df for t in tables], ignore_index=True)

        # Clean
        pdf_df.replace(r"^\s*$", pd.NA, regex=True, inplace=True)
        pdf_df.dropna(how="all", axis=0, inplace=True)
        pdf_df.dropna(how="all", axis=1, inplace=True)

        # Promote header if first row looks like column names
        header_candidates = ["Company", "Company Name", "Turnover", "Prv.Rate", "Open", "Highest", "Lowest", "Last", "Rate", "Diff"]
        if not pdf_df.empty and any(any(isinstance(x, str) and hc.lower() in x.lower() for x in pdf_df.iloc[0].tolist()) for hc in header_candidates):
            pdf_df.columns = pdf_df.iloc[0].astype(str).str.strip()
            pdf_df = pdf_df.iloc[1:].reset_index(drop=True)

        # Normalize col names
        pdf_df.columns = [normalize(c) for c in pdf_df.columns]

        # Add Date
        if date_val is not None:
            pdf_df.insert(0, "Date", date_val)

        # Save per-PDF CSV
        out_csv = os.path.join(out_dir, fname.replace(".pdf", ".csv"))
        pdf_df.to_csv(out_csv, index=False, encoding="utf-8-sig")

        # Append to master
        mask_data = pdf_df.notna().sum(axis=1) >= 3
        if mask_data.any():
            all_rows.append(pdf_df[mask_data])

        logs.append(("ok", f"{fname}  | rows: {len(pdf_df):>5} | flavor: {used_flavor} | saved: {out_csv}"))

        if sample_preview is None and not pdf_df.empty:
            sample_preview = pdf_df.head(25)

    master_path = None
    master_df = None
    if all_rows:
        master_df = pd.concat(all_rows, ignore_index=True)
        master_path = os.path.join(out_dir, "psx_closing_rates_master.csv")
        master_df.to_csv(master_path, index=False, encoding="utf-8-sig")
        logs.append(("master", f"Combined rows: {len(master_df)} | saved: {master_path}"))
    else:
        logs.append(("master-warn", "No rows extracted — try changing flavors/order or page ranges."))

    return (master_df, master_path, sample_preview), logs

# UI: Inputs & Run
if mode == "Use a local folder":
    col1, col2 = st.columns(2)
    with col1:
        in_dir = st.text_input("PDF folder path", value="", placeholder=r"C:\path\to\closing_rates_pdfs or /path/...")
    with col2:
        out_dir = st.text_input("Output CSV folder", value="", placeholder=r"C:\path\to\closing_rates_csv or /path/...")

    if st.button("Run conversion", type="primary", disabled=not in_dir or not out_dir):
        if not os.path.isdir(in_dir):
            st.error(f"PDF folder not found: {in_dir}")
        else:
            os.makedirs(out_dir, exist_ok=True)
            result, logs = process_pdfs(in_dir, out_dir, pages, flavor_order)
            # Logs
            for level, msg in logs:
                if level.startswith("ok"):
                    st.success(msg)
                elif level.startswith("master"):
                    st.info(msg)
                else:
                    st.warning(msg)

            # Preview + download master
            if result and result[0] is not None:
                master_df, master_path, sample_preview = result
                st.subheader("Master preview")
                st.dataframe(master_df.head(200), use_container_width=True)
                with open(master_path, "rb") as f:
                    st.download_button("Download master CSV", f, file_name=os.path.basename(master_path), mime="text/csv")

elif mode == "Upload PDFs":
    uploads = st.file_uploader("Drop multiple PDFs", type=["pdf"], accept_multiple_files=True)
    out_dir_up = st.text_input("Output CSV folder (local)", value="", placeholder=r"C:\path\to\closing_rates_csv or /path/...")
    run = st.button("Run conversion", type="primary", disabled=(not uploads or not out_dir_up))

    if run:
        if not out_dir_up:
            st.error("Please provide an output folder.")
        else:
            os.makedirs(out_dir_up, exist_ok=True)
            with tempfile.TemporaryDirectory() as td:
                in_dir = os.path.join(td, "pdfs")
                os.makedirs(in_dir, exist_ok=True)
                # write uploads to temp dir
                for uf in uploads:
                    with open(os.path.join(in_dir, uf.name), "wb") as f:
                        f.write(uf.read())

                result, logs = process_pdfs(in_dir, out_dir_up, pages, flavor_order)

                for level, msg in logs:
                    if level.startswith("ok"):
                        st.success(msg)
                    elif level.startswith("master"):
                        st.info(msg)
                    else:
                        st.warning(msg)

                if result and result[0] is not None:
                    master_df, master_path, sample_preview = result
                    st.subheader("Master preview")
                    st.dataframe(master_df.head(200), use_container_width=True)
                    with open(master_path, "rb") as f:
                        st.download_button("Download master CSV", f, file_name=os.path.basename(master_path), mime="text/csv")

st.markdown("---")
st.caption("Tip: Camelot 'lattice' works best with visible gridlines; 'stream' works better for whitespace-separated columns. For scanned PDFs, OCR first (e.g., `ocrmypdf`).")
