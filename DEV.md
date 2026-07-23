# DEV.md - 개발 가이드

> 선수개념 진단형 RAG 튜터 — 강의 콘텐츠를 검색해 '선수개념 결손'을 진단, 답 대신 질문으로 유도
> **현재 구현 = Python RAG (`app.py`).** LLM = Gemini(무료). 아래 Supabase-JS 계획은 나중에 '학습로그 계정 저장' 붙일 때만 참고(현재 데모엔 불필요).

## ▶ 실행 (지금 이거)
```bash
pip install -r requirements.txt
# Windows PowerShell:  $env:GEMINI_API_KEY="..."   (aistudio.google.com 무료 발급)
uvicorn app:app --reload      # → http://localhost:8000
python app.py                 # API 없이 검색로직 셀프체크만
```
파일: `app.py`(청킹·임베딩·검색·생성) · `system_prompt.md`(튜터 규칙) · `lecture_info1.txt`(정보이론 1강 프리셋) · `index.html`(채팅)

---


## Requirements
- [ ] 강의 콘텐츠 입력(텍스트/업로드), '정보이론 1강' 프리셋 제공
- [ ] 진단 대화: AI가 정답 대신 질문으로 수준·선수개념 파악
- [ ] 선수개념 결손 감지 → "먼저 X(확통)가 필요" backfill 선언
- [ ] 유도형 튜터링(폴리아 4단계·오개념 선제 대응·워크드예제 페이딩)
- [ ] 숙달 확인 후 원 강의 복귀(Bloom)
- [ ] 로그인(Supabase Auth) 후 진단 이력·취약 개념 계정 저장
- [ ] 발표에서 라이브 시연 가능(로컬/정적 호스팅 + Supabase)

## Non-goals
- 결제·구독, 다과목 선수개념 그래프(1쌍만), 모바일/네이티브, 초·중·고 확장(비전만)

