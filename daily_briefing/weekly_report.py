#!/usr/bin/env python3
"""주간 리포트 → HTML 이메일 생성 + (열쇠 있으면) Gmail API 발송.

docs/data/weekly.json(= tools/build.py가 만든 최근 7일 집계)을 읽어 주간 메일을 만든다.
LLM을 쓰지 않는다 — 전부 코드 집계라 프롬프트 변경 없이 매주 같은 품질이 나온다.

발송 분기는 일간 메일(email_report.py)과 동일:
  - GMAIL_CLIENT_ID/SECRET/REFRESH_TOKEN 있고 --send면 Gmail API 발송 → "[SENT] <id>"
  - 없으면 "[DRAFT-NEEDED]" → 루틴이 Gmail MCP create_draft 로 폴백

사용:
  SITE_URL="https://..." python daily_briefing/weekly_report.py --data docs/data --to jupiter@sk.com --send
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from email_report import CAT, SITE_URL, esc, send_via_gmail_api  # noqa: E402

REPO = Path(__file__).resolve().parent.parent


def _chip(div: str) -> str:
    c, bg = CAT.get(div, ("#1c1c1c", "#f4f6f8"))
    return (f'<span style="display:inline-block;background:{bg};color:{c};font-size:11px;font-weight:600;'
            f'border-radius:5px;padding:2px 7px;">{esc(div)}</span>')


def build_email(w: dict, recipients: list[str]) -> dict:
    """주간 리포트 메일: 헤더 KPI → 핵심 기사 5건 → 부문 분포 → 부상 테마 → 움직인 사안."""
    delta = w.get("delta", 0)
    if delta > 0:
        dl = f'<span style="color:#e23b34;font-weight:700;">▲ {delta}건</span>'
    elif delta < 0:
        dl = f'<span style="color:#2f6fd0;font-weight:700;">▼ {-delta}건</span>'
    else:
        dl = '<span style="color:#8a8f99;">변동 없음</span>'

    divs = w.get("byDivision", {})
    mx = max(divs.values()) if divs else 1
    div_rows = "".join(
        f'<tr><td style="padding:4px 10px 4px 0;font-size:12px;color:#555;white-space:nowrap;">{esc(d)}</td>'
        f'<td style="padding:4px 0;width:100%;">'
        f'<div style="background:#e9edf2;border-radius:5px;height:8px;">'
        f'<div style="background:#95a4fc;border-radius:5px;height:8px;width:{int(n / mx * 100)}%;"></div></div></td>'
        f'<td style="padding:4px 0 4px 8px;font-size:12px;font-weight:700;text-align:right;">{n}</td></tr>'
        for d, n in divs.items())

    tops = []
    for i, c in enumerate(w.get("top", []), 1):
        link = c.get("leadUrl") or ""
        title = esc(c.get("title", ""))
        title_html = (f'<a href="{esc(link)}" style="color:#1c1c1c;text-decoration:none;">{title}</a>'
                      if link else title)
        meta = " · ".join(x for x in [esc(c.get("date", "")), esc(c.get("leadPublisher", ""))] if x)
        tops.append(
            f'<tr><td style="padding:14px 0;border-bottom:1px solid #eef0f3;vertical-align:top;">'
            f'<table role="presentation" cellpadding="0" cellspacing="0" width="100%"><tr>'
            f'<td width="30" style="vertical-align:top;padding-top:2px;">'
            f'<div style="width:24px;height:24px;border-radius:7px;background:#ecedfc;color:#4f53cf;'
            f'font-size:12px;font-weight:800;text-align:center;line-height:24px;">{i}</div></td>'
            f'<td style="padding-left:10px;">'
            f'<div style="margin-bottom:5px;">{_chip(c.get("division", ""))}'
            f'<span style="font-size:11px;color:#8a8f99;margin-left:7px;">{meta}</span>'
            f'<span style="font-size:11px;color:#e23b34;font-weight:700;margin-left:7px;">'
            f'영향력 {c.get("impact_score", 0)}</span></div>'
            f'<div style="font-size:15px;font-weight:700;line-height:1.45;margin-bottom:4px;">{title_html}</div>'
            f'<div style="font-size:13px;line-height:1.65;color:#555;">{esc(c.get("summary", ""))}</div>'
            f'</td></tr></table></td></tr>')

    rise = "".join(
        f'<tr><td style="padding:5px 0;font-size:13px;font-weight:700;white-space:nowrap;">{esc(r["kw"])}</td>'
        f'<td style="padding:5px 8px;font-size:12px;color:#8a8f99;">{r["prev"]} → <b>{r["cur"]}</b>건</td>'
        f'<td style="padding:5px 0;font-size:11px;color:#e23b34;font-weight:700;text-align:right;'
        f'white-space:nowrap;">{r["prevShare"]}% → {r["curShare"]}%</td></tr>'
        for r in w.get("rising", []))
    rise_block = (
        f'<div style="margin-top:22px;"><div style="font-size:14px;font-weight:700;margin-bottom:4px;">'
        f'📈 이번 주 부상한 테마</div>'
        f'<div style="font-size:11px;color:#8a8f99;margin-bottom:8px;">'
        f'주마다 수집량이 달라 건수 대신 <b>전체 중 비중</b>으로 비교합니다</div>'
        f'<table role="presentation" cellpadding="0" cellspacing="0" width="100%">{rise}</table></div>'
    ) if rise else ""

    iss = "".join(
        f'<tr><td style="padding:6px 0;border-bottom:1px solid #f2f4f7;">'
        f'{_chip(i.get("division", ""))}'
        f'<span style="font-size:13px;font-weight:600;margin-left:7px;">{esc(i.get("label", ""))}</span>'
        f'<span style="font-size:11px;color:#8a8f99;margin-left:6px;">{i.get("count", 0)}건</span></td></tr>'
        for i in w.get("issues", []))
    iss_block = (
        f'<div style="margin-top:22px;"><div style="font-size:14px;font-weight:700;margin-bottom:8px;">'
        f'⎇ 이번 주 움직인 사안</div>'
        f'<table role="presentation" cellpadding="0" cellspacing="0" width="100%">{iss}</table></div>'
    ) if iss else ""

    site = (f'<div style="margin-top:26px;text-align:center;">'
            f'<a href="{esc(SITE_URL)}?tab=weekly" style="display:inline-block;background:#4f53cf;color:#fff;'
            f'text-decoration:none;font-size:13px;font-weight:600;border-radius:999px;padding:11px 24px;">'
            f'웹에서 주간 리포트 전체 보기 →</a></div>') if SITE_URL else ""

    ins_n = len(w.get("insights", []))
    html = f"""<div style="max-width:680px;margin:0 auto;padding:24px 20px;font-family:'Apple SD Gothic Neo',
