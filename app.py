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
GEN_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
EMB_MODEL = "gemini-embedding-001"

def gemini(path, body):
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("GEMINI_API_KEY 환경변수를 설정하세요 (aistudio.google.com 무료 발급)")
    req = urllib.request.Request(
        f"https://generativelanguage.googleapis.com/v1beta/{path}?key={key}",
        data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())

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
    global CHUNKS, LECTURE_NAME, SUBJECT_ID, _VECS, _VEC_FILE, LECTURE_UNIT
    p = PRESETS[sid]
    CHUNKS = chunk((HERE / p["txt"]).read_text(encoding="utf-8"))
    LECTURE_NAME, SUBJECT_ID = p["name"], sid
    LECTURE_UNIT = p.get("unit", "강")
    _VECS, _VEC_FILE = None, HERE / p["vec"]

def set_lecture(name, text):
    """업로드된 강의로 교체 (커스텀 과목, 다음 질의 때 지연 인덱싱)"""
    global CHUNKS, LECTURE_NAME, SUBJECT_ID, _VECS, _VEC_FILE, LECTURE_UNIT
    CHUNKS = chunk(text) or ["(빈 문서)"]
    LECTURE_NAME, SUBJECT_ID = name, "custom"
    _VECS, _VEC_FILE = None, None

def retrieve(query, k=4):
    global _VECS
    if _VECS is None:
        if _VEC_FILE and _VEC_FILE.exists():      # 프리셋: 사전계산 벡터 로드
            v = np.load(_VEC_FILE)
            if len(v) == len(CHUNKS):
                _VECS = v.astype(np.float32)
        if _VECS is None:                          # 커스텀/미계산: 1회 임베딩
            _VECS = np.stack([embed(c) for c in CHUNKS])
    return [CHUNKS[i] for i in cosine_topk(_VECS, embed(query), k)]

select_subject("ling")  # 기본 과목

def source_meta(hits):
    """검색에 실제 쓰인 청크를 UI 인라인 근거칩용 짧은 정보로 변환"""
    sources, seen = [], set()
    for i, text in enumerate(hits, 1):
        # 마커는 "p7"(단일 강의) 또는 "p3.7"(3강 7쪽) 두 형태를 쓴다
        pages = re.findall(r"===== p(\d+(?:\.\d+)?)", text)
        if pages:
            lec, _, pg = pages[-1].partition(".")
            label = f"{LECTURE_NAME} {lec}{LECTURE_UNIT} · {pg}쪽" if pg else f"{LECTURE_NAME} · {lec}쪽"
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
    text = re.sub(r"=====\s*p(\d+)\s*=====", r"[\1쪽]", text)
    return re.sub(r"=====\s*(.+?)\s*=====", r"[\1]", text)

def answer(messages):
    """messages: [{'role':'user'|'assistant','text':...}] → {reply, sources}"""
    last_user = next((m["text"] for m in reversed(messages) if m["role"] == "user"), "")
    hits = retrieve(last_user) if last_user else CHUNKS[:4]
    ctx = "\n---\n".join(humanize(h) for h in hits)   # 내부 마커(p3.5)가 답변에 노출되지 않게
    body = {
        "systemInstruction": {"parts": [{"text": SYSTEM + "\n\n[검색된 강의 근거]\n" + ctx}]},
        "contents": [{"role": "model" if m["role"] == "assistant" else "user",
                      "parts": [{"text": m["text"]}]} for m in messages],
    }
    r = gemini(f"models/{GEN_MODEL}:generateContent", body)
    return {"reply": r["candidates"][0]["content"]["parts"][0]["text"], "sources": source_meta(hits)}

# Vercel FastAPI 감지를 위해 app은 반드시 top-level (try 블록 안이면 인식 못 함)
from fastapi import FastAPI
from fastapi.responses import FileResponse
from pydantic import BaseModel

app = FastAPI()

class ChatIn(BaseModel):
    messages: list

class UploadIn(BaseModel):
    name: str
    text: str

class SelectIn(BaseModel):
    id: str

@app.post("/api/chat")
def chat(inp: ChatIn):
    return answer(inp.messages)

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
    set_lecture(inp.name, inp.text)
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
