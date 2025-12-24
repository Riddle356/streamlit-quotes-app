# app.py
from metals_section import metals_section
import streamlit as st
import requests
import pandas as pd
import datetime as dt
from dateutil.relativedelta import relativedelta
import plotly.express as px
import io
import json
from typing import Dict, List

BASE = "https://api.nbrb.by/exrates"

st.set_page_config(page_title="BYN Exchange Dashboard", layout="wide")

# ----- Helper functions -----
@st.cache_data(show_spinner=False)
def fetch_currency_list() -> pd.DataFrame:
    """Fetch list of currencies from NBRB and return dataframe with Cur_ID, Cur_Abbreviation, Cur_Scale, Cur_Name."""
    url = f"{BASE}/currencies"
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    df = pd.DataFrame(data)
    # Keep essential columns
    df = df[["Cur_ID", "Cur_Abbreviation", "Cur_Scale", "Cur_Name", "Cur_DateStart", "Cur_DateEnd"]]
    return df

@st.cache_data(show_spinner=False)
def fetch_today_rates() -> pd.DataFrame:
    """Get today's daily rates (periodicity=0). Returns DataFrame of rates (includes Cur_Scale)."""
    url = f"{BASE}/rates"
    params = {"periodicity": 0}
    resp = requests.get(url, params=params, timeout=10)
    resp.raise_for_status()
    rates = resp.json()
    df = pd.DataFrame(rates)
    # Convert date string to datetime
    if "Date" in df.columns:
        df["Date"] = pd.to_datetime(df["Date"]).dt.date
    return df

def fetch_rate_on_date(cur_identifier: str, parammode: int, ondate: dt.date) -> dict:
    """Get a single rate for currency on specific date. Can use when needed; not used in bulk logic usually."""
    url = f"{BASE}/rates/{cur_identifier}"
    params = {"parammode": parammode, "ondate": ondate.strftime("%Y-%m-%d")}
    resp = requests.get(url, params=params, timeout=10)
    if resp.status_code == 404:
        return {}
    resp.raise_for_status()
    return resp.json()

def chunked_dynamics_fetch(cur_id: int, start: dt.date, end: dt.date) -> pd.DataFrame:
    """
    Fetch dynamics for a currency across arbitrary interval by splitting into <=365-day chunks.
    Returns DataFrame with columns ['Date', 'Cur_OfficialRate'].
    """
    if end < start:
        return pd.DataFrame(columns=["Date", "Cur_OfficialRate"])
    max_days = 365
    dfs = []
    s = start
    while s <= end:
        chunk_end = min(s + dt.timedelta(days=max_days-1), end)
        url = f"{BASE}/rates/dynamics/{cur_id}"
        params = {"startdate": s.strftime("%Y-%m-%d"), "enddate": chunk_end.strftime("%Y-%m-%d")}
        resp = requests.get(url, params=params, timeout=15)
        if resp.status_code == 404:
            # no data for this chunk
            s = chunk_end + dt.timedelta(days=1)
            continue
        resp.raise_for_status()
        chunk = resp.json()
        if chunk:
            df_chunk = pd.DataFrame(chunk)
            df_chunk["Date"] = pd.to_datetime(df_chunk["Date"]).dt.date
            dfs.append(df_chunk[["Date", "Cur_OfficialRate"]])
        s = chunk_end + dt.timedelta(days=1)
    if dfs:
        df_all = pd.concat(dfs).drop_duplicates(subset="Date").sort_values("Date").reset_index(drop=True)
    else:
        df_all = pd.DataFrame(columns=["Date", "Cur_OfficialRate"])
    return df_all