'Malgun Gothic',sans-serif;color:#1c1c1c;background:#fff;">
  <div style="font-size:19px;font-weight:800;">🗓 주간 리포트 · SK이노베이션 계열</div>
  <div style="font-size:13px;color:#8a8f99;margin-top:5px;">{esc(w.get('label',''))} · 최근 7일 집계</div>
  <table role="presentation" cellpadding="0" cellspacing="0" width="100%"
    style="margin-top:18px;background:#f7f9fb;border-radius:14px;">
    <tr>
      <td style="padding:16px 18px;"><div style="font-size:24px;font-weight:800;">{w.get('total',0)}</div>
        <div style="font-size:11px;color:#8a8f99;">이번 주 기사</div></td>
      <td style="padding:16px 18px;"><div style="font-size:24px;font-weight:800;">{w.get('prevTotal',0)}</div>
        <div style="font-size:11px;color:#8a8f99;">직전 주</div></td>
      <td style="padding:16px 18px;"><div style="font-size:17px;">{dl}</div>
        <div style="font-size:11px;color:#8a8f99;">증감</div></td>
      <td style="padding:16px 18px;"><div style="font-size:24px;font-weight:800;color:#4f53cf;">{ins_n}</div>
        <div style="font-size:11px;color:#8a8f99;">AI 인사이트</div></td>
    </tr>
  </table>
  <div style="margin-top:24px;font-size:14px;font-weight:700;">⭐ 이번 주 핵심 기사
    <span style="font-weight:400;color:#8a8f99;font-size:12px;">· 영향력순 {len(w.get('top', []))}건</span></div>
  <table role="presentation" cellpadding="0" cellspacing="0" width="100%">{''.join(tops)}</table>
  <div style="margin-top:22px;"><div style="font-size:14px;font-weight:700;margin-bottom:8px;">🗂 부문별 기사</div>
    <table role="presentation" cellpadding="0" cellspacing="0" width="100%">{div_rows}</table></div>
  {rise_block}{iss_block}{site}
  <div style="margin-top:26px;padding-top:14px;border-top:1px solid #eef0f3;font-size:11px;
    color:#8a8f99;line-height:1.7;">
    요약·분류는 AI가 생성한 내용입니다. 인용 전 원문 기사를 확인해 주세요.<br>
    제목을 누르면 원문 기사로 이동합니다.
  </div>
</div>"""

    return {"to": recipients,
            "subject": f"[SK이노베이션 계열 주간 리포트] {w.get('label', '')}",
            "htmlBody": html}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=str(REPO / "docs" / "data"))
    ap.add_argument("--to", default="jupiter@sk.com")
    ap.add_argument("--out", default="/tmp/weekly_email_output.json")
    ap.add_argument("--send", action="store_true")
    a = ap.parse_args()

    path = Path(a.data) / "weekly.json"
    if not path.exists():
        print("[ERR] weekly.json 없음 — tools/build.py 를 먼저 실행하세요.", file=sys.stderr)
        return 1
    w = json.loads(path.read_text(encoding="utf-8"))
    if not w or not w.get("total"):
        print("[SKIP] 최근 7일 기사가 없어 주간 리포트를 만들지 않습니다.")
        return 0

    recipients = [x.strip() for x in a.to.split(",") if x.strip()]
    msg = build_email(w, recipients)
    Path(a.out).write_text(json.dumps(msg, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[WEEKLY] 제목: {msg['subject']}")
    print(f"[WEEKLY] 수신: {', '.join(recipients)} · 본문 {len(msg['htmlBody'])}자 → {a.out}")

    has_creds = all(os.environ.get(k) for k in
                    ("GMAIL_CLIENT_ID", "GMAIL_CLIENT_SECRET", "GMAIL_REFRESH_TOKEN"))
    if a.send and has_creds:
        try:
            print(f"[SENT] Gmail API message id: {send_via_gmail_api(msg)}")
            return 0
        except Exception as e:
            print(f"[SEND-FAILED] {e}", file=sys.stderr)
            print("[DRAFT-NEEDED] Gmail MCP create_draft 폴백 필요")
            return 0
    print("[DRAFT-NEEDED] 발송 열쇠 없음 또는 --send 미지정 → Gmail MCP create_draft 폴백 필요")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
