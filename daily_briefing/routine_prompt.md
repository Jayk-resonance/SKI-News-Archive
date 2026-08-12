# Daily SK이노베이션 계열 뉴스 브리핑 루틴

너는 이 저장소(`Jayk-resonance/SKI-News-Archive`)에서 매일 SK이노베이션 계열 7개 사업부문
뉴스를 수집·분석해 정적 사이트를 갱신하고 이메일을 보낸다. 아래 순서를 질문 없이 자율로
수행하라. **분류·인사이트 작성은 네가 직접 한다(별도 API 키 불필요).** 수집·정규화·기록·빌드는
저장소의 파이썬 도구를 호출한다. 각 단계 실패 시 로그를 남기고 가능한 다음 단계로 진행하라.

- 작업 브랜치(개발·푸시·배포): **`main`** (이 저장소는 SKI 브리핑 전용 — GitHub Pages도 `main`의 `/docs`에서 배포)
- 하루 총 수집 목표: ~30건 (부문별 배분은 `config/divisions.py`의 `daily_target`, 배터리는 `materials_target`)

**모델 티어링(비용 최적화):** 이 루틴의 **메인 모델은 Sonnet 권장**(오케스트레이션 + 분류 + 인사이트).
토큰이 많이 드는 **수집(Step 1)은 `collector` 서브에이전트(Haiku)에 위임**하면 가장 좋다 — 정의는
`.claude/agents/collector.md`(git으로 자동 전파). **단, `Task`(Agent) 도구가 없으면 서브에이전트를
못 띄우므로**, 그때는 메인에서 직접 수집하는 폴백을 쓴다(아래 Step 1 참고). 서브에이전트는 **1단계
평면 구조**로만 쓴다(수집 서브에이전트가 또 다른 서브에이전트를 부르지 않는다).

## Step 0 — 준비
```
git fetch origin main && git checkout main && git pull origin main
python tools/clear_seed.py   # 예시(시드) 데이터 1회 제거. 멱등 — 이미 정리됐으면 무동작
```

## Step 1 — 수집 (collector 서브에이전트 우선, 없으면 메인 폴백)

**경로 A — `Task`(Agent) 도구가 있으면: `collector` 서브에이전트에 위임(권장).**
7개 부문 각각에 `collector` 서브에이전트를 하나씩 띄운다(병렬 가능). 각 호출 지시는 짧게:
> "부문 `<부문명>`의 뉴스를 수집해 `/tmp/exa_raw_<safe>.json`에 저장하고 한 줄 요약만 반환하라."

`collector`는 자기 부문 쿼리(`config/divisions.py`)를 Exa 고급검색(발행일 48시간, 하이라이트만,
`textMaxCharacters:10`)으로 돌려 `title/url/publishedAt/snippet/publisher/division` 압축 JSON을
파일에 쓰고 **한 줄 통계만** 반환한다. 세부는 `.claude/agents/collector.md`. 원본이 메인 컨텍스트에
안 들어와 토큰을 크게 아끼고, Haiku로 처리된다.
부문명: `배터리·소재`, `정유·트레이딩`, `석유화학`, `윤활유·기유`, `E&P`, `LNG`, `전력·수소`.

**경로 B — `Task` 도구가 없으면(서브에이전트 불가): 메인에서 직접 수집.**
`.claude/agents/collector.md`의 절차·규칙을 **네가 직접** 그대로 수행한다. **반드시 하이라이트만 받아라**
(`textMaxCharacters:10`, `enableHighlights:true`, `highlightsMaxCharacters:250`) — 전문(text)을 받으면
컨텍스트가 폭증한다. 부문별로 `/tmp/exa_raw_<safe>.json`에 저장한다.

**⚠️ 경로 A·B 공통 — Exa 호출에 `category: "news"`를 반드시 넣어라.**
빠뜨리면 뉴스가 아닌 문서가 절반 넘게 섞인다. 2026-08-12에 이걸 빠뜨린 채 돌린 결과 정유
쿼리 15건 중 실제 기사는 3건뿐이었고 나머지는 인스타그램 릴스·부품 쇼핑몰·주식 시세
페이지였다. 같은 쿼리에 `category:"news"`만 넣으니 7건으로 늘었다. 날짜필터와 함께 써도
정상이다(400 에러가 나는 건 `category:"company"` 쪽이다).
`excludeDomains`에는 `config/sources.py`의 `BLOCKLIST_DOMAINS`를 넘긴다.