def rates_for_currencies(codes: List[str], currency_map: pd.DataFrame, start: dt.date, end: dt.date) -> pd.DataFrame:
    """
    Get timeseries rates for given currency ISO codes between start and end (inclusive).
    Returns DataFrame with Date and columns for each currency (normalized to 1 unit).
    """
    series = []
    for code in codes:
        # Фильтруем все записи по коду валюты
        rows = currency_map[currency_map["Cur_Abbreviation"] == code].copy()
        if rows.empty:
            st.warning(f"Currency {code} not found in NBRB list.")
            continue
        # Преобразуем даты в datetime
        rows["Cur_DateStart"] = pd.to_datetime(rows["Cur_DateStart"]).dt.date
        rows["Cur_DateEnd"] = pd.to_datetime(rows["Cur_DateEnd"]).dt.date
        # Ищем Cur_ID, у которого диапазон дат включает весь нужный период
        valid = rows[(rows["Cur_DateStart"] <= start) & (rows["Cur_DateEnd"] >= end)]
        if valid.empty:
            # Если нет Cur_ID, покрывающего весь период, ищем тот, что покрывает хотя бы начало периода
            valid = rows[(rows["Cur_DateStart"] <= start) & (rows["Cur_DateEnd"] >= start)]
        if valid.empty:
            st.info(f"Нет подходящего Cur_ID для {code} на период {start} — {end}")
            continue
        cur_id = int(valid.iloc[0]["Cur_ID"])
        # scale = int(valid.iloc[0]["Cur_Scale"])
        # Ограничиваем фактический диапазон дат Cur_ID
        cur_start = max(start, valid.iloc[0]["Cur_DateStart"])
        cur_end = min(end, valid.iloc[0]["Cur_DateEnd"])
        df_dyn = chunked_dynamics_fetch(cur_id, cur_start, cur_end)
        if df_dyn.empty:
            st.info(f"Нет данных dynamics для {code} в период {cur_start} — {cur_end}")
            continue
        # Используем Cur_OfficialRate (курс за Cur_Scale единиц)
        df_dyn = df_dyn[["Date", "Cur_OfficialRate"]].rename(columns={"Cur_OfficialRate": code})
        series.append(df_dyn.set_index("Date"))
    if not series:
        return pd.DataFrame()
    df_all = pd.concat(series, axis=1).sort_index()
    return df_all.reset_index().rename(columns={"index":"Date"})

# ----- UI -----
st.title("Дашборд курсов к BYN (на основе API НБРБ)")

# Load currency list and today's rates
with st.spinner("Загружаю справочник валют и текущие курсы..."):
    try:
        currency_df = fetch_currency_list()
        today_rates_df = fetch_today_rates()
    except Exception as e:
        st.error(f"Ошибка при получении данных с API НБРБ: {e}")
        st.stop()

# Default currencies of interest
DEFAULT_CODES = ["RUB", "CNY", "USD", "EUR", "JPY"]

# Sidebar controls
st.sidebar.header("Настройки")
codes_available = sorted(currency_df["Cur_Abbreviation"].unique())
selected_codes = st.sidebar.multiselect("Валюты для отображения:", options=codes_available, default=DEFAULT_CODES)
if not selected_codes:
    st.sidebar.warning("Выберите хотя бы одну валюту.")
    st.stop()

# Date range controls for chart / downloads
today = dt.date.today()
default_start_5y = today - relativedelta(years=5)
st.sidebar.markdown("**Интервал для графика и скачивания**")
start_date = st.sidebar.date_input("Дата начала", value=default_start_5y, max_value=today)
end_date = st.sidebar.date_input("Дата конца", value=today, max_value=today)
if start_date > end_date:
    st.sidebar.error("Дата начала должна быть раньше или равна дате конца.")
    st.stop()

# Quick buttons: last 5 years
if st.sidebar.button("Поставить интервал: последние 5 лет"):
    start_date = default_start_5y
    end_date = today

# --- Table: latest rates + 7-day change ---
st.subheader("Актуальные курсы (последний день) и изменение за 7 дней")

# Filter today's rates for selected currencies
today_filtered = today_rates_df[today_rates_df["Cur_Abbreviation"].isin(selected_codes)].copy()


# Используем Cur_OfficialRate (курс за Cur_Scale единиц)
today_filtered = today_filtered[["Cur_Abbreviation", "Cur_Name", "Cur_Scale", "Date", "Cur_OfficialRate"]]
today_filtered = today_filtered.rename(columns={
    "Cur_Abbreviation":"Code",
    "Cur_Name":"Name",
    "Cur_Scale":"Scale",
    "Date":"Date",
    "Cur_OfficialRate":"Rate_BYN_per_scale"
})

