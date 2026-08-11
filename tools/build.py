#!/usr/bin/env python3
"""content/**/*.md (기사별 front-matter) → site/data/*.json 빌드.

LLM 미사용. 순수 코드. UI가 실제 로드하는 산출물을 생성한다:
  - index.json      : 검색 인덱스(요약·본문 제외로 경량). 클라이언트 검색용
  - YYYY-MM.json    : 월별 상세(+summary·highlight·sources). 지연 로드
  - issues.json     : 사안 타임라인 그룹 (eventId 기준)
  - today.json      : 최신일 브리핑 카드 + PICK 인사이트 + 부문 코멘트
  - manifest.json   : 사용 가능한 월 샤드 목록·집계

또한 데이터 무결성 검증(division/topic/impact_score)을 수행하고 이상치를 경고한다.
"""
from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from config.sources import tier_for, credibility_weight, TIER_BY_NAME  # noqa: E402
sys.path.insert(0, str(REPO / "tools"))
from normalize import get_kst_window, parse_dt, KST  # noqa: E402
from trends import build_trends, build_weekly  # noqa: E402

CONTENT = REPO / "content"
INSIGHTS = CONTENT / "insights"
OUT = REPO / "docs" / "data"

# 7개 표준 사업부문
DIVISIONS = ["배터리·소재", "정유·트레이딩", "석유화학", "윤활유·기유", "E&P", "LNG", "전력·수소"]

# 부문별 허용 토픽(검증용). Design UI의 TOPICS와 일치시켜 유지한다.
TOPICS = {
    "배터리·소재": ["ESS·BESS", "EV 시장", "EV Maker·수주", "경쟁사", "정책·규제",
                 "생산·증설", "공급망", "소재", "기술·R&D", "실적·재무"],
    "정유·트레이딩": ["시황·마진", "저탄소 연료", "설비·운영", "트레이딩", "정책·규제", "실적·재무"],
    "석유화학": ["리사이클·순환경제", "고부가 소재", "시황·수급", "설비·운영", "정책·규제"],
    "윤활유·기유": ["기유·윤활유", "열관리·신사업", "시황", "실적·재무"],
    "E&P": ["탐사·개발", "생산·운영", "자산·포트폴리오", "정책·규제"],
    "LNG": ["도입·트레이딩", "인프라·터미널", "상류(가스전)", "발전"],
    "전력·수소": ["수소", "재생에너지·전력", "연료전지", "정책·규제"],
}

IMPORTANCE_RANK = {"high": 0, "mid": 1, "low": 2}

# 인덱스(검색)에 넣는 경량 필드 — summary/sources/highlight 제외
INDEX_FIELDS = ["id", "date", "division", "topic", "company", "importance",
                "impact_score", "publisherTier", "leadPublisher", "eventId",
                "title", "title_ko", "keywords", "tags", "entities"]

# 제목 꼬리표(매체명) 판별용 — 알려진 매체명 + 매체성 접미사
_KNOWN_PUB = {p.strip() for p in TIER_BY_NAME}
_PUB_SUFFIX = re.compile(
    r"(뉴스|경제|신문|일보|타임스|투데이|데일리|저널|미디어|방송|증권|이코노믹|비즈|위클리|"
    r"News|Times|Post|Journal|Media|Magazine|Insight|Wire|Daily|Report|Politics|Business|Global|"
    r"Inc\.?|Ltd\.?|\.com|\.kr|\.it|\.net)$", re.IGNORECASE)


def _looks_domain(x: str) -> bool:
    # 대소문자 무시 — 'Energy-Storage.News', 'Mining.com'처럼 대문자 섞인 도메인도 잡는다.
    return bool(re.match(r"^[\w.-]+\.[a-z]{2,}$", x.strip(), re.I)) and " " not in x.strip()


