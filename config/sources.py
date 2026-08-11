"""출처 신뢰도 레지스트리 (코드 기반, LLM 미사용).

뉴스 매체를 등급으로 분류한다. Exa 수집 단계에서 includeDomains/excludeDomains로,
빌드/정규화 단계에서 대표 출처(lead) 선정과 정렬 가중치로 사용한다.

등급:
  1 = Tier1  통신사·주요 경제/산업지 (최고 신뢰)
  2 = Tier2  일반 일간지·업계 전문지
  3 = Tier3  기타(레지스트리 미등록 기본값)
  0 = Blocklist  보도자료 재배포·광고성·저품질 (수집 제외)

매체명(한글/영문)과 도메인 둘 다로 조회 가능. 실서비스에서는 도메인 우선.
"""
from __future__ import annotations

# 매체명 → 등급
TIER_BY_NAME: dict[str, int] = {
    # Tier1 — 통신사·주요 경제/산업지·종합일간지(조선·중앙·동아)
    "연합뉴스": 1, "뉴시스": 1, "뉴스1": 1,
    "한국경제": 1, "매일경제": 1, "조선비즈": 1, "조선일보": 1, "전자신문": 1,
    "서울경제": 1, "파이낸셜뉴스": 1, "헤럴드경제": 1,
    "중앙일보": 1, "동아일보": 1,
    # Tier2 — 종합일간지·영자지
    "세계일보": 2, "코리아헤럴드": 2, "코리아타임스": 2,
    # Tier1 — 글로벌 통신·주요 경제지
    "로이터": 1, "Reuters": 1, "블룸버그": 1, "Bloomberg": 1,
    "Financial Times": 1, "The Wall Street Journal": 1, "Wall Street Journal": 1,
    "Nikkei": 1, "닛케이": 1, "CNBC": 1, "AP": 1, "Associated Press": 1,
    "S&P Global": 1, "S&P Global Platts": 1, "Platts": 1, "ICIS": 1, "Argus": 1,
    # Tier2 — 일반 일간지·업계 전문지·경제방송
    "이데일리": 2, "아주경제": 2, "머니투데이": 2, "디지털타임스": 2,
    "ZDNet Korea": 2, "에너지경제": 2, "더벨": 2, "이투데이": 2,
    "뉴스핌": 2, "비즈니스포스트": 2, "아시아경제": 2, "문화일보": 2,
    "디일렉": 2, "한국경제TV": 2, "서울경제TV": 2, "데일리한국": 2,
    "연합인포맥스": 2, "이뉴스투데이": 2,
    # Tier2 — 해외 상품·에너지 전문지
    "OilPrice": 2, "Oilprice.com": 2, "Upstream": 2, "Hart Energy": 2,
}

