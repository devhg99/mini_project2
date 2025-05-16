from flask import Flask, render_template, request, redirect, url_for # Flask 웹 서버
import os
import re
import requests                 # Kakao API 호출을 위한 HTTP 클라이언트트
import markdown                 # GPT 응답 마크다운→HTML 변환
from openai import OpenAI
from dotenv import load_dotenv  # .env 파일에서 API 키 로드

# 환경변수(.env)에서 API 키 읽기
load_dotenv()
app = Flask(__name__)

# OpenAI 클라이언트 생성 (GPT 호출용)
client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

# GPT에 여행 일정 생성 요청을 보내고, 마크다운 형식 텍스트 반환
def generate_itinerary(prompt: str) -> str:
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "당신은 전문 여행 일정 플래너입니다."},
                {"role": "user", "content": prompt}
            ]
        )
        # GPT 응답 중 첫 번째 메시지 콘텐츠 반환
        return response.choices[0].message.content
    except Exception as e:
        return f"에러 발생: {e}"

# 일정 텍스트에서 "",''로 묶인 장소명만 추출
def extract_places(text: str) -> list:
    pattern = r"['‘“\"](.+?)['’”\"]"
    matches = re.findall(pattern, text)
    return list(set(matches)) # 중복 제거

# HTML 결과에서 장소명을 <span> 태그로 감싸 클릭 가능하게 변환(**안되면 삭제)
def linkify_places(html: str, place_names: list) -> str:
    for place in place_names:
        html = html.replace(
            place,
            f'<span class="place-link" data-name=\"{place}\">{place}</span>'
        )
    return html

# Kakao REST API로 장소명을 위도/경도로 변환
def get_kakao_coords(place_name: str):
    KEY = os.environ["KAKAO_REST_API_KEY"]
    url = "https://dapi.kakao.com/v2/local/search/keyword.json"
    headers = {"Authorization": f"KakaoAK {KEY}"}
    params = {"query": place_name}

    res = requests.get(url, headers=headers, params=params).json()
    if res.get('documents'):
        lat = res['documents'][0]['y']
        lng = res['documents'][0]['x']
        return lat, lng
    return None

# 추가
def extract_schedule_entries(text: str) -> list:
    """
    GPT 응답 원문에서 '1일차', '2일차' 단위로
    날짜(day), 시간(time), 장소(place), 설명(desc)를 파싱하여 리스트 반환

    - 정규표현식으로 각 일차별 블록 캡처
    - 각 줄에서 시간(예: '09:00') 추출
    - 큰따옴표 묶음에서 장소명 추출, 나머지는 desc
    """
    pattern = r"(\d+일차)(?:\s*[:\-]?\s*)?(.*?)(?=\d+일차|$)"
    entries = re.findall(pattern, text, re.DOTALL)
    schedule = []
    for day, body in entries:
        for line in body.strip().split("\n"):
            # 시간 추출(없으면 빈 문자열)
            time_match = re.match(r"(\d{1,2}:\d{2})", line)
            time = time_match.group(1) if time_match else ""
            # "" 안의 장소명
            place_match = re.search(r"[\"“‘'](.+?)[\"”’']", line)
            if place_match:
                place = place_match.group(1)
                # desc: "" 부분 제거 후 남은 텍스트
                desc = line.replace(place_match.group(0), "").strip(" :-~")
                schedule.append({
                    "day": day,
                    "time": time,
                    "place": place,
                    "desc": desc
                })
    return schedule

# 5.15 15시 신규함수
def search_category(category_code: str, region: str, size=15) -> list:
    """
    카카오 로컬 카테고리 검색 API 호출
    category_code: 'CE7'(카페), 'FD6'(음식점), 'AT4'(관광지) 등
    region: 검색할 지역 키워드
    """
    REST_KEY = os.environ["KAKAO_REST_API_KEY"]
    url = "https://dapi.kakao.com/v2/local/search/category.json"
    headers = {"Authorization": f"KakaoAK {REST_KEY}"}
    params = {
        "category_group_code": category_code,
        "query": region,
        "size": size
    }
    res = requests.get(url, headers=headers, params=params).json()
    return res.get("documents", [])