# 자주 나오거나 알아볼 만한 매체 도메인 → 표시명(국내 한글명 / 해외 정식명)
_DOMAIN_MAP = {
    # 국내
    "yna.co.kr": "연합뉴스", "mk.co.kr": "매일경제", "heraldcorp.com": "헤럴드경제",
    "sedaily.com": "서울경제", "asiae.co.kr": "아시아경제", "etoday.co.kr": "이투데이",
    "wowtv.co.kr": "한국경제TV", "g-enews.com": "글로벌이코노믹", "thelec.net": "디일렉",
    "zdnet.co.kr": "ZDNet Korea", "fnnews.com": "파이낸셜뉴스", "dt.co.kr": "디지털타임스",
    "businesspost.co.kr": "비즈니스포스트", "mt.co.kr": "머니투데이", "chosun.com": "조선일보",
    "ajunews.com": "아주경제", "newspim.com": "뉴스핌", "sportalkorea.com": "스포탈코리아",
    "itinsight.kr": "IT인사이트", "hansbiz.co.kr": "한스경제", "sentv.co.kr": "서울경제TV",
    "ceoscoredaily.com": "CEO스코어데일리", "seoulfn.com": "서울파이낸스",
    "koreajoongangdaily.com": "코리아중앙데일리", "e2news.com": "이투뉴스",
    "energydaily.co.kr": "에너지데일리", "energy-news.co.kr": "에너지신문",
    "smedaily.co.kr": "중소기업신문", "einfomax.co.kr": "연합인포맥스", "infomaxai.com": "연합인포맥스",
    "hankooki.com": "한국일보", "seoulfn.co.kr": "서울파이낸스", "chemlocus.co.kr": "케미컬로커스",
    "monthlymaritimekorea.com": "월간해양한국",
    # 해외
    "oilprice.com": "OilPrice", "electrek.co": "Electrek", "seekingalpha.com": "Seeking Alpha",
    "insideevs.com": "InsideEVs", "techtimes.com": "Tech Times", "tradingview.com": "TradingView",
    "apnews.com": "AP", "cnn.com": "CNN", "nature.com": "Nature", "phys.org": "Phys.org",
    "mining.com": "Mining.com", "energy-storage.news": "Energy Storage News", "ess-news.com": "ESS News",
    "lngprime.com": "LNG Prime", "energyintel.com": "Energy Intelligence",
    "naturalgasintel.com": "Natural Gas Intelligence", "chemorbis.com": "ChemOrbis", "icis.com": "ICIS",
    "freightwaves.com": "FreightWaves", "newscientist.com": "New Scientist",
    "interestingengineering.com": "Interesting Engineering", "economictimes.indiatimes.com": "Economic Times",
    "thenationalnews.com": "The National", "kedglobal.com": "KED Global", "bnamericas.com": "BNamericas",
    "indexbox.io": "IndexBox", "spartacommodities.com": "Sparta", "qcintel.com": "QC Intel",
    "mysteel.com": "Mysteel", "mysteel.net": "Mysteel", "baseoilnews.com": "Base Oil News",
    "fuelsandlubes.com": "Fuels & Lubes", "rigzone.com": "Rigzone", "worldoil.com": "World Oil",
    "ogj.com": "Oil & Gas Journal", "cnevpost.com": "CnEVPost", "carnewschina.com": "CarNewsChina",
    "datacenterdynamics.com": "DCD", "chinaspecialmetal.com": "China Special Metal",
    "notebookcheck.net": "Notebookcheck", "europesays.com": "Europe Says",
    # 국내 추가
    "hankyung.com": "한국경제", "theguru.co.kr": "더구루", "leadeconomy.co.kr": "리드경제",
    "econovill.com": "이코노믹리뷰", "dailyinvest.kr": "데일리인베스트", "impacton.net": "임팩트온",
    "newsworker.co.kr": "뉴스워커", "withnews.kr": "위드뉴스", "hankooki.com": "한국일보",
    "nate.com": "네이트뉴스", "heraldk.com": "헤럴드K",
    # 해외 추가 — 에너지·화학·배터리 전문지
    "cnbc.com": "CNBC", "iea.org": "IEA", "rystadenergy.com": "Rystad Energy",
    "chemanalyst.com": "ChemAnalyst", "electrive.com": "electrive", "ainvest.com": "AInvest",
    "plasticstoday.com": "PlasticsToday", "hydrocarbonengineering.com": "Hydrocarbon Engineering",
    "gasprocessingnews.com": "Gas Processing News", "grist.org": "Grist",
    "energytrend.com": "EnergyTrend", "conocophillips.com": "ConocoPhillips(보도자료)",
    "wastedive.com": "Waste Dive", "businessgreen.com": "BusinessGreen",
    "datacentremagazine.com": "Data Centre Magazine", "jkempenergy.com": "John Kemp Energy",
    "transportenvironment.org": "Transport & Environment", "carscoops.com": "Carscoops",
    "hydrogenfuelnews.com": "Hydrogen Fuel News", "investingnews.com": "Investing News Network",
    "benchmarkminerals.com": "Benchmark Minerals", "techxplore.com": "TechXplore",
    "hydrogen-central.com": "Hydrogen Central", "pv-magazine-usa.com": "pv magazine USA",
    "miningweekly.com": "Mining Weekly", "montelnews.com": "Montel", "boereport.com": "BOE Report",
    "gasoutlook.com": "Gas Outlook", "thenextweb.com": "The Next Web", "yahoo.com": "Yahoo",
    "indonesia-investments.com": "Indonesia Investments", "oilauthority.com": "Oil Authority",
    "energiesmedia.com": "Energies Media", "crugroup.com": "CRU", "general-index.com": "General Index",
    "thecooldown.com": "The Cool Down", "fortuneindia.com": "Fortune India",
    "mexicobusiness.news": "Mexico Business", "brazilenergyinsight.com": "Brazil Energy Insight",
    "energyreader.io": "Energy Reader", "lngpriceindex.com": "LNG Price Index",
    "thebuzzevnews.com": "The Buzz EV News", "evmagz.com": "EV Magz",
    "energymetalnews.com": "Energy Metal News", "sodiumbatteryhub.com": "Sodium Battery Hub",
    "advancedbiofuelsusa.info": "Advanced Biofuels USA", "bioenergytimes.com": "Bioenergy Times",
    "skillings.net": "Skillings Mining Review", "republicofmining.com": "Republic of Mining",
    "c-macc.com": "C-MACC", "coxautoinc.com": "Cox Automotive", "cfnmedianews.com": "CFN Media",
    "headtopics.com": "HeadTopics(수집매체)", "24chemicalresearch.com": "24ChemicalResearch",
}


