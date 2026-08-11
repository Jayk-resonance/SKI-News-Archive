#!/usr/bin/env python3
"""수집 원본(/tmp/exa_raw_*.json) 병합 + 부문별 수집 상태 진단.

왜 필요한가: 2026-08-05~09에 7개 부문이 일제히 44~78% 줄었는데 아무 신호가 없었다.
그 시간대 Exa에는 기사가 그대로 있었으니(정유 15건·LNG 10건) 수집 단계가 조용히
실패한 것인데, 실패한 쿼리를 건너뛰도록 되어 있어 3건짜리 브리핑이 그대로 커밋됐다.
어느 부문이 통째로 비었는지만 알아도 원인 추적이 훨씬 빨라진다.

부문 판별은 파일명이 아니라 각 항목의 `division` 값으로 한다 — 파일명 안전문자
규칙(`E&P` → `E_P`?)이 경로 A/B에서 달라질 수 있어서 파일명은 믿지 않는다.

사용:
  python tools/collect_check.py                 # 병합 + 진단표 출력
  python tools/collect_check.py --no-merge      # 진단만
"""
from __future__ import annotations

import argparse
import glob
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config.divisions import DIVISIONS  # noqa: E402

MERGED = "/tmp/exa_raw.json"


def load_raw(tmp: str = "/tmp") -> tuple[list[dict], list[str]]:
    """부문별 원본 파일을 모두 읽어 (항목, 읽은 파일) 반환.

    glob 패턴에 밑줄이 있어 병합본 exa_raw.json 은 잡히지 않는다(이중 집계 방지).
    """
    items: list[dict] = []
    files: list[str] = []
    for f in sorted(glob.glob(f"{tmp}/exa_raw_*.json")):
        try:
            data = json.loads(Path(f).read_text(encoding="utf-8"))
        except Exception as e:
            print(f"[WARN] {f} 읽기 실패: {e}", file=sys.stderr)
            continue
        if isinstance(data, list):
            items.extend(data)
            files.append(f)
    return items, files


def check(items: list[dict]) -> dict:
    """부문별 수집 건수 · 목표 대비 · 빈 부문 목록."""
    got: dict[str, int] = defaultdict(int)
    for it in items:
        d = (it or {}).get("division") or "(부문없음)"
        got[d] += 1
    rows = []
    for name, cfg in DIVISIONS.items():
        target = cfg.get("daily_target", 0) + cfg.get("materials_target", 0)
        n = got.get(name, 0)
        rows.append({"division": name, "count": n, "target": target,
                     "ratio": round(n / target, 2) if target else None})
    unknown = {k: v for k, v in got.items() if k not in DIVISIONS}
    empty = [r["division"] for r in rows if r["count"] == 0]
    total_target = sum(r["target"] for r in rows)
    # 심각 판정: 전체가 목표의 40% 미만이거나, 통째로 빈 부문이 3개 이상.
    # 이 상태로 진행하면 8/5~8/9처럼 3건짜리 브리핑이 조용히 커밋·발송된다.
    severe = bool(total_target and len(items) < 0.4 * total_target) or len(empty) >= 3
    return {"rows": rows, "total": len(items), "empty": empty, "unknown": unknown,
            "totalTarget": total_target, "severe": severe}


def format_report(res: dict, files: list[str]) -> str:
    out = [f"[수집 진단] 원본 파일 {len(files)}개 · 총 {res['total']}건"]
    for r in res["rows"]:
        mark = "❌ 0건" if r["count"] == 0 else ("⚠️ 저조" if r["ratio"] is not None and r["ratio"] < 0.4 else "OK")
        tg = f"/목표 {r['target']}" if r["target"] else ""
        out.append(f"  {r['division']:<12} {r['count']:>3}건{tg:<9} {mark}")
    if res["unknown"]:
        out.append(f"  [부문명 불일치] {res['unknown']}")
    if res["empty"]:
        out.append(f"[ALERT] 수집 0건 부문 {len(res['empty'])}개: {', '.join(res['empty'])}")
        out.append("        → 해당 부문 쿼리를 다시 시도하거나, Exa 응답 오류를 확인하세요.")
    if res["severe"]:
        out.append(f"[COLLECT-BLOCK] 총 {res['total']}건 / 목표 {res['totalTarget']}건 — 수집이 무너졌다.")
        out.append('        → 이 상태로 다음 단계에 넘어가지 마라. Exa 호출에 `category:"news"`가'
                   ' 들어갔는지 먼저 확인하고, 전 부문을 1회 재수집하라.')
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tmp", default="/tmp")
    ap.add_argument("--no-merge", action="store_true")
    a = ap.parse_args()

    items, files = load_raw(a.tmp)
    if not files:
        print("[ERR] /tmp/exa_raw_*.json 이 없습니다 — 수집 단계가 아예 실행되지 않았습니다.",
              file=sys.stderr)
        return 2

    if not a.no_merge:
        Path(MERGED).write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
        print(f"병합 {len(items)}건 → {MERGED}")

    res = check(items)
    print(format_report(res, files))
    # 2 = 반드시 전 부문 재수집, 1 = 일부 부문만 재시도, 0 = 정상
    return 2 if res["severe"] else (1 if res["empty"] else 0)


if __name__ == "__main__":
    raise SystemExit(main())
