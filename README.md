# 신제품 출시 전 인증·리콜 리스크 진단 AI

사용자가 출시 예정 제품 정보를 입력하면, 제품안전 관련 기준 데이터와 공공데이터를 기반으로 법정 품목명 후보, 인증유형, 안전기준, 시험기관 안내, 국내 리콜 사유 요약, KC 유사 인증사례, 출시 전 체크리스트를 제공하는 FastAPI 기반 AI 모듈입니다.

> **주의**: 본 서비스는 최종 법적 판단을 대체하지 않습니다. 인증·리콜 리스크를 사전에 점검하기 위한 보조 도구입니다.

---

## 기술 스택

| 항목 | 내용 |
|---|---|
| Python | 3.11 |
| 웹 프레임워크 | FastAPI + Uvicorn |
| 스키마 검증 | Pydantic v2 + pydantic-settings |
| 환경변수 | python-dotenv |
| 데이터 처리 | JSON / JSONL 파일 기반 Rule / Keyword 검색 |
| 검색 | 경량 BM25 (`rank-bm25`) — 국내 리콜 대표 사례 정렬 + RAG 근거 chunk 검색 |
| 보고서 생성 | Markdown 템플릿 기반 (LLM 연동 선택적 활성화 — `ENABLE_LLM=true`) |

---

## 프로젝트 구조

```text
ieat_2606_ai/
├── app/
│   ├── main.py               # FastAPI 앱 + lifespan 데이터 로딩
│   ├── core/
│   │   └── config.py         # 환경변수 및 경로 설정
│   ├── schemas/
│   │   ├── request.py        # DiagnosisRequest
│   │   └── response.py       # DiagnosisResponse 및 서브모델
│   ├── services/
│   │   ├── category_matcher.py        # Phase 2: 법정 품목명 후보 매칭
│   │   ├── certification_service.py   # Phase 3: 인증유형 및 안전기준 조회
│   │   ├── institution_service.py     # Phase 4: 시험기관 및 절차 안내
│   │   ├── recall_service.py          # Phase 5: 국내 리콜 사유 요약
│   │   ├── kc_certification_service.py # Phase 6: KC 유사 인증사례
│   │   ├── diagnosis_service.py       # 전체 파이프라인 조율
│   │   └── report_service.py          # Markdown 보고서 생성 (템플릿)
│   ├── loaders/              # JSON / JSONL 안전 로더
│   ├── search/               # Keyword 검색 유틸리티
│   └── llm/
│       └── llm_service.py    # LLM 연동 (미구현 — 템플릿 fallback)
│
├── data/
│   ├── master_json/          # 품목 매칭·인증·안전기준·기관 기준 데이터 (11개 파일)
│   ├── safety_json/          # domestic_recall.json + kc_certification.json
│   └── rag_jsonl/            # 검색용 RAG chunk 파일 (JSONL)
│
├── docs/
│   ├── PROGRESS.md           # 작업 진행 내역
│   └── README.md             # docs 폴더 안내
│
├── requirements.txt
├── .env                      # 환경변수 (Git 제외)
└── .gitignore
```

---

## 설치 및 실행

### 1. 저장소 Clone

```bash
git clone https://github.com/ChanPark-student/ieat_2606_ai.git
cd ieat_2606_ai
```

> **브랜치 안내**: 현재 최신 기능(Phase 2~6)은 `feat/domestic-recall-summary` 브랜치에 있습니다.  
> `main` 브랜치는 초기 베이스라인 상태입니다. 최신 코드를 사용하려면 아래 명령어로 브랜치를 전환하세요.
>
> ```bash
> git checkout feat/domestic-recall-summary
> ```

### 2. 가상환경 생성 및 활성화

**Git Bash (Windows)**
```bash
python -m venv .venv
source .venv/Scripts/activate
```

**PowerShell (Windows)**
```powershell
python -m venv .venv
.\.venv\Scripts\activate
```

### 3. 의존성 설치

```bash
pip install -r requirements.txt
```

### 4. 환경변수 설정

프로젝트 루트에 `.env` 파일을 만들고 아래 항목을 설정합니다.

**기본 설정 (LLM 비활성화 — 템플릿 보고서 사용)**

```env
ENABLE_LLM=false
HF_TOKEN=
HF_MODEL_NAME=Qwen/Qwen2.5-1.5B-Instruct
LLM_MAX_NEW_TOKENS=1200
LLM_TEMPERATURE=0.2
```

> **기본값은 `ENABLE_LLM=false`입니다.** LLM을 사용하지 않아도 모든 기능이 정상 동작합니다.  
> 모델 다운로드 없이 서버가 즉시 실행됩니다.

**LLM 활성화 설정 (선택)**

LLM을 사용하여 보고서 문장을 자연스럽게 정제하려면:

1. `.env`에서 `ENABLE_LLM=true`로 변경합니다.
2. `requirements.txt`에서 LLM 의존성 주석을 해제한 뒤 설치합니다.

```bash
pip install transformers>=4.45.0 accelerate>=0.26.0 torch>=2.1.0
```

3. 서버 실행 후 `/diagnose` 첫 요청 시 모델을 자동으로 다운로드합니다 (모델 크기에 따라 수 분 소요).