# 도메인 → 등급 (실서비스 Exa 결과의 host 기준)
# 서브도메인(en.·biz.·news.·www1. 등)은 tier_for가 왼쪽 라벨을 벗겨가며 재조회하므로
# 등록 도메인(예: chosun.com)만 넣으면 en.chosun.com·biz.chosun.com 등도 함께 매칭된다.
TIER_BY_DOMAIN: dict[str, int] = {
    # --- 국내 Tier1: 통신사·주요 경제/산업지·종합일간지 ---
    "yna.co.kr": 1, "hankyung.com": 1, "mk.co.kr": 1,
    "chosun.com": 1, "biz.chosun.com": 1,
    "etnews.com": 1, "sedaily.com": 1, "fnnews.com": 1, "heraldcorp.com": 1,
    "newsis.com": 1, "news1.kr": 1,
    "joongang.co.kr": 1, "donga.com": 1,  # 중앙·동아(종합일간지 빅3)
    # --- 해외 Tier1: 글로벌 통신·주요 경제지·상품/에너지 전문지·최상위 학술지 ---
    "reuters.com": 1, "bloomberg.com": 1, "ft.com": 1, "wsj.com": 1,
    "nikkei.com": 1, "asia.nikkei.com": 1, "cnbc.com": 1, "apnews.com": 1,
    "spglobal.com": 1, "icis.com": 1, "argusmedia.com": 1,
    "nature.com": 1,  # 최상위 학술지(전고체·소재 연구 원문)
    "upi.com": 1, "fastmarkets.com": 1,  # UPI(통신사)·Fastmarkets(가격보고기관)
    # --- 국내 Tier2: 일반 일간지·영자지·업계 전문지·경제방송 ---
    "edaily.co.kr": 2, "ajunews.com": 2, "ajupress.com": 2, "mt.co.kr": 2,
    "dt.co.kr": 2, "zdnet.co.kr": 2, "ekn.kr": 2, "thebell.co.kr": 2,
    "etoday.co.kr": 2, "newspim.com": 2, "businesspost.co.kr": 2,
    "asiae.co.kr": 2, "munhwa.com": 2, "thelec.kr": 2, "thelec.net": 2,
    "wowtv.co.kr": 2, "sentv.co.kr": 2, "hankooki.com": 2, "einfomax.co.kr": 2,
    "enewstoday.co.kr": 2, "g-enews.com": 2,
    "segye.com": 2, "econovill.com": 2,          # 세계일보·이코노믹리뷰
    "koreaherald.com": 2, "koreatimes.co.kr": 2,  # 국내 영자지
    "kedglobal.com": 2, "businesskorea.co.kr": 2,  # 한경 영문판·비즈니스코리아
    "batterydaily.co.kr": 2, "dongascience.com": 2, "digitaltoday.co.kr": 2,  # 배터리데일리·동아사이언스·디지털투데이
    # --- 해외 Tier2: 상품·에너지 전문 매체 ---
    "oilprice.com": 2, "upstreamonline.com": 2, "hartenergy.com": 2,
    "rigzone.com": 2, "spglobalcommodityinsights.com": 2,
    # --- 해외 Tier2: 주요 지역지·과학지·배터리/에너지 전문지 ---
    "scmp.com": 2, "irishtimes.com": 2, "newscientist.com": 2,
    "economictimes.indiatimes.com": 2,            # 인도 이코노믹타임스
    "pv-magazine.com": 2, "pv-magazine-usa.com": 2,
    "energy-storage.news": 2, "cnevpost.com": 2,  # ESS·중국EV 전문지
    "ess-news.com": 2, "utilitydive.com": 2, "canarymedia.com": 2,  # 에너지/전력 트레이드
    # 정통 EV·자동차 전문 매체
    "electrek.co": 2, "electrive.com": 2, "insideevs.com": 2,
    "thedriven.io": 2, "chargedevs.com": 2, "automotiveworld.com": 2,
    # 과학·기술 매체·경제지
    "techxplore.com": 2, "newatlas.com": 2, "thenextweb.com": 2, "fortuneindia.com": 2,
}

# 수집 제외 도메인(보도자료 재배포·광고성 예시). 필요 시 확장.
BLOCKLIST_DOMAINS: set[str] = {
    "prnewswire.com", "businesswire.com",
}

DEFAULT_TIER = 3


def domain_of(url: str | None) -> str | None:
    if not url:
        return None
    m = url.split("//", 1)[-1].split("/", 1)[0].lower()
    return m[4:] if m.startswith("www.") else m


def _domain_candidates(dom: str) -> list[str]:
    """host를 왼쪽 라벨부터 하나씩 벗겨 등록 도메인 후보를 만든다.

    en.sedaily.com → [en.sedaily.com, sedaily.com]
    news.mt.co.kr  → [news.mt.co.kr, mt.co.kr, co.kr]
    마지막 2개 라벨(co.kr 같은 공용접미사)까지 내려가지만, 그 후보가 레지스트리에
    없으면 매칭되지 않으므로 오탐 위험은 없다(등록된 실도메인만 히트).
    """
    parts = dom.split(".")
    return [".".join(parts[i:]) for i in range(len(parts) - 1)]


def tier_for(publisher: str | None = None, url: str | None = None) -> int:
    """매체명·URL로 신뢰 등급 조회. 0=blocklist, 1/2/3.

    도메인은 정확히 일치하지 않아도 서브도메인을 벗겨가며 재조회한다
    (en.chosun.com·biz.chosun.com·www1.edaily.co.kr 등도 매칭).
    """
    dom = domain_of(url)
    if dom:
        for cand in _domain_candidates(dom):
            if cand in BLOCKLIST_DOMAINS:
                return 0
            if cand in TIER_BY_DOMAIN:
                return TIER_BY_DOMAIN[cand]
    if publisher and publisher in TIER_BY_NAME:
        return TIER_BY_NAME[publisher]
    return DEFAULT_TIER


def credibility_weight(tier: int) -> float:
    """정렬 가중치. Tier1이 가장 높음, blocklist는 0."""
    return {0: 0.0, 1: 1.0, 2: 0.7, 3: 0.4}.get(tier, 0.4)


# Exa includeDomains에 넘길 신뢰 도메인 화이트리스트(Tier1+2)
TRUSTED_DOMAINS: list[str] = sorted(TIER_BY_DOMAIN.keys())