# 표시명 매핑에 없는 도메인을 사람이 읽는 형태로 정리(예: battery-tech.net → Battery Tech)
_TLD_TAIL = re.compile(r"\.(com|net|org|io|info|news|co\.kr|kr|co|us|uk|cn|jp|eu|me|ai|tv|biz)$", re.I)


def _beautify_domain(d: str) -> str:
    s = _TLD_TAIL.sub("", d)
    if "." in s:                       # 남은 서브도메인 제거(news.einfomax → einfomax)
        s = s.split(".")[-1]
    parts = [p for p in re.split(r"[-_]", s) if p]
    return " ".join(p[:1].upper() + p[1:] for p in parts) or d


def _domain_name(d: str) -> str:
    """도메인 → 표시명. 맵에 있으면 매체명, 없으면 읽기 좋은 이름으로 정리."""
    key = d.strip().lower()
    for pre in ("www.", "en.", "biz.", "m.", "news.", "daily.", "source.", "autos.", "car."):
        if key.startswith(pre):
            key = key[len(pre):]
    if key in _DOMAIN_MAP:
        return _DOMAIN_MAP[key]
    # 서브도메인을 벗긴 상위 도메인으로 한 번 더 조회(bcinsight.crugroup.com → crugroup.com)
    parts = key.split(".")
    for i in range(1, len(parts) - 1):
        cand = ".".join(parts[i:])
        if cand in _DOMAIN_MAP:
            return _DOMAIN_MAP[cand]
    return _beautify_domain(key)


# 국내 매체가 영문명으로 들어오는 경우 + 같은 매체의 표기 흔들림 통합.
# (KED Global·Korea JoongAng Daily 등 실제 영문 매체는 그대로 둔다.)
_NAME_ALIAS = {
    "the herald business": "헤럴드경제", "herald business": "헤럴드경제",
    "the elec": "디일렉", "seoul economic daily": "서울경제",
    "yonhap news agency": "연합뉴스", "yonhap": "연합뉴스",
    "moneytoday": "머니투데이", "money today": "머니투데이",
    "maeil business news korea": "매일경제", "korea economic daily": "한국경제",
    "chosun biz": "조선비즈", "chosunbiz": "조선비즈",
    "qc intelligence": "QC Intel", "energy-storage.news": "Energy Storage News",
}


def clean_publisher(p: str) -> str:
    """대표 출처명 정규화: 'Name (domain)'·'domain (Name)' → 사람이 읽는 이름만 남김.

    Exa publisher 필드가 이름·도메인을 뒤섞어(예: '글로벌이코노믹 (g-enews.com)',
    'g-enews.com (글로벌이코노믹)', 'sportalkorea.com') 지저분하다. 괄호 안팎 중
    도메인처럼 생긴 쪽을 버리고 이름을 남긴다. 맨 도메인만 있으면 그대로 둔다.
    """
    p = (p or "").strip()
    m = re.match(r"^(.*?)\s*\(([^)]+)\)\s*$", p)
    if m:
        a, b = m.group(1).strip(), m.group(2).strip()
        if _looks_domain(b) and not _looks_domain(a):
            return a
        if _looks_domain(a) and not _looks_domain(b):
            return b
        return a or b
    if _looks_domain(p):          # 맨 도메인 → 매체명 매핑(없으면 접두어만 정리)
        return _domain_name(p)
    return _NAME_ALIAS.get(p.lower(), p)


