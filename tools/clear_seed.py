#!/usr/bin/env python3
"""예시(시드) 데이터만 제거.  멱등 — 실데이터는 건드리지 않는다.

시드 판별 기준(둘 중 하나):
  - 기사의 모든 sources URL이 example.com (시드 기사)
  - data/archive.md (시드 원본 파일)
그리고 pick 기사가 사라진 인사이트 파일도 함께 제거한다.

실제 루틴 기사는 실 언론사 URL을 가지므로 이 스크립트에 걸리지 않는다.
이미 지워졌으면 아무것도 하지 않는다(멱등).

사용:  python tools/clear_seed.py [--dry-run]
이후 반드시:  python tools/build.py
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))
from build import parse_md  # noqa: E402

CONTENT = REPO / "content"
INSIGHTS = CONTENT / "insights"
ARCHIVE = REPO / "data" / "archive.md"


def is_seed(art: dict) -> bool:
    srcs = art.get("sources", [])
    return bool(srcs) and all("example.com" in (s.get("url") or "") for s in srcs)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    removed_articles = []
    remaining_ids = set()
    for path in sorted(CONTENT.rglob("*.md")):
        if INSIGHTS in path.parents:
            continue
        try:
            art = parse_md(path)
        except Exception:
            continue
        if is_seed(art):
            removed_articles.append(path)
            if not a.dry_run:
                path.unlink()
        else:
            remaining_ids.add(art.get("id"))

    # pick 기사가 사라진 인사이트 제거
    removed_insights = []
    if INSIGHTS.exists():
        for path in sorted(INSIGHTS.glob("*.md")):
            text = path.read_text(encoding="utf-8")
            m = re.search(r"^pick:\s*(\S+)", text, re.MULTILINE)
            pick = m.group(1) if m else None
            if pick and pick not in remaining_ids:
                removed_insights.append(path)
                if not a.dry_run:
                    path.unlink()

    # 시드 원본 파일
    removed_seed_file = False
    if ARCHIVE.exists():
        removed_seed_file = True
        if not a.dry_run:
            ARCHIVE.unlink()

    # 빈 날짜 디렉터리 정리
    if not a.dry_run:
        for d in sorted(CONTENT.iterdir()):
            if d.is_dir() and d.name != "insights" and not any(d.iterdir()):
                d.rmdir()

    tag = "[DRY-RUN] " if a.dry_run else ""
    print(f"{tag}시드 기사 {len(removed_articles)}건, 인사이트 {len(removed_insights)}건, "
          f"archive.md {'제거' if removed_seed_file else '없음'} → 남은 실기사 {len(remaining_ids)}건")
    if not removed_articles and not removed_seed_file:
        print("  (제거할 시드 없음 — 이미 정리됨)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
