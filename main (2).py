import html
import io
import math

import folium
import pandas as pd
import streamlit as st
from folium.plugins import MarkerCluster, Fullscreen
from streamlit_folium import st_folium

# =========================================================
# 우리 학교 주변 음식점 상권 분석
# =========================================================
st.set_page_config(
    page_title="우리 학교 주변 음식점 상권 분석",
    page_icon="🍽️",
    layout="wide",
)

st.markdown("""
<style>
    .block-container {padding-top: 2rem; padding-bottom: 2rem; max-width: 1500px;}
    h1 {font-weight: 800; letter-spacing: -1.5px;}
    [data-testid="stMetric"] {background:#f4f7fc; border:1px solid #e5ebf5; padding:16px 18px; border-radius:14px;}
    div[data-testid="stFileUploader"] {background:#f7f9fd; padding:12px; border-radius:12px;}
    .small-note {color:#748094; font-size:0.9rem;}
</style>
""", unsafe_allow_html=True)

st.title("🍽️ 우리 학교 주변 음식점 상권 분석")
st.caption("음식점 데이터를 지도에 표시하고, 업종별 분포와 주변 경쟁 밀집도를 살펴봅니다.")
st.divider()

# ---------------------------
# 데이터 처리 도우미
# ---------------------------
def find_col(columns, candidates):
    normalized = {str(c).strip(): c for c in columns}
    for candidate in candidates:
        if candidate in normalized:
            return normalized[candidate]
    return None


@st.cache_data(show_spinner="CSV 파일을 읽는 중입니다. 파일 크기에 따라 시간이 걸릴 수 있습니다.")
def load_data(file_bytes: bytes) -> pd.DataFrame:
    """필요한 열만 읽어 70MB 이상의 원본 CSV 메모리 사용량을 줄입니다."""
    encodings = ("cp949", "utf-8-sig", "euc-kr", "utf-8")
    last_error = None
    for enc in encodings:
        try:
            header = pd.read_csv(io.BytesIO(file_bytes), encoding=enc, nrows=0)
            header.columns = [str(c).strip() for c in header.columns]
            cols = list(header.columns)
            wanted_aliases = {
                "상호명", "사업장명", "업소명", "점포명", "지점명",
                "상권업종대분류명", "업종대분류명", "업종대분류",
                "상권업종중분류명", "업종중분류명",
                "상권업종소분류명", "업종소분류명", "업종명",
                "위도", "경도", "도로명주소", "지번주소", "주소",
                "시도명", "시군구명", "행정동명", "법정동명",
            }
            usecols = [c for c in cols if c in wanted_aliases]
            if not usecols:
                usecols = None
            df = pd.read_csv(
                io.BytesIO(file_bytes), encoding=enc, usecols=usecols,
                low_memory=False, dtype_backend="numpy_nullable"
            )
            df.columns = [str(c).strip() for c in df.columns]
            return df
        except UnicodeDecodeError as exc:
            last_error = exc
    raise ValueError(f"CSV 인코딩을 확인할 수 없습니다. 마지막 오류: {last_error}")


def haversine_km(lat1, lon1, lat2, lon2):
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def clean_text(series):
    return series.fillna("정보 없음").astype(str).str.strip().replace("", "정보 없음")


# ---------------------------
# 사이드바: 업로드 및 조건
# ---------------------------
st.sidebar.header("⚙️ 데이터 설정")
uploaded_file = st.sidebar.file_uploader(
    "공공데이터 CSV 업로드", type=["csv"],
    help="소상공인시장진흥공단 상가(상권)정보 CSV를 권장합니다."
)

if uploaded_file is None:
    st.info("왼쪽 메뉴에서 상권 데이터 CSV 파일을 업로드해 주세요.")
    st.markdown("**지원하는 주요 열:** 상호명, 상권업종대분류명, 상권업종소분류명, 위도, 경도")
    st.stop()

try:
    raw_df = load_data(uploaded_file.getvalue())
except Exception as exc:
    st.error(f"CSV 파일을 읽지 못했습니다: {exc}")
    st.stop()

name_col = find_col(raw_df.columns, ["상호명", "사업장명", "업소명", "점포명"])
major_col = find_col(raw_df.columns, ["상권업종대분류명", "업종대분류명", "업종대분류"])
mid_col = find_col(raw_df.columns, ["상권업종중분류명", "업종중분류명"])
category_col = find_col(raw_df.columns, ["상권업종소분류명", "업종소분류명", "업종명"])
lat_col = find_col(raw_df.columns, ["위도", "좌표정보(Y)", "Y좌표"])
lon_col = find_col(raw_df.columns, ["경도", "좌표정보(X)", "X좌표"])
address_col = find_col(raw_df.columns, ["도로명주소", "지번주소", "주소"])

