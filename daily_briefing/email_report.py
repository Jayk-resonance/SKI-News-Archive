#!/usr/bin/env python3
"""오늘의 브리핑 → HTML 이메일 생성 + (열쇠 있으면) Gmail API 발송.

파이프라인 메일 단계. docs/data/today.json 을 읽어 이메일 본문(HTML)을 만들고
/tmp/email_output.json 에 {to, subject, htmlBody} 를 저장한다.

발송 분기:
  - GMAIL_CLIENT_ID/SECRET/REFRESH_TOKEN 환경변수가 있고 --send면 Gmail API로 직접 발송
    → 성공 시 "[SENT] <id>" 출력
  - 없으면 "[DRAFT-NEEDED]" 출력 → 루틴이 Gmail MCP create_draft 로 초안 생성(폴백)
  email_output.json 은 발송 성공/실패와 무관하게 항상 먼저 생성된다.

사용:
  python daily_briefing/email_report.py --data docs/data --to jupiter@sk.com [--send]
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
from email.mime.text import MIMEText
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SITE_URL = os.environ.get("SITE_URL", "")  # GitHub Pages URL (있으면 버튼 노출)

CAT = {
    "배터리·소재": ("#4f53cf", "#ecedfc"), "정유·트레이딩": ("#c07a16", "#fbf1e2"),
    "석유화학": ("#b85c2a", "#fbeee6"), "윤활유·기유": ("#9c7a10", "#f8f2dd"),
    "E&P": ("#bd5140", "#fbeae7"), "LNG": ("#1f8f7c", "#e4f4f0"),
    "전력·수소": ("#2f9440", "#eaf6ec"),
}
IMP = {"high": ("#e23b34", "상"), "mid": ("#bf7414", "중"), "low": ("#8a8f99", "하")}


def esc(s) -> str:
    return (str(s) if s is not None else "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def md_to_html(t: str) -> str:
    """인사이트 마크다운(##, **bold**, 문단) → 간단 HTML."""
    out, inp = [], False
    for ln in esc(t).split("\n"):
        ln = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", ln)
        if re.match(r"^##\s+", ln):
            if inp:
                out.append("</p>"); inp = False
            out.append(f'<h3 style="margin:16px 0 6px;font-size:14px;color:#4f53cf;">{ln[3:].strip()}</h3>')
        elif not ln.strip():
            if inp:
                out.append("</p>"); inp = False
        else:
            if not inp:
                out.append('<p style="margin:0 0 8px;font-size:14px;line-height:1.7;color:#333;">'); inp = True
            else:
                out.append(" ")
            out.append(ln)
    if inp:
        out.append("</p>")
    return "".join(out)


def _window_str(date: str, window: dict) -> str:
    """수집 기간 문자열 'M월 D일 08:00 ~ M월 D일 08:00 KST'. window 우선, 없으면 date에서 유도."""
    from datetime import datetime, timedelta
    s = e = None
    try:
        if window.get("startKST") and window.get("endKST"):
            s = datetime.fromisoformat(window["startKST"])
            e = datetime.fromisoformat(window["endKST"])
    except Exception:
        s = e = None
    if s is None or e is None:
        try:
            e = datetime.strptime(date, "%Y-%m-%d").replace(hour=8, minute=0)
            s = e - timedelta(days=1)
        except Exception:
            return ""
    return f"{s.month}월 {s.day}일 {s:%H:%M} ~ {e.month}월 {e.day}일 {e:%H:%M} KST"


def _score_color(score: int) -> str:
    """점수 색상(상=빨강, 중=주황, 하=회색)."""
    return "#e23b34" if score >= 8 else ("#bf7414" if score >= 6 else "#8a8f99")


def build_email(today: dict, recipients: list[str]) -> dict:
    """지난 브리핑 양식: 수집 건수·기간 헤더 → TOP STORY 히어로 → 번호매김 영향력순 표."""
    date = today.get("date", "")
    cards = sorted(today.get("cards", []), key=lambda c: c.get("impact_score", 0), reverse=True)
    divc: dict[str, int] = {}
    for c in cards:
        divc[c["division"]] = divc.get(c["division"], 0) + 1
    divsum = " · ".join(f"{d} {n}" for d, n in sorted(divc.items(), key=lambda x: -x[1]))
    insight = today.get("insight") or {}
    pick = None
    if insight.get("pickId"):
        pick = next((c for c in cards if c["id"] == insight["pickId"]), None)
    win = _window_str(date, today.get("window") or {})

    subject = f"[SK이노베이션 계열 AI News Clipping] {date}"

    # --- TOP STORY 히어로 (오늘의 PICK 인사이트) ---
    hero = ""
    if pick and insight.get("markdown"):
        cc = CAT.get(pick["division"], ("#4f53cf", "#ecedfc"))
        purl = pick.get("leadUrl") or (pick.get("sources") or [{}])[0].get("url", "")
        sc = int(pick.get("impact_score", 0))
        hero = f"""
      <div style="border-left:4px solid {cc[0]};background:{cc[1]};border-radius:0 12px 12px 0;padding:20px 22px;margin:0 0 26px;">
        <span style="display:inline-block;font-size:11px;font-weight:800;letter-spacing:.04em;color:#fff;background:{cc[0]};border-radius:5px;padding:3px 9px;">🔍 오늘의 PICK · AI 심층분석</span>
        <span style="display:inline-block;font-size:11px;font-weight:800;color:#fff;background:{_score_color(sc)};border-radius:5px;padding:3px 8px;margin-left:5px;">영향력 {sc}</span>
        <div style="font-size:18px;font-weight:800;color:#1c1c1c;margin:12px 0 6px;line-height:1.42;">
          <a href="{esc(purl)}" style="color:#1c1c1c;text-decoration:none;">{esc(pick.get('title_ko') or pick['title'])}</a></div>
        <span style="display:inline-block;font-size:11px;font-weight:700;color:{cc[0]};background:#fff;border-radius:5px;padding:2px 8px;">{esc(pick['division'])}</span>{(' <span style="font-size:11px;color:#999;">· ' + esc(pick.get('leadPublisher','')) + '</span>') if pick.get('leadPublisher') else ''}
        <div style="margin-top:12px;">{md_to_html(insight['markdown'])}</div>
        {f'<div style="margin-top:14px;padding-top:12px;border-top:1px solid rgba(0,0,0,.08);"><a href="{esc(SITE_URL)}?insight={esc(date)}" style="color:{cc[0]};font-size:13px;font-weight:700;text-decoration:none;">웹에서 원문 기사·전문 보기 →</a></div>' if SITE_URL else ''}
      </div>"""

    # --- 전체 수집 기사(영향력순) 번호매김 표 ---
    rows = []
    for i, c in enumerate(cards, 1):
        cc = CAT.get(c["division"], ("#1c1c1c", "#f4f6f8"))
        url = c.get("leadUrl") or (c.get("sources") or [{}])[0].get("url", "")
        sc = int(c.get("impact_score", 0))
        try:
            _y, _m, _d = c.get("date", "").split("-")
            dstr = f"{int(_m)}/{int(_d)}"
        except Exception:
            dstr = c.get("date", "")
        meta = " · ".join(x for x in [esc(c.get("leadPublisher", "")), esc(dstr)] if x)
        rows.append(f"""
        <tr>
          <td style="padding:11px 8px;border-bottom:1px solid #eee;text-align:center;color:#aab;font-size:12px;width:24px;vertical-align:top;">{i}</td>
          <td style="padding:11px 8px;border-bottom:1px solid #eee;white-space:nowrap;vertical-align:top;">
            <span style="display:inline-block;font-size:11px;font-weight:700;color:{cc[0]};background:{cc[1]};border-radius:5px;padding:2px 7px;">{esc(c['division'])}</span></td>
          <td style="padding:11px 8px;border-bottom:1px solid #eee;font-size:13px;line-height:1.5;vertical-align:top;">
            <a href="{esc(url)}" style="color:#1c1c1c;text-decoration:none;font-weight:600;">{esc(c.get('title_ko') or c['title'])}</a>
            <div style="color:#9aa;font-size:11px;margin-top:3px;">{meta}</div></td>
          <td style="padding:11px 8px;border-bottom:1px solid #eee;text-align:center;font-weight:800;font-size:14px;color:{_score_color(sc)};width:44px;vertical-align:top;">{sc}</td>
        </tr>""")

    site_btn = f'<div style="text-align:center;margin:24px 0 8px;"><a href="{esc(SITE_URL)}" style="display:inline-block;background:#1c1c1c;color:#fff;text-decoration:none;font-size:13px;font-weight:600;border-radius:8px;padding:10px 20px;">전체 아카이브 보기 →</a></div>' if SITE_URL else ""

    # 수집량 급감 경보 — 조용히 3건짜리 브리핑이 나가던 문제를 메일에서 바로 알아채기 위함.
    h = today.get("health") or {}
    alert = (
        f'<div style="background:#fff4f3;border:1px solid #f3c9c6;border-radius:10px;'
        f'padding:12px 14px;margin-bottom:18px;font-size:12.5px;line-height:1.65;color:#8a3a35;">'
        f'⚠️ <b>수집량이 평소보다 적습니다</b> — 오늘 {h.get("count")}건 · 최근 {h.get("dayType","")} '
        f'평균 수준 {h.get("median")}건 대비 <b>{int(h.get("ratio", 0) * 100)}%</b>.<br>'
        f'뉴스가 적었을 수도 있지만, 수집 단계가 일부 실패했을 가능성이 있습니다.</div>'
    ) if h.get("low") else ""

    html = f"""<!DOCTYPE html><html><body style="margin:0;background:#f4f6f8;">
  <div style="max-width:680px;margin:0 auto;padding:24px 16px;font-family:'Apple SD Gothic Neo','Malgun Gothic',sans-serif;color:#333;">
    <div style="border-bottom:2px solid #1c1c1c;padding-bottom:12px;margin-bottom:22px;">
      <div style="font-size:20px;font-weight:800;color:#1c1c1c;">📰 AI Morning Brief</div>
      <div style="font-size:13px;color:#555;margin-top:4px;">{esc(date)} · SK이노베이션 계열 핵심 동향</div>
      <div style="font-size:12px;color:#888;margin-top:6px;">📊 총 {len(cards)}건 수집{(' · 수집 기간 ' + esc(win)) if win else ''}</div>
      {f'<div style="font-size:12px;color:#888;margin-top:4px;">🗂 {esc(divsum)}</div>' if divsum else ''}
    </div>
    {alert}{hero}
    <div style="font-size:14px;font-weight:700;color:#1c1c1c;margin:0 0 6px;">전체 수집 기사 <span style="color:#999;font-weight:400;">({len(cards)}건 · 영향력순)</span></div>
    <table style="width:100%;border-collapse:collapse;background:#fff;border-radius:12px;overflow:hidden;">
      <tr style="background:#f7f8fa;">
        <td style="padding:8px;font-size:11px;font-weight:700;color:#99a;text-align:center;width:24px;">#</td>
        <td style="padding:8px;font-size:11px;font-weight:700;color:#99a;white-space:nowrap;">부문</td>
        <td style="padding:8px;font-size:11px;font-weight:700;color:#99a;">기사 · 출처</td>
        <td style="padding:8px;font-size:11px;font-weight:700;color:#99a;text-align:center;width:44px;">영향력</td>
      </tr>{''.join(rows)}</table>
    {site_btn}
    <div style="text-align:center;font-size:11px;color:#aab;padding:16px 0;">본 브리핑은 AI가 자동 생성했습니다 · 원문 링크로 내용을 검증하세요</div>
  </div></body></html>"""
    return {"to": recipients, "subject": subject, "htmlBody": html}


def send_via_gmail_api(msg: dict) -> str:
    """OAuth refresh token으로 access token 발급 후 Gmail API 발송. message id 반환."""
    import urllib.parse
    import urllib.request

    cid = os.environ["GMAIL_CLIENT_ID"]
    secret = os.environ["GMAIL_CLIENT_SECRET"]
    refresh = os.environ["GMAIL_REFRESH_TOKEN"]

    tok_req = urllib.request.Request(
        "https://oauth2.googleapis.com/token",
        data=urllib.parse.urlencode({
            "client_id": cid, "client_secret": secret,
            "refresh_token": refresh, "grant_type": "refresh_token",
        }).encode(),
        method="POST",
    )
    with urllib.request.urlopen(tok_req, timeout=30) as r:
        access = json.load(r)["access_token"]

    mime = MIMEText(msg["htmlBody"], "html", "utf-8")
    mime["To"] = ", ".join(msg["to"])
    mime["Subject"] = msg["subject"]
    raw = base64.urlsafe_b64encode(mime.as_bytes()).decode()

    send_req = urllib.request.Request(
        "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
        data=json.dumps({"raw": raw}).encode(),
        headers={"Authorization": f"Bearer {access}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(send_req, timeout=30) as r:
        return json.load(r)["id"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=str(REPO / "docs" / "data"))
    ap.add_argument("--to", default="jupiter@sk.com")
    ap.add_argument("--out", default="/tmp/email_output.json")
    ap.add_argument("--send", action="store_true")
    a = ap.parse_args()

    today = json.loads((Path(a.data) / "today.json").read_text(encoding="utf-8"))
    recipients = [x.strip() for x in a.to.split(",") if x.strip()]
    msg = build_email(today, recipients)
    Path(a.out).write_text(json.dumps(msg, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[EMAIL] 제목: {msg['subject']}")
    print(f"[EMAIL] 수신: {', '.join(recipients)} · 본문 {len(msg['htmlBody'])}자 → {a.out}")

    has_creds = all(os.environ.get(k) for k in ("GMAIL_CLIENT_ID", "GMAIL_CLIENT_SECRET", "GMAIL_REFRESH_TOKEN"))
    if a.send and has_creds:
        try:
            mid = send_via_gmail_api(msg)
            print(f"[SENT] Gmail API message id: {mid}")
            return 0
        except Exception as e:  # 발송 실패 → 폴백 안내
            print(f"[SEND-FAILED] {e}", file=sys.stderr)
            print("[DRAFT-NEEDED] Gmail MCP create_draft 폴백 필요")
            return 0
    print("[DRAFT-NEEDED] 발송 열쇠 없음 또는 --send 미지정 → Gmail MCP create_draft 폴백 필요")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
