# app.py — 선수개념 진단 RAG 튜터 (데모 최소 구현)
# 실행: pip install -r requirements.txt  &&  GEMINI_API_KEY=... uvicorn app:app --reload
# 열기: http://localhost:8000
import os, re, json, pathlib, urllib.request
import numpy as np

HERE = pathlib.Path(__file__).parent

_envfile = HERE / ".env"  # ponytail: 수동 .env 로더, python-dotenv 의존성 안 씀
if _envfile.exists():
    for line in _envfile.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

SYSTEM = (HERE / "system_prompt.md").read_text(encoding="utf-8")
# gemini-2.5-flash는 신규 프로젝트에서 404(더 이상 제공 안 됨)가 난다.
# gemini-3-flash-preview는 기존·신규 키 모두에서 동작해 이쪽을 기본값으로 둔다.
GEN_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3-flash-preview")
EMB_MODEL = "gemini-embedding-001"

def api_keys():
    """GEMINI_API_KEY, GEMINI_API_KEY_2, _3 … 순서로 사용 가능한 키를 모은다."""
    keys, i = [os.environ.get("GEMINI_API_KEY")], 2
    while os.environ.get(f"GEMINI_API_KEY_{i}"):
        keys.append(os.environ[f"GEMINI_API_KEY_{i}"]); i += 1
    return [k for k in keys if k]

_KEY_IDX = 0     # 마지막으로 성공한 키. 매 호출마다 죽은 키를 다시 때리지 않기 위해 기억한다.
_KEY_DEAD = set() # 403(프로젝트 차단)처럼 복구되지 않는 키는 이후 호출에서 건너뛴다.