missing = []
if name_col is None: missing.append("상호명")
if lat_col is None: missing.append("위도")
if lon_col is None: missing.append("경도")
if major_col is None and category_col is None and mid_col is None: missing.append("업종 분류 열")
if missing:
    st.error("필수 열을 찾지 못했습니다: " + ", ".join(missing))
    st.write("현재 CSV 열 이름:", list(raw_df.columns))
    st.stop()

# 음식점만 필터링: 공공데이터 표준 대분류는 보통 '음식'으로 표시됨
if major_col is not None:
    food_mask = clean_text(raw_df[major_col]).str.contains("음식", na=False)
    food_df = raw_df.loc[food_mask].copy()
else:
    # 대분류 열이 없는 변형 파일은 중/소분류에서 음식 관련 항목을 사용
    source_col = mid_col if mid_col is not None else category_col
    food_df = raw_df.loc[clean_text(raw_df[source_col]).str.contains(
        "음식|한식|중식|일식|양식|분식|치킨|피자|카페|제과|패스트푸드|주점", regex=True
    )].copy()

food_df["_name"] = clean_text(food_df[name_col])
food_df["_lat"] = pd.to_numeric(food_df[lat_col], errors="coerce")
food_df["_lon"] = pd.to_numeric(food_df[lon_col], errors="coerce")
food_df = food_df.dropna(subset=["_lat", "_lon"])
food_df = food_df[
    food_df["_lat"].between(33, 39) & food_df["_lon"].between(124, 132)
].copy()
if food_df.empty:
    st.warning("음식점 데이터 또는 유효한 위도·경도 데이터가 없습니다. CSV의 업종 및 좌표 열을 확인해 주세요.")
    st.stop()

if category_col:
    food_df["_category"] = clean_text(food_df[category_col])
elif mid_col:
    food_df["_category"] = clean_text(food_df[mid_col])
else:
    food_df["_category"] = clean_text(food_df[major_col])
food_df["_address"] = clean_text(food_df[address_col]) if address_col else "주소 정보 없음"

st.sidebar.success(f"음식점 {len(food_df):,}곳을 불러왔습니다.")

# 지역/업종 선택
st.sidebar.subheader("🔎 분석 조건")
all_categories = sorted(food_df["_category"].unique().tolist())
selected_categories = st.sidebar.multiselect(
    "음식점 업종", all_categories, default=all_categories,
    help="선택한 업종만 지도와 통계에 표시합니다."
)
search_name = st.sidebar.text_input("음식점 이름 검색", placeholder="예: 김밥, 치킨")

# 학교 좌표는 사용자가 직접 입력할 수 있도록 함. 미입력 시 데이터 중심으로 표시.
st.sidebar.subheader("🏫 학교 위치 (선택)")
center_lat = st.sidebar.number_input("학교 위도", min_value=0.0, max_value=90.0, value=0.0, format="%.6f", help="학교 위도를 입력하면 학교 주변 반경 분석을 할 수 있습니다. 모르면 0으로 두세요.")
center_lon = st.sidebar.number_input("학교 경도", min_value=0.0, max_value=180.0, value=0.0, format="%.6f", help="학교 경도를 입력하면 학교 주변 반경 분석을 할 수 있습니다. 모르면 0으로 두세요.")
use_school_center = center_lat > 0 and center_lon > 0
radius_km = st.sidebar.slider("학교 주변 분석 반경 (km)", 0.5, 5.0, 1.0, 0.5, disabled=not use_school_center)

filtered = food_df[food_df["_category"].isin(selected_categories)].copy()
if search_name.strip():
    filtered = filtered[filtered["_name"].str.contains(search_name.strip(), case=False, na=False)]
if use_school_center:
    filtered["_distance"] = filtered.apply(
        lambda r: haversine_km(center_lat, center_lon, r["_lat"], r["_lon"]), axis=1
    )
    filtered = filtered[filtered["_distance"] <= radius_km]

if filtered.empty:
    st.warning("선택한 조건에 해당하는 음식점이 없습니다. 업종이나 검색어, 분석 반경을 바꿔보세요.")
    st.stop()

