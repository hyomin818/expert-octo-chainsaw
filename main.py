import re
import requests
import pandas as pd
import streamlit as st
import plotly.express as px


# --------------------------------------------------
# 기본 설정
# --------------------------------------------------

st.set_page_config(
    page_title="전국 인구 지도",
    layout="wide"
)

st.title("🗺️ 전국 인구 지도")
st.caption("시군구별 전체 인구 수 (행정안전부 주민등록 인구)")


POP_URL = "https://raw.githubusercontent.com/greatsong/modudata/main/data/population_yearly.csv.gz"
GEO_URL = "https://raw.githubusercontent.com/greatsong/modudata/main/data/boundaries/sigungu_kr.geojson"


# --------------------------------------------------
# 인구 데이터 불러오기
# --------------------------------------------------

@st.cache_data(show_spinner="인구 데이터를 불러오는 중입니다...")
def load_population():

    response = requests.get(
        POP_URL,
        timeout=60
    )

    response.raise_for_status()

    # gzip으로 압축된 CSV 파일을 읽습니다.
    # 코드 열은 앞자리 0이 사라지지 않도록 글자로 읽습니다.
    return pd.read_csv(
        response.content,
        compression="gzip",
        dtype={"코드": str}
    )


# --------------------------------------------------
# 지도 경계 데이터 불러오기
# --------------------------------------------------

@st.cache_data(show_spinner="지도 경계를 불러오는 중입니다...")
def load_geojson():

    response = requests.get(
        GEO_URL,
        timeout=60
    )

    response.raise_for_status()

    geojson = response.json()

    # 지도 경계의 코드도 문자열 5자리로 통일합니다.
    for feature in geojson["features"]:

        feature["properties"]["코드"] = (
            str(feature["properties"]["코드"])
            .strip()
            .zfill(5)
        )

    return geojson


# --------------------------------------------------
# 데이터 불러오기
# --------------------------------------------------

df = load_population()
geojson = load_geojson()


# --------------------------------------------------
# 1. 가장 최신 연도만 사용
# --------------------------------------------------

df["연도"] = pd.to_numeric(
    df["연도"],
    errors="coerce"
)

latest_year = int(df["연도"].max())

df = df[
    df["연도"] == latest_year
].copy()


# --------------------------------------------------
# 2. '계_'로 시작하는 나이별 전체 인구 열 찾기
#
# 계_0세, 계_1세, ... 계_100세 이상
#
# 남_ / 여_ 열은 사용하지 않습니다.
# '계_'가 이미 남녀 합계이기 때문입니다.
# --------------------------------------------------

total_cols = [
    c for c in df.columns
    if c.startswith("계_")
]


# --------------------------------------------------
# 3. 인구 열을 숫자로 변환
# --------------------------------------------------

for col in total_cols:

    df[col] = pd.to_numeric(
        df[col],
        errors="coerce"
    ).fillna(0)


# --------------------------------------------------
# 4. 읍·면·동별 전체 인구 계산
# --------------------------------------------------

df["전체인구"] = df[
    total_cols
].sum(axis=1)


# --------------------------------------------------
# 5. 행정동 코드 앞 5자리 = 시군구 코드
# --------------------------------------------------

df["코드"] = (
    df["코드"]
    .astype("string")
    .str.strip()
)

df["시군구코드"] = df[
    "코드"
].str[:5]


# --------------------------------------------------
# 6. 시군구별 전체 인구 합산
# --------------------------------------------------

grouped = (
    df.groupby("시군구코드")["전체인구"]
    .sum()
    .reset_index()
)


# --------------------------------------------------
# 7. 지도 경계에서 시군구·시도 이름 가져오기
# --------------------------------------------------

names = pd.DataFrame([

    {
        "시군구코드": str(
            f["properties"]["코드"]
        ).strip().zfill(5),

        "시군구": f["properties"]["시군구"],

        "시도": f["properties"]["시도"],
    }

    for f in geojson["features"]

])


# --------------------------------------------------
# 8. 인구 자료와 지도 경계를 코드로 연결
# --------------------------------------------------

merged = grouped.merge(
    names,
    on="시군구코드",
    how="left"
)


# --------------------------------------------------
# 9. 인구 수를 5단계로 구분
#
# 시군구 전체의 실제 인구 분포를 기준으로
# 5개의 구간을 자동으로 나눕니다.
#
# 낮은 인구 → 옅은 색
# 높은 인구 → 진한 색
# --------------------------------------------------

# 인구 수의 20%, 40%, 60%, 80% 지점을 구합니다.
q20 = merged["전체인구"].quantile(0.2)
q40 = merged["전체인구"].quantile(0.4)
q60 = merged["전체인구"].quantile(0.6)
q80 = merged["전체인구"].quantile(0.8)


# 보기 편하도록 반올림합니다.
q20 = round(q20)
q40 = round(q40)
q60 = round(q60)
q80 = round(q80)