def _is_publisher(tail: str, source_pubs: set[str]) -> bool:
    t = tail.strip().strip("'\"")
    if not t or len(t) > 24:
        return False
    return t in source_pubs or t in _KNOWN_PUB or bool(_PUB_SUFFIX.search(t))


def clean_title(title: str, source_pubs: set[str]) -> str:
    """스크랩 제목의 사이트 네비게이션·언론사 꼬리표 제거(표시용).

    Exa가 긁어온 원본 제목에 붙는 `... < 기사본문 - 매체`, `제목 | 언론사` 같은 꼬리표를
    떼어 기사 본문 제목만 남긴다. 꼬리가 (기사 출처 publisher | 알려진 매체 | 매체성 접미사)에
    해당할 때만 제거하므로 정상 제목을 훼손하지 않는다. content MD는 원본 유지(비파괴).
    """
    if not title:
        return title
    new = title
    # 1) breadcrumb 체인(< A < B < 기사본문 ...) — '기사본문' 신호가 있을 때만
    if "기사본문" in new and "<" in new:
        new = re.sub(r"\s*<\s.*$", "", new).strip()
    # 2) 트레일링 ' - 매체' / ' | 매체' (최대 2회)
    for _ in range(2):
        m = re.match(r"^(.+?)\s+[-|]\s+([^-|]{1,24})$", new)
        if m and _is_publisher(m.group(2), source_pubs):
            new = m.group(1).strip()
        else:
            break
    return new or title


def enrich_sources(art: dict) -> None:
    """출처 레지스트리로 각 source 등급 부여, blocklist 제거, 대표(lead) 선정.

    publisherTier(기사 최고 등급)·leadPublisher·leadUrl을 art에 추가한다. 등급은
    저장하지 않고 빌드 시 유도 → 레지스트리가 단일 진실원(config/sources.py).
    """
    kept = []
    for s in art.get("sources", []):
        t = tier_for(s.get("publisher"), s.get("url"))
        if t == 0:  # blocklist 제외
            continue
        s = {**s, "tier": t}
        kept.append(s)
    # 신뢰 등급 오름차순(Tier1 먼저) 정렬 → 첫 번째가 대표
    kept.sort(key=lambda s: s["tier"])
    art["sources"] = kept
    if kept:
        art["publisherTier"] = kept[0]["tier"]
        art["leadPublisher"] = clean_publisher(kept[0]["publisher"])
        art["leadUrl"] = kept[0]["url"]
    else:
        art["publisherTier"] = 3
        art["leadPublisher"] = ""
        art["leadUrl"] = ""


# ---------- front-matter 파서 (의존성 없이 자체 구현) ----------

_INLINE_OBJ = re.compile(r"\{(.+)\}")


def _parse_scalar(v: str):
    v = v.strip()
    if v.startswith("[") and v.endswith("]"):
        inner = v[1:-1].strip()
        if not inner:
            return []
        return [x.strip().strip('"') for x in inner.split(",") if x.strip()]
    if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
        # write_articles.q()가 값 안의 "를 \"로 저장하므로 읽을 때 원복
        return v[1:-1].replace('\\"', '"').replace("\\'", "'")
    if re.fullmatch(r"-?\d+", v):
        return int(v)
    return v


def _parse_inline_obj(s: str) -> dict:
    """{ publisher: "x", title: "y", url: "z" } 형태 파싱."""
    obj = {}
    for m in re.finditer(r'(\w+):\s*"((?:[^"\\]|\\.)*)"', s):
        obj[m.group(1)] = m.group(2).replace('\\"', '"')
    return obj


