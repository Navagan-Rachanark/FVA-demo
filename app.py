import difflib
import sqlite3
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

st.set_page_config(page_title="RM Forecast Tracker", layout="wide", page_icon="📊")

# ---------------------------------------------------------------------------
# Local data folder — created next to app.py the first time it runs.
#
#   data/
#     forecast_tracker.db   ← SQLite database (rules + every week ever uploaded)
#     uploads/               ← a copy of every Excel file ever uploaded, kept
#                               for audit ("ใครอัปโหลดอะไรเมื่อไหร่")
#     exports/                ← a copy of every report exported from the app
#
# Everything here lives on THIS machine only. Back up the data/ folder
# regularly — it is the only place forecast history is kept.
# ---------------------------------------------------------------------------
APP_DIR = Path(__file__).parent
DATA_DIR = APP_DIR / "data"
DB_PATH = DATA_DIR / "forecast_tracker.db"
UPLOADS_DIR = DATA_DIR / "uploads"
EXPORTS_DIR = DATA_DIR / "exports"
for d in (DATA_DIR, UPLOADS_DIR, EXPORTS_DIR):
    d.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Look & feel
# ---------------------------------------------------------------------------
st.markdown(
    """
    <style>
    .block-container { padding-top: 2rem; max-width: 1200px; }
    .badge { display: inline-block; padding: 3px 10px; border-radius: 999px;
             font-size: 12px; font-weight: 600; }
    .badge-ok   { background: #E4EFE9; color: #1F6F5C; }
    .badge-warn { background: #FBEFD9; color: #8A5A0A; }
    .badge-bad  { background: #FBE7E1; color: #B23A13; }
    .kpi-card { background: #FFFFFF; border: 1px solid #E4E0D4; border-radius: 12px;
                padding: 16px 20px; }
    .kpi-label { font-size: 13px; color: #6B6A63; margin-bottom: 6px; }
    .kpi-value { font-size: 24px; font-weight: 700; font-family: monospace; }
    </style>
    """,
    unsafe_allow_html=True,
)

# ---------------------------------------------------------------------------
# Database layer
# ---------------------------------------------------------------------------

def get_conn():
    return sqlite3.connect(DB_PATH)