**공통 — 병합 + 수집 진단(코드):** 두 경로 모두 아래 한 줄로 병합하고 부문별 상태를 점검한다:
```
python tools/collect_check.py
```
출력은 부문별 수집 건수·목표 대비 표다. **종료 코드로 다음 행동이 정해진다 — 무시하지 마라.**

| 종료 코드 | 뜻 | 해야 할 일 |
|---|---|---|
| `0` | 정상 | Step 2로 진행 |
| `1` | 일부 부문 0건 (`[ALERT]`) | **그 부문만** 쿼리 재시도 → 다시 `collect_check.py`. 재시도 후에도 0건이면 진행하되 Step 9에 부문명과 이유를 적는다 |
| `2` | 수집 붕괴 (`[COLLECT-BLOCK]`) | **진행 금지.** 아래 절차를 밟는다 |

**`[COLLECT-BLOCK]`(종료 코드 2)일 때:**
1. Exa 호출에 `category:"news"`가 들어갔는지 **먼저 확인한다.** 빠졌으면 넣고 전 부문 재수집.
2. 들어갔는데도 붕괴면 **전 부문을 1회 재수집**한다(Exa 응답이 일시적으로 비는 경우가 있다).
3. 재수집 후에도 종료 코드 2이면 **커밋·발송하지 말고 Step 9로 건너뛰어** 실패로 보고한다.
   이때 수신자에게 **짧은 실패 알림 메일**을 보낸다(제목: `[SKI 브리핑] {today} 수집 실패`,
   본문: 부문별 수집 건수 표 + 원인 추정). 조용히 넘어가는 것이 가장 나쁜 결과다.

- 8/5~8/9에 7개 부문이 일제히 44~78% 줄었는데 아무 신호가 없어 4일간 발견되지 못했다.
  같은 시간대 Exa에는 기사가 그대로 있었다 — 즉 **수집 실패는 실제로 일어나며 조용히 일어난다.**
- 8/12에는 경보가 정확히 떴는데도(`오늘 3건 vs 중앙값 20건`) 그대로 커밋·발송됐다.
  **경보를 보고도 진행하는 것이 이 파이프라인의 가장 큰 실패 유형이다.**
결과 `/tmp/exa_raw.json`은 모든 부문을 합친 배열이며 각 항목에 `division` 포함:
```json
[{"division":"배터리·소재","title":"…","url":"https://…","publishedAt":"2026-…Z","snippet":"…","publisher":"연합뉴스"}]
```
수집 세부 규칙(발행일 필터·`category:"company"` 금지·Exa 실패 시 Naver 대체·전문 미수신)은
`.claude/agents/collector.md`에 정의돼 있다(경로 A·B 공통).

## Step 2 — 정규화 (코드)
```
python tools/normalize.py --raw /tmp/exa_raw.json --out /tmp/normalized.json
```
KST 창(전날 08:00~오늘 08:00) 밖·blocklist·무날짜 자동 제외. 당일 동일 사안은 카드 1개로
병합(sources[] 합침). 최근 아카이브와 대조해 각 카드에 `suggested_action`(NEW/MERGE_EXISTING/SIMILAR) 부여.

## Step 3 — 분류·판정 (네가 직접 = LLM 단계)
`/tmp/normalized.json`의 각 `cluster`를 판단해 `/tmp/classified.json`(배열)을 작성하라.

**(A) 3갈래 최종 action** (`suggested_action` 참고, 최종 결정):
- `NEW`: 신규 사안 → 새 기사
- `MERGE_EXISTING`: 사실상 동일 → 기존 기사에 출처만 추가. `mergeInto`=`match.articleId`
- `SIMILAR` → **LINK**(같은 사안의 새 전개: 새 기사로 만들되 타임라인 연결) 또는 **DROP**(새 내용
  없는 우려먹기: 기록 안 함). **애매하면 LINK로 편향**(정보 손실 지양).