# ---------------------------
# 핵심 지표
# ---------------------------
col1, col2, col3, col4 = st.columns(4)
col1.metric("음식점 수", f"{len(filtered):,}곳")
col2.metric("분석 업종 수", f"{filtered['_category'].nunique():,}개")
col3.metric("표시 지역", "학교 주변" if use_school_center else "전체 데이터")
col4.metric("분석 반경", f"{radius_km:.1f} km" if use_school_center else "미설정")

st.divider()

# ---------------------------
# 지도
# ---------------------------
st.subheader("📍 음식점 위치 지도")
if use_school_center:
    map_center = [center_lat, center_lon]
    zoom = 15
else:
    map_center = [float(filtered["_lat"].mean()), float(filtered["_lon"].mean())]
    zoom = 13

m = folium.Map(location=map_center, zoom_start=zoom, tiles="OpenStreetMap", control_scale=True)
Fullscreen(position="topleft").add_to(m)
if use_school_center:
    folium.Marker(
        [center_lat, center_lon], tooltip="학교 위치",
        popup="<b>학교 위치</b>",
        icon=folium.Icon(color="red", icon="graduation-cap", prefix="fa")
    ).add_to(m)
    folium.Circle(
        [center_lat, center_lon], radius=radius_km * 1000,
        color="#3978d4", weight=2, fill=True, fill_color="#3978d4", fill_opacity=0.08,
        tooltip=f"학교 주변 {radius_km:.1f}km"
    ).add_to(m)

cluster = MarkerCluster(name="음식점")
# 너무 많은 마커로 인한 브라우저 멈춤 방지
MAX_MARKERS = 5000
map_rows = filtered.head(MAX_MARKERS)
for row in map_rows.itertuples(index=False):
    # itertuples는 내부 열 이름이 변할 수 있어 원본 인덱스로 안전하게 접근
    pass
for idx, row in map_rows.iterrows():
    safe_name = html.escape(str(row["_name"]))
    safe_category = html.escape(str(row["_category"]))
    safe_address = html.escape(str(row["_address"]))
    popup_html = (
        f"<div style='width:220px'><b>{safe_name}</b><br>업종: {safe_category}"
        f"<br>주소: {safe_address}</div>"
    )
    folium.Marker(
        [float(row["_lat"]), float(row["_lon"])],
        tooltip=f"{safe_name} · {safe_category}",
        popup=folium.Popup(popup_html, max_width=280),
        icon=folium.Icon(color="blue", icon="cutlery", prefix="fa"),
    ).add_to(cluster)
cluster.add_to(m)
folium.LayerControl().add_to(m)

if len(filtered) > MAX_MARKERS:
    st.info(f"음식점이 많아 지도에는 처음 {MAX_MARKERS:,}곳만 표시합니다. 통계는 전체 {len(filtered):,}곳을 기준으로 계산합니다.")
st_folium(m, height=600, use_container_width=True, returned_objects=[])

# ---------------------------
# 업종 분석
# ---------------------------
st.divider()
st.subheader("📊 업종별 경쟁 밀집도 분석")
left, right = st.columns([1, 1])
counts = filtered["_category"].value_counts().rename_axis("업종").reset_index(name="음식점 수")
with left:
    st.markdown("**업종별 음식점 수**")
    st.bar_chart(counts.set_index("업종"), height=350)
with right:
    st.markdown("**업종별 비율**")
    share = counts.copy()
    share["비율(%)"] = (share["음식점 수"] / share["음식점 수"].sum() * 100).round(1)
    st.dataframe(share, use_container_width=True, hide_index=True, height=350)

# ---------------------------
# 상세 목록 및 다운로드
# ---------------------------
st.divider()
st.subheader("🏪 음식점 목록")
show_cols = ["_name", "_category", "_address", "_lat", "_lon"]
list_df = filtered[show_cols].rename(columns={
    "_name": "상호명", "_category": "업종", "_address": "주소",
    "_lat": "위도", "_lon": "경도"
})
st.dataframe(list_df, use_container_width=True, hide_index=True, height=400)
csv_data = list_df.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig")
st.download_button(
    "⬇️ 현재 분석 결과 CSV 다운로드", data=csv_data,
    file_name="음식점_상권분석_결과.csv", mime="text/csv"
)

st.caption("※ 학교 위치를 입력하지 않으면 지도는 필터링된 음식점 데이터의 중심을 기준으로 표시됩니다. 학교 위도·경도는 지도 앱에서 확인해 입력할 수 있습니다.")
