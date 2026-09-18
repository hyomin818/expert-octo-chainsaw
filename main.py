# main.py

import io
import json

import numpy as np
import pandas as pd
import plotly.express as px
import requests
import streamlit as st


# ---------------------------------------------------------
# 1. 페이지 기본 설정
# ---------------------------------------------------------
st.set_page_config(
    page_title="전국 시군구 고령화 지도",
    page_icon="🗺️",
    layout="wide",
)

st.title("🗺️ 전국 시군구 고령화 지도")
st.write(
    "전국 시군구별 **65세 이상 인구 비율**을 나타낸 지도입니다. "
    "데이터에 포함된 가장 최신 연도를 자동으로 사용합니다."
)


# ---------------------------------------------------------
# 2. 데이터 주소
# ---------------------------------------------------------
POPULATION_URL = (
    "https://raw.githubusercontent.com/greatsong/modudata/"
    "main/data/population_yearly.csv.gz"
)

GEOJSON_URL = (
    "https://raw.githubusercontent.com/greatsong/modudata/"
    "main/data/boundaries/sigungu_kr.geojson"
)


# ---------------------------------------------------------
# 3. 인구 데이터 불러오기
#    Streamlit이 새로 실행될 때마다 다운로드하지 않도록 캐시합니다.
# ---------------------------------------------------------
@st.cache_data(show_spinner=False)
def load_population():
    response = requests.get(POPULATION_URL, timeout=60)
    response.raise_for_status()

    # gzip 압축 CSV를 메모리에서 바로 읽습니다.
    # '코드'는 계산할 숫자가 아니므로 반드시 문자열로 읽습니다.
    df = pd.read_csv(
        io.BytesIO(response.content),
        compression="gzip",
        dtype={"코드": "string"},
    )

    return df


# ---------------------------------------------------------
# 4. 시군구 경계 GeoJSON 불러오기
# ---------------------------------------------------------
@st.cache_data(show_spinner=False)
def load_geojson():
    response = requests.get(GEOJSON_URL, timeout=60)
    response.raise_for_status()

    geojson = response.json()

    # GeoJSON의 코드도 문자열 5자리로 맞춥니다.
    for feature in geojson["features"]:
        code = feature["properties"]["코드"]
        feature["properties"]["코드"] = str(code).zfill(5)

    return geojson


# ---------------------------------------------------------
# 5. 나이 열에서 실제 나이를 알아내는 함수
#
# 예:
# '계_0세' -> 0
# '계_65세' -> 65
# '계_100세 이상' -> 100
# ---------------------------------------------------------
def get_age_from_column(column_name):
    if not column_name.startswith("계_"):
        return None

    text = column_name.replace("계_", "")

    # '100세 이상'도 숫자 100으로 처리합니다.
    text = text.replace("세 이상", "")
    text = text.replace("세", "")

    try:
        return int(text)
    except ValueError:
        return None


# ---------------------------------------------------------
# 6. 고령화율 계산
# ---------------------------------------------------------
@st.cache_data(show_spinner=False)
def calculate_aging_rate(df):
    data = df.copy()

    # 연도 값이 문자열이어도 비교할 수 있도록 숫자로 변환합니다.
    data["연도"] = pd.to_numeric(data["연도"], errors="coerce")

    # 가장 최신 연도를 자동으로 찾습니다.
    latest_year = int(data["연도"].max())

    # 최신 연도 자료만 남깁니다.
    data = data[data["연도"] == latest_year].copy()

    # 행정동 코드가 없는 행은 제외합니다.
    data = data[data["코드"].notna()].copy()

    # 혹시 코드 주변에 공백이 있을 경우 제거합니다.
    data["코드"] = data["코드"].astype("string").str.strip()

    # 행정동 코드 앞 5자리가 시군구 코드입니다.
    data["시군구코드"] = data["코드"].str[:5]

    # -----------------------------------------------------
    # 전체 인구 계산에 사용할 나이 열 찾기
    # -----------------------------------------------------
    age_columns = []

    # 65세 이상 인구 계산에 사용할 열
    elderly_columns = []

    for column in data.columns:
        age = get_age_from_column(column)

        if age is not None:
            age_columns.append(column)

            if age >= 65:
                elderly_columns.append(column)

    if len(age_columns) == 0:
        raise ValueError("'계_0세'와 같은 나이별 인구 열을 찾지 못했습니다.")

    if len(elderly_columns) == 0:
        raise ValueError("65세 이상 인구 열을 찾지 못했습니다.")

    # 인구 열을 숫자로 변환합니다.
    # 숫자가 아닌 값은 0으로 처리합니다.
    for column in age_columns:
        data[column] = pd.to_numeric(
            data[column].astype(str).str.replace(",", ""),
            errors="coerce",
        ).fillna(0)

    # 읍·면·동별 전체 인구
    data["전체인구"] = data[age_columns].sum(axis=1)

    # 읍·면·동별 65세 이상 인구
    data["65세이상인구"] = data[elderly_columns].sum(axis=1)

    # -----------------------------------------------------
    # 읍·면·동 자료를 시군구 단위로 합칩니다.
    # 같은 앞 5자리 코드를 가진 지역끼리 합산합니다.
    # -----------------------------------------------------
    sigungu = (
        data.groupby("시군구코드", as_index=False)
        .agg(
            전체인구=("전체인구", "sum"),
            65세이상인구=("65세이상인구", "sum"),
        )
    )

    # 0으로 나누는 일을 막기 위해 전체 인구 0인 지역은 제외합니다.
    sigungu = sigungu[sigungu["전체인구"] > 0].copy()

    # 고령화율 계산
    sigungu["고령화율"] = (
        sigungu["65세이상인구"]
        / sigungu["전체인구"]
        * 100
    )

    return latest_year, sigungu