def gemini(path, body):
    """키를 여러 개 두고 실패 시 다음 키로 넘어간다.

    무료 등급에서 실제로 마주친 상황들:
      429 RESOURCE_EXHAUSTED  일일/분당 한도 초과 → 다른 키로 재시도(시간이 지나면 복구)
      404 NOT_FOUND           그 키의 프로젝트에서 해당 모델을 쓸 수 없음 → 다른 키로 재시도
      403 PERMISSION_DENIED   프로젝트 자체가 차단됨 → 복구되지 않으므로 그 키를 버린다
    """
    global _KEY_IDX
    keys = api_keys()
    if not keys:
        raise RuntimeError("GEMINI_API_KEY 환경변수를 설정하세요 (aistudio.google.com 무료 발급)")
    usable = [i for i in range(len(keys)) if i not in _KEY_DEAD] or list(range(len(keys)))
    order = [i for i in usable if i >= _KEY_IDX] + [i for i in usable if i < _KEY_IDX]
    last_err = None
    for i in order:
        # 키는 URL이 아니라 헤더로: URL은 프록시·접근 로그에 그대로 남는 자리다
        req = urllib.request.Request(
            f"https://generativelanguage.googleapis.com/v1beta/{path}",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json", "x-goog-api-key": keys[i]})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                if i != _KEY_IDX:
                    print(f"[gemini] {i+1}번 키로 전환")
                _KEY_IDX = i
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 403:
                if i not in _KEY_DEAD:
                    print(f"[gemini] {i+1}번 키 사용 불가(프로젝트 접근 거부) — 이후 건너뜀")
                _KEY_DEAD.add(i)
            elif e.code not in (429, 404):
                raise
            last_err = e
    raise last_err

def embed(text):
    r = gemini(f"models/{EMB_MODEL}:embedContent",
               {"model": f"models/{EMB_MODEL}", "content": {"parts": [{"text": text}]}})
    return np.array(r["embedding"]["values"], dtype=np.float32)

def chunk(text, size=500):
    parts, cur = [], ""
    for line in text.splitlines():
        cur += line + "\n"
        if len(cur) >= size:
            parts.append(cur.strip()); cur = ""
    if cur.strip():
        parts.append(cur.strip())
    return [p for p in parts if p]

def cosine_topk(mat, q, k):
    sims = mat @ q / (np.linalg.norm(mat, axis=1) * np.linalg.norm(q) + 1e-9)
    return [int(i) for i in sims.argsort()[::-1][:k]]

# 과목 프리셋 레지스트리. 각 과목: 표시명 + 코퍼스 텍스트 + 사전계산 벡터(.npy)
PRESETS = {
    "ling":   {"name": "언어학입문",         "txt": "lecture_ling.txt",   "vec": "lecture_ling_vecs.npy"},
    "survey": {"name": "사회과학 연구방법론", "txt": "lecture_survey.txt", "vec": "lecture_survey_vecs.npy"},
    "bizstat":{"name": "경영통계학",         "txt": "lecture_bizstat.txt","vec": "lecture_bizstat_vecs.npy", "unit": "주차"},
    "acct":   {"name": "회계원리",           "txt": "lecture_acct.txt",   "vec": "lecture_acct_vecs.npy",  "unit": "장"},
    "info":   {"name": "AI정보이론",         "txt": "lecture_info.txt",   "vec": "lecture_info_vecs.npy"},
    "ml":     {"name": "머신러닝",           "txt": "lecture_ml.txt",     "vec": "lecture_ml_vecs.npy"},
}

# 강의 원문은 저작권 때문에 배포본에 포함되지 않을 수 있다. 파일이 없는 과목은 목록에서 숨긴다.
PRESETS = {k: v for k, v in PRESETS.items() if (HERE / v["txt"]).exists()}

# 활성 과목 전역 상태
CHUNKS, LECTURE_NAME, SUBJECT_ID = [], "", ""
LECTURE_UNIT = "강"   # 근거칩 표기 단위 (과목별: 강 / 주차 / 장)
_VECS = None       # ponytail: in-memory 인덱스. 코퍼스 커지면 FAISS/pgvector
_VEC_FILE = None   # 활성 프리셋의 사전계산 벡터 경로 (업로드 과목은 None → 지연 임베딩)

def select_subject(sid):
    """프리셋 과목으로 전환"""
    global CHUNKS, LECTURE_NAME, SUBJECT_ID, _VECS, _VEC_FILE, LECTURE_UNIT, EXTRA_RULE
    EXTRA_RULE = ""          # 업로드 과목에서 넘어올 때 추가 지시를 남기지 않는다
    p = PRESETS[sid]
    CHUNKS = chunk((HERE / p["txt"]).read_text(encoding="utf-8"))
    LECTURE_NAME, SUBJECT_ID = p["name"], sid
    LECTURE_UNIT = p.get("unit", "강")
    _VECS, _VEC_FILE = None, HERE / p["vec"]

# 업로드 과목에 붙는 추가 지시. 계열·학년·과목 성격에 따라 되짚는 범위와 질문 방식이 달라진다.
EXTRA_RULE = ""

SERIES_RULE = {
    "lang":   "어문·인문계열 과목이다. 선수개념이 수식이 아니라 개념의 구분·용어 체계·분석 관점인 경우가 많다. "
              "계산을 묻지 말고, 두 개념의 차이를 자기 말로 구분해 설명할 수 있는지부터 확인한다.",
    "social": "사회과학계열 과목이다. 추상적 개념을 관찰 가능한 형태로 바꾸는 조작화, 자료 해석, 기초 통계가 자주 "
              "전제된다. 용어를 아는지보다 그 개념을 무엇으로 측정하는지 말할 수 있는지를 확인한다.",
    "biz":    "상경(경영·경제)계열 과목이다. 수식 자체는 단순한데 정의·시점·부호 규칙에서 막히는 경우가 많다. "
              "'왜 이 시점에 이 금액을 인식하는가'처럼 판단 근거를 묻는 방향으로 진단한다.",
    "stem":   "공학·자연과학계열 과목이다. 수식과 기호가 나오면 각 기호가 무엇을 가리키는지 말할 수 있는지 먼저 "
              "확인하고, 미적분·선형대수·확률 같은 수학 선수과목의 결손을 함께 점검한다.",
    "edu":    "사범·교육계열 과목이다. 교과내용 지식과 교육학 개념이 섞여 나오므로, 막힌 지점이 내용 지식 쪽인지 "
              "교육학 개념 쪽인지 먼저 구분한다.",
}
GRADE_RULE = {
    "y1":   "1학년 과목이다. 고등학교 교과에 대응물이 거의 없어, 결손이 상위 과목 미이수가 아니라 새 내용을 걸어둘 "
            "사전 지식이 아예 없는 형태로 나타난다. 필요하면 고교 수준 개념(비율·백분율, 함수와 그래프, 경우의 수, "
            "표·도수분포 읽기)까지 내려가 확인한다.",
    "y2":   "2학년 과목이다. 1학년 개론 과목에서 다룬 개념이 비어 있을 가능성이 크므로 그 수준부터 확인한다.",
    "y34":  "3~4학년 전공심화 과목이다. 선행 전공과목의 개념이 전제되므로, 어느 선행 과목의 개념이 비었는지 특정해 "
            "그 지점부터 짚어 올라간다.",
    "grad": "대학원 수준 과목이다. 학부 전공 전반이 전제되므로, 막힌 지점을 학부 어느 과목의 개념까지 되돌려야 "
            "하는지 함께 밝힌다.",
}
KIND_RULE = {
    "basic":   "전공기초(개론) 과목이다. 정의와 용어 자체가 선수개념이므로, 계산보다 정의를 자기 말로 말할 수 "
               "있는지를 먼저 확인한다.",
    "deep":    "전공심화 과목이다. 여러 선행 개념이 결합되어 나오므로, 막힌 부분을 갈래별로 분해해 어느 갈래가 "
               "비었는지 확인한다.",
    "general": "교양 과목이다. 전공 배경이 없는 학습자를 전제하고, 전문 용어가 나오면 먼저 풀어서 설명한 뒤 진단한다.",
}

def set_lecture(name, text, unit="", series="", grade="", kind=""):
    """업로드된 강의로 교체 (커스텀 과목, 다음 질의 때 지연 인덱싱)"""
    global CHUNKS, LECTURE_NAME, SUBJECT_ID, _VECS, _VEC_FILE, LECTURE_UNIT, EXTRA_RULE
    CHUNKS = chunk(text) or ["(빈 문서)"]
    LECTURE_NAME, SUBJECT_ID = name, "custom"
    LECTURE_UNIT = unit or "강"
    EXTRA_RULE = " ".join(r for r in (SERIES_RULE.get(series),
                                      GRADE_RULE.get(grade),
                                      KIND_RULE.get(kind)) if r)
    _VECS, _VEC_FILE = None, None

def lexical_topk(query, k):
    """임베딩을 쓸 수 없을 때의 폴백 검색.

    무료 API의 임베딩 한도를 초과하면(429) 질의 임베딩이 불가능해 검색 전체가 멈춘다.
    시연 중 화면이 죽는 것을 막기 위해 어절 겹침 점수로 대체한다.
    지식베이스가 과목당 12~13청크로 작아 실용적인 수준의 결과가 나온다.
    """
    toks = [t for t in re.split(r"[^0-9A-Za-z가-힣]+", query.lower()) if len(t) > 1]
    if not toks:
        return list(range(min(k, len(CHUNKS))))
    scored = sorted(range(len(CHUNKS)),
                    key=lambda i: -sum(CHUNKS[i].lower().count(t) for t in toks))
    return scored[:k]

def retrieve(query, k=4):
    global _VECS
    if _VECS is None:
        if _VEC_FILE and _VEC_FILE.exists():      # 프리셋: 사전계산 벡터 로드
            v = np.load(_VEC_FILE)
            if len(v) == len(CHUNKS):
                _VECS = v.astype(np.float32)
        if _VECS is None:                          # 커스텀/미계산: 1회 임베딩
            try:
                _VECS = np.stack([embed(c) for c in CHUNKS])
            except Exception as e:
                print(f"[retrieve] 코퍼스 임베딩 실패 → 어절 검색으로 대체: {e}")
                return [CHUNKS[i] for i in lexical_topk(query, k)]
    try:
        idx = cosine_topk(_VECS, embed(query), k)
    except Exception as e:
        print(f"[retrieve] 질의 임베딩 실패 → 어절 검색으로 대체: {e}")
        idx = lexical_topk(query, k)
    return [CHUNKS[i] for i in idx]

select_subject("ling")  # 기본 과목

def source_meta(hits):
    """검색에 실제 쓰인 청크를 UI 인라인 근거칩용 짧은 정보로 변환"""
    sources, seen = [], set()
    for i, text in enumerate(hits, 1):
        # 마커는 "p7"(단일 강의) 또는 "p3.7"(3강 7쪽) 두 형태를 쓴다
        pages = re.findall(r"===== p(\d+(?:\.\d+)?)", text)
        if pages:
            lec, _, pg = pages[-1].partition(".")
            # "p3.7" → 3강 7쪽 (원문 보유 과목) / "p3" → 3강 (분석 자료만 보유한 과목)
            label = f"{LECTURE_NAME} {lec}{LECTURE_UNIT} · {pg}쪽" if pg else f"{LECTURE_NAME} {lec}{LECTURE_UNIT}"
        else:
            label = f"{LECTURE_NAME} · 근거 {i}"
        if label in seen:
            continue
        seen.add(label)
        excerpt = re.sub(r"=====[^=]*=====|\[PAGES=\d+\]", " ", text)
        sources.append({"label": label, "excerpt": re.sub(r"\s+", " ", excerpt).strip()[:120]})
    return sources

def humanize(text):
    """청크 안의 내부 페이지 마커를 모델이 그대로 인용해도 읽히는 형태로 바꾼다."""
    text = re.sub(r"=====\s*p(\d+)\.(\d+)\s*=====",
                  lambda m: f"[{m.group(1)}{LECTURE_UNIT} {m.group(2)}쪽]", text)
    text = re.sub(r"=====\s*p(\d+)[a-z]?\s*=====",
                  lambda m: f"[{m.group(1)}{LECTURE_UNIT}]", text)
    return re.sub(r"=====\s*(.+?)\s*=====", r"[\1]", text)

def answer(messages):
    """messages: [{'role':'user'|'assistant','text':...}] → {reply, sources}"""
    last_user = next((m["text"] for m in reversed(messages) if m["role"] == "user"), "")
    hits = retrieve(last_user) if last_user else CHUNKS[:4]
    ctx = "\n---\n".join(humanize(h) for h in hits)   # 내부 마커(p3.5)가 답변에 노출되지 않게
    body = {
        "systemInstruction": {"parts": [{"text":
            SYSTEM
            + (f"\n\n[이 과목에 대한 추가 지시]\n{EXTRA_RULE}" if EXTRA_RULE else "")
            + "\n\n[검색된 강의 근거]\n" + ctx}]},
        "contents": [{"role": "model" if m["role"] == "assistant" else "user",
                      "parts": [{"text": m["text"]}]} for m in messages],
    }
    r = gemini(f"models/{GEN_MODEL}:generateContent", body)
    # 안전 필터·출력 한도에 걸리면 candidates가 비거나 parts가 없는 응답이 온다.
    # 그대로 파싱하면 500이라, 학습자에게는 정상 형태의 안내로 떨어뜨린다.
    try:
        reply = r["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError):
        reason = (r.get("candidates") or [{}])[0].get("finishReason", "빈 응답")
        print(f"[answer] 응답에 본문 없음: {reason}")
        reply = "죄송해요, 이번에는 답변을 만들지 못했어요. 질문을 조금 바꿔 다시 물어봐 주시겠어요?"
    return {"reply": reply, "sources": source_meta(hits)}

# Vercel FastAPI 감지를 위해 app은 반드시 top-level (try 블록 안이면 인식 못 함)
from fastapi import FastAPI
from fastapi.responses import FileResponse
from pydantic import BaseModel

app = FastAPI()

class ChatIn(BaseModel):
    messages: list
    subject: str = ""   # 화면이 보고 있는 과목. 서버 전역과 어긋나면 요청 기준으로 맞춘다.

class UploadIn(BaseModel):
    name: str
    text: str
    unit: str = ""      # 파일을 여러 개 올린 경우 근거칩 단위 표기(예: "번째 자료")
    series: str = ""    # lang | social | biz | stem | edu
    grade: str = ""     # y1 | y2 | y34 | grad
    kind: str = ""      # basic | deep | general

class SelectIn(BaseModel):
    id: str

@app.post("/api/chat")
def chat(inp: ChatIn):
    # 다른 탭이 과목을 바꿔 전역 상태가 어긋난 경우 요청이 말하는 과목으로 되돌린다.
    # ponytail: 전역 상태 자체는 그대로라 동시 요청은 여전히 경합함.
    # 다중 사용자 배포 시 과목 컨텍스트를 요청별로 분리해야 한다.
    if inp.subject and inp.subject != SUBJECT_ID and inp.subject in PRESETS:
        select_subject(inp.subject)
    # 업로드 과목은 서버 메모리에만 있다. 재시작으로 지워졌으면
    # 엉뚱한 과목 근거로 조용히 답하지 말고 다시 올리라고 알린다.
    if inp.subject == "custom" and SUBJECT_ID != "custom":
        return {"reply": "서버가 재시작되어 올렸던 강의자료가 지워졌어요. "
                         "'과목 바꾸기 · 자료 올리기'에서 자료를 다시 올려 주세요.", "sources": []}
    # 형태가 어긋난 항목은 버리고, 모델에 보내는 이력은 최근 20개로 제한한다
    # (이력 전체를 매번 보내면 긴 세션에서 토큰이 선형으로 불어난다)
    msgs = [m for m in inp.messages if isinstance(m, dict)
            and m.get("role") in ("user", "assistant") and isinstance(m.get("text"), str)][-20:]
    if not msgs:
        return {"reply": "질문을 입력해 주세요.", "sources": []}
    return answer(msgs)

@app.get("/api/subjects")
def subjects():
    return {"subjects": [{"id": k, "name": v["name"]} for k, v in PRESETS.items()],
            "current": SUBJECT_ID}

@app.post("/api/select")
def select(inp: SelectIn):
    if inp.id not in PRESETS:
        return {"error": "unknown subject"}
    select_subject(inp.id)
    return {"id": SUBJECT_ID, "name": LECTURE_NAME, "chunks": len(CHUNKS)}

@app.post("/api/upload")
def upload(inp: UploadIn):
    # 클라이언트에도 20MB 제한이 있지만 우회 가능하므로 서버에서 한 번 더 막는다
    if len(inp.text) > 2_000_000:
        return {"error": "자료가 너무 큽니다. 파일을 나눠 올려 주세요."}
    set_lecture(inp.name[:80], inp.text, inp.unit, inp.series, inp.grade, inp.kind)
    return {"id": SUBJECT_ID, "name": LECTURE_NAME, "chunks": len(CHUNKS)}

@app.get("/api/lecture")
def lecture():
    return {"id": SUBJECT_ID, "name": LECTURE_NAME}

@app.get("/")
def landing():
    return FileResponse(HERE / "landing.html")

@app.get("/start")
def start():
    return FileResponse(HERE / "select.html")

@app.get("/chat")
def index():
    return FileResponse(HERE / "index.html")

if __name__ == "__main__":
    # 오프라인 셀프체크: 검색 로직 + 청킹 (API 불필요)
    m = np.array([[1, 0], [0, 1], [0.9, 0.1]], dtype=float)
    assert cosine_topk(m, np.array([1.0, 0.0]), 2) == [0, 2], "retrieval 로직 깨짐"
    assert len(CHUNKS) > 5, "청킹 실패"
    print(f"selfcheck OK - chunks={len(CHUNKS)}")