def parse_md(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        raise ValueError(f"front-matter 없음: {path}")
    _, fm, body = text.split("---", 2)
    art: dict = {"sources": []}
    lines = fm.strip("\n").split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        if line.startswith("sources:"):
            i += 1
            while i < len(lines) and lines[i].lstrip().startswith("-"):
                m = _INLINE_OBJ.search(lines[i])
                if m:
                    art["sources"].append(_parse_inline_obj(m.group(1)))
                i += 1
            continue
        m = re.match(r"^(\w+):\s*(.*)$", line)
        if m:
            art[m.group(1)] = _parse_scalar(m.group(2))
        i += 1
    art["summary"] = body.strip()
    return art


# ---------- 검증 ----------

def validate(art: dict, path: Path, warns: list[str]) -> None:
    div = art.get("division")
    if div not in DIVISIONS:
        warns.append(f"{path.name}: division 비표준 '{div}'")
    else:
        topic = art.get("topic")
        if topic and topic not in TOPICS.get(div, []):
            warns.append(f"{path.name}: topic '{topic}' ∉ {div} 허용목록")
    score = art.get("impact_score")
    if not isinstance(score, int) or not (1 <= score <= 10):
        warns.append(f"{path.name}: impact_score 범위 이탈 '{score}'")
    if art.get("importance") not in IMPORTANCE_RANK:
        warns.append(f"{path.name}: importance 비표준 '{art.get('importance')}'")


# ---------- 사안(타임라인) 그룹 ----------

def _auto_event_id(art: dict) -> str:
    """명시적 eventId가 없을 때 (부문·회사·토픽) 기준 합성 그룹키.

    실 파이프라인에서는 keywords/entities 겹침 + LLM으로 정교화하지만, 빌드 단계
    표시용으로는 이 결정론적 키가 충분하다. 회사가 없으면 (부문·토픽)으로 묶는다.
    """
    div = art.get("division", "")
    company = art.get("company", "")
    topic = art.get("topic", "")
    key = f"auto:{div}:{company}:{topic}"
    return key


def _issue_kind(eid: str, arts: list[dict]) -> str:
    """사건(event) vs 테마(theme) 판정.
    - 명시적 eventId(비 auto:) 또는 회사 앵커/ event_title 있으면 '사건'.
    - 회사 없이 (부문·토픽)으로만 묶인 자동 버킷은 '테마'(시황·주제 피드).
    """
    if not eid.startswith("auto:"):
        return "event"
    parts = eid.split(":")  # auto:div:company:topic
    company = parts[2] if len(parts) > 2 else ""
    if company.strip():
        return "event"
    return "theme"


def _split_theme(arts: list[dict]) -> list[list[dict]]:
    """거대 테마 버킷을 '대표 키워드' 기준 서브 스레드로 분할.
    각 기사를 그 기사 키워드 중 버킷 내 최다빈도 키워드(대표어)로 그룹핑 →
    '정제마진'·'크랙스프레드'·'호르무즈' 같은 갈래가 생긴다. 어디에도 안 붙는
    1건짜리는 버리지 않고 잔여(기타) 스레드로 모아 흐름 손실을 막는다.
    """
    freq: dict[str, int] = {}
    for a in arts:
        for k in a.get("keywords", []):
            freq[k] = freq.get(k, 0) + 1
    threads: dict[str, list[dict]] = defaultdict(list)
    for a in sorted(arts, key=lambda x: x["date"]):
        kws = a.get("keywords", [])
        pk = max(kws, key=lambda k: freq[k]) if kws else "기타"
        threads[pk].append(a)
    result: list[list[dict]] = []
    residual: list[dict] = []
    for group in threads.values():
        if len(group) >= 2:
            result.append(group)
        else:
            residual.extend(group)
    if len(residual) >= 2:
        result.append(sorted(residual, key=lambda x: x["date"]))
    elif residual and result:
        result[0].extend(residual)
        result[0].sort(key=lambda x: x["date"])
    elif residual:
        result.append(residual)
    return result


def _dominant_kw(arts: list[dict]) -> str:
    freq: dict[str, int] = {}
    for a in arts:
        for k in a.get("keywords", []):
            freq[k] = freq.get(k, 0) + 1
    return max(freq, key=freq.get) if freq else ""


def build_issues(articles: list[dict]) -> dict:
    """사안 그룹핑(2건 이상). 명시적 eventId 우선, 없으면 자동 클러스터링.
    사건/테마를 구분(kind)하고, 거대 테마 더미는 서브 스레드로 분할한다.
    """
    groups: dict[str, list[dict]] = defaultdict(list)
    for a in articles:
        eid = a.get("eventId") or _auto_event_id(a)
        groups[eid].append(a)

    issues = {}
    for eid, arts in groups.items():
        kind = _issue_kind(eid, arts)
        # 거대 테마(6건+)는 서브 스레드로 분할, 그 외는 통째
        subgroups = _split_theme(arts) if (kind == "theme" and len(arts) >= 6) else [arts]
        for si, sub in enumerate(subgroups):
            if len(sub) < 2:
                continue
            arts_sorted = sorted(sub, key=lambda x: x["date"])
            divs = [a["division"] for a in arts_sorted]
            div = max(set(divs), key=divs.count)
            topic = arts_sorted[-1].get("topic", "")
            kws: list[str] = []
            for a in arts_sorted:
                for k in a.get("keywords", []):
                    if k not in kws:
                        kws.append(k)
            key = eid if len(subgroups) == 1 else f"{eid}#{si}"
            if kind == "event":
                label = arts_sorted[-1].get("event_title") or arts_sorted[-1].get("title_ko") or arts_sorted[-1]["title"]
            else:  # 테마: 한 기사 제목을 빌리지 않고 토픽(+대표 키워드)으로 정직하게
                dom = _dominant_kw(arts_sorted)
                label = f"{topic} · {dom}" if (dom and dom != topic) else (topic or div)
            issues[key] = {
                "eventId": key,
                "kind": kind,
                "label": label,
                "division": div,
                "keywords": kws[:8],
                "articleIds": [a["id"] for a in arts_sorted],
                "firstDate": arts_sorted[0]["date"],
                "lastDate": arts_sorted[-1]["date"],
                "count": len(arts_sorted),
                "peakImpact": max((a.get("impact_score", 0) or 0) for a in arts_sorted),
            }
    return issues


# ---------- 인사이트(오늘의 브리핑) ----------

def parse_insight(path: Path) -> dict:
    """인사이트 MD 파싱. 선택적 front-matter(pick·division·title) + 본문 마크다운."""
    text = path.read_text(encoding="utf-8")
    meta = {"pickId": None, "division": None, "title": None, "backfill": False}
    body = text
    if text.startswith("---"):
        _, fm, body = text.split("---", 2)
        for key, field in (("pick", "pickId"), ("division", "division"), ("title", "title")):
            m = re.search(rf"^{key}:\s*(.+)$", fm, re.MULTILINE)
            if m:
                v = m.group(1).strip()
                if len(v) >= 2 and v[0] == '"' and v[-1] == '"':
                    v = v[1:-1]
                meta[field] = v.replace('\\"', '"').replace("\\'", "'")
        bm = re.search(r"^backfill:\s*(\S+)$", fm, re.MULTILINE)
        if bm:
            meta["backfill"] = bm.group(1).strip().lower() in ("true", "1", "yes")
    return {"date": path.stem, **meta, "markdown": body.strip()}


def build_insights() -> tuple[list[dict], dict[str, dict]]:
    """content/insights/*.md → (목록 인덱스, 월별 샤드).

    인덱스는 날짜·PICK·부문·한줄제목만(경량). 본문은 월 샤드에 담아 지연 로드.
    """
    if not INSIGHTS.exists():
        return [], {}
    index: list[dict] = []
    by_month: dict[str, dict] = defaultdict(dict)
    for path in sorted(INSIGHTS.glob("*.md")):
        ins = parse_insight(path)
        d = ins["date"]
        index.append({k: ins[k] for k in ("date", "pickId", "division", "title", "backfill")})
        by_month[d[:7]][d] = ins
    index.sort(key=lambda x: x["date"], reverse=True)
    return index, by_month


def today_insight_from(by_month: dict[str, dict], date_str: str) -> dict | None:
    if not date_str:
        return None
    day = by_month.get(date_str[:7], {}).get(date_str)
    if not day:
        return None
    return {"pickId": day["pickId"], "markdown": day["markdown"]}


def pick_today_insight(by_month: dict[str, dict], date_str: str,
                       today_cards: list[dict]) -> dict | None:
    """오늘 브리핑에 붙일 인사이트 선정.

    루틴이 인사이트 파일명 날짜를 브리핑 창과 어긋나게(±1일) 저장하는 경우가 있어
    파일명 날짜로만 매칭하면 인사이트가 누락된다. 그래서 **PICK 기사가 오늘 창(cards)
    안에 있는 인사이트**를 우선 붙인다(가장 최신). 없으면 파일명 날짜로 폴백.
    """
    card_ids = {a.get("id") for a in today_cards}
    all_ins = [ins for month in by_month.values() for ins in month.values()]
    inwin = [i for i in all_ins if i.get("pickId") in card_ids and i.get("markdown")]
    if inwin:
        chosen = max(inwin, key=lambda i: i.get("date", ""))
        return {"pickId": chosen["pickId"], "markdown": chosen["markdown"]}
    return today_insight_from(by_month, date_str)


def check_pick_credibility(insight: dict | None, articles: list[dict],
                           warns: list[str]) -> None:
    """오늘의 PICK이 저신뢰(tier3) 단독 출처 기사면 경고.

    PICK은 하루 브리핑의 대표 기사라 미검증 매체 단독 보도가 PICK이 되면 신뢰도
    리스크가 크다. blocklist(tier0)는 normalize 단계에서 걸러지지만 tier3(미등록
    매체 기본값)는 통과하므로 여기서 사후 검출한다. 프롬프트 Step 5 규칙과 짝을 이룬다.
    """
    if not insight or not insight.get("pickId"):
        return
    pid = insight["pickId"]
    art = next((a for a in articles if a.get("id") == pid), None)
    if art is None:
        warns.append(f"PICK 기사 '{pid}' 미발견 — 인사이트 pick id 확인 필요")
        return
    if art.get("publisherTier") == 3 and len(art.get("sources", [])) <= 1:
        lead = art.get("leadPublisher") or "미상"
        warns.append(
            f"PICK 기사가 저신뢰 단독 출처 (tier3·{lead}, id={pid}) "
            f"— 교차검증 매체 확보 또는 PICK 재선정 권장")


def collection_health(articles: list[dict], brief_date: str, today_cards: list[dict]) -> dict:
    """수집량이 평소보다 급감했는지 판정.

    2026-08-05~09에 7개 부문이 일제히 44~78% 줄어든 적이 있다. 뉴스가 없어서가 아니라
    수집 단계가 조용히 실패했는데(같은 시간대 Exa에는 기사가 그대로 있었다), 파이프라인은
    아무 신호 없이 3~5건짜리 브리핑을 커밋했다. 그래서 코드 쪽에 경보를 둔다.

    주말은 원래 절반 수준이라 같은 요일 유형(평일/주말)끼리만 비교한다.
    """
    from datetime import date as _date
    try:
        d0 = _date.fromisoformat(brief_date)
    except Exception:
        return {}
    is_weekend = d0.weekday() >= 5
    today_count = len(today_cards)

    per_day: dict[str, int] = defaultdict(int)
    for a in articles:
        per_day[a["date"]] += 1
    base = []
    for ds, n in per_day.items():
        try:
            d = _date.fromisoformat(ds)
        except Exception:
            continue
        if d >= d0 or (d0 - d).days > 28:
            continue
        if (d.weekday() >= 5) == is_weekend:
            base.append(n)
    if len(base) < 4:
        return {}
    base.sort()
    med = base[len(base) // 2]
    ratio = today_count / med if med else 1.0

    # 총량만 보면 '적다'까지밖에 모른다. 어느 부문이 통째로 비었는지가 원인 추적의 실마리다
    # (8/7은 7개 부문 중 6개가 0건이었고, 그게 수집 실패라는 가장 강한 신호였다).
    div_hist: dict[str, list[int]] = defaultdict(list)
    for ds, per_div in sorted(_by_day_div(articles).items()):
        try:
            d = _date.fromisoformat(ds)
        except Exception:
            continue
        if d >= d0 or (d0 - d).days > 28 or (d.weekday() >= 5) != is_weekend:
            continue
        for dv in DIVISIONS:
            div_hist[dv].append(per_div.get(dv, 0))
    # 오늘 분포는 반드시 today_cards(= 브리핑 수집 창)에서 센다. 달력 날짜로 세면
    # 창이 이틀에 걸쳐 있어 모수가 달라지고, 아직 안 끝난 오늘 날짜라 대부분 0으로 잡힌다.
    today_div: dict[str, int] = defaultdict(int)
    for c in today_cards:
        today_div[c["division"]] += 1
    silent = []
    for dv, hist in div_hist.items():
        if len(hist) < 4:
            continue
        hist.sort()
        m = hist[len(hist) // 2]
        if m >= 1 and today_div.get(dv, 0) == 0:
            silent.append(dv)

    return {"count": today_count, "median": med, "ratio": round(ratio, 2),
            "low": ratio < 0.45 or len(silent) >= 4,
            "silentDivisions": silent,
            "dayType": "주말" if is_weekend else "평일"}


def _by_day_div(articles: list[dict]) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for a in articles:
        out[a["date"]][a["division"]] += 1
    return out


def main() -> int:
    if not CONTENT.exists():
        print(f"[ERR] content 없음: {CONTENT}", file=sys.stderr)
        return 1
    OUT.mkdir(parents=True, exist_ok=True)

    articles: list[dict] = []
    warns: list[str] = []
    for path in sorted(CONTENT.rglob("*.md")):
        if INSIGHTS in path.parents:
            continue
        art = parse_md(path)
        enrich_sources(art)
        pubs = {s.get("publisher", "") for s in art.get("sources", [])}
        if art.get("title"):
            art["title"] = clean_title(art["title"], pubs)
        if art.get("title_ko"):
            art["title_ko"] = clean_title(art["title_ko"], pubs)
        validate(art, path, warns)
        articles.append(art)

    # 최신순 정렬
    articles.sort(key=lambda a: (a["date"], a.get("impact_score", 0)), reverse=True)

    # 1) index.json (경량)
    index = [{k: a.get(k) for k in INDEX_FIELDS if k in a} for a in articles]
    (OUT / "index.json").write_text(
        json.dumps(index, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    # 2) 월별 샤드 (상세)
    by_month: dict[str, list[dict]] = defaultdict(list)
    for a in articles:
        by_month[a["date"][:7]].append(a)
    for month, arts in by_month.items():
        (OUT / f"{month}.json").write_text(
            json.dumps(arts, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    # 3) issues.json
    issues = build_issues(articles)
    (OUT / "issues.json").write_text(
        json.dumps(issues, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    # 4) 인사이트 아카이브 (목록 인덱스 + 월샤드)
    ins_index, ins_by_month = build_insights()
    (OUT / "insights_index.json").write_text(
        json.dumps(ins_index, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    ins_dir = OUT / "insights"
    ins_dir.mkdir(exist_ok=True)
    for month, days in ins_by_month.items():
        (ins_dir / f"{month}.json").write_text(
            json.dumps(days, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    # 5) today.json (브리핑) — 수집 창(전날 08:00~오늘 08:00 KST)에 '발행'된 기사.
    #    달력 날짜가 아니라 발행 시각 기준이라, 오전 8시 이후 뉴스가 사각지대로 빠지지 않는다.
    latest_date = articles[0]["date"] if articles else None  # manifest용(아카이브 최신 발행일)
    win_start, win_end = get_kst_window()

    def _in_window(a: dict) -> bool:
        dt = parse_dt(a.get("publishedAt"))
        return dt is not None and win_start <= dt < win_end

    brief_date = win_end.astimezone(KST).strftime("%Y-%m-%d")  # 브리핑 기준일 = 창 끝(오늘)
    today_cards = [a for a in articles if _in_window(a)]
    if not today_cards and articles:  # 폴백: 발행시각 미보유(이관 전 기존 데이터) → 최신 발행일
        brief_date = latest_date
        today_cards = [a for a in articles if a["date"] == latest_date]
    insight = pick_today_insight(ins_by_month, brief_date, today_cards)
    check_pick_credibility(insight, articles, warns)
    health = collection_health(articles, brief_date, today_cards)
    if health.get("low"):
        sil = health.get("silentDivisions") or []
        warns.append(
            f"수집량 급감 — 오늘 {health['count']}건 vs 최근 {health['dayType']} 중앙값 "
            f"{health['median']}건 ({int(health['ratio'] * 100)}%)."
            + (f" 평소 기사가 있는데 오늘 0건인 부문: {', '.join(sil)}." if sil else "")
            + " Exa 수집이 조용히 실패했을 수 있습니다 → python tools/collect_check.py 로 확인하세요.")
    today = {"date": brief_date, "cards": today_cards, "insight": insight, "health": health,
             "window": {"startKST": win_start.astimezone(KST).isoformat(),
                        "endKST": win_end.astimezone(KST).isoformat()}}
    (OUT / "today.json").write_text(
        json.dumps(today, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    # 6) trends.json / weekly.json — 트렌드·경쟁사 비교·주간 리포트용 시계열 집계
    trends = build_trends(articles, latest_date)
    (OUT / "trends.json").write_text(
        json.dumps(trends, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    weekly = build_weekly(articles, issues, ins_index, trends, latest_date)
    (OUT / "weekly.json").write_text(
        json.dumps(weekly, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    # 7) manifest.json (집계)
    div_counts: dict[str, int] = defaultdict(int)
    for a in articles:
        div_counts[a["division"]] += 1
    manifest = {
        "total": len(articles),
        "months": sorted(by_month.keys(), reverse=True),
        "divisions": {d: div_counts.get(d, 0) for d in DIVISIONS},
        "latestDate": latest_date,
        "issueCount": len(issues),
        "insightCount": len(ins_index),
    }
    (OUT / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[OK] {len(articles)}건 · 월샤드 {len(by_month)}개 · 사안 {len(issues)}개 → {OUT}/")
    if warns:
        print(f"[WARN] 검증 경고 {len(warns)}건:", file=sys.stderr)
        for w in warns:
            print(f"  - {w}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