# ---------------------------------------------------------
# 7. GeoJSON에서 시도·시군구 이름을 표 형태로 가져옵니다.
# ---------------------------------------------------------
def make_region_table(geojson):
    regions = []

    for feature in geojson["features"]:
        properties = feature["properties"]

        regions.append(
            {
                "시군구코드": str(properties["코드"]).zfill(5),
                "시도": properties["시도"],
                "시군구": properties["시군구"],
            }
        )

    return pd.DataFrame(regions)


# ---------------------------------------------------------
# 8. 데이터 불러오기
# ---------------------------------------------------------
try:
    with st.spinner("전국 인구 데이터를 불러오고 있습니다..."):
        population = load_population()
        geojson = load_geojson()

    latest_year, aging = calculate_aging_rate(population)

except Exception as e:
    st.error("데이터를 불러오거나 처리하는 과정에서 문제가 발생했습니다.")
    st.exception(e)
    st.stop()


# ---------------------------------------------------------
# 9. 시군구 이름과 고령화율 자료 합치기
#
# 이름이 아니라 반드시 '코드'를 기준으로 연결합니다.
# ---------------------------------------------------------
region_table = make_region_table(geojson)

result = region_table.merge(
    aging,
    on="시군구코드",
    how="left",
)


# ---------------------------------------------------------
# 10. 고령화율을 5단계로 나누기
#
# 기준:
# 19% 미만
# 19% 이상 ~ 23% 미만
# 23% 이상 ~ 28% 미만
# 28% 이상 ~ 38% 미만
# 38% 이상
# ---------------------------------------------------------
labels = [
    "19% 미만",
    "19% 이상 ~ 23% 미만",
    "23% 이상 ~ 28% 미만",
    "28% 이상 ~ 38% 미만",
    "38% 이상",
]

result["고령화단계"] = pd.cut(
    result["고령화율"],
    bins=[-np.inf, 19, 23, 28, 38, np.inf],
    labels=labels,
    right=False,
)


# ---------------------------------------------------------
# 11. 단계별 색
#     낮은 고령화율은 옅게,
#     높은 고령화율은 진하게 표시합니다.
# ---------------------------------------------------------
color_map = {
    "19% 미만": "#fff5eb",
    "19% 이상 ~ 23% 미만": "#fdd0a2",
    "23% 이상 ~ 28% 미만": "#fdae6b",
    "28% 이상 ~ 38% 미만": "#e6550d",
    "38% 이상": "#a63603",
}


# ---------------------------------------------------------
# 12. 화면 위쪽 간단한 정보
# ---------------------------------------------------------
st.subheader(f"📅 {latest_year}년 기준")

col1, col2, col3 = st.columns(3)

with col1:
    st.metric(
        "지도 경계 시군구",
        f"{len(region_table):,}개",
    )

with col2:
    st.metric(
        "인구 자료가 연결된 시군구",
        f"{result['고령화율'].notna().sum():,}개",
    )

with col3:
    national_rate = (
        result["65세이상인구"].sum()
        / result["전체인구"].sum()
        * 100
    )

    st.metric(
        "전국 고령화율",
        f"{national_rate:.1f}%",
    )


# ---------------------------------------------------------
# 13. 단계구분도 만들기
#
# Plotly의 geo 방식이라 일반적인 배경 지도 타일을 사용하지 않습니다.
# 시군구 경계만 중심으로 보여 줍니다.
# ---------------------------------------------------------
map_data = result[result["고령화율"].notna()].copy()