**(B) 분류·요약 필드** (NEW/LINK만):
- `division`(7부문 중), `topic`(`tools/build.py`의 `TOPICS` 참고), `company`(SK 계열사/주요기업 or null)
- `entities`(등장 개체), `importance`(high/mid/low), `impact_score`(1~10: 산업영향+SK관련성+시장/정책 파급)
- `summary`(한국어 2~3문장, 전문 아님), `keywords`(표시 3~5), `tags`(숨김 검색용 동의어·개체·영문 5~10)
  - `keywords`는 **구체적 신호어**로. 지역(미국·유럽·중국…)·기간(2분기·상반기…)·초광의어(전기차·배터리 단독)처럼
    범용적인 단어는 keywords에 넣지 말고 필요하면 `tags`에만 둔다(통계 랭킹 노이즈 방지).
- LINK만: `eventId`(기존 사안 id 있으면 승계, 없으면 생략), `event_title`(선택)

**impact_score 채점 시 부문별 SK 계열 연관성을 핵심 가중치로 삼는다.** 각 부문의 초점은 그 부문
담당 SK이노베이션 계열사(`config/divisions.py`의 `sk_affiliates`)다:
배터리·소재=**SK온·SKIET**, 정유·트레이딩=**SK에너지·SK트레이딩인터내셔널**, 석유화학=**SK지오센트릭**,
윤활유·기유=**SK엔무브**, E&P=**SK어스온**, LNG=**SK E&S**, 전력·수소=**SK E&S·SK플러그하이버스**.
- 해당 계열사의 **제품·설비·실적·계약·경쟁구도**와 직접 맞닿을수록 impact_score를 높인다.
  (석유화학이면 SK지오센트릭의 화학적 재활용·넥슬렌/고부가 폴리올레핀·고순도 PP 등, 그 외 부문도 동일 원리.)
- SK 계열과 **직접 연관이 없는 순수 업계 일반론**(예: SK지오센트릭 제품군과 무관한 범용 석화 시황,
  반도체 소재 전환 등)은 impact_score 5 이하로 제한하고, 부문 상한 초과 시 위 (D)에 따라 우선 DROP한다.
- 경쟁사·정책 기사라도 SK 계열에 **시사점이 뚜렷하면** 관련성 가중을 인정한다(무조건 감점 아님).

**(C) 출처 신뢰도 반영**: `normalized.json`의 각 클러스터에는 `publisherTier`(1=상위, 2=중위,
3=미검증/미등록 매체 기본값)와 `credibility`가 이미 계산돼 있다. `publisherTier`가 3이면서
**단독 출처**(sources 1개)인 클러스터는 impact_score를 high 구간(8+)으로 올리지 말 것. 같은 사안을
tier1·2 매체가 함께 보도(교차검증)하면 그때 승급한다. 미검증 단독 보도로 판단되면 DROP도 적극 고려한다.

**(D) 저가치·비대상 기사 원칙적 DROP**: 아래에 해당하면 부문 목표에 여유가 있어도 DROP한다.
- **정례성·홍보성 기사**: ESG/지속가능경영보고서 발간, 사회공헌·후원, 단순 인사·조직개편,
  수상 소식, 정례 IR·공시 안내 등 "새 사업·산업 함의가 없는" 기사.
- **저impact 기사**: impact_score 4 이하이면서 SK 계열 직접 함의가 약한 기사.
- **비대상 주체 기사**: SK이노베이션 계열(각 부문 `sk_affiliates`)이 아닌 회사만 다루고
  업계 파급도 뚜렷하지 않은 기사. (예: SK케미칼은 SK디스커버리 소속이라 이 브리핑 대상이 아님 →
  단독 소재면 DROP.) 단, 경쟁사 동향이 SK에 시사점을 주면 남긴다.

부문별 상한(`daily_target` 합 ≈30)을 넘기지 말고, impact_score 낮은 중복성 기사부터 DROP.