# Get 7 days earlier date (ondate param). Because NBRB may not set rates on weekends, we'll attempt date -7
seven_days_ago = today - dt.timedelta(days=7)


# Для сравнения за 7 дней используем Cur_OfficialRate (курс за Cur_Scale единиц)
weekly_change = []
try:
    params = {"ondate": seven_days_ago.strftime("%Y-%m-%d"), "periodicity": 0}
    resp = requests.get(f"{BASE}/rates", params=params, timeout=10)
    resp.raise_for_status()
    rates_7 = pd.DataFrame(resp.json())
    rates_7 = rates_7[["Cur_Abbreviation", "Cur_OfficialRate"]].rename(columns={"Cur_Abbreviation":"Code", "Cur_OfficialRate":"Rate_7d_ago"})
except Exception:
    rates_7 = pd.DataFrame(columns=["Code", "Rate_7d_ago"])


table_df = today_filtered.merge(rates_7, on="Code", how="left")
table_df["Rate_7d_ago"] = table_df["Rate_7d_ago"].astype(float)
table_df["Change_abs"] = table_df["Rate_BYN_per_scale"] - table_df["Rate_7d_ago"]
# Percentage change (if available)
table_df["Change_pct"] = (table_df["Change_abs"] / table_df["Rate_7d_ago"]) * 100
table_df = table_df.sort_values("Code").reset_index(drop=True)


# Format for display
display_df = table_df[["Code", "Name", "Scale", "Date", "Rate_BYN_per_scale", "Rate_7d_ago", "Change_abs", "Change_pct"]].copy()
display_df["Rate_BYN_per_scale"] = display_df["Rate_BYN_per_scale"].round(6)
display_df["Rate_7d_ago"] = display_df["Rate_7d_ago"].round(6)
display_df["Change_abs"] = display_df["Change_abs"].round(6)
display_df["Change_pct"] = display_df["Change_pct"].map(lambda x: f"{x:.2f}%" if pd.notna(x) else "—")

# Styling: highlight positive green, negative red for Change_abs
def highlight_change(val):
    try:
        v = float(val)
    except Exception:
        return ''
    color = "color: green;" if v > 0 else ("color: red;" if v < 0 else "")
    return color

styled = display_df.style.applymap(lambda v: 'color: green;' if isinstance(v, (int,float)) and v>0 and v==v and False else '')
# We can't apply conditional style easily for column with formatted pct strings; use pandas styler on numeric column
styled = display_df.style.format({
    "Rate_BYN_per_1_unit":"{:.6f}",
    "Rate_7d_ago":"{:.6f}",
    "Change_abs":"{:.6f}",
}).applymap(lambda v: 'color: green;' if isinstance(v,(int,float)) and v>0 else ('color: red;' if isinstance(v,(int,float)) and v<0 else ''), subset=["Change_abs"])

st.write(styled, unsafe_allow_html=True)

# --- Chart: timeseries for selected currencies ---
st.subheader("График котировок")

with st.spinner("Загружаю исторические данные... (может занять некоторое время для 5 лет и нескольких валют)"):
    try:
        df_rates_ts = rates_for_currencies(selected_codes, currency_df, start_date, end_date)
    except Exception as e:
        st.error(f"Ошибка при загрузке динамики: {e}")
        df_rates_ts = pd.DataFrame()

if df_rates_ts.empty:
    st.info("Нет доступных исторических данных за выбранный интервал для выбранных валют.")
