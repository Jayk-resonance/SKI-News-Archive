"""시계열 집계 — 트렌드 뷰 · 경쟁사 비교 뷰 · 주간 리포트가 공유한다.

기간은 전부 **최신 발행일 기준 7일 롤링 버킷**으로 자른다. ISO 주(월~일)를 쓰면
이번 주가 늘 잘린 채로 잡혀 "지난주 대비" 비교가 왜곡되기 때문이다.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

# 통계 노이즈만 키우는 범용어 — 사이트 GENERIC_KW와 같은 목록을 쓴다.
GENERIC_KW = {
    "미국", "유럽", "중국", "한국", "일본", "인도", "러시아", "중동", "아시아", "유럽연합", "EU",
    "국내", "해외", "글로벌", "세계", "1분기", "2분기", "3분기", "4분기", "상반기", "하반기",
    "올해", "내년", "작년", "연간",
}

# 경쟁사 비교 대상. entities 표기가 흔들려(SKIET↔SK아이이테크놀로지, 에쓰오일↔S-OIL)
# 별칭을 묶지 않으면 같은 회사가 쪼개져 집계된다.
COMPANY_GROUPS: list[tuple[str, list[tuple[str, list[str]]]]] = [
    ("SK 계열", [
        ("SK이노베이션", ["SK이노베이션", "SK이노", "SK Innovation"]),
        ("SK온", ["SK온", "SK On", "SKOn"]),
        ("SK엔무브", ["SK엔무브", "SK Enmove", "SK루브리컨츠"]),
        ("SK지오센트릭", ["SK지오센트릭", "SK Geo Centric", "SK종합화학"]),
        ("SK에너지", ["SK에너지", "SK Energy"]),
        ("SK아이이테크놀로지", ["SK아이이테크놀로지", "SKIET", "SK IE Technology"]),
        ("SK E&S", ["SK E&S", "SKE&S", "SK이엔에스"]),
    ]),
    ("배터리 경쟁사", [
        ("LG에너지솔루션", ["LG에너지솔루션", "LG엔솔", "LG Energy Solution", "LGES"]),
        ("삼성SDI", ["삼성SDI", "Samsung SDI"]),
        ("CATL", ["CATL", "닝더스다이"]),
        ("BYD", ["BYD", "비야디"]),
        ("파나소닉", ["파나소닉", "Panasonic"]),
    ]),
    ("정유 경쟁사", [
        ("GS칼텍스", ["GS칼텍스", "GS Caltex"]),
        ("에쓰오일", ["에쓰오일", "S-OIL", "S-Oil", "에스오일"]),
        ("HD현대오일뱅크", ["HD현대오일뱅크", "현대오일뱅크", "HD현대케미칼"]),
    ]),
    ("화학 경쟁사", [
        ("LG화학", ["LG화학", "LG Chem"]),
        ("롯데케미칼", ["롯데케미칼", "Lotte Chemical"]),
        ("한화솔루션", ["한화솔루션", "한화토탈", "Hanwha Solutions"]),
    ]),
]

BUCKET_DAYS = 7
MAX_BUCKETS = 8

# 태그 표기 흔들림 통합 + 한글 라벨. 같은 주제가 'refining-margin' / 'refining margin' /
# '정제마진'으로 흩어져 집계되던 문제를 라벨을 공유시켜 한 덩어리로 만든다.
TAG_LABEL = {
    "ess": "ESS", "energy storage": "ESS", "grid storage": "ESS", "bess": "BESS",
    "hormuz": "호르무즈", "strait of hormuz": "호르무즈", "호르무즈": "호르무즈",
    "refining margin": "정제마진", "정제마진": "정제마진", "margin": "정제마진",
    "crack spread": "크랙스프레드", "refining": "정유 시황",
    "geopolitics": "지정학 리스크", "지정학리스크": "지정학 리스크", "energy security": "에너지 안보",
    "sodium ion": "나트륨이온 배터리", "solid state": "전고체 배터리",
    "차세대배터리": "차세대 배터리", "next gen": "차세대 배터리", "lfp": "LFP",
    "base oil": "기유", "group iii": "그룹III 기유", "lubricant": "윤활유",
    "chemical recycling": "화학적 재활용", "circular economy": "순환경제",
    "recycling": "리사이클", "pyrolysis": "열분해",
    "ai datacenter": "AI 데이터센터", "datacenter": "AI 데이터센터", "data center": "AI 데이터센터",
    "critical minerals": "핵심광물", "graphite": "흑연", "lithium": "리튬",
    "nickel": "니켈", "cobalt": "코발트", "battery materials": "배터리 소재",
    "separator": "분리막", "fuel cell": "연료전지", "hydrogen": "수소", "solar": "태양광",
    "feoc": "FEOC 규제", "ira": "IRA", "policy": "정책·규제", "eu": "EU 규제",
    "supply chain": "공급망", "overcapacity": "공급과잉", "oversupply": "공급과잉",
    "earnings": "실적", "q2 earnings": "분기 실적", "실적": "실적",
    "petrochemical restructuring": "석유화학 구조조정", "restructuring": "구조조정",
    "구조조정": "구조조정", "petrochemical": "석유화학 시황",
    "opec": "OPEC", "brent": "국제유가", "wti": "국제유가", "oil price": "국제유가",
    "crude supply": "원유 수급", "diesel": "경유", "gasoline": "휘발유",
    "naphtha": "나프타", "distillate": "중간유분",
    "jkm": "JKM(아시아 LNG가)", "ttf": "TTF(유럽 가스가)", "lng demand": "LNG 수요", "lng": "LNG 시황",
    "upstream": "상류(E&P)", "exploration": "탐사", "fid": "최종투자결정",
    "saf": "SAF(지속가능항공유)", "low carbon fuel": "저탄소 연료",
    "market share": "시장점유율", "power demand": "전력수요", "전력수요": "전력수요",
    "ev sales": "EV 판매", "china ev": "중국 EV", "ev": "EV 시장",
    "recall": "리콜", "nhtsa": "NHTSA 조사", "battery fire": "배터리 화재",
    "turnaround": "정기보수", "feedstock": "원료", "shaheen": "샤힌 프로젝트",
    "k battery": "K배터리", "trading": "트레이딩", "시황": "시황",
    "black mass": "블랙매스", "bis": "BIS 수출규제", "dpa": "국방물자생산법",
    "offshore": "해상 광구", "tariff": "관세", "subsidy": "보조금",
}
# 국가·회사·범용어 — 경쟁사 뷰나 부문 필터가 이미 다루거나, 신호가 없는 축.
TAG_SKIP = {
    "korea", "china", "us", "usa", "europe", "india", "indonesia", "asia", "middle east",
    "iran", "iraq", "qatar", "japan", "russia", "brazil", "africa", "mexico", "ulsan", "yeosu",
    "competitor", "경쟁사", "supply", "cost", "battery", "market", "news", "global",
    "sk on", "sk온", "sk innovation", "sk enmove", "sk energy", "sk e&s", "sk geocentric",
    "skiet", "catl", "byd", "tesla", "hyundai", "kia", "samsung sdi", "lg energy solution",
    "s oil", "exxonmobil", "totalenergies", "bloom energy", "iea", "conocophillips",
}


def _d(s: str) -> date:
    return date.fromisoformat(s)


def norm_tag(t: str) -> str:
    """소문자 + 구분자 통일 — 'Refining-Margin'과 'refining margin'을 한 키로 모은다."""
    return " ".join((t or "").lower().replace("-", " ").replace("_", " ").split())


def tag_label(t: str) -> str | None:
    """표시용 한글 라벨. 스킵 대상이면 None, 미등록이면 읽기 좋은 영문으로."""
    n = norm_tag(t)
    if not n or n in TAG_SKIP:
        return None
    if n in TAG_LABEL:
        return TAG_LABEL[n]
    if len(n) <= 2:
        return None
    # 미등록 태그: 짧은 단일 토큰은 약어로 보고 대문자(bis→BIS), 나머지는 단어별 첫 글자만.
    ws = n.split()
    if len(ws) == 1 and len(n) <= 4 and n.isalpha():
        return n.upper()
    return " ".join(w.upper() if len(w) <= 3 and w.isalpha() else w.capitalize() for w in ws)


def buckets(latest: str, n: int = MAX_BUCKETS, earliest: str | None = None) -> list[dict]:
    """최신일에서 7일씩 거슬러 올라가는 구간 목록(오래된 것부터).

    시작일이 아카이브 개시일보다 앞선 '반쪽 구간'은 버린다 — 수집 자체가 없던 기간이
    포함되면 맨 왼쪽 점이 실제보다 낮게 찍혀 추세가 왜곡된다.
    """
    end = _d(latest)
    out = []
    for i in range(n):
        b_end = end - timedelta(days=BUCKET_DAYS * i)
        b_start = b_end - timedelta(days=BUCKET_DAYS - 1)
        if earliest and b_start < _d(earliest):
            break
        out.append({
            "start": b_start.isoformat(), "end": b_end.isoformat(),
            "label": f"{b_start.month}/{b_start.day}~{b_end.month}/{b_end.day}",
        })
    return list(reversed(out))


def _surface(a: dict) -> str:
    """회사명 탐지용 검색면. entities만 보면 본문에만 언급된 회사를 놓친다."""
    parts = [a.get("title") or "", a.get("title_ko") or "", a.get("company") or ""]
    for k in ("entities", "keywords", "tags"):
        parts.extend(a.get(k) or [])
    return " ".join(parts).lower()


def _bucket_of(d: str, bks: list[dict]) -> int:
    for i, b in enumerate(bks):
        if b["start"] <= d <= b["end"]:
            return i
    return -1


def build_trends(articles: list[dict], latest: str) -> dict:
    """부문별·키워드별 주간 추이 + 급상승 키워드 + 회사별 언급량."""
    if not articles or not latest:
        return {}
    earliest = min(a["date"] for a in articles)
    bks = buckets(latest, MAX_BUCKETS, earliest)
    nb = len(bks)

    total = [0] * nb
    by_div: dict[str, list[int]] = defaultdict(lambda: [0] * nb)
    by_topic: dict[str, list[int]] = defaultdict(lambda: [0] * nb)
    tag_series: dict[str, list[int]] = defaultdict(lambda: [0] * nb)
    for a in articles:
        i = _bucket_of(a["date"], bks)
        if i < 0:
            continue
        total[i] += 1
        by_div[a["division"]][i] += 1
        if a.get("topic"):
            by_topic[a["topic"]][i] += 1
        for lb in {tag_label(t) for t in (a.get("tags") or [])} - {None}:
            tag_series[lb][i] += 1

    # 급상승: 주간 수집량 자체가 크게 출렁이므로(예: 141건→54건) 절대 건수가 아니라
    # '그 주 기사 중 몇 %를 차지했는가'(점유율) 변화로 판단한다.
    rising: list[dict] = []
    if nb >= 2 and total[-1] and total[-2]:
        for k, s in tag_series.items():
            cur, prev = s[-1], s[-2]
            # 건수까지 늘어난 것만 남긴다. 점유율만 보면 전체가 더 크게 줄었을 때
            # '14건→8건'인 테마가 부상으로 뜨는데, 읽는 사람에게는 혼란만 준다.
            if cur < 2 or cur < prev:
                continue
            cur_sh, prev_sh = cur / total[-1], prev / total[-2]
            if cur_sh > prev_sh:
                rising.append({"kw": k, "cur": cur, "prev": prev, "delta": cur - prev,
                               "curShare": round(cur_sh * 100, 1),
                               "prevShare": round(prev_sh * 100, 1)})
        rising.sort(key=lambda x: (-(x["curShare"] - x["prevShare"]), -x["cur"], x["kw"]))

    # 동점일 때 라벨순으로 확정한다. tag_series는 집합 순회로 채워져 삽입 순서가 실행마다
    # 달라지는데, 안정 정렬이 그 순서를 그대로 물려받아 같은 데이터에서 매번 다른 JSON이
    # 나왔다(순환경제↔화학적 재활용이 자리를 바꿔 빌드마다 헛 diff가 생겼다).
    top_tags = sorted(tag_series.items(), key=lambda kv: (-sum(kv[1]), kv[0]))[:12]
    top_topic = sorted(by_topic.items(), key=lambda kv: sum(kv[1]), reverse=True)[:10]

    companies = []
    for group, members in COMPANY_GROUPS:
        for name, aliases in members:
            al = [x.lower() for x in aliases]
            hits = [a for a in articles if any(x in _surface(a) for x in al)]
            if not hits:
                continue
            series = [0] * nb
            divs: dict[str, int] = defaultdict(int)
            for a in hits:
                i = _bucket_of(a["date"], bks)
                if i >= 0:
                    series[i] += 1
                divs[a["division"]] += 1
            recent = sorted(hits, key=lambda a: (a["date"], a.get("impact_score", 0)),
                            reverse=True)[:3]
            companies.append({
                "name": name, "group": group, "total": len(hits), "weekly": series,
                "divisions": dict(sorted(divs.items(), key=lambda kv: -kv[1])),
                "recent": [{"id": a["id"], "date": a["date"], "division": a["division"],
                            "title": a.get("title_ko") or a.get("title", "")} for a in recent],
            })
    companies.sort(key=lambda c: -c["total"])

    return {
        "latest": latest,
        "buckets": bks,
        "total": total,
        "byDivision": {d: v for d, v in sorted(by_div.items(), key=lambda kv: -sum(kv[1]))},
        "byTopic": [{"name": k, "series": s} for k, s in top_topic],
        "themes": [{"kw": k, "series": s} for k, s in top_tags],
        "rising": rising[:10],
        "companies": companies,
    }


def build_weekly(articles: list[dict], issues: dict, ins_index: list[dict],
                 trends: dict, latest: str) -> dict:
    """최근 7일 다이제스트 — 사이트 '주간 리포트' 탭과 주간 메일이 함께 쓴다."""
    if not articles or not latest:
        return {}
    end = _d(latest)
    start = end - timedelta(days=BUCKET_DAYS - 1)
    s, e = start.isoformat(), end.isoformat()
    week = [a for a in articles if s <= a["date"] <= e]
    if not week:
        return {}

    prev_s = (start - timedelta(days=BUCKET_DAYS)).isoformat()
    prev_cnt = len([a for a in articles if prev_s <= a["date"] < s])

    divs: dict[str, int] = defaultdict(int)
    for a in week:
        divs[a["division"]] += 1

    top = sorted(week, key=lambda a: (a.get("impact_score", 0), a["date"]), reverse=True)[:5]

    # 이번 주에 새 기사가 붙은 사안만 — 지난주에 멈춘 사안은 주간 리포트의 관심사가 아니다.
    live = [i for i in issues.values() if i.get("lastDate", "") >= s]
    # 테마 버킷은 날마다 쌓여 건수가 크므로 그냥 정렬하면 상위를 다 차지한다.
    # 주간 리포트에서 궁금한 건 '무슨 일이 있었나'이므로 실제 사건(event)을 앞세운다.
    live.sort(key=lambda i: (i.get("kind") != "theme", i.get("count", 0),
                             i.get("peakImpact", 0)), reverse=True)

    return {
        "start": s, "end": e,
        "label": f"{start.month}월 {start.day}일 ~ {end.month}월 {end.day}일",
        "total": len(week), "prevTotal": prev_cnt, "delta": len(week) - prev_cnt,
        "byDivision": dict(sorted(divs.items(), key=lambda kv: -kv[1])),
        "top": [{"id": a["id"], "date": a["date"], "division": a["division"],
                 "title": a.get("title_ko") or a.get("title", ""),
                 "titleOrig": a.get("title") if a.get("title_ko") else "",
                 "summary": a.get("summary", ""), "impact_score": a.get("impact_score", 0),
                 "leadPublisher": a.get("leadPublisher", ""), "leadUrl": a.get("leadUrl", "")}
                for a in top],
        "rising": (trends or {}).get("rising", [])[:6],
        "issues": [{"eventId": i["eventId"], "label": i["label"], "division": i["division"],
                    "count": i["count"], "firstDate": i["firstDate"], "lastDate": i["lastDate"],
                    "kind": i.get("kind", "")} for i in live[:6]],
        "insights": [i for i in ins_index if s <= i.get("date", "") <= e],
    }
