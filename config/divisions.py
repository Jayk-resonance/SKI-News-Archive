"""7개 사업부문 정의 — Exa 수집 쿼리·관찰 대상·수집 예산.

daily 파이프라인이 부문별로 Exa에 던질 검색어와, 관련성 판단·개체 매칭에 쓰는
SK 계열사/경쟁사 목록을 담는다. 하루 총 수집 목표는 ~20건(부문별 가중 배분).
비배터리 부문 키워드는 iM증권 정유/화학 Weekly(2Q26 Preview) 리포트를 반영해 보강.
쿼리 언어 규칙: 계열사·국내 제도·SK 고유 브랜드는 한국어(계열사 특정 뉴스 안전망),
일반 제품·시장·시황·정책은 영어(글로벌 원문 포괄). 교차언어 중복은 normalize의
개체 별칭 dedup + Step 3 병합으로 정리. 영어 제목은 그대로 두고 요약만 한국어로 쓴다.
검토·수정 지점: 각 부문의 queries / watch / daily_target.
"""
from __future__ import annotations

# 부문별: SK 계열사(자사), 관찰 경쟁사/기관, Exa 검색어, 일일 수집 목표
DIVISIONS: dict[str, dict] = {
    "배터리·소재": {
        "sk_affiliates": ["SK온", "SK아이이테크놀로지", "SKIET", "블루오벌SK"],
        # 셀 메이커·완성차 등 본류 관찰 대상
        "watch": ["CATL", "LG에너지솔루션", "삼성SDI", "파나소닉", "BYD",
                   "포드", "현대차", "폭스바겐", "테슬라"],
        # 소재 관찰 대상 — 분리막 사업 영위 회사
        "watch_materials": ["더블유씨피", "WCP", "아사히카세이", "Asahi Kasei",
                             "SEMCORP", "창신신소재", "도레이", "스미토모화학"],
        # 계열사 = 한국어 앵커 / 시장·기술·정책 = 영어 글로벌 — 목표 ~5건
        "queries": [
            "SK온 배터리 수주 실적 증설 투자 가동률",  # 계열사(한국어)
            "global EV sales demand outlook chasm Tesla BYD Hyundai Kia",
            "solid-state LFP 4680 sodium-ion next-gen battery technology development",
            # FEOC(PFE·CFE)·유럽 산업가속화법(IAA) 등 정책
            "US IRA AMPC FEOC PFE CFE EU battery regulation IAA policy supply chain",
            "ESS BESS grid energy storage AI datacenter power demand deployment",
            "battery critical minerals lithium nickel cobalt graphite supply refining",
            "EV battery cell maker supply deal OEM contract earnings LG Samsung SDI CATL",
            "EV battery fire recall safety defect quality issue NHTSA",  # 안전·리콜(SK온 직접 관련 다발)
        ],
        # 분리막 — 계열사/경쟁사(한국어) + 시장(영어) — 목표 ~1건(materials_target)
        "materials_queries": [
            "SK아이이테크놀로지 SKIET 분리막 실적 공급 수주",  # 계열사(한국어)
            "battery separator market wet dry coating supply Asahi Kasei SEMCORP WCP",
        ],
        "daily_target": 6,
        "materials_target": 1,  # 6건 중 분리막·소재에 배분(daily_target의 서브쿼터 — 가산 아님)
    },
    "정유·트레이딩": {
        "sk_affiliates": ["SK에너지", "SK인천석유화학", "SK트레이딩인터내셔널", "SKTI"],
        "watch": ["Saudi Aramco", "Shell", "BP", "ExxonMobil", "Vitol", "OPEC",
                   "이란", "러시아", "우크라이나", "GS칼텍스", "S-OIL", "현대오일뱅크", "EIA"],
        "queries": [
            "SK에너지 SK인천석유화학 정제마진 실적 가동률",  # 계열사(한국어)
            "SK이노베이션 정유 실적 샤힌 프로젝트 트레이딩",  # 계열사(한국어)
            "crude oil WTI Brent Dubai OSP refining margin crack spread lagging",
            "gasoline diesel kerosene naphtha bunker crack margin inventory",
            "OPEC production cut Iran Russia Ukraine Hormuz geopolitics oil price",
            "sustainable aviation fuel SAF low-carbon fuel refinery transition",
            # 글로벌 통신·상품전문지 대상 — 유가·정제마진 원문
            "global oil refining market margin outlook Reuters Bloomberg Platts",
        ],
        "daily_target": 3,
    },
    "석유화학": {
        "sk_affiliates": ["SK지오센트릭"],
        # SK지오센트릭 대표 제품·브랜드·설비 — 개체 매칭/관련성 판단용
        "products": ["울산ARC", "Ulsan ARC", "그린워터"],
        "watch": ["LG화학", "롯데케미칼", "롯데타이탄", "대한유화", "한화솔루션",
                   "금호석유화학", "BASF", "Dow Chemical"],
        # SK지오센트릭 사업(화학적 재활용·고부가 폴리올레핀·고순도 PP) 중심으로 조정.
        # 순수 업계 일반론(HBM 소재·반도체 전환 등)은 관련성이 낮아 최소화하고,
        # 시황은 SK지오센트릭 키워드를 함께 걸어 초점을 맞춘다.
        # SK지오센트릭 고유 브랜드·사업은 영어 커버리지가 얇아 한국어 유지
        "queries": [
            "SK지오센트릭 화학적 재활용 울산ARC 열분해유 해중합 순환경제",  # 계열사·브랜드(한국어)
            "SK지오센트릭 고순도 폴리프로필렌 PP 고부가 폴리올레핀 공급계약 실적",  # 계열사(한국어)
            "circular economy chemical recycling advanced pyrolysis polyolefin market",
            "petrochemical naphtha cracker spread overcapacity restructuring Reuters ICIS",
        ],
        "daily_target": 3,
    },
    "윤활유·기유": {
        "sk_affiliates": ["SK엔무브"],
        "watch": ["에쓰오일", "GS칼텍스", "쉐브론", "Shell"],
        "queries": [
            "SK엔무브 윤활유 기유 실적 수출 판매 투자",  # 계열사(한국어)
            "base oil Group III GTL lubricant market price margin spread outlook",
            "base oil demand supply capacity lubricant ICIS Argus",
        ],
        "daily_target": 2,
    },
    "E&P": {
        "sk_affiliates": ["SK어스온"],
        "watch": ["한국석유공사", "KNOC", "ExxonMobil", "Chevron",
                   "TotalEnergies", "Petronas", "CNOOC", "Woodside"],
        # SK어스온은 영어 보도가 거의 없어 한국어 유지
        "queries": [
            "SK어스온 광구 원유 가스 생산 탐사 해외자원 매장량",  # 계열사(한국어)
            "oil gas upstream exploration production block field reserves Reuters Upstream",
            "Middle East geopolitics oil producer crude export supply OPEC",
        ],
        "daily_target": 2,
    },
    "LNG": {
        "sk_affiliates": ["SK E&S"],
        "watch": ["한국가스공사", "KOGAS", "바로사", "칼디타", "QatarEnergy",
                   "Shell", "Cheniere", "Woodside", "Santos", "TotalEnergies"],
        "queries": [
            "SK E&S LNG 터미널 직도입 보령 인프라 발전 트레이딩",  # 계열사(한국어)
            "global LNG market price JKM TTF Henry Hub spot cargo supply demand",
            "LNG upstream gas field import project terminal Reuters Platts",
        ],
        "daily_target": 2,
    },
    "전력·수소": {
        "sk_affiliates": ["SK E&S", "SK플러그하이버스"],
        "watch": ["두산퓨얼셀", "블룸에너지", "Plug Power", "한국전력", "전력거래소"],
        # 국내 전력시장(SMP·계통)·계열사는 한국어 / 글로벌 수소·데이터센터 전력은 영어
        "queries": [
            "SK E&S 수소 액화수소 연료전지 충전 SMP 전력시장 분산전원 VPP",  # 계열사·국내제도(한국어)
            "hydrogen fuel cell clean power AI datacenter electricity demand grid",
            "renewable energy PPA RE100 solar wind power market storage",
        ],
        "daily_target": 2,
    },
}

# 하루 총 수집 목표(부문 합) — 참고용
TOTAL_DAILY_TARGET = sum(d["daily_target"] for d in DIVISIONS.values())  # = 20