항목 스키마:
```json
{"tmpId":"c1","action":"NEW|LINK|MERGE_EXISTING|DROP","division":"배터리·소재","topic":"공급망",
 "company":"SK온","entities":["SK온"],"importance":"high","impact_score":8,
 "summary":"…","keywords":["FEOC","공급망다변화"],"tags":["feoc","supply-chain","양극재"],
 "title_ko":"…","eventId":null,"event_title":null,"mergeInto":null}
```

**`title_ko`(제목 한국어 병기)**: 원제(title)가 **한국어가 아닌 경우**(영어 등), 원제를 자연스러운
한국어 제목으로 번역해 `title_ko`에 넣는다. 원제가 이미 한국어면 `title_ko`는 생략(null)한다.
사이트는 `title_ko`가 있으면 한글 제목을 크게, 원제를 작게 병기한다.

**제목 찌꺼기 제거**: 스크랩 제목에 붙는 사이트 네비게이션·언론사 꼬리표
(예: `< 일반 < 기업 < 기사본문 - IT조선`, `| 연합뉴스`, `- The Elec Inc.`)는 `title`(및 `title_ko`)에서
제거하고 **기사 본문 제목만** 남긴다.

**`eventId`/`event_title` 적극 부여(사건 타임라인 품질)**: 같은 **구체적 사건**(특정 회사·프로젝트·규제·리콜
등 주체가 뚜렷한 건)이 여러 날/여러 매체로 이어지면, 반드시 `eventId`로 이어 붙이고 `event_title`에 사건명을
한국어로 단다(예: "현대차·기아 SK온 배터리 화재 리콜"). 반대로 **유가·정제마진·LNG 가격 같은 시황·주제 흐름**은
특정 사건이 아니므로 억지로 `eventId`를 만들지 말 것 — 이런 건 사이트가 '시황·테마'로 자동 분류한다.
판단이 애매하면 **사건(고유 주체 있음)일 때만 eventId**를 부여한다.

## Step 4 — 기사 기록 (코드)
```
python tools/write_articles.py --classified /tmp/classified.json --normalized /tmp/normalized.json
```

## Step 5 — 오늘의 PICK 인사이트 (네가 직접 = LLM 단계)
오늘 생성/갱신된 기사 중 **impact_score 최고 1건**을 PICK으로 선정해 `content/insights/{today}.md`를
작성하라(600~700단어 한국어).

**굵게(`**text**`) 표시를 충분히 쓴다 — 훑어읽는 사람이 굵은 부분만 따라가도 요지가 잡혀야 한다.**
- 각 섹션(배경/핵심 내용/산업 영향/SK 관점에서의 시사점)마다 **최소 2~3군데**를 굵게 한다.
- 대상: 수치·시점·규모, 고유명(회사·프로젝트·규제명), 그리고 **그 문단의 결론에 해당하는 판단 문구**.
- 판단 문구를 빠뜨리지 마라. 수치는 코드가 자동으로 굵게 처리하므로(메일 렌더링 단계),
  네가 신경 쓸 것은 **"그래서 무슨 뜻인가"에 해당하는 서술**이다.
- 한 문단을 통째로 굵게 하지는 마라. 굵은 구간은 길어야 한 문장 이내다.
**⚠️ 날짜 정합성**: 인사이트 파일명 `{today}`와 PICK 기사는 **같은 브리핑 창**(브리핑일 = KST 창 끝 날짜,
`docs/data/today.json`의 `date`)에 속해야 한다. PICK은 반드시 **이번 창(today.json cards)에 실린 기사**에서
고른다 — 창 밖(어제·내일) 기사를 PICK으로 쓰면 웹/이메일에서 인사이트가 숨겨진다. `{today}`는
today.json의 `date`와 반드시 일치시킨다.
**단, `publisherTier`가 3이면서 단독 출처인 기사는 impact_score가 최고여도 PICK으로 쓰지 말 것** —
교차검증된 차순위 기사로 대체한다. 이 규칙 위반은 Step 6 빌드가
`[WARN] … PICK 기사가 저신뢰 단독 출처`로 검출한다.

