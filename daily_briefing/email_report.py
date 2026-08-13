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
# 인사이트 소제목·강조에 쓰는 단일 강조색. 부문별 색을 쓰면 날마다 톤이 바뀌어 산만하다.
ACCENT = "#3b3fa8"
RULE = "#e4e7ee"      # 표 구분선
MUTED = "#8b919c"     # 보조 텍스트
LINK = "#0563c1"      # 기사 제목 하이퍼링크 색(Word/Outlook 기본 링크색과 동일 — 링크임이 바로 보이게)

# 표에서 부문 그룹 순서. build.py의 표준 7부문 순서와 동일하게 맞춘다.
DIVISION_ORDER = ["배터리·소재", "정유·트레이딩", "석유화학", "윤활유·기유", "E&P", "LNG", "전력·수소"]


def _division_rank(div: str) -> int:
    try:
        return DIVISION_ORDER.index(div)
    except ValueError:
        return len(DIVISION_ORDER)


def esc(s) -> str:
    return (str(s) if s is not None else "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# 수치·단위는 코드가 굵게 처리한다. LLM이 굵게 표시하는 건 '판단·결론'이고, 정량 사실은
# 규칙으로 확실히 잡는 편이 빠짐이 없다. 긴 단위를 먼저 둬야 '억원'이 '원'으로 잘리지 않는다.
_NUM_UNIT = re.compile(
    r"(?<![\w.])\d[\d,]*(?:\.\d+)?\s*"
    r"(?:%p|%|억달러|만달러|달러|조원|억원|만원|GWh|GW|TWh|MWh|MW|kWh|"
    r"만배럴|배럴|bpd|만톤|톤|만대|개월|분기|년|배)(?!\w)")


def _autobold(line: str) -> str:
    """이미 굵은 구간(**…**)은 건드리지 않고, 바깥의 수치+단위만 굵게."""
    parts = re.split(r"(\*\*.+?\*\*)", line)
    return "".join(p if p.startswith("**") else _NUM_UNIT.sub(r"<b>\g<0></b>", p)
                   for p in parts)


def md_to_html(t: str) -> str:
    """인사이트 마크다운(##, **bold**, 문단) → 간단 HTML.

    Outlook(Word 렌더링 엔진) 대응: 음영·라운드 코너를 쓰지 않고, 소제목은 색·굵기와
    아래 실선만으로 구분한다. 배경색 블록은 Outlook에서 문단 사이가 흰색으로 끊겨 보인다.
    """
    out, inp = [], False
    for ln in esc(t).split("\n"):
        ln = _autobold(ln)
        ln = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", ln)
        if re.match(r"^##\s+", ln):
            if inp:
                out.append("</p>"); inp = False
            out.append(
                '<div style="margin:22px 0 9px;padding-bottom:5px;border-bottom:1px solid #d8dbe4;">'
                f'<span style="font-size:14.5px;font-weight:800;color:{ACCENT};'
                f'letter-spacing:-.2px;">{ln[3:].strip()}</span></div>')
        elif not ln.strip():
            if inp:
                out.append("</p>"); inp = False
        else:
            if not inp:
                out.append('<p style="margin:0 0 10px;font-size:14px;line-height:1.75;color:#2f3338;">'); inp = True
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
    """지난 브리핑 양식: 수집 건수·기간 헤더 → TOP STORY 히어로 → 부문별·영향력순 표."""
    date = today.get("date", "")
    cards = sorted(today.get("cards", []),
                   key=lambda c: (_division_rank(c.get("division", "")), -c.get("impact_score", 0)))
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

    # --- AI Insight (오늘의 PICK 심층분석) ---
    # 음영을 쓰지 않는다: Outlook은 배경색 블록 안의 문단 사이를 흰색으로 끊어 그린다.
    # 구분은 왼쪽 굵은 실선 + 소제목 아래 실선으로만 준다.
    hero = ""
    if pick and insight.get("markdown"):
        cc = CAT.get(pick["division"], (ACCENT, "#ecedfc"))
        purl = pick.get("leadUrl") or (pick.get("sources") or [{}])[0].get("url", "")
        sc = int(pick.get("impact_score", 0))
        meta = esc(pick["division"])
        if pick.get("leadPublisher"):
            meta += " · " + esc(pick["leadPublisher"])
        hero = f"""
      <div style="border-left:3px solid {ACCENT};padding:2px 0 2px 18px;margin:0 0 30px;">
        <div style="font-size:12.5px;font-weight:800;letter-spacing:.04em;color:{ACCENT};">AI Insight</div>
        <div style="font-size:19px;font-weight:800;margin:9px 0 7px;line-height:1.4;letter-spacing:-.3px;">
          <a href="{esc(purl)}" style="color:{LINK};text-decoration:none;">{esc(pick.get('title_ko') or pick['title'])}</a></div>
        <div style="font-size:11.5px;color:{MUTED};">
          <span style="color:{cc[0]};font-weight:700;">{meta}</span>
          <span style="color:#c9ced8;">&nbsp;&nbsp;|&nbsp;&nbsp;</span>영향력 <span style="color:{_score_color(sc)};font-weight:800;">{sc}</span></div>
        <div style="margin-top:6px;">{md_to_html(insight['markdown'])}</div>
        {f'<div style="margin-top:18px;padding-top:13px;border-top:1px solid {RULE};"><a href="{esc(SITE_URL)}?insight={esc(date)}" style="color:{ACCENT};font-size:13px;font-weight:700;text-decoration:none;">웹에서 원문 기사·전문 보기 &rarr;</a></div>' if SITE_URL else ''}
      </div>"""

    # --- 전체 수집 기사(부문별·영향력순) 번호매김 표 ---
    # 부문은 칩(배경색) 대신 제목 위 색 텍스트로 올린다 — Outlook에서 배경 칩이 깨진다.
    # 요약은 분류 단계에서 이미 만들어 둔 card['summary']를 그대로 쓴다(추가 LLM 호출 없음).
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
        summary = (c.get("summary") or "").strip()
        if len(summary) > 170:
            summary = summary[:169].rstrip() + "…"
        rows.append(f"""
        <tr>
          <td width="26" valign="top" style="padding:15px 8px 15px 0;border-bottom:1px solid {RULE};text-align:center;color:#b9bec8;font-size:12px;font-weight:700;">{i}</td>
          <td valign="top" style="padding:15px 0;border-bottom:1px solid {RULE};">
            <div style="font-size:11px;font-weight:800;color:{cc[0]};letter-spacing:.02em;margin-bottom:4px;">{esc(c['division'])}</div>
            <div style="font-size:14px;line-height:1.45;">
              <a href="{esc(url)}" style="color:{LINK};text-decoration:none;font-weight:700;">{esc(c.get('title_ko') or c['title'])}</a></div>
            {f'<div style="font-size:12.5px;line-height:1.7;color:#5b616b;margin-top:6px;">{esc(summary)}</div>' if summary else ''}
            <div style="color:{MUTED};font-size:11px;margin-top:6px;">{meta}</div></td>
          <td width="42" valign="top" align="center" style="padding:15px 0 15px 10px;border-bottom:1px solid {RULE};font-weight:800;font-size:15px;color:{_score_color(sc)};">{sc}</td>
        </tr>""")

    site_btn = f'<div style="text-align:center;margin:24px 0 8px;"><a href="{esc(SITE_URL)}" style="display:inline-block;background:#1c1c1c;color:#fff;text-decoration:none;font-size:13px;font-weight:600;border-radius:8px;padding:10px 20px;">전체 아카이브 보기 →</a></div>' if SITE_URL else ""

    # 수집량 급감 경보 — 조용히 3건짜리 브리핑이 나가던 문제를 메일에서 바로 알아채기 위함.
    h = today.get("health") or {}
    alert = (
        f'<div style="border-left:3px solid #c8352d;padding:2px 0 2px 14px;margin:0 0 24px;'
        f'font-size:12.5px;line-height:1.7;color:#a33831;">'
        f'<b>수집량이 평소보다 적습니다</b> — 오늘 {h.get("count")}건 · 최근 {h.get("dayType","")} '
        f'평균 수준 {h.get("median")}건 대비 <b>{int(h.get("ratio", 0) * 100)}%</b>.<br>'
        f'뉴스가 적었을 수도 있지만, 수집 단계가 일부 실패했을 가능성이 있습니다.</div>'
    ) if h.get("low") else ""

    # Pretendard 웹폰트: Apple Mail·Gmail 웹/앱·신형(Chromium) Outlook은 @font-face를 그려낸다.
    # 구 Outlook 데스크톱(Windows, Word 렌더링 엔진)은 웹폰트 자체를 지원하지 않아 이 선언을
    # 무시하고 font-family 스택의 다음 순위(맑은 고딕)로 폴백한다 — Word 엔진의 알려진 한계이며
    # 이 파일이 손댈 수 있는 범위 밖이다.
    html = f"""<!DOCTYPE html><html><head><meta charset="utf-8"><style>
@font-face{{font-family:'Pretendard';font-weight:400;font-style:normal;font-display:swap;src:url('https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/static/woff2/Pretendard-Regular.woff2') format('woff2');}}
@font-face{{font-family:'Pretendard';font-weight:700;font-style:normal;font-display:swap;src:url('https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/static/woff2/Pretendard-Bold.woff2') format('woff2');}}
@font-face{{font-family:'Pretendard';font-weight:800;font-style:normal;font-display:swap;src:url('https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/static/woff2/Pretendard-ExtraBold.woff2') format('woff2');}}
</style></head><body style="margin:0;background:#ffffff;">
  <div style="max-width:680px;margin:0 auto;padding:26px 18px;font-family:'Pretendard','Apple SD Gothic Neo','Malgun Gothic','맑은 고딕',sans-serif;color:#2f3338;">
    <div style="border-bottom:2px solid #15181d;padding-bottom:13px;margin-bottom:26px;">
      <div style="font-size:20px;font-weight:800;color:#15181d;letter-spacing:-.4px;">SK이노베이션 계열 사업부문별 동향</div>
      <div style="font-size:13px;color:#5b616b;margin-top:6px;">{esc(date)}{(' · 수집 기간 ' + esc(win)) if win else ''}</div>
      <div style="font-size:12px;color:{MUTED};margin-top:5px;">총 {len(cards)}건 수집{(' · ' + esc(divsum)) if divsum else ''}</div>
    </div>
    {alert}{hero}
    <div style="font-size:15px;font-weight:800;color:#15181d;margin:0 0 2px;letter-spacing:-.2px;">전체 수집 기사 <span style="color:{MUTED};font-weight:500;font-size:13px;">{len(cards)}건 · 부문별·영향력순</span></div>
    <table width="100%" cellpadding="0" cellspacing="0" border="0" style="border-collapse:collapse;">{''.join(rows)}</table>
    {site_btn}
    <div style="text-align:center;font-size:11px;color:#a8aeb8;padding:18px 0;">본 브리핑은 AI가 자동 생성했습니다 · 원문 링크로 내용을 검증하세요</div>
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