def init_db():
    with get_conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS rules (
                compound TEXT, customer TEXT, sales TEXT, csr TEXT,
                threshold_kg REAL, threshold_pct REAL, mode TEXT,
                stale_days INTEGER, email TEXT,
                PRIMARY KEY (compound, customer)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS weekly (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                compound TEXT, customer TEXT, week TEXT,
                forecast_kg REAL, actual_kg REAL,
                uploaded_at TEXT, source_file TEXT
            )
            """
        )
        # Seed with the same starting point as the earlier mock-up, but only
        # the very first time — after that, whatever is in data/ wins.
        if conn.execute("SELECT COUNT(*) FROM rules").fetchone()[0] == 0:
            conn.executemany(
                """INSERT INTO rules VALUES (?,?,?,?,?,?,?,?,?)""",
                [
                    ("EPDM-5153REL", "Kitz", "สมชาย วงศ์ไทย", "เอ - อรวรรณ",
                     600, 25, "ถึงเกณฑ์ไหนก่อน แจ้งก่อน", 7, "a@chemicalinnovation.co.th"),
                    ("NBR-6601", "Siam Keeper", "สมชาย วงศ์ไทย", "เอ - อรวรรณ",
                     800, 20, "ใช้ กก. เป็นหลัก", 7, "a@chemicalinnovation.co.th"),
                    ("ECO-CG105", "TRINEX", "นวกานต์ ราชานาค", "บี - ปวีณา",
                     300, 15, "ใช้ % เป็นหลัก", 14, "b@chemicalinnovation.co.th"),
                    ("FKM-7012Y", "Yamaha", "นวกานต์ ราชานาค", "ซี - ธนพล",
                     200, 20, "ถึงเกณฑ์ไหนก่อน แจ้งก่อน", 7, "c@chemicalinnovation.co.th"),
                ],
            )
        if conn.execute("SELECT COUNT(*) FROM weekly").fetchone()[0] == 0:
            today = datetime.now().strftime("%Y-%m-%d")
            conn.executemany(
                """INSERT INTO weekly
                   (compound, customer, week, forecast_kg, actual_kg, uploaded_at, source_file)
                   VALUES (?,?,?,?,?,?,?)""",
                [
                    ("EPDM-5153REL", "Kitz", "W39", 2400, 2150, today, "(ข้อมูลตั้งต้นของเดโม)"),
                    ("NBR-6601", "Siam Keeper", "W39", 3200, 2600, today, "(ข้อมูลตั้งต้นของเดโม)"),
                    ("ECO-CG105", "TRINEX", "W39", 1800, 1920, today, "(ข้อมูลตั้งต้นของเดโม)"),
                    ("FKM-7012Y", "Yamaha", "W39", 950, 980, today, "(ข้อมูลตั้งต้นของเดโม)"),
                ],
            )


def load_rules():
    with get_conn() as conn:
        return pd.read_sql("SELECT * FROM rules", conn)


def save_rules(df: pd.DataFrame):
    with get_conn() as conn:
        conn.execute("DELETE FROM rules")
        df.to_sql("rules", conn, if_exists="append", index=False)


def load_weekly():
    with get_conn() as conn:
        return pd.read_sql("SELECT * FROM weekly", conn)


def insert_weekly_rows(rows: pd.DataFrame, source_file: str):
    rows = rows.copy()
    rows["uploaded_at"] = datetime.now().strftime("%Y-%m-%d")
    rows["source_file"] = source_file
    with get_conn() as conn:
        rows.to_sql("weekly", conn, if_exists="append", index=False)


init_db()

# ---------------------------------------------------------------------------
# Calculation — backlog is now a TRUE cumulative total across every week
# ever uploaded for that compound/customer, because the data actually
# persists in data/forecast_tracker.db instead of living only in memory.
# ---------------------------------------------------------------------------

def compute_dashboard():
    weekly = load_weekly()
    rules = load_rules()
    if weekly.empty or rules.empty:
        return pd.DataFrame()

    weekly["shortfall_kg"] = (weekly["forecast_kg"] - weekly["actual_kg"]).clip(lower=0)

    cumulative = (
        weekly.groupby(["compound", "customer"])["shortfall_kg"].sum()
        .rename("backlog_kg").reset_index()
    )
    latest_idx = weekly.groupby(["compound", "customer"])["uploaded_at"].idxmax()
    latest = weekly.loc[latest_idx, ["compound", "customer", "week", "forecast_kg",
                                      "actual_kg", "uploaded_at"]]

    df = latest.merge(cumulative, on=["compound", "customer"])
    df = df.merge(rules, on=["compound", "customer"], how="left")

    df["variance_pct"] = ((df["actual_kg"] - df["forecast_kg"]) / df["forecast_kg"] * 100).round(1)
    df["days_since_upload"] = (
        pd.Timestamp.now().normalize() - pd.to_datetime(df["uploaded_at"])
    ).dt.days

    def status_row(r):
        over_kg = r["backlog_kg"] > r["threshold_kg"]
        over_pct = abs(r["variance_pct"]) > r["threshold_pct"]
        stale = r["days_since_upload"] > r["stale_days"]

        if r["mode"] == "ใช้ กก. เป็นหลัก":
            flagged = over_kg
        elif r["mode"] == "ใช้ % เป็นหลัก":
            flagged = over_pct
        else:
            flagged = over_kg or over_pct

        if stale:
            return "ข้อมูลขาดหาย", "badge-bad"
        if flagged and r["backlog_kg"] > r["threshold_kg"] * 1.5:
            return "เกินเกณฑ์", "badge-bad"
        if flagged:
            return "เฝ้าระวัง", "badge-warn"
        return "ปกติ", "badge-ok"

    df[["status", "badge_class"]] = df.apply(lambda r: pd.Series(status_row(r)), axis=1)
    return df


FIELD_SYNONYMS = {
    "compound": ["compound", "สูตร", "code", "item", "grade"],
    "customer": ["customer", "cust", "client", "ลูกค้า", "cust name"],
    "forecast_kg": ["forecast", "forecast qty", "qty", "quantity", "จำนวน"],
    "actual_kg": ["actual", "actual qty", "sales", "shipped", "ยอดขาย", "ยอดส่ง"],
    "week": ["week", "wk", "สัปดาห์"],
}
FIELD_LABELS = {
    "compound": "รหัสสูตรนะจ๊ะ", "customer": "ลูกค้านะจ๊ะ", "forecast_kg": "Forecast (kg) นะจ๊ะ",
    "actual_kg": "Actual (kg) นะจ๊ะ", "week": "สัปดาห์นะจ๊ะ",
}


def best_match(column_name: str):
    name = column_name.strip().lower()
    best_field, best_score = None, 0.0
    for field, synonyms in FIELD_SYNONYMS.items():
        for syn in synonyms:
            score = difflib.SequenceMatcher(None, name, syn).ratio()
            if score > best_score:
                best_field, best_score = field, score
    return best_field, best_score


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------
st.sidebar.title("RM Forecast Tracker นะจ๊ะ")
page = st.sidebar.radio("เมนู", ["📊 Dashboard", "📤 อัปโหลดข้อมูล", "⚙️ ตั้งค่า"])
st.sidebar.divider()
st.sidebar.caption("ข้อมูลเก็บถาวรที่เครื่องนี้ ในโฟลเดอร์:")
st.sidebar.code(str(DATA_DIR), language=None)

# ---------------------------------------------------------------------------
# Page: Dashboard
# ---------------------------------------------------------------------------
if page == "📊 Dashboard":
    st.title("รายงาน Forecast vs Actual นะจ๊ะ")
    st.caption(f"ข้อมูล ณ {datetime.now().strftime('%d %b %Y')} "
               "— RM ค้างสะสมคือยอดสะสมจริงจากทุกสัปดาห์ที่เคยอัปโหลด")

    df = compute_dashboard()
    if df.empty:
        st.info("ยังไม่มีข้อมูล ไปที่หน้า 'อัปโหลดข้อมูล' เพื่อเริ่มต้น")
    else:
        c1, c2, c3, c4 = st.columns(4)
        c1.markdown(f'<div class="kpi-card"><div class="kpi-label">Forecast (สัปดาห์ล่าสุด) นะจ๊ะ</div>'
                    f'<div class="kpi-value">{df.forecast_kg.sum():,.0f} kg</div></div>',
                    unsafe_allow_html=True)
        c2.markdown(f'<div class="kpi-card"><div class="kpi-label">Actual (สัปดาห์ล่าสุด) นะจ๊ะ</div>'
                    f'<div class="kpi-value">{df.actual_kg.sum():,.0f} kg</div></div>',
                    unsafe_allow_html=True)
        total_var = (df.actual_kg.sum() - df.forecast_kg.sum()) / df.forecast_kg.sum() * 100
        c3.markdown(f'<div class="kpi-card"><div class="kpi-label">ส่วนต่างล่าสุดนะจ๊ะ</div>'
                    f'<div class="kpi-value">{total_var:+.1f}%</div></div>',
                    unsafe_allow_html=True)
        c4.markdown(f'<div class="kpi-card"><div class="kpi-label">RM ค้างสะสมรวม (ทุกสัปดาห์) นะจ๊ะ</div>'
                    f'<div class="kpi-value">{df.backlog_kg.sum():,.0f} kg</div></div>',
                    unsafe_allow_html=True)

        st.write("")
        tab_all, tab_flagged = st.tabs(
            [f"ทั้งหมด ({len(df)})", f"ต้องติดตาม ({(df.status != 'ปกติ').sum()})"]
        )

        def render_table(view):
            header = st.columns([2, 1.6, 2, 1, 1, 1, 1.3, 1.3])
            for col, label in zip(header, ["สูตรนะจ๊ะ", "ลูกค้านะจ๊ะ", "ผู้รับผิดชอบนะจ๊ะ", "Forecast นะจ๊ะ",
                                            "Actual นะจ๊ะ", "ส่วนต่างนะจ๊ะ", "RM ค้างสะสมนะจ๊ะ", "สถานะนะจ๊ะ"]):
                col.caption(label)
            for _, r in view.iterrows():
                cls = {"ปกติ": "badge-ok", "เฝ้าระวัง": "badge-warn"}.get(r.status, "badge-bad")
                cols = st.columns([2, 1.6, 2, 1, 1, 1, 1.3, 1.3])
                cols[0].write(f"**{r.compound}**")
                cols[1].write(r.customer)
                cols[2].write(f"{r.sales} / {r.csr}")
                cols[3].write(f"{r.forecast_kg:,.0f}")
                cols[4].write(f"{r.actual_kg:,.0f}")
                cols[5].write(f"{r.variance_pct:+.1f}%")
                cols[6].write(f"{r.backlog_kg:,.0f} kg")
                cols[7].markdown(f'<span class="badge {cls}">{r.status}</span>',
                                  unsafe_allow_html=True)

        with tab_all:
            render_table(df)
        with tab_flagged:
            render_table(df[df.status != "ปกติ"])

        st.write("")
        export_df = df[["compound", "customer", "sales", "csr", "forecast_kg",
                         "actual_kg", "variance_pct", "backlog_kg", "status"]]
        export_path = EXPORTS_DIR / f"report_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"
        if st.button("ส่งออก Excel"):
            export_df.to_excel(export_path, index=False)
            st.success(f"บันทึกไฟล์แล้วที่ {export_path}")
            with open(export_path, "rb") as f:
                st.download_button("ดาวน์โหลดไฟล์ที่เพิ่งสร้าง", f,
                                    file_name=export_path.name)

    st.caption("รายงานนี้จะถูกส่งอีเมลให้ Sales และ CSR ผู้รับผิดชอบทุกวันศุกร์ "
               "(การส่งเมลจริงยังไม่ได้เชื่อมในเดโมนี้)")

# ---------------------------------------------------------------------------
# Page: Upload
# ---------------------------------------------------------------------------
elif page == "📤 อัปโหลดข้อมูล":
    st.title("อัปโหลด Forecast / Actual ประจำสัปดาห์นะจ๊ะ")
    st.caption("ไฟล์ต้นฉบับจะถูกเก็บสำเนาไว้ที่ data/uploads/ และข้อมูลจะถูกบันทึกถาวรลง "
               "data/forecast_tracker.db ทันทีที่กดยืนยัน")

    if st.session_state.get("just_confirmed"):
        st.success("บันทึกข้อมูลแล้ว — ไปที่ Dashboard เพื่อดู RM ค้างสะสมที่อัปเดต")
        st.session_state.just_confirmed = False

    uploaded = st.file_uploader("เลือกไฟล์ Excel (.xlsx)", type=["xlsx"])

    if uploaded is not None:
        raw = pd.read_excel(uploaded)
        st.subheader("1. คอลัมน์ที่พบในไฟล์ → จับคู่กับฟิลด์ระบบนะจ๊ะ")

        mapping_rows = []
        for col in raw.columns:
            field, score = best_match(str(col))
            mapping_rows.append(
                {
                    "คอลัมน์ในไฟล์": col,
                    "ตัวอย่างข้อมูล": str(raw[col].iloc[0]) if len(raw) else "",
                    "field_key": field,
                    "จับคู่กับฟิลด์ระบบ": FIELD_LABELS.get(field, "(ไม่พบที่ตรงกัน)"),
                    "ความมั่นใจ": "มั่นใจ" if score >= 0.6 else "ตรวจสอบ",
                }
            )
        mapping_df = pd.DataFrame(mapping_rows)

        def highlight(row):
            color = "" if row["ความมั่นใจ"] == "มั่นใจ" else "background-color: #FFFBF2"
            return [color] * len(row)

        st.dataframe(
            mapping_df.drop(columns=["field_key"]).style.apply(highlight, axis=1),
            width='stretch', hide_index=True,
        )

        low_conf = mapping_df[mapping_df["ความมั่นใจ"] == "ตรวจสอบ"]
        if len(low_conf):
            st.warning("มีคอลัมน์ที่ระบบไม่มั่นใจ โปรดตรวจสอบก่อนบันทึก: "
                       + ", ".join(low_conf["คอลัมน์ในไฟล์"]))

        st.subheader("2. ตัวอย่างข้อมูลนะจ๊ะ")
        st.dataframe(raw.head(), width='stretch', hide_index=True)

        required = {"compound", "customer", "forecast_kg", "actual_kg"}
        mapped_keys = set(mapping_df["field_key"].dropna())
        missing = required - mapped_keys

        if missing:
            st.error("ไฟล์นี้ขาดคอลัมน์ที่จำเป็น: "
                     + ", ".join(FIELD_LABELS[m] for m in missing)
                     + " — ไม่สามารถบันทึกได้จนกว่าจะมีครบ")
        elif st.button("ยืนยันและบันทึกข้อมูล", type="primary"):
            rename_map = {
                row["คอลัมน์ในไฟล์"]: row["field_key"]
                for row in mapping_rows if row["field_key"]
            }
            clean = raw.rename(columns=rename_map)
            if "week" not in clean.columns:
                clean["week"] = datetime.now().strftime("W%U")
            clean = clean[["compound", "customer", "week", "forecast_kg", "actual_kg"]]

            # keep a timestamped copy of the original file for audit
            saved_name = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uploaded.name}"
            with open(UPLOADS_DIR / saved_name, "wb") as f:
                f.write(uploaded.getbuffer())

            insert_weekly_rows(clean, source_file=saved_name)
            st.session_state.just_confirmed = True
            st.rerun()
    else:
        st.info("ยังไม่ได้เลือกไฟล์ — ลองใช้ไฟล์ตัวอย่างใน sample_data/ ที่แนบมาด้วยก็ได้")

# ---------------------------------------------------------------------------
# Page: Settings
# ---------------------------------------------------------------------------
else:
    st.title("ผู้รับผิดชอบ และเกณฑ์แจ้งเตือนนะจ๊ะ")
    st.caption("แก้ไขแล้วบันทึกด้านล่าง — มีผลกับ Dashboard ทันที และถูกเก็บถาวรใน data/forecast_tracker.db")

    rules = load_rules()
    edited = st.data_editor(
        rules,
        column_config={
            "compound": "สูตรนะจ๊ะ",
            "customer": "ลูกค้านะจ๊ะ",
            "sales": "Sales",
            "csr": "CSR",
            "threshold_kg": st.column_config.NumberColumn("เกณฑ์ (กก.) นะจ๊ะ"),
            "threshold_pct": st.column_config.NumberColumn("เกณฑ์ (%) นะจ๊ะ"),
            "mode": st.column_config.SelectboxColumn(
                "ใช้เกณฑ์ไหนตัดสิน",
                options=["ถึงเกณฑ์ไหนก่อน แจ้งก่อน", "ใช้ กก. เป็นหลัก", "ใช้ % เป็นหลัก"],
            ),
            "stale_days": st.column_config.NumberColumn("ไม่มีข้อมูลเกิน (วัน) นะจ๊ะ"),
            "email": "อีเมล",
        },
        hide_index=True,
        width='stretch',
        num_rows="dynamic",
    )

    if st.button("บันทึกการตั้งค่า", type="primary"):
        save_rules(edited)
        st.success("บันทึกแล้ว")

    st.write("")
    st.subheader("กำหนดการส่งรายงานนะจ๊ะ")
    col1, col2, col3 = st.columns(3)
    col1.selectbox("วันที่ส่ง", ["ทุกวันศุกร์"])
    col2.text_input("เวลาที่ส่ง", "17:00 น.")
    col3.text_input("ส่งจากอีเมล", "forecast-report@chemicalinnovation.co.th")

    st.write("")
    st.subheader("ประวัติการอัปโหลดนะจ๊ะ")
    weekly = load_weekly()
    if weekly.empty:
        st.caption("ยังไม่มีการอัปโหลด")
    else:
        st.dataframe(
            weekly[["uploaded_at", "source_file", "compound", "customer", "week",
                    "forecast_kg", "actual_kg"]].sort_values("uploaded_at", ascending=False),
            width='stretch', hide_index=True,
        )