@app.route("/", methods=["GET", "POST"])
def index():
    # 렌더링 변수 초기화
    result = ""
    markers = []
    center_lat, center_lng = 36.5, 127.5 # 대한민국 중심 좌표 기본값

    if request.method == "POST":
        # 1) 사용자 입력 수집
        start_date     = request.form.get("start_date")     # 시작일
        end_date       = request.form.get("end_date")       # 종료일
        companions     = request.form.get("companions")     # 누구와
        people_count   = request.form.get("people_count")   # 인원수
        theme          = request.form.getlist("theme")      # 여행테마
        theme_str      = ", ".join(theme)
        user_prompt    = request.form.get("user_prompt")    # 프럼프트
        location       = request.form.get("location")
        transport_mode = request.form.get("transport_mode") # 교통수단
        
        # 2) 입력된 장소로 지도 중심 좌표 업데이트
        coords = get_kakao_coords(location)
        if coords:
            center_lat, center_lng = coords

        # 3) GPT에게 보낼 최종 프롬프트 구성
        prompt = f"""
        여행 날짜: {start_date} ~ {end_date}
        동행: {companions}, 총 인원: {people_count}명
        여행지: {location}, 테마: {theme_str}
        교통수단: {transport_mode}
        추가 조건: {user_prompt}

        **출력 형식**
        - 각 일정 항목은 다음처럼 작성해주세요.
        1일차:
        09:00~10:00: \"해운대 해수욕장\"
        • 해운대의 상징인 해변에서 기분 좋은 아침을 맞이하세요. 바다의 파도 소리와 함께 상쾌한 자연경관을 즐길 수 있습니다.
        10:30~12:00: \"더베이101\"
        • 해운대 여러 카페 중 하나인 더베이101에서 커피를 마시며 해변의 풍경을 감상할 수 있습니다.
        14:00~16:00: \"동백섬\"
        •  동백섬으로 이동해 짧은 산책을 즐겨보세요. 이곳은 다양한 식물과 함께 한국 전통의 "동백꽃"을 즐길 수 있는 명소입니다.
        - 각 일정에 설명을 전문 여행 일정 플래너처럼 성의있게 정성껏 짜주세요(설명에는 장소가 안들어가도 됩니다).
        - 각 일정에 따라 정해진 장소간에 거리가 멀어지면 이동이 어려우니 멀지않은곳으로 추천해주세요.
        - 모든 추천 장소는 반드시 {location} 시/군/구 내에 있는 식당, 카페, 관광지만 제안해줘.
        - 교통수단에 따라 일정을 조율해주세요.(예를들어 자차를 타면 일정끼리 거리가 멀어도 괜찮을것이고, 도보같은경우 일정끼리 거리가 가까워야합니다)
        - 가게(음식점, 카페)나 관광지같은경우 영업시간, 입장료, 대표메뉴 등등 추천해주세요.
        - 시간 앞에 적힌 장소명은 반드시 큰따옴표(\"\")로 묶어주세요.
        - 가능하면 위 형식을 **Markdown** 스타일로 유지해주세요.
        """

        # 4) GPT 일정 텍스트 받아오기
        raw_result = generate_itinerary(prompt)

        # 5) 마크다운→HTML 변환 + 장소 링크화
        result = markdown.markdown(raw_result)
        place_names = extract_places(raw_result)
        result = linkify_places(result, place_names)

        # 파싱: 일정 데이터와 장소 링크 변환
        schedule_data = extract_schedule_entries(raw_result)

        # 6) 마커용 장소 추출 및 좌표 계산
        for entry in schedule_data:
            coord = get_kakao_coords(entry["place"])
            if coord:
                markers.append({
                    "name" : entry["place"],
                    "lat"  : coord[0],
                    "lng"  : coord[1],
                    "day"  : entry["day"],
                    "time" : entry["time"],
                    "desc" : entry["desc"]
                })
    
    # 7) 템플릿 렌더링
    return render_template("index.html",
                           result=result,
                           kakao_key=os.environ["KAKAO_JAVASCRIPT_KEY"],
                           markers=markers,
                           center_lat=center_lat,
                           center_lng=center_lng)

# 5.15 15시 신규함수
@app.route("/search/<category>")
def search(category):
    # 1) 카테고리 코드 매핑
    code_map = {
        "cafe":        "CE7",
        "restaurant":  "FD6",
        "tourism":     "AT4",
        # 필요시 추가…
    }
    code = code_map.get(category)
    if not code:
        # 잘못된 카테고리면 메인으로 리다이렉트
        return redirect(url_for("index"))

    # 2) 지역 파라미터 읽기
    region = request.args.get("region", "")

    # 3) 카테고리 검색 API 호출
    places = search_category(code, region)

    # 4) 결과 렌더링
    return render_template(
        "search.html",
        category=category,
        region=region,
        places=places
    )

if __name__ == "__main__":
    app.run(debug=True)
