# SKI News Archive

SK이노베이션 계열 7개 사업부문의 데일리 뉴스 아카이브.
매일 아침 뉴스를 수집·분류·요약해 정적 사이트를 갱신하고 이메일 브리핑을 보낸다.

**사이트:** https://jayk-resonance.github.io/SKI-News-Archive/

## 구조

```
content/YYYY-MM-DD/*.md   기사 원본(front-matter). 이 저장소의 진짜 데이터
content/insights/*.md     날짜별 PICK 인사이트
tools/                    수집 진단·정규화·기록·집계·빌드 (전부 코드, LLM 미사용)
config/                   7부문 정의·검색어(divisions.py), 출처 신뢰등급(sources.py)
daily_briefing/           일일/주간 이메일 생성기 + 루틴 프롬프트 원본
docs/                     GitHub Pages로 배포되는 정적 사이트
docs/data/*.json          build.py 산출물. 사이트가 실제로 읽는 파일
```

## 파이프라인

`Exa 수집` → `normalize.py`(KST 창·중복 병합) → **분류·요약**(LLM) → `write_articles.py`
→ **PICK 인사이트**(LLM) → `build.py` → 커밋·푸시 → Pages 배포 → 이메일 발송

LLM이 하는 일은 분류·요약·인사이트 3가지뿐이고, 나머지는 전부 파이썬 코드다.
전체 순서와 판정 규칙은 [`daily_briefing/routine_prompt.md`](daily_briefing/routine_prompt.md)에 있다 —
이 파일이 매일 실행되는 스케줄 루틴의 지침 원본이다.

## 수동 실행

```bash
python tools/build.py                       # content/ → docs/data/ 재빌드
python -m http.server -d docs 8000          # 로컬 미리보기
```

배포 브랜치는 `main`이며, GitHub Pages는 `main`의 `/docs` 폴더에서 서빙한다.
