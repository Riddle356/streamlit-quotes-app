import streamlit as st
import pandas as pd
import requests
from datetime import datetime, timedelta
import io
import json
import matplotlib.pyplot as plt

METALS_URL = "https://api.nbrb.by/metals"
METALS_PRICES_URL = "https://api.nbrb.by/bankingots/prices/{metal_id}?startdate={start}&enddate={end}"

def fetch_metals():
    """Получает перечень драгоценных металлов"""
    resp = requests.get(METALS_URL)
    resp.raise_for_status()
    return resp.json()

def fetch_metal_prices(metal_id: int, start_date: str, end_date: str):
    """Получает цены на выбранный металл за период"""
    url = METALS_PRICES_URL.format(metal_id=metal_id, start=start_date, end=end_date)
    resp = requests.get(url)
    resp.raise_for_status()
    return resp.json()

def metals_section():
    """Интерактивный блок Streamlit для отображения курсов драгоценных металлов"""
    st.header("💎 Курсы драгоценных металлов (по данным НБ РБ)")

    # === Получаем список металлов ===
    metals = fetch_metals()
    metals_dict = {m["Name"]: m["Id"] for m in metals}

    # === Выбор металла и периода ===
    selected_metal = st.selectbox("Выберите металл:", list(metals_dict.keys()))
    metal_id = metals_dict[selected_metal]

    today = datetime.today()
    start_default = today - timedelta(days=365)
    start_date = st.date_input("Дата начала:", start_default)
    end_date = st.date_input("Дата окончания:", today)

    if start_date > end_date:
        st.error("Дата начала должна быть раньше даты окончания.")
        return

    # === Загрузка данных ===
    st.info("Загрузка данных с API НБ РБ...")
    data = fetch_metal_prices(metal_id, start_date.strftime("%Y-%m-%d"), end_date.strftime("%Y-%m-%d"))

    if not data:
        st.warning("Данные за указанный период отсутствуют.")
        return

    # === Преобразование в DataFrame ===
    df = pd.DataFrame(data)
    df["Date"] = pd.to_datetime(df["Date"])
    df = df.sort_values("Date")

    # === Отображение таблицы ===
    st.subheader("📋 Таблица цен")
    st.dataframe(df.rename(columns={"Value": "Цена (BYN за грамм)"}), use_container_width=True)

    # === График ===
    st.subheader("📈 График динамики цен")
    fig, ax = plt.subplots()
    ax.plot(df["Date"], df["Value"], label=selected_metal, linewidth=2)
    ax.set_xlabel("Дата")
    ax.set_ylabel("Цена, BYN за грамм")
    ax.legend()
    st.pyplot(fig)

    # === Выгрузка данных ===
    st.subheader("⬇️ Выгрузить данные")
    csv_buffer = io.StringIO()
    df.to_csv(csv_buffer, index=False)
    json_data = df.to_json(orient="records", force_ascii=False, indent=2)

    col1, col2 = st.columns(2)
    with col1:
        st.download_button(
            label="Скачать CSV",
            data=csv_buffer.getvalue(),
            file_name=f"metal_{selected_metal}_{start_date}_{end_date}.csv",
            mime="text/csv"
        )
    with col2:
        st.download_button(
            label="Скачать JSON",
            data=json_data,
            file_name=f"metal_{selected_metal}_{start_date}_{end_date}.json",
            mime="application/json"
        )

# Пример для локального теста
if __name__ == "__main__":
    import streamlit.web.cli as stcli
    import sys
    sys.argv = ["streamlit", "run", __file__]
    sys.exit(stcli.main())
