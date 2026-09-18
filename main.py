import gzip
import io
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


POP_URL = (
    "https://raw.githubusercontent.com/greatsong/modudata/"
    "main/data/population_yearly.csv.gz"
)

GEO_URL = (
    "https://raw.githubusercontent.com/greatsong/modudata/"
    "main/data/boundaries/sigungu_kr.geojson"
)


# --------------------------------------------------
# 인구 데이터 불러오기
# --------------------------------------------------

@st.cache_data(show_spinner="인구 데이터를 불러오는 중입니다...")
def load_population():

    response = requests.get(
        POP_URL,
        timeout=120
    )

    response.raise_for_status()

    # 다운로드한 gzip 파일을 직접 압축 해제합니다.
    # pandas가 gzip을 직접 처리하지 않도록 합니다.
    csv_bytes = gzip.decompress(response.content)

    # 압축이 풀린 CSV를 pandas로 읽습니다.
    # '코드'는 반드시 문자열로 읽습니다.
    df = pd.read_csv(
        io.BytesIO(csv_bytes),
        dtype={"코드": "string"}
    )

    return df


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

    # GeoJSON의 시군구 코드도 문자열 5자리로 통일합니다.
    for feature in geojson["features"]:

        code = feature["properties"]["코드"]

        feature["properties"]["코드"] = (
            str(code)
            .strip()
            .zfill(5)
        )

    return geojson


# --------------------------------------------------
# 데이터 불러오기
# --------------------------------------------------

try:

    df = load_population()

    geojson = load_geojson()

except Exception as e:

    st.error("데이터를 불러오는 중 오류가 발생했습니다.")

    st.error(
        "인구 CSV 또는 지도 경계 데이터를 가져오지 못했습니다."
    )

    with st.expander("오류 자세히 보기"):
        st.exception(e)

    st.stop()


# --------------------------------------------------
# 1. 최신 연도만 사용
# --------------------------------------------------

df["연도"] = pd.to_numeric(
    df["연도"],
    errors="coerce"
)

latest_year = int(
    df["연도"].max()
)

df = df[
    df["연도"] == latest_year
].copy()


# --------------------------------------------------
# 2. 코드 열 정리
# --------------------------------------------------

df["코드"] = (
    df["코드"]
    .astype("string")
    .str.strip()
)

# 행정동 코드 앞 5자리가 시군구 코드입니다.
df["시군구코드"] = (
    df["코드"]
    .str[:5]
)


# --------------------------------------------------
# 3. '계_'로 시작하는 전체 인구 열 찾기
#
# 계_0세
# 계_1세
# ...
# 계_100세 이상
#
# '계_'는 남녀 합계이므로
# 남_ / 여_ 열은 사용하지 않습니다.
# --------------------------------------------------

total_cols = [
    column
    for column in df.columns
    if column.startswith("계_")
]


if len(total_cols) == 0:

    st.error(
        "'계_'로 시작하는 인구 열을 찾지 못했습니다."
    )

    st.stop()


# --------------------------------------------------
# 4. 인구 열을 숫자로 변환
# --------------------------------------------------

for column in total_cols:

    df[column] = pd.to_numeric(
        df[column],
        errors="coerce"
    ).fillna(0)


# --------------------------------------------------
# 5. 읍·면·동별 전체 인구 계산
# --------------------------------------------------

df["전체인구"] = (
    df[total_cols]
    .sum(axis=1)
)


# --------------------------------------------------
# 6. 시군구별 인구 합산
# --------------------------------------------------

grouped = (
    df.groupby(
        "시군구코드",
        as_index=False
    )["전체인구"]
    .sum()
)


# --------------------------------------------------
# 7. 지도 경계에서 지역 이름 가져오기
# --------------------------------------------------

names = []

for feature in geojson["features"]:

    properties = feature["properties"]

    names.append(
        {
            "시군구코드": str(
                properties["코드"]
            ).strip().zfill(5),

            "시군구": properties["시군구"],

            "시도": properties["시도"],
        }
    )


names = pd.DataFrame(names)


