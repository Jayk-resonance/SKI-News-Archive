#!/usr/bin/env python3
"""data/archive.md (레거시 블록 포맷 시드) → content/YYYY-MM-DD/{id}.md 기사별 front-matter 변환.

일회성 마이그레이션. LLM 사용 안 함 — 기존 필드를 그대로 재배치하는 순수 코드 변환.
변환 이후의 소스 오브 트루스는 content/ 디렉터리다.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SEED = REPO / "data" / "archive.md"
CONTENT = REPO / "content"

# 레거시 category 라벨 → 표준 division 라벨(동일하게 유지, 매핑은 미래 변경 대비 훅)
DIVISION_MAP = {
    "배터리·소재": "배터리·소재",
    "정유·트레이딩": "정유·트레이딩",
    "석유화학": "석유화학",
    "윤활유·기유": "윤활유·기유",
    "E&P": "E&P",
    "LNG": "LNG",
    "전력·수소": "전력·수소",
}

# 스칼라 단일값 필드
SCALAR_FIELDS = {"id", "date", "category", "topic", "company",
                 "importance", "title", "summary", "eventId"}


def parse_blocks(text: str) -> list[dict]:
    """`---`로 구분된 블록을 파싱. 주석(#)·빈 블록은 건너뜀."""
    articles: list[dict] = []
    for raw in text.split("\n---\n"):
        block = raw.strip()
        if not block or block.startswith("#"):
            continue
        art: dict = {"sources": []}
        lines = block.split("\n")
        i = 0
        while i < len(lines):
            line = lines[i]
            if line.strip().startswith("#") or not line.strip():
                i += 1
                continue
            if line.startswith("sources:"):
                i += 1
                while i < len(lines) and lines[i].lstrip().startswith("-"):
                    entry = lines[i].lstrip()[1:].strip()
                    parts = [p.strip() for p in entry.split("::")]
                    if len(parts) == 3:
                        art["sources"].append(
                            {"publisher": parts[0], "title": parts[1], "url": parts[2]}
                        )
                    i += 1
                continue
            m = re.match(r"^([A-Za-z_]+):\s*(.*)$", line)
            if m:
                key, val = m.group(1), m.group(2).strip()
                if key in ("keywords", "tags"):
                    art[key] = [t.strip() for t in val.split(",") if t.strip()]
                elif key in SCALAR_FIELDS:
                    art[key] = val
            i += 1
        if art.get("id"):
            articles.append(art)
    return articles


def yaml_list(items: list[str]) -> str:
    return "[" + ", ".join(items) + "]"


def to_front_matter(art: dict) -> str:
    """표준 front-matter MD 문자열 생성. 스키마는 계획서 참조."""
    div = DIVISION_MAP.get(art.get("category", ""), art.get("category", ""))
    lines = ["---"]
    lines.append(f"id: {art['id']}")
    lines.append(f"date: {art['date']}")
    lines.append(f"division: {div}")
    if art.get("topic"):
        lines.append(f"topic: {art['topic']}")
    if art.get("company"):
        lines.append(f"company: {art['company']}")
        entities = [art["company"]]
    else:
        entities = []
    lines.append(f"entities: {yaml_list(entities)}")
    lines.append(f"importance: {art.get('importance', 'mid')}")
    # 레거시엔 impact_score가 없으므로 importance에서 보수적으로 유도(표시용 아님, 정렬 fallback)
    imp_score = {"high": 8, "mid": 5, "low": 3}.get(art.get("importance", "mid"), 5)
    lines.append(f"impact_score: {imp_score}")
    lines.append(f"keywords: {yaml_list(art.get('keywords', []))}")
    lines.append(f"tags: {yaml_list(art.get('tags', []))}")
    if art.get("eventId"):
        lines.append(f"eventId: {art['eventId']}")
    # title에 콜론이 있을 수 있어 따옴표로 감쌈
    title = art.get("title", "").replace('"', '\\"')
    lines.append(f'title: "{title}"')
    lines.append("sources:")
    for s in art.get("sources", []):
        st = s["title"].replace('"', '\\"')
        lines.append(f'  - {{ publisher: "{s["publisher"]}", title: "{st}", url: "{s["url"]}" }}')
    lines.append("---")
    lines.append(art.get("summary", "").strip())
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    if not SEED.exists():
        print(f"[ERR] 시드 파일 없음: {SEED}", file=sys.stderr)
        return 1
    articles = parse_blocks(SEED.read_text(encoding="utf-8"))
    written = 0
    for art in articles:
        date = art["date"]
        out_dir = CONTENT / date
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{art['id']}.md"
        out_path.write_text(to_front_matter(art), encoding="utf-8")
        written += 1
    print(f"[OK] {written}건 → {CONTENT}/ 변환 완료")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