else:

    # Melt for plotly
    df_melt = df_rates_ts.melt(id_vars=["Date"], value_vars=[c for c in selected_codes if c in df_rates_ts.columns],
                               var_name="Currency", value_name="BYN_per_scale")
    # Plot
    fig = px.line(
        df_melt,
        x="Date",
        y="BYN_per_scale",
        color="Currency",
        title=f"Курсы к BYN (за Cur_Scale единиц): {', '.join(selected_codes)}"
    )
    fig.update_layout(hovermode="x unified")
    st.plotly_chart(fig, use_container_width=True)

    # Allow interactive exclusion via multiselect (duplicate control in main area)
    cols1, cols2 = st.columns([2,1])
    with cols1:
        excluded = st.multiselect("Исключить валюты с графика (если хотите):", options=selected_codes, default=[])
    with cols2:
        st.write("Параметры вывода")
        st.write(f"Данные с {start_date} по {end_date} ({(end_date - start_date).days+1} дней)")

    # Re-plot with exclusions if any
    shown = [c for c in selected_codes if c not in excluded]
    if not shown:
        st.warning("Все валюты исключены — нет данных для графика.")
    else:
        df_melt2 = df_rates_ts.melt(id_vars=["Date"], value_vars=shown,
                                    var_name="Currency", value_name="BYN_per_scale")
        fig2 = px.line(
            df_melt2,
            x="Date",
            y="BYN_per_scale",
            color="Currency",
            title=f"Курсы (с исключениями, за Cur_Scale единиц): {', '.join(shown)}"
        )
        st.plotly_chart(fig2, use_container_width=True)

# --- Downloads ---
st.subheader("Скачать данные")

# Prepare full-last-5-years dataset
start_5y = today - relativedelta(years=5)
with st.spinner("Готовлю данные для скачивания..."):
    df_5y = rates_for_currencies(selected_codes, currency_df, start_5y, today)

def df_to_csv_bytes(df: pd.DataFrame) -> bytes:
    buf = io.StringIO()
    df.to_csv(buf, index=False)
    return buf.getvalue().encode("utf-8")

def df_to_json_bytes(df: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    # Convert dates to ISO strings
    out = df.copy()
    if "Date" in out.columns:
        out["Date"] = out["Date"].astype(str)
    buf.write(json.dumps(out.to_dict(orient="records"), ensure_ascii=False, indent=2).encode("utf-8"))
    buf.seek(0)
    return buf.read()

colA, colB = st.columns(2)
with colA:
    st.markdown("**Последние 5 лет (JSON / CSV)**")
    if df_5y.empty:
        st.info("Нет данных для скачивания (5 лет).")
    else:
        st.download_button("Скачать CSV (5 лет)", data=df_to_csv_bytes(df_5y), file_name="rates_5y.csv", mime="text/csv")
        st.download_button("Скачать JSON (5 лет)", data=df_to_json_bytes(df_5y), file_name="rates_5y.json", mime="application/json")

with colB:
    st.markdown("**Выбранный интервал (JSON / CSV)**")
    df_interval = rates_for_currencies(selected_codes, currency_df, start_date, end_date)
    if df_interval.empty:
        st.info("Нет данных для выбранного интервала.")
    else:
        st.download_button("Скачать CSV (интервал)", data=df_to_csv_bytes(df_interval), file_name="rates_interval.csv", mime="text/csv")
        st.download_button("Скачать JSON (интервал)", data=df_to_json_bytes(df_interval), file_name="rates_interval.json", mime="application/json")

# Footer / notes
st.markdown("---")
st.markdown("""
**Замечания и ограничения**
- Источником является API Национального банка Республики Беларусь: `https://api.nbrb.by`.  
- Метод `rates/dynamics/{cur_id}` возвращает максимум 365 дней за один вызов — приложение автоматически разбивает запрос на чанки не более 365 дней.  
- `Cur_OfficialRate` в API — курс BYN за `Cur_Scale` единиц иностранной валюты. В графиках и скачиваемых файлах значения нормализованы до **1 единицы иностранной валюты** (например, BYN за 1 RUB, 1 USD и т.д.).  
- На выходные/праздники курсы могут отсутствовать — в таких датах данных не будет (нет форсированного интерполяционного заполнения).
- Если хотите, могу добавить: автоматическое ежедневное обновление (cron), кэширование на диск, экспорт в Excel, или сравнение с онлайн-биржами.
""")

# Settings for metals import
metals_section()