# --------------------------------------------------
# 8. 인구 자료 + 지도 경계 연결
#
# 지역 이름이 아니라 코드로 연결합니다.
# --------------------------------------------------

merged = grouped.merge(
    names,
    on="시군구코드",
    how="left"
)


# --------------------------------------------------
# 9. 지도에 필요한 데이터만 사용
# --------------------------------------------------

merged = merged[
    merged["전체인구"].notna()
].copy()


# --------------------------------------------------
# 10. 인구 수를 5단계로 구분
#
# 전국 시군구의 인구 분포를 기준으로
# 5개 구간으로 나눕니다.
# --------------------------------------------------

q20 = merged["전체인구"].quantile(0.2)
q40 = merged["전체인구"].quantile(0.4)
q60 = merged["전체인구"].quantile(0.6)
q80 = merged["전체인구"].quantile(0.8)


q20 = int(round(q20))
q40 = int(round(q40))
q60 = int(round(q60))
q80 = int(round(q80))


LABELS = [
    f"{q20:,}명 미만",
    f"{q20:,}~{q40:,}명",
    f"{q40:,}~{q60:,}명",
    f"{q60:,}~{q80:,}명",
    f"{q80:,}명 이상"
]


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
#
# 인구가 적을수록 옅게
# 인구가 많을수록 진하게
# --------------------------------------------------

COLORS = {

    LABELS[0]: "#eff3ff",

    LABELS[1]: "#c6dbef",

    LABELS[2]: "#9ecae1",

    LABELS[3]: "#6baed6",

    LABELS[4]: "#2171b5",

}


# --------------------------------------------------
# 12. 지도 만들기
# --------------------------------------------------

fig = px.choropleth(

    merged,

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

        "전체인구": "인구 수",

        "단계": "인구 구간",

    }
)


# --------------------------------------------------
# 13. 시군구 경계선
# --------------------------------------------------

fig.update_traces(

    marker_line_color="#666666",

    marker_line_width=0.5

)


# --------------------------------------------------
# 14. 배경 지도 타일 없이 경계만 표시
# --------------------------------------------------

fig.update_geos(

    fitbounds="locations",

    visible=False

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
    )

)


# --------------------------------------------------
# 15. 지도 출력
# --------------------------------------------------

st.plotly_chart(
    fig,
    use_container_width=True
)


st.caption(
    "읍·면·동별 인구를 시군구별로 합산하여 표시했습니다."
)


# --------------------------------------------------
# 16. 순위표
# --------------------------------------------------

st.subheader("📊 시군구별 인구 순위")


c1, c2 = st.columns(2)


# --------------------------------------------------
# 인구가 많은 곳 TOP 10
# --------------------------------------------------

with c1:

    st.subheader("🔴 인구가 많은 곳 TOP 10")

    high_10 = (
        merged
        .sort_values(
            "전체인구",
            ascending=False
        )
        .head(10)
        [
            ["시도", "시군구", "전체인구"]
        ]
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
# 인구가 적은 곳 TOP 10
# --------------------------------------------------

with c2:

    st.subheader("🟢 인구가 적은 곳 TOP 10")

    low_10 = (
        merged
        .sort_values(
            "전체인구",
            ascending=True
        )
        .head(10)
        [
            ["시도", "시군구", "전체인구"]
        ]
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
# 17. 데이터 처리 방법
# --------------------------------------------------

with st.expander("ℹ️ 데이터 처리 방법"):

    st.write(
        f"""
        - {latest_year}년의 가장 최신 인구 자료를 사용했습니다.
        - 읍·면·동별 인구를 시군구별로 합산했습니다.
        - 행정동 코드의 앞 5자리를 시군구 코드로 사용했습니다.
        - 지도 경계와 인구 자료는 지역 이름이 아니라 코드로 연결했습니다.
        - '계_'로 시작하는 나이별 인구를 합산하여 전체 인구를 계산했습니다.
        - 인구가 적은 지역은 옅은 색, 인구가 많은 지역은 진한 색으로 표시했습니다.
        """
    )