**⚠️ 인사이트는 어떤 경우에도 건너뛰지 마라.** 후보가 빈약해도 아래 순서로 반드시 1건을 고른다:
1. tier1·2 기사 중 impact_score 최고 → 그대로 PICK.
2. 남은 게 **전부 단독 출처(sources 1개)**여도 상관없다 — 위 금지는 "tier3 **그리고** 단독"에만
   해당한다. tier1·2면 단독 출처여도 PICK으로 쓰되, 인사이트 본문에 **교차검증이 아직 없다**는
   점을 한 줄 밝힌다.
3. 정말로 tier3 단독 기사밖에 없으면 그중 최고점을 쓰되, 제목 아래에 **단독 보도 · 미검증**을
   명시한다.
2026-08-12에 후보 3건이 전부 단독 출처였는데 tier2 2건이 PICK 가능했음에도 인사이트를 통째로
빠뜨려 발송됐다. "빈약해서 못 쓰겠다"는 선택지는 없다.
```
---
pick: {기사 id}
division: {부문}
title: {인사이트 제목 — 반드시 한국어. PICK 기사가 영어면 번역하고, 언론사·breadcrumb 찌꺼기는 제거}
---
## 배경
## 핵심 내용
## 산업 영향
## SK 관점에서의 시사점
```

## Step 6 — 빌드 (코드)
```
python tools/build.py
```
검증 경고가 뜨면 해당 MD를 고친다.

**종료 코드 3(`[BLOCK] 수집량이 평소의 N%`)이면 커밋·발송 금지다.** 이건 마지막 관문이다 —
Step 1을 통과했더라도 최종 카드 수가 평소 대비 급감했으면 여기서 멈춘다.
- 아직 재수집을 안 했으면 Step 1로 돌아가 전 부문을 1회 재수집한 뒤 Step 6까지 다시 온다.
- 재수집 후에도 종료 코드 3이면 **Step 7(커밋)·Step 8(정상 메일)을 건너뛰고**, Step 1에
  적힌 대로 **짧은 실패 알림 메일**만 보낸 뒤 Step 9로 간다.

## Step 7 — 커밋 & 푸시 (코드) → Pages 자동 배포
```
git add content docs && git commit -m "chore(daily): {today} 브리핑 갱신" && git push origin main
```
network 오류 시 2s,4s,8s,16s 백오프로 최대 4회 재시도.

## Step 8 — 메일 (발송 또는 초안)
```
SITE_URL="https://jayk-resonance.github.io/SKI-News-Archive/" python daily_briefing/email_report.py --data docs/data --to jupiter@sk.com --send
```
- `[SENT] …` → 성공. 제목·수신자·id 보고 후 다음.
- `[DRAFT-NEEDED]` → Gmail MCP `create_draft`를 `/tmp/email_output.json`의 `to`·`subject`·`htmlBody`
  (수정 금지) 그대로 호출해 초안 생성. "자동 발송 실패 → 초안 대체" 명시.

## Step 8-1 — 주간 리포트 (금요일에만)
오늘이 **금요일이 아니면 이 단계를 건너뛴다.** 금요일이면 아래를 실행한다.
```
SITE_URL="https://jayk-resonance.github.io/SKI-News-Archive/" python daily_briefing/weekly_report.py --data docs/data --to jupiter@sk.com --send
```
- 최근 7일 집계(`docs/data/weekly.json`)로 주간 메일을 만든다. **LLM 호출 없음** — 전부 코드 집계다.
- `[SENT] …` → 성공. `[DRAFT-NEEDED]` → Gmail MCP `create_draft`를 `/tmp/weekly_email_output.json`의
  `to`·`subject`·`htmlBody`(수정 금지) 그대로 호출해 초안 생성.
- `[SKIP]` → 최근 7일 기사가 없다는 뜻. 그대로 넘어간다.

## Step 9 — 보고
수집→정규화→기록(NEW/LINK/MERGE/DROP 수)→PICK→커밋 해시→메일(발송/초안) 결과를 요약 출력.
금요일이면 주간 리포트 발송 결과도 한 줄 덧붙인다.
**수집 단계에서 0건이었던 부문이 있으면 부문명과 재시도 결과를 반드시 명시한다.**
`tools/build.py`가 `[WARN] 수집량 급감 …`을 출력했다면 그 줄을 그대로 보고에 포함한다.
```