## Style
- 톤: 차분·학습 친화(보라 포인트 #5b2a86, 화이트 배경), 한국어 UI
- 채팅 중심 레이아웃 + 우측 '선수개념 진단' 패널
- 모바일/데스크톱 반응형(Tailwind sm:/md:/lg:)

## Key Concepts
- **선수개념 결손 / 숨은 선수과목**: 공식 선수과목엔 없으나 실제 필요한 개념(정보이론↔확통). 정보이론 1강=확률 복습인 실제 사례로 검증.
- **backfill**: 결손 선수개념을 과목 경계 넘어 먼저 보완
- **유도형(Socratic)**: 답이 아니라 질문·힌트로 스스로 도달

## Open Questions
- LLM 모델: **Gemini(권장 — 무료 티어)**. 데모는 `gemini-2.5-flash`로 충분. 모델명은 Edge Function env로 관리. (무료 티어는 데이터가 학습에 쓰일 수 있음 → 데모엔 무방)
- Auth 범위: 데모는 이메일 매직링크 또는 익명 로그인으로 단순화할지
- 파일 업로드(PDF 파싱)까지 데모에 넣을지, 텍스트 붙여넣기 + 프리셋만으로 갈지(권장: 후자)

---

## 선택된 개발 구조 — Supabase JS
- **Frontend**: `index.html` + `app.js` — Supabase JS 클라이언트를 소비하는 경량 SPA(React CDN 또는 vanilla). 채팅·진단 패널 UI.
- **Auth**: Supabase Auth(이메일 매직링크/익명). 로그인 후 개인 학습 로그 연결.
- **Database**: Supabase PostgreSQL + **RLS**(본인 데이터만 접근). 테이블: `sessions`, `messages`, `weak_concepts`.
- **LLM 프록시**: **Supabase Edge Function `tutor`** 가 **Gemini API(Google AI Studio, 무료 티어)** 를 호출(⚠️ API 키는 Edge Function secret에 보관, 프론트 노출 금지). 시스템 프롬프트(`05_데모_LLM_시스템프롬프트.md`)는 Gemini의 `systemInstruction` 필드에 그대로 전달.
- **왜 Supabase**: 로그인+DB를 커스텀 백엔드 없이 즉시 확보 → '취약 개념 누적(지식 지도)' 차별화 기능을 데모에서 실제로 보여줄 수 있음. RLS로 보안 자동화.

## 프로젝트 구조
```
/
├── index.html            # 앱 진입(로그인/입력/채팅 뷰)
├── app.js                # 앱 로직(뷰 전환, 채팅, 진단 패널)
├── supabase.js           # Supabase 클라이언트 init(URL/anon key)
├── auth.js               # 로그인/로그아웃/세션
├── prototype-v1.html     # [Phase 1] 더미 데이터 프로토타입(서버·키 불필요)
├── supabase/
│   ├── schema.sql        # 테이블 + RLS 정책
│   └── functions/tutor/  # Edge Function(Gemini 프록시)
│       └── index.ts
├── package.json
├── .env.example
└── .gitignore
```

---

## 📋 TODO List

### Phase 1: 디자인 & 프로토타이핑
- [ ] 🟢 UI 프로토타입 — `prototype-v1.html` (더미 데이터, 서버 불필요, 브라우저로 직접 열기)
  - 화면: ①입력(강의 콘텐츠 + '정보이론 1강' 프리셋 버튼) ②튜터 채팅(진단→backfill→유도 더미 대화) + 우측 '선수개념 진단' 패널 ③학습 로그(취약 개념 더미) ④로그인(더미 버튼)
- 📌 체크포인트: 더미로 전체 흐름(입력→채팅→진단패널→로그)이 눈에 보이고 뷰 전환 동작

### Phase 2: 기본 기능 (쉬운 것부터)
- [ ] 🟢 프로젝트 초기화(`package.json`, 정적 서버 `npx serve`)
- [ ] 🟢 `prototype-v1.html` → `index.html` + `app.js` 전환/리팩토링
- [ ] 🟢 강의 콘텐츠 입력 상태 관리 + 프리셋 로드(정보이론 1강 텍스트)
- [ ] 🟢 채팅 UI 동작(메시지 리스트/입력창, 우선 로컬 더미 응답)
- [ ] 🟡 선수개념 진단 패널(감지 개념 카드, 로컬 상태 연동)
- 📌 체크포인트: 브라우저에서 입력→채팅→진단패널 플로우가 (LLM 없이) 실제로 동작

### Phase 2.5: 플랫폼 연결 검증 (Supabase)
- [ ] 🟡 Supabase 프로젝트 생성, `supabase.js` init(URL/anon key)
- [ ] 🟡 Auth 연결(이메일 매직링크 또는 익명) — 로그인/로그아웃
- [ ] 🟡 `schema.sql` 적용: `sessions/messages/weak_concepts` + **RLS**
- [ ] 🟡 로그인 상태에서 세션·메시지 DB read/write 1회 왕복 확인
- 📌 체크포인트: 실제 Supabase에서 로그인 + 데이터 저장/조회가 동작

### Phase 3: 핵심 & 어려운 기능 (불확실한 것부터)
- [ ] 🔴 **LLM 튜터링 파이프라인** ⚠️ 가장 불확실 — Edge Function `tutor`가 Gemini `generateContent` 호출(systemInstruction=파일05), 응답 반환. 우회안: Edge Function 대신 로컬 프록시 스크립트로 시연
- [ ] 🔴 진단→backfill→유도 상태 머신(대화 단계 추적) ⚠️ 우회안: 프롬프트에 단계 규칙 내장해 LLM이 스스로 진행
- [ ] 🟡 결손 선수개념 감지 결과를 `weak_concepts`에 저장 → 재방문 시 반영
- [ ] 🟡 오개념 선제 대응/워크드예제 페이딩 프롬프트 튜닝(정보이론↔확통)
- 📌 체크포인트: 정보이론 1강 프리셋으로 진단→확통 backfill→유도→복귀가 실제 LLM으로 작동

### Phase 4: 마무리 & 배포
- [ ] 🟡 UI 폴리싱·로딩/에러/빈 상태 처리
- [ ] 🟡 발표용 데모 안정화(프리셋 시나리오 리허설)
- [ ] 🟡 정적 호스팅(Vercel/Netlify) + Supabase 연결, 발표 링크 확보
- 📌 체크포인트: 배포된 링크에서 데모가 재현 가능

📌 각 Phase 완료 후: `git commit`(세이브포인트) → 동작 확인 → 실패 시 이전 커밋 롤백

---

## 🔧 외부 설정 필요 항목

### 필수 (Must Have)
| 항목 | 설명 | 획득 방법 |
|------|------|----------|
| SUPABASE_URL | 프로젝트 URL | supabase.com 프로젝트 생성 → Settings > API |
| SUPABASE_ANON_KEY | 프론트용 공개 키(RLS로 보호) | 동 위치 API 탭 |
| GEMINI_API_KEY | Gemini API 키 (**Edge Function secret에만** 저장) | **aistudio.google.com → Get API key (무료)**. `supabase secrets set GEMINI_API_KEY=...` |
| GEMINI_MODEL | 사용할 모델명(무료 티어: `gemini-2.5-flash`) | Edge Function env |

### 선택 (Nice to Have)
| 항목 | 설명 |
|------|------|
| Vercel/Netlify 계정 | 정적 프론트 배포용 |
| Supabase CLI | `npm i -g supabase` — Edge Function 배포·secrets 관리 |

> ⚠️ `GEMINI_API_KEY`는 절대 `index.html`/`app.js`/anon key와 함께 두지 말 것. 반드시 Edge Function 서버측에만. (Gemini 엔드포인트: `https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent`)

---

## 시작하기
```bash
# 0) 프로젝트 폴더에서
cd "C:/Users/JongyoonWon/Documents/GitHub/hufs-ares-prereq-tutor-supabase"

# 1) Phase 1: 서버·키 없이 프로토타입 확인
#    prototype-v1.html 을 브라우저로 직접 열기 (더블클릭 또는 Live Server)

# 2) Phase 2: 정적 서버로 실행
npm init -y
npx serve .        # http://localhost:3000

# 3) Phase 2.5: Supabase
#    supabase.com 에서 프로젝트 생성 → .env 에 URL/anon key 기입
#    supabase/schema.sql 을 SQL Editor에 실행 (테이블+RLS)

# 4) Phase 3: Edge Function(LLM 프록시)
npm i -g supabase
supabase login
supabase functions new tutor
supabase secrets set GEMINI_API_KEY=...   # aistudio.google.com 에서 무료 발급
supabase functions deploy tutor
```
