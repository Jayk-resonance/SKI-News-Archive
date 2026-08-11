#!/usr/bin/env python3
"""분류 결과(LLM) + 정규화 출처 → 기사별 front-matter MD 기록.  LLM 미사용(코드 전용).

파이프라인 후반. 루틴의 Claude가 normalize.py 출력(clusters)을 보고 각 카드에
분류·요약·3갈래 최종 action을 붙인 classified.json 을 만든다. 이 스크립트는 그걸
받아 content/ 에 실제 MD를 쓴다.

action 처리:
  NEW            : content/{date}/{새id}.md 신규 생성
  LINK           : 신규 생성 + eventId=기존 사안(타임라인에 이어붙음)
  MERGE_EXISTING : 기존 기사 MD의 sources[]에 새 출처만 추가(중복 URL 제외)
  DROP           : 기록 안 함(로그만)

classified.json 스키마(배열):
  [{ "tmpId":"c1", "action":"NEW|LINK|MERGE_EXISTING|DROP",
     "division":"..", "topic":"..", "company":".."|null, "entities":[..],
     "importance":"high|mid|low", "impact_score":8,
     "summary":"2~3문장", "keywords":[..], "tags":[..],
     "eventId":".."|null, "event_title":".."|null,
     "mergeInto":"e28"|null }]   # MERGE_EXISTING 시 대상 기존 id

사용:
  python tools/write_articles.py --classified /tmp/classified.json \
      --normalized /tmp/normalized.json [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))
from build import parse_md  # noqa: E402  (기존 MD 파서 재사용)

CONTENT = REPO / "content"


def q(s: str) -> str:
    return '"' + str(s or "").replace('"', '\\"') + '"'


def render(a: dict) -> str:
    """기사 dict → 표준 front-matter MD 문자열. build.parse_md와 호환."""
    L = ["---", f"id: {a['id']}", f"date: {a['date']}", f"division: {a['division']}"]
    if a.get("publishedAt"):
        L.append(f"publishedAt: {a['publishedAt']}")
    if a.get("topic"):
        L.append(f"topic: {a['topic']}")
    if a.get("company"):
        L.append(f"company: {a['company']}")
    L.append(f"entities: [{', '.join(a.get('entities', []))}]")
    L.append(f"importance: {a.get('importance', 'mid')}")
    L.append(f"impact_score: {int(a.get('impact_score', 5))}")
    L.append(f"keywords: [{', '.join(a.get('keywords', []))}]")
    L.append(f"tags: [{', '.join(a.get('tags', []))}]")
    if a.get("eventId"):
        L.append(f"eventId: {a['eventId']}")
    if a.get("event_title"):
        L.append(f"event_title: {q(a['event_title'])}")
    L.append(f"title: {q(a.get('title', ''))}")
    if a.get("title_ko"):
        L.append(f"title_ko: {q(a['title_ko'])}")
    if a.get("highlight"):
        L.append(f"highlight: {q(a['highlight'])}")
    L.append("sources:")
    for s in a.get("sources", []):
        L.append(f'  - {{ publisher: {q(s.get("publisher"))}, title: {q(s.get("title"))}, url: {q(s.get("url"))} }}')
    L.append("---")
    L.append((a.get("summary") or "").strip())
    L.append("")
    return "\n".join(L)


def find_existing(article_id: str) -> Path | None:
    hits = list(CONTENT.rglob(f"{article_id}.md"))
    return hits[0] if hits else None


def used_ids() -> set[str]:
    return {p.stem for p in CONTENT.rglob("*.md") if "insights" not in p.parts}


def next_id(date: str, used: set[str]) -> str:
    """a{YYYYMMDD}{NN} 형식으로 미사용 id 생성."""
    base = "a" + date.replace("-", "")
    for n in range(1, 100):
        cand = f"{base}{n:02d}"
        if cand not in used:
            used.add(cand)
            return cand
    raise RuntimeError(f"id 소진: {date}")


def clean_sources(sources: list[dict]) -> list[dict]:
    """표시용 필드만 남김(tier는 빌드시 유도)."""
    out, seen = [], set()
    for s in sources:
        u = s.get("url")
        if not u or u in seen:
            continue
        seen.add(u)
        out.append({"publisher": s.get("publisher", ""), "title": s.get("title", ""), "url": u})
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--classified", required=True)
    ap.add_argument("--normalized", required=True)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    classified = json.loads(Path(a.classified).read_text(encoding="utf-8"))
    norm = json.loads(Path(a.normalized).read_text(encoding="utf-8"))
    by_tmp = {c["tmpId"]: c for c in norm["clusters"]}
    used = used_ids()

    log = {"NEW": [], "LINK": [], "MERGE_EXISTING": [], "DROP": [], "SKIP": []}
    for item in classified:
        tmp = by_tmp.get(item.get("tmpId"))
        if tmp is None:
            log["SKIP"].append(f"{item.get('tmpId')}: 정규화 카드 없음")
            continue
        action = item.get("action", "NEW")
        sources = clean_sources(tmp.get("sources", []))
        date = tmp.get("date") or item.get("date")

        if action == "DROP":
            log["DROP"].append(f"{tmp['title'][:40]} (우려먹기)")
            continue

        if action == "MERGE_EXISTING":
            target = item.get("mergeInto") or (tmp.get("match") or {}).get("articleId")
            path = find_existing(target) if target else None
            if not path:
                log["SKIP"].append(f"MERGE 대상 없음: {target} → NEW로 강등")
                action = "NEW"
            else:
                art = parse_md(path)
                merged = clean_sources(art.get("sources", []) + sources)
                art["sources"] = merged
                if not a.dry_run:
                    path.write_text(render(art), encoding="utf-8")
                log["MERGE_EXISTING"].append(f"{target} ← +{len(sources)}출처")
                continue

        # NEW / LINK
        art = {
            "id": next_id(date, used), "date": date,
            "publishedAt": tmp.get("publishedAt"),
            "division": item.get("division"), "topic": item.get("topic"),
            "company": item.get("company"), "entities": item.get("entities", []),
            "importance": item.get("importance", "mid"),
            "impact_score": item.get("impact_score", 5),
            "keywords": item.get("keywords", []), "tags": item.get("tags", []),
            "title": tmp.get("title"), "title_ko": item.get("title_ko"),
            "summary": item.get("summary", ""),
            "highlight": tmp.get("snippet", "")[:200], "sources": sources,
        }
        if action == "LINK":
            art["eventId"] = item.get("eventId") or (tmp.get("match") or {}).get("eventId")
            art["event_title"] = item.get("event_title")
            log["LINK"].append(f"{art['id']} → 사안 {art.get('eventId')}")
        else:
            log["NEW"].append(art["id"])
        out_dir = CONTENT / date
        if not a.dry_run:
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / f"{art['id']}.md").write_text(render(art), encoding="utf-8")

    tag = "[DRY-RUN] " if a.dry_run else ""
    print(f"{tag}NEW {len(log['NEW'])} / LINK {len(log['LINK'])} / "
          f"MERGE {len(log['MERGE_EXISTING'])} / DROP {len(log['DROP'])} / SKIP {len(log['SKIP'])}")
    for k in ("LINK", "MERGE_EXISTING", "DROP", "SKIP"):
        for line in log[k]:
            print(f"  {k}: {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