> **주의**: 모델 캐시(`hf_cache/`), 모델 가중치 파일(`.bin`, `.safetensors`, `.gguf`)은 `.gitignore`에 등록되어 있으며 GitHub에 업로드되지 않습니다.

**LLM 비활성화 또는 실패 시 동작**

- `ENABLE_LLM=false`: 즉시 템플릿 보고서 반환
- `ENABLE_LLM=true`이지만 모델 로딩 실패 또는 LLM 출력 검증 실패: 템플릿 보고서로 자동 fallback
- 응답 필드 `report_generation_mode`로 실제 사용 방식 확인 가능 (`"template"` 또는 `"llm"`)

### 5. 데이터 파일 확인

`data/` 폴더 아래 3개 하위 폴더(`master_json/`, `safety_json/`, `rag_jsonl/`)가 필요합니다.  
Git에 포함되지 않는 파일이 있으므로 아래 [데이터 파일 안내](#데이터-파일-안내) 섹션을 확인하세요.

### 6. 서버 실행

```bash
uvicorn app.main:app --reload
```

> **참고**: 서버 시작 시 `kc_certification.json`(226MB)을 로드하고 집계합니다. 첫 기동에 약 3~5초가 소요됩니다. 정상 완료되면 아래와 같은 로그가 출력됩니다.
>
> ```
> INFO:     kc_certification.json 집계 완료: 41개 카테고리 인덱스 구축
> INFO:     Uvicorn running on http://127.0.0.1:8000
> ```

---

## 서버 접속

| 항목 | 주소 |
|---|---|
| API 서버 기본 주소 | http://127.0.0.1:8000 |
| Swagger 문서 (API 테스트) | http://127.0.0.1:8000/docs |
| Health Check | http://127.0.0.1:8000/health |

---

## 정상 동작 확인

### 방법 1 — 브라우저에서 /health 확인

http://127.0.0.1:8000/health 접속 후 아래 응답이 오면 정상입니다.

```json
{
  "status": "ok",
  "loaded": {
    "master_json": true,
    "safety_json": true,
    "rag_chunk_all": true,
    "recall_bm25": true,
    "rag_retriever": true,
    "rag_chunk_count": 385,
    "kc_index": true,
    "llm": false
  }
}
```

- `master_json`이 `false`이면 `data/master_json/` 파일을 확인하세요.
- `rag_retriever`가 `false`이면 `data/rag_jsonl/rag_chunk_all*.jsonl`이 없거나 `rank-bm25`가 설치되지 않은 것입니다. 이 경우에도 서버는 정상 동작하며, 근거 chunk 검색(`used_rag_chunk_ids`)만 비활성화됩니다.

### 방법 2 — Swagger에서 /diagnose 테스트

http://127.0.0.1:8000/docs 에서 `POST /diagnose` 항목을 클릭하고 아래 JSON을 입력합니다.

```json
{
  "product_name": "장난감 자동차",
  "user_query": "5세 어린이가 사용하는 건전지 장난감 자동차를 수입하려고 합니다.",
  "target_age": "5세",
  "material_text": "플라스틱, 금속 나사",
  "power_type": "건전지",
  "battery_included": true,
  "import_or_manufacture": "수입"
}
```

### 방법 3 — curl

```bash
curl -X POST http://127.0.0.1:8000/diagnose \
  -H "Content-Type: application/json" \
  -d '{"product_name": "장난감 자동차", "target_age": "5세"}'
```

---

## API 사용 방법

### GET /health

서버 기동 상태 및 데이터 로드 여부 확인.

```bash
curl http://127.0.0.1:8000/health
```

### POST /diagnose

제품 정보를 입력하면 인증·리콜 리스크 진단 결과를 반환합니다.

**요청 필드**

| 필드 | 타입 | 필수 | 설명 |
|---|---|---|---|
| `product_name` | string | **필수** | 제품명 |
| `user_query` | string | 선택 | 자유 문의 내용 |
| `target_age` | string | 선택 | 사용 대상 연령 (예: `5세`, `12개월`) |
| `material_text` | string | 선택 | 소재 (예: `플라스틱, ABS`) |
| `power_type` | string | 선택 | 전원 (예: `건전지`, `USB`) |
| `battery_included` | boolean | 선택 | 배터리 포함 여부 |
| `import_or_manufacture` | string | 선택 | `수입` 또는 `제조` |

**응답 주요 필드**

| 필드 | 설명 |
|---|---|
| `legal_product_candidates` | 법정 품목명 후보 목록 (신뢰도·매칭 근거 포함) |
| `certification_diagnosis` | 예상 인증유형 및 적용 안전기준 |
| `institution_guidance` | 시험기관 및 절차 안내 |
| `recall_reason_summary` | 국내 유사 리콜 사유 요약 및 예방 포인트 |
| `kc_certification_summary` | KC 유사 인증사례 참고 (보조 근거) |
| `launch_checklist` | 출시 전 확인 체크리스트 |
| `final_report_markdown` | 전체 진단 결과 Markdown 보고서 |
| `source_refs` | 근거 데이터 참조 목록 |
| `disclaimer` | 법적 면책 안내 문구 |

---

## 데이터 파일 안내

### data/master_json/

품목 매칭·인증유형·안전기준·시험기관 관련 기준 데이터. 총 11개 JSON 파일.  
Git에 포함됩니다.

### data/safety_json/

| 파일 | 크기 | Git 포함 |
|---|---|---|
| `domestic_recall.json` | ~11MB | O |
| `kc_certification.json` | ~226MB | **X** (별도 배치 필요) |

> `kc_certification.json`은 용량이 크므로 Git에 업로드하지 않습니다.  
> KC 인증사례 기능을 사용하려면 SafetyKorea 공공데이터 등에서 해당 파일을 받아 `data/safety_json/kc_certification.json` 경로에 직접 배치하세요.  
> 파일이 없으면 KC 섹션이 빈 값으로 반환되며, 서버는 정상 실행됩니다.

### data/rag_jsonl/

RAG 기반 검색용 JSONL chunk 파일. Git에 포함됩니다.

---

## GitHub 업로드 제외 파일

아래 파일·폴더는 `.gitignore`에 등록되어 있으며 Git에 업로드되지 않습니다.

```
.env
.env.*
.venv/
__pycache__/
hf_cache/
models/
*.bin
*.safetensors
*.gguf
*.zip
*.tar.gz
*.log
data/safety_json/kc_certification.json
```

---

## RAG Retriever (근거 chunk 검색)

`data/rag_jsonl/rag_chunk_all*.jsonl` 기반의 **경량 검색기**입니다.

- **방식**: `rank-bm25` 기반 BM25 + Rule 결과 가중(boost)·문서유형 quota. 임베딩/벡터 DB는 현재 MVP에서 사용하지 않습니다(설치 부담·재현성 고려).
- **역할**: Retriever는 **법적 판단 엔진이 아니라 근거 chunk 검색기**입니다. 법정 품목명·인증유형·KC 매칭·리콜 count 등 Rule 판단 결과를 바꾸지 않으며, LLM/보고서에 넣을 "근거 chunk"를 찾고 `used_rag_chunk_ids`·`source_refs`를 채우는 데만 사용됩니다.
- **품목 확정 시**: 해당 품목(완구 등)의 인증기준·안전기준·체크리스트·시험기관·KC 요약 chunk를 균형 있게 검색합니다.
- **불확실 입력**(법정 품목명 미확정 / 인증유형 `확인 전`): 품목 특정 근거를 과확정처럼 가져오지 않으며 `used_rag_chunk_ids`가 비어 있을 수 있습니다.
- **Fallback**: `rag_chunk_all*.jsonl`이 없거나 `rank-bm25` 미설치 시 retriever만 비활성화되고 서버·진단은 정상 동작합니다. 깨진 JSONL 라인은 건너뜁니다.
- 사용자 보고서 본문에는 BM25·retriever score 등 기술 용어나 내부 chunk ID를 노출하지 않습니다.

### 재현/점검 스크립트

```bash
python scripts/run_smoke_tests.py        # A~E 케이스 핵심 불변식 검증 (종료코드 0=통과)
python scripts/generate_demo_outputs.py  # docs/demo_outputs/*.md 재생성
```

---

## 현재 구현 상태

| 기능 | 상태 |
|---|---|
| 제품 정보 입력 API (`POST /diagnose`) | ✅ 완료 |
| 법정 품목명 후보 매칭 (신뢰도 3단계) | ✅ 완료 |
| 인증유형 및 안전기준 조회 | ✅ 완료 |
| 시험기관 및 절차 안내 | ✅ 완료 |
| 국내 리콜 사유 요약 및 예방 포인트 (BM25 정렬) | ✅ 완료 |
| KC 유사 인증사례 검색 | ✅ 완료 |
| 출시 전 체크리스트 생성 | ✅ 완료 |
| RAG 근거 chunk 검색 (경량 BM25) | ✅ 완료 |
| Markdown 보고서 생성 (템플릿 기반) | ✅ 완료 |
| LLM 연동 보고서 생성 (선택) | ✅ 완료 (`ENABLE_LLM=true` 설정 시 활성화) |

---

## 자주 발생하는 오류

**가상환경 활성화가 안 될 때 (PowerShell)**
```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
.\.venv\Scripts\activate
```

**`uvicorn` 명령어를 찾을 수 없을 때**  
가상환경이 활성화되지 않은 상태입니다. 위 활성화 명령어를 먼저 실행하세요.

**data 파일이 없어서 결과가 비어 있을 때**  
`/health` 응답에서 `master_json: false`이면 `data/master_json/` 내 JSON 파일이 없는 것입니다. 파일을 해당 경로에 배치 후 서버를 재시작하세요.

**포트 8000이 이미 사용 중일 때**
```bash
uvicorn app.main:app --reload --port 8001
```

**KC 인증사례가 모두 빈 값으로 나올 때**  
`data/safety_json/kc_certification.json` 파일이 없는 경우입니다. 파일을 해당 경로에 배치 후 서버를 재시작하세요. 파일이 없어도 나머지 기능은 정상 동작합니다.
