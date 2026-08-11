#!/usr/bin/env python3
"""Exa 수집 원본 → 정규화(중복제거·날짜필터·신뢰출처).  LLM 미사용(코드 전용).

파이프라인 3단계. Exa가 부문별로 긁어온 후보 기사들을 받아:
  1) KST 시간창(전날 08:00~오늘 08:00) 밖 기사 제외
  2) blocklist 출처 제거 + 각 출처 신뢰등급 부여(config/sources.py)
  3) 3갈래 중복제거:
     - MERGE(당일)   : 같은 사안을 여러 매체가 다룬 것 → 카드 1개로 묶고 sources[] 합침
     - 최근 아카이브 대조 : 최근 N일 index.json과 비교해 기존 기사와의 유사도 계산
       · action=MERGE_EXISTING : 기존 기사에 출처만 추가(사실상 동일)
       · action=SIMILAR        : 유사 → LINK(새 전개)/DROP(우려먹기) 판단은 LLM 단계로 위임
       · action=NEW            : 신규 사안

출력(JSON)은 LLM 분류 단계가 그대로 소비한다. 실제 LINK/DROP 확정과 분류·요약은
다음 단계(루틴의 Claude)가 수행한다.

사용:
  python tools/normalize.py --raw /tmp/exa_raw.json --index docs/data/index.json \
      --out /tmp/normalized.json [--recent-days 21] [--now 2026-07-13T00:05:00Z]

raw 스키마(부문별 Exa 결과를 합친 배열):
  [{ "division": "...", "title": "...", "url": "...", "publishedAt": "ISO8601",
     "snippet": "...", "publisher": "(선택)" }, ...]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from config.sources import tier_for, domain_of, credibility_weight  # noqa: E402
from config.divisions import DIVISIONS  # noqa: E402

KST = timezone(timedelta(hours=9))

# 중복 판정 임계값
DUP_SIM = 0.85     # 이 이상 → 동일 기사(MERGE)
SIMILAR_SIM = 0.50  # 이 이상(DUP 미만) → 유사(LLM이 LINK/DROP 판정)

# 코드 측 개체 사전 — 매칭·태깅용(모든 부문 계열사+관찰대상+SK 제품/브랜드)
_ENTITIES: list[str] = []
for _d in DIVISIONS.values():
    for _k in ("sk_affiliates", "watch", "watch_materials", "products"):
        _ENTITIES.extend(_d.get(_k, []))
_ENTITIES = sorted(set(_ENTITIES), key=len, reverse=True)

# KO↔EN 개체 별칭 — 영어 쿼리 도입에 따른 교차언어 태깅·병합용. 값=대표(한국어) 개체.
# 한/영 기사 모두 같은 대표 개체로 태깅돼 아카이브 대조(match_recent)와 LLM Step 3
# 병합을 돕는다. 대소문자 구분(예: 'Kia'≠'Nokia')으로 부분일치 오탐을 줄인다.
_ENTITY_ALIASES: dict[str, str] = {
    "SK On": "SK온", "SKON": "SK온",
    "Samsung SDI": "삼성SDI",
    "LG Energy Solution": "LG에너지솔루션", "LGES": "LG에너지솔루션",
    "SK IE Technology": "SK아이이테크놀로지",
    "Hyundai": "현대차", "Kia": "기아", "Ford": "포드",
    "Volkswagen": "폭스바겐", "Tesla": "테슬라", "Panasonic": "파나소닉",
    "SK Energy": "SK에너지", "SK Incheon": "SK인천석유화학",
    "SK Geo Centric": "SK지오센트릭", "SKGC": "SK지오센트릭",
    "SK Enmove": "SK엔무브", "SK Earthon": "SK어스온",
    "S-Oil": "에쓰오일", "S-OIL": "에쓰오일",
    "GS Caltex": "GS칼텍스", "Hyundai Oilbank": "현대오일뱅크",
    "Lotte Chemical": "롯데케미칼", "LG Chem": "LG화학", "Hanwha Solutions": "한화솔루션",
    "KEPCO": "한국전력", "Doosan Fuel Cell": "두산퓨얼셀", "Bloom Energy": "블룸에너지",
}
_ALIAS_KEYS = sorted(_ENTITY_ALIASES, key=len, reverse=True)


# ---------- 시간창 ----------

def get_kst_window(now: datetime | None = None) -> tuple[datetime, datetime]:
    """오늘 기준 수집 창(KST): 전날 08:00 ~ 오늘 08:00. UTC-aware 반환."""
    now = (now or datetime.now(timezone.utc)).astimezone(KST)
    end = now.replace(hour=8, minute=0, second=0, microsecond=0)
    if now < end:  # 오전 8시 전 실행이면 창을 하루 당김
        end -= timedelta(days=1)
    start = end - timedelta(days=1)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    s = s.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        try:
            dt = datetime.strptime(s[:10], "%Y-%m-%d")
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


# ---------- 텍스트 유사도 ----------

_BRACKET = re.compile(r"\[[^\]]*\]|\([^)]*\)")
_NONWORD = re.compile(r"[^\w가-힣]+")
_STOP = {"단독", "속보", "종합", "그룹", "기자", "부문"}


def title_tokens(title: str) -> set[str]:
    t = _BRACKET.sub(" ", title or "").lower()
    t = _NONWORD.sub(" ", t)
    return {w for w in t.split() if len(w) >= 2 and w not in _STOP}


def jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def detect_entities(text: str) -> list[str]:
    """개체 탐지. config 원표기 + KO↔EN 별칭(대표 개체로 승격)."""
    text = text or ""
    found: list[str] = []
    for e in _ENTITIES:
        if e in text and e not in found:
            found.append(e)
    for k in _ALIAS_KEYS:  # 영어/약어 표층형 → 대표(한국어) 개체
        if k in text:
            c = _ENTITY_ALIASES[k]
            if c not in found:
                found.append(c)
    return found


def kst_date(dt: datetime | None) -> str:
    return dt.astimezone(KST).strftime("%Y-%m-%d") if dt else ""


# ---------- 출처 정규화 ----------

def to_source(cand: dict) -> dict | None:
    """후보 → source dict(등급 부여). blocklist(tier 0)면 None."""
    url = cand.get("url", "")
    pub = cand.get("publisher") or domain_of(url) or ""
    tier = tier_for(pub, url)
    if tier == 0:
        return None
    return {"publisher": pub, "title": cand.get("title", ""), "url": url, "tier": tier}


# ---------- 당일 클러스터링(MERGE) ----------

def same_story(atoks: set, aent: set, btoks: set, bent: set) -> bool:
    """같은 사안 여부. 제목 매우 유사하거나, (제목 어느정도 유사 + 개체 공유 + 핵심어 다수 공유).

    한국어 헤드라인은 표현 차이(박차/가속화/가속)로 순수 제목 Jaccard가 낮아지므로,
    같은 주체(개체)와 다수의 공통 핵심어가 있으면 낮은 임계에서도 병합한다.
    """
    j = jaccard(atoks, btoks)
    if j >= DUP_SIM:
        return True
    if j >= 0.55 and len(aent & bent) >= 1 and len(atoks & btoks) >= 3:
        return True
    return False


def cluster_today(cands: list[dict]) -> list[dict]:
    """URL·제목·개체로 같은 사안을 묶는다(그리디). 각 클러스터=카드 후보 1개."""
    clusters: list[dict] = []
    for c in cands:
        toks = title_tokens(c.get("title", ""))
        ent = set(detect_entities(f"{c.get('title','')} {c.get('snippet','')}"))
        url = c.get("url", "")
        placed = False
        for cl in clusters:
            same_url = url and url in cl["_urls"]
            if same_url or same_story(toks, ent, cl["_tokens"], cl["_ent"]):
                cl["_members"].append(c)
                cl["_tokens"] |= toks
                cl["_ent"] |= ent
                if url:
                    cl["_urls"].add(url)
                placed = True
                break
        if not placed:
            clusters.append({"_members": [c], "_tokens": set(toks), "_ent": set(ent),
                             "_urls": {url} if url else set()})
    return clusters


def finalize_cluster(cl: dict, tmp_id: str) -> dict | None:
    """클러스터 → 카드 후보(대표 제목·병합 스니펫·등급정렬 sources)."""
    sources, seen = [], set()
    latest = None
    div_votes: dict[str, int] = {}
    snippet = ""
    for m in cl["_members"]:
        s = to_source(m)
        if not s or s["url"] in seen:
            continue
        seen.add(s["url"])
        sources.append(s)
        dt = parse_dt(m.get("publishedAt"))
        if dt and (latest is None or dt > latest):
            latest = dt
        div_votes[m.get("division", "")] = div_votes.get(m.get("division", ""), 0) + 1
        sn = (m.get("snippet") or "").strip()
        if len(sn) > len(snippet):
            snippet = sn
    if not sources:  # 전부 blocklist
        return None
    sources.sort(key=lambda s: s["tier"])
    title = sources[0]["title"]
    division = max(div_votes, key=div_votes.get) if div_votes else ""
    entities = detect_entities(f"{title} {snippet}")
    return {
        "tmpId": tmp_id,
        "division": division,
        "title": title,
        "snippet": snippet[:600],
        "publishedAt": latest.isoformat() if latest else None,
        "date": kst_date(latest),
        "entities": entities,
        "sources": sources,
        "publisherTier": sources[0]["tier"],
        "credibility": round(max(credibility_weight(s["tier"]) for s in sources), 2),
    }


# ---------- 최근 아카이브 대조 ----------

def load_recent_index(index_path: Path, recent_days: int, ref: datetime) -> list[dict]:
    if not index_path.exists():
        return []
    idx = json.loads(index_path.read_text(encoding="utf-8"))
    cutoff = (ref.astimezone(KST) - timedelta(days=recent_days)).strftime("%Y-%m-%d")
    out = []
    for a in idx:
        if a.get("date", "") >= cutoff:
            a = dict(a)
            a["_tokens"] = title_tokens(a.get("title", ""))
            a["_ent"] = set(a.get("entities", []))
            out.append(a)
    return out


def match_recent(card: dict, recent: list[dict]) -> dict:
    """기존 기사와 최고 유사도 매칭 → action 제안."""
    best, best_sim = None, 0.0
    ctoks = title_tokens(card["title"])
    cent = set(card.get("entities", []))
    for a in recent:
        sim = jaccard(ctoks, a["_tokens"])
        ent_ov = len(cent & a["_ent"])
        # 개체가 겹치면 유사도 소폭 가산(같은 주체의 후속일 가능성)
        score = sim + (0.1 if ent_ov >= 1 else 0.0)
        if score > best_sim:
            best_sim, best = score, a
    if best is None:
        return {"action": "NEW", "articleId": None, "similarity": 0.0, "eventId": None}
    if best_sim >= DUP_SIM:
        action = "MERGE_EXISTING"
    elif best_sim >= SIMILAR_SIM:
        action = "SIMILAR"  # LINK/DROP는 LLM이 판단
    else:
        return {"action": "NEW", "articleId": None, "similarity": round(best_sim, 2), "eventId": None}
    return {"action": action, "articleId": best.get("id"),
            "similarity": round(best_sim, 2), "eventId": best.get("eventId"),
            "existingTitle": best.get("title")}


# ---------- 메인 ----------

def normalize(raw: list[dict], recent: list[dict], window: tuple[datetime, datetime]) -> dict:
    start, end = window
    dropped = {"out_of_window": 0, "blocklisted": 0, "no_date": 0}
    kept = []
    for c in raw:
        dt = parse_dt(c.get("publishedAt"))
        if dt is None:
            dropped["no_date"] += 1
            continue
        if not (start <= dt < end):
            dropped["out_of_window"] += 1
            continue
        if to_source(c) is None:  # 단일 출처가 blocklist면 후보에서 제외
            dropped["blocklisted"] += 1
            continue
        kept.append(c)

    clusters = cluster_today(kept)
    cards = []
    for i, cl in enumerate(clusters, 1):
        card = finalize_cluster(cl, f"c{i}")
        if not card:
            continue
        card["match"] = match_recent(card, recent)
        card["suggested_action"] = card["match"]["action"]
        cards.append(card)

    # 신뢰도·최신순 정렬
    cards.sort(key=lambda c: (c["credibility"], c.get("date", "")), reverse=True)
    return {
        "window": {"startUTC": start.isoformat(), "endUTC": end.isoformat(),
                   "startKST": start.astimezone(KST).isoformat(),
                   "endKST": end.astimezone(KST).isoformat()},
        "clusters": cards,
        "counts": {"raw": len(raw), "kept": len(kept), "cards": len(cards),
                   "new": sum(1 for c in cards if c["suggested_action"] == "NEW"),
                   "merge_existing": sum(1 for c in cards if c["suggested_action"] == "MERGE_EXISTING"),
                   "similar": sum(1 for c in cards if c["suggested_action"] == "SIMILAR")},
        "dropped": dropped,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", required=True)
    ap.add_argument("--index", default=str(REPO / "docs" / "data" / "index.json"))
    ap.add_argument("--out", required=True)
    ap.add_argument("--recent-days", type=int, default=21)
    ap.add_argument("--now", default=None, help="테스트용 기준시각 ISO8601")
    a = ap.parse_args()

    raw = json.loads(Path(a.raw).read_text(encoding="utf-8"))
    now = parse_dt(a.now) if a.now else None
    window = get_kst_window(now)
    recent = load_recent_index(Path(a.index), a.recent_days, window[1])
    result = normalize(raw, recent, window)
    Path(a.out).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    c = result["counts"]
    print(f"[OK] raw {c['raw']} → 유효 {c['kept']} → 카드 {c['cards']} "
          f"(NEW {c['new']} / MERGE_EXISTING {c['merge_existing']} / SIMILAR {c['similar']})")
    print(f"     제외: {result['dropped']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