fig = px.choropleth(
    map_data,

    # 지도 경계 데이터
    geojson=geojson,

    # 인구 자료의 시군구 코드
    locations="시군구코드",

    # GeoJSON 안에서 코드가 들어 있는 위치
    featureidkey="properties.코드",

    # 연속 숫자가 아니라 5단계 범주를 색칠합니다.
    color="고령화단계",

    # 단계 순서 고정
    category_orders={
        "고령화단계": labels
    },

    # 단계별 색상
    color_discrete_map=color_map,

    # 마우스를 올렸을 때 보여 줄 정보
    hover_name="시군구",
    hover_data={
        "시도": True,
        "시군구코드": False,
        "고령화단계": False,
        "고령화율": ":.1f",
        "전체인구": False,
        "65세이상인구": False,
    },

    labels={
        "고령화단계": "고령화율 구간",
        "고령화율": "고령화율(%)",
        "시도": "시도",
    },
)


# ---------------------------------------------------------
# 14. 지도 모양 조정
# ---------------------------------------------------------
fig.update_traces(
    # 시군구 경계선
    marker_line_color="#666666",
    marker_line_width=0.5,
)


fig.update_geos(
    # 실제 데이터가 있는 지역에 맞춰 지도 크기를 자동 조절
    fitbounds="locations",

    # 위도·경도 축이나 세계지도 배경을 숨깁니다.
    visible=False,

    # 배경을 투명하게
    bgcolor="rgba(0,0,0,0)",
)


fig.update_layout(
    height=750,

    margin=dict(
        l=0,
        r=0,
        t=10,
        b=0,
    ),

    # 범례 설정
    legend=dict(
        title="65세 이상 인구 비율",
        orientation="v",
        yanchor="top",
        y=0.98,
        xanchor="left",
        x=0.01,
        bgcolor="rgba(255,255,255,0.85)",
    ),

    paper_bgcolor="rgba(0,0,0,0)",
)


# ---------------------------------------------------------
# 15. 지도 출력
# ---------------------------------------------------------
st.plotly_chart(
    fig,
    use_container_width=True,
)


st.caption(
    "고령화율 = 시군구의 65세 이상 인구 ÷ 시군구 전체 인구 × 100"
)


# ---------------------------------------------------------
# 16. 높은 지역 / 낮은 지역 각각 10개
# ---------------------------------------------------------
st.divider()
st.subheader("📊 시군구별 고령화율 순위")


ranking_data = result[
    result["고령화율"].notna()
].copy()


# 높은 순서 10개
high_10 = (
    ranking_data
    .sort_values(
        "고령화율",
        ascending=False,
    )
    .head(10)
    .copy()
)


# 낮은 순서 10개
low_10 = (
    ranking_data
    .sort_values(
        "고령화율",
        ascending=True,
    )
    .head(10)
    .copy()
)


# 화면에 표시하기 좋은 형태로 정리
def make_ranking_table(df):
    table = df[
        [
            "시도",
            "시군구",
            "고령화율",
        ]
    ].copy()

    table["고령화율"] = table["고령화율"].round(1)

    table = table.rename(
        columns={
            "고령화율": "고령화율(%)"
        }
    )

    # 순위를 1부터 표시
    table.index = range(1, len(table) + 1)
    table.index.name = "순위"

    return table


high_table = make_ranking_table(high_10)
low_table = make_ranking_table(low_10)


# ---------------------------------------------------------
# 17. 두 표를 나란히 표시
# ---------------------------------------------------------
left, right = st.columns(2)


with left:
    st.markdown("### 🔴 고령화율 높은 지역 10개")

    st.dataframe(
        high_table,
        use_container_width=True,
        column_config={
            "고령화율(%)": st.column_config.NumberColumn(
                "고령화율(%)",
                format="%.1f%%",
            )
        },
    )


with right:
    st.markdown("### 🟢 고령화율 낮은 지역 10개")

    st.dataframe(
        low_table,
        use_container_width=True,
        column_config={
            "고령화율(%)": st.column_config.NumberColumn(
                "고령화율(%)",
                format="%.1f%%",
            )
        },
    )


# ---------------------------------------------------------
# 18. 자료 설명
# ---------------------------------------------------------
with st.expander("ℹ️ 데이터 처리 방법 보기"):
    st.write(
        f"""
        - 인구 데이터에 포함된 연도 중 가장 최신인 **{latest_year}년** 자료를 사용했습니다.
        - 인구 데이터의 `코드`는 숫자로 계산하지 않고 문자열로 읽었습니다.
        - 10자리 행정동 코드의 **앞 5자리**를 시군구 코드로 사용했습니다.
        - 같은 시군구 코드를 가진 읍·면·동의 인구를 합산했습니다.
        - `계_65세`부터 `계_100세 이상`까지 합산하여 65세 이상 인구를 구했습니다.
        - `계_0세`부터 `계_100세 이상`까지 합산하여 전체 인구를 구했습니다.
        - 고령화율은 **65세 이상 인구 ÷ 전체 인구 × 100**으로 계산했습니다.
        - 지도 경계와 인구 데이터는 시군구 이름이 아니라 **5자리 코드**로 연결했습니다.
        """
    )