# 단계별 이름
LABELS = [
    f"{q20:,}명 미만",
    f"{q20:,}~{q40:,}명",
    f"{q40:,}~{q60:,}명",
    f"{q60:,}~{q80:,}명",
    f"{q80:,}명 이상"
]


# --------------------------------------------------
# 10. 인구 단계 만들기
# --------------------------------------------------

BINS = [
    -float("inf"),
    q20,
    q40,
    q60,
    q80,
    float("inf")
]


merged["단계"] = pd.cut(
    merged["전체인구"],
    bins=BINS,
    labels=LABELS,
    right=False
)


# --------------------------------------------------
# 11. 단계별 색상
# --------------------------------------------------

COLORS = {

    LABELS[0]: "#eff3ff",

    LABELS[1]: "#c6dbef",

    LABELS[2]: "#9ecae1",

    LABELS[3]: "#6baed6",

    LABELS[4]: "#2171b5",

}


# --------------------------------------------------
# 12. 지도에 사용할 데이터
# --------------------------------------------------

map_data = merged[
    merged["전체인구"].notna()
].copy()


# --------------------------------------------------
# 13. 단계구분도 만들기
#
# 기존 지도와 같은 형태입니다.
# 배경 지도 타일은 사용하지 않습니다.
# --------------------------------------------------

fig = px.choropleth(

    map_data,

    geojson=geojson,

    locations="시군구코드",

    featureidkey="properties.코드",

    color="단계",

    category_orders={
        "단계": LABELS
    },

    color_discrete_map=COLORS,

    hover_name="시군구",

    hover_data={

        "시도": True,

        "전체인구": ":,.0f",

        "시군구코드": False,

        "단계": False,

    },

    labels={

        "시도": "시도",

        "전체인구": "전체 인구",

        "단계": "인구 구간",

    },

)


# --------------------------------------------------
# 14. 지도 경계선 설정
# --------------------------------------------------

fig.update_traces(

    marker_line_color="#666666",

    marker_line_width=0.5,

)


# --------------------------------------------------
# 15. 지도 모양 설정
# --------------------------------------------------

fig.update_geos(

    fitbounds="locations",

    visible=False,

)


fig.update_layout(

    margin=dict(
        l=0,
        r=0,
        t=10,
        b=0
    ),

    height=700,

    legend_title_text=(
        f"시군구 인구 ({latest_year}년)"
    ),

)


# --------------------------------------------------
# 16. 지도 출력
# --------------------------------------------------

st.plotly_chart(
    fig,
    use_container_width=True
)


st.caption(
    "읍·면·동별 인구를 시군구별로 합산하여 나타낸 지도입니다."
)


# --------------------------------------------------
# 17. 인구 순위 표
# --------------------------------------------------

st.subheader("📊 시군구별 인구 순위")


c1, c2 = st.columns(2)

cols = [
    "시도",
    "시군구",
    "전체인구"
]


# --------------------------------------------------
# 인구가 많은 지역 TOP 10
# --------------------------------------------------

with c1:

    st.subheader("🔴 인구가 많은 곳 TOP 10")

    high_10 = (
        merged
        .dropna(subset=["전체인구"])
        .nlargest(
            10,
            "전체인구"
        )[cols]
        .reset_index(drop=True)
    )

    high_10.index = high_10.index + 1

    high_10 = high_10.rename(
        columns={
            "전체인구": "인구 수"
        }
    )

    st.dataframe(
        high_10,
        use_container_width=True
    )


# --------------------------------------------------
# 인구가 적은 지역 TOP 10
# --------------------------------------------------

with c2:

    st.subheader("🟢 인구가 적은 곳 TOP 10")

    low_10 = (
        merged
        .dropna(subset=["전체인구"])
        .nsmallest(
            10,
            "전체인구"
        )[cols]
        .reset_index(drop=True)
    )

    low_10.index = low_10.index + 1

    low_10 = low_10.rename(
        columns={
            "전체인구": "인구 수"
        }
    )

    st.dataframe(
        low_10,
        use_container_width=True
    )


# --------------------------------------------------
# 18. 데이터 설명
# --------------------------------------------------

with st.expander("ℹ️ 데이터 처리 방법"):

    st.write(
        f"""
        - {latest_year}년의 가장 최신 인구 자료를 사용했습니다.
        - 읍·면·동별 인구를 시군구 단위로 합산했습니다.
        - 행정동 코드의 앞 5자리를 시군구 코드로 사용했습니다.
        - 지도 경계와 인구 자료는 시군구 이름이 아니라 코드로 연결했습니다.
        - 인구가 적은 지역은 옅은 색, 인구가 많은 지역은 진한 색으로 표시했습니다.
        - 지도 색상은 시군구 인구 분포를 5개 구간으로 나누어 표시했습니다.
        """
    )
