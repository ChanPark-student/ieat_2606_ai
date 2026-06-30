# ieat_2606_ai — 작업 진행 내역 정리

---

## 프로젝트 개요

- **목적**: 신제품 출시 전 인증·리콜 리스크 진단 어시스턴트 AI MVP
- **스택**: Python 3.x · FastAPI · Pydantic v2 · uvicorn
- **데이터**: JSON/JSONL 파일 기반 (PostgreSQL 없음)
- **원칙**: 하드코딩 금지, 데이터 기반 조회, LLM은 문장 조립기 역할

---

## 현재 구현 상태 (최신)

| Phase | 항목 | 상태 |
|---|---|---|
| Phase 0 | 프로젝트 구조 파악 및 초기 세팅 | ✅ 완료 |
| Phase 2 | 법정 품목명 후보 매칭 | ✅ 완료 |
| — | 보수적 매처 (저신호 토큰 down-weight, 과확정 방지) | ✅ 완료 |
| Phase 3 | 인증유형 및 안전기준 조회 | ✅ 완료 |
| Phase 4 | 기관 및 절차 안내 | ✅ 완료 |
| Phase 5 | 국내 리콜 사유 검색 | ✅ 완료 |
| Phase 6 | KC 유사 인증사례 | ✅ 완료 |
| Phase 7 | Markdown 보고서 품질 고도화 (템플릿 기반) | ✅ 완료 |
| Phase 8 | LLM 보고서 생성 | ⏳ 미구현 |

---

## 세션 작업 내역

### Phase 0 — 프로젝트 구조 파악 및 초기 상태 확인

**확인한 것**
- `app/main.py` → FastAPI lifespan에서 JSON/JSONL 파일 로드
- `app/core/config.py` → pydantic-settings, 경로 설정, HF_TOKEN 환경변수
- `app/loaders/json_loader.py`, `jsonl_loader.py` → 파일 없으면 `{}` / `[]` 반환하는 안전 로더
- `app/schemas/request.py`, `response.py` → DiagnosisRequest / DiagnosisResponse 스키마
- `app/services/diagnosis_service.py` → run_diagnosis 메인, 당시엔 빈 결과만 반환
- `app/services/category_matcher.py` → 존재했으나 미사용 + 요구사항 위반 다수
- `app/llm/llm_service.py` → NotImplementedError 발생 → try/except로 template fallback

**발견한 주요 문제**
- `category_matcher`가 `diagnosis_service`에 연결되지 않아 `legal_product_candidates`가 항상 `[]`
- 기존 matcher가 매칭 실패 시 `"NO_MATCH"` 더미 후보를 강제 삽입 (요구사항 위반)
- `confidence_level`이 `HIGH/MEDIUM/LOW` → 핸드오프 기준은 `CONFIRMED/CANDIDATE/NEEDS_CONFIRMATION`
- `main.py`에서 master_json 파일 3개만 로드 (핸드오프 기준 9개 필요)
- `.gitignore`에 `kc_certification.json` (237MB), `hf_cache/`, `models/` 등 누락

---

### 보강 작업 — .gitignore / main.py / confidence_level

#### .gitignore 보강

```
hf_cache/          # Hugging Face 모델 캐시
models/            # 다운로드 모델
*.bin, *.safetensors, *.gguf  # 모델 파일
*.zip, *.tar.gz    # 압축 파일
*.log
data/safety_json/kc_certification.json  # 237MB 대용량 원천 데이터
```

> 단, `data/**/*.json` 전체를 막지 않음 — 작은 기준 JSON은 Git에 포함

#### main.py — 파일 로딩 전면 개선

기존 3개 목록 → 실제 파일명과 app_data 키를 명시적으로 매핑하는 dict 방식으로 교체

```python
master_file_map = {
    "product_category_index.json":                              "product_category_index",
    "product_category_dictionary.json":                         "product_category_dictionary",
    "product_category_alias.json":                              "product_category_alias",
    "certification_annex_rule(DB 적재용 원본 JSON).json":        "certification_annex_rule",
    "certification_process_rule.json":                          "certification_process_rule",
    "safety_standard_document.json":                            "safety_standard_document",
    "safety_standard_check_items.json":                         "safety_standard_check_items",
    "test_institution.json":                                    "test_institution",
    "institution_scope.json":                                   "institution_scope",
    "supplier_conformity_scope.json":                           "supplier_conformity_scope",
    "product_institution_lookup.json":                          "product_institution_lookup",
}
```

> 파일이 없으면 warning 로그 + 빈 리스트로 처리, 서버 죽지 않음

#### confidence_level 명칭 정정

| 변경 전 | 변경 후 | 임계값 |
|---|---|---|
| `HIGH` | `CONFIRMED` | score ≥ 0.7 |
| `MEDIUM` | `CANDIDATE` | score ≥ 0.4 |
| `LOW` | `NEEDS_CONFIRMATION` | score < 0.4 |

---

### Phase 2 — category_matcher 구현 및 wire-up

**수정 파일**: `app/services/category_matcher.py`, `app/services/diagnosis_service.py`

#### category_matcher.py — 전면 재작성

| 항목 | 내용 |
|---|---|
| 함수 시그니처 | `match_category(request, index_data) → List[LegalProductCandidate]` |
| 검색 쿼리 구성 | `product_name + user_query + material_text` 결합 후 토큰화 |
| 검색 대상 필드 | `legal_product_name`(3.0), `display_product_name`(3.0), `user_expression`(2.0), `normalized_expression`(2.0), `aliases`(2.0), `keywords`(1.0), `hazard_keywords`(1.0) |
| dedup | `legal_product_name` 기준 그룹핑 후 최고점 항목만 선택 (978개 중복 항목 통합) |
| 매칭 없음 | `[]` 반환 (더미 추가 금지) |

---

### category_matcher 보수화 — 오탐 방지

**배경**: "어린이용"이 36개 품목 중 25개 법정 품목명에 등장해, 이 단어 하나만으로 여러 품목이 CONFIRMED(1.0)로 과확정되는 문제 발견.

**수정 파일**: `app/services/category_matcher.py`, `app/services/certification_service.py`

#### 핵심 변경 내용

| 변경 | 설명 |
|---|---|
| **데이터 기반 document frequency (substring)** | 토큰이 substring으로 등장하는 품목 수를 세어, 25% 이상 품목에 등장하면 low-signal(×0.15) 처리. "어린이용"을 하드코딩 없이 자동 식별 |
| **필드 간 중복합산 제거** | 한 토큰은 등장 필드 중 최대 가중치 1개만 반영 (기존: legal+display+user_expr+norm 4중 합산 → "어린이용"이 10점) |
| **부분일치 보너스 조건** | `user_expression`/`aliases`가 `product_name`의 substring이고, **high-signal 토큰을 포함할 때만** +8점 부여 |
| **CONFIRMED 게이팅** | high-signal 토큰 ≥ 1개 + near-tie 아님 → 두 조건 모두 만족해야 CONFIRMED 허용 |
| **near-tie 규칙** | 상위 후보가 max 점수의 85% 이상으로 3개 이상이면 전체 확정 금지 |
| **match_basis 개선** | 핵심 매칭 토큰 / 보조 토큰을 명시적으로 기록 |

#### certification_service.py — 과확정 방지 가드

최우선 후보가 NEEDS_CONFIRMATION이면 `certification_type="확인 전"`, `applied_standards=[]`, `launch_checklist=[]` 반환. 후보 목록은 응답에 그대로 남아 사용자가 확인 가능.

#### 테스트 케이스별 결과

| 케이스 | top 후보 | CONFIRMED 수 | cert 확정 |
|---|---|---|---|
| A 어린이용 책가방 | 아동용 섬유제품 (1.00) | 1 | 공급자적합성확인 |
| B 장난감 자동차 | 완구 (1.00) | 1 | 안전확인 |
| C 정체불명 어린이용 반짝이 물건 | 합성수지제 어린이제품 (0.29) | **0** | **확인 전** |
| D 유아용 내의 | 유아용 섬유제품 (0.31) | 0 | 확인 전 |

---

### Phase 3 — 인증유형 및 안전기준 조회

**수정 파일**: `app/services/certification_service.py`, `app/services/diagnosis_service.py`, `app/services/report_service.py`

#### 데이터 파일 구조

| 파일 | 레코드 수 | 핵심 필드 |
|---|---|---|
| `certification_annex_rule(DB 적재용 원본 JSON).json` | 38건 | `product_name`, `certification_type`, `common_safety_standard`, `product_safety_standard`, `rule_id` |
| `safety_standard_document.json` | 73건 | `product_name`, `certification_type`, `standard_doc_id`, `is_latest`, `is_active` |
| `safety_standard_check_items.json` | 241건 | `product_name`, `pre_launch_check_item`, `hazard_keyword`, `is_active` |

> 연결 키: **모든 파일이 `product_name` == `legal_product_name`** 으로 매핑

#### certification_service.py — 3단계 조회 흐름

```
최우선 후보 선택 (CONFIRMED > CANDIDATE, 동점이면 score 높은 순)
  → NEEDS_CONFIRMATION만 있으면 즉시 "확인 전" 반환 (과확정 방지)
       ↓
Step 1: certification_annex_rule → certification_type, applied_standards, source_refs
Step 2: safety_standard_document (is_latest=True) → source_refs
Step 3: safety_standard_check_items (is_active=True) → launch_checklist (최대 15개)
```

#### report_service.py — 핸드오프 §10.2 기준 9섹션 구조

| 섹션 | 내용 |
|---|---|
| §1 입력 제품 요약 | 한글 레이블로 표시 |
| §2 법정 품목명 후보 | confidence_level, score, match_basis 포함 |
| §3 예상 인증유형 및 적용 안전기준 | certification_type, applied_standards, source_refs |
| §4 기관 및 절차 안내 | institution_guidance |
| §5 국내 리콜 사유 요약 | recall_reason_summary |
| §6 KC 유사 인증사례 | kc_certification_summary |
| §7 출시 전 확인 체크리스트 | launch_checklist (safety_standard_check_items 기반) |
| §8 최종 확인 필요사항 | needs_user_confirmation 후보 목록 |
| §9 안내 문구 | disclaimer |

---

### Phase 4 — 기관 및 절차 안내

**수정 파일**: `app/schemas/response.py`, `app/services/institution_service.py`, `app/services/diagnosis_service.py`, `app/services/report_service.py`

#### 데이터 파일 구조

| 파일 | 레코드 수 | 핵심 필드 |
|---|---|---|
| `certification_process_rule.json` | 3건 | `certification_type`, `summary`, `institution_required`, `institution_role`, `workflow`, `required_documents` |
| `supplier_conformity_scope.json` | 15건 | `product_name`, `certification_type`, `institution_required`, `institution_guidance` |
| `product_institution_lookup.json` | 22건 | `certification_type`, `product_name`, `institution_role`, `institutions[]` |
| `institution_scope.json` | 79건 | `certification_type`, `institution_role`, `institution_name`, `short_name`, `website_url`, `product_name` |
| `test_institution.json` | 14건 | `institution_name`, `short_name`, `certification_type`, `institution_role`, `product_scope[]` |

#### 스키마 변경: InstitutionInfo 서브모델 추가

`candidate_institutions: List[str]` → `List[InstitutionInfo]`

```python
class InstitutionInfo(BaseModel):
    institution_name: str
    short_name: str
    institution_role: str
    certification_type: str
    website_url: str
    product_scope: List[str]
    source_refs: List[str]
```

#### institution_service.py — 4단계 우선순위 조회

```
Step 1: certification_process_rule → summary, institution_required (cert_type 기준)
Step 2: supplier_conformity_scope → 공급자적합성확인 품목별 institution_guidance 텍스트
Step 3: product_institution_lookup → (product_name, cert_type) 기준 기관 목록 (가장 구체적)
  → fallback: institution_scope → test_institution
```

#### 결과 예시

| 케이스 | institution_required | 기관 수 |
|---|---|---|
| 아동용 섬유제품 (공급자적합성확인) | false | 0 (지정기관 불필요) |
| 완구 (안전확인) | true | 7개 (KCL, KTC, KTR, KATRI, FITI, KOTITI, SGS) |

---

### Phase 5 — 국내 리콜 사유 검색

**수정 파일**: `app/services/recall_service.py`, `app/services/diagnosis_service.py`, `app/main.py`

#### 데이터 파일

| 파일 | 레코드 수 | 비고 |
|---|---|---|
| `data/safety_json/domestic_recall.json` | 4,188건 (11MB) | `mapped_legal_product_name` 사전 매핑 포함 (1,253건) |
| `data/rag_jsonl/rag_chunk_all_with_kc.jsonl` | 385 chunks (911KB) | RECALL 타입 청크 없음 → JSON 직접 사용 |

#### domestic_recall.json 주요 필드

| 필드 | 용도 |
|---|---|
| `mapped_legal_product_name` | **1차 매칭 키** — 법정 품목명으로 사전 매핑 |
| `reason_keywords` | 카테고리화된 리콜 사유 (`프탈레이트계 가소제`, `납`, `표시사항` 등) |
| `harmDscr` | 위해 설명 (representative_cases 출력용) |
| `accidentCaseDscr` | 사고 사례 |
| `publishActionDscr` | 리콜 조치 |
| `recallProductName` | 리콜 제품명 |
| `recallUid` | source_refs 식별자 |

#### recall_service.py — 구현 흐름

```
1. 후보에서 검색 대상 법정 품목명 결정
   CONFIRMED/CANDIDATE → 해당 품목들 (정밀 검색)
   NEEDS_CONFIRMATION  → 최고점 1개만 (union 팽창 방지)

2. domestic_recall 필터: mapped_legal_product_name == 타겟 품목명

3. reason_keywords Counter → top_recall_reasons (상위 7개)

4. representative_cases: recallProductName + publishDate + harmDscr 80자 요약

5. prevention_points: safety_standard_check_items에서
   hazard_keyword × reason_keywords 교차 조회 (품목명 일치 우선)
```

#### main.py 보완

RAG 파일명 폴백 추가 — 실제 파일이 `rag_chunk_all_with_kc.jsonl`인 경우에도 자동 로드

#### 테스트 케이스별 결과

| 케이스 | recall_count | top_reasons (상위 3) | prevention_points |
|---|---|---|---|
| A 어린이용 책가방 | 198건 | 프탈레이트, 표시사항, 납 | 6개 |
| B 장난감 자동차 | 224건 | 프탈레이트, 표시사항, 납 | 5개 |
| C 정체불명 | 39건 (합성수지제만) | 프탈레이트, 표시사항, 작은 부품 | 5개 |
| no-match | 0건 | — | 0개 |

---

## 현재 최신 응답 구조 (어린이용 책가방 기준)

```
legal_product_candidates[0]
  legal_product_name : 아동용 섬유제품
  confidence_level   : CONFIRMED (1.00)
  certification_type : 공급자적합성확인
  match_basis        : 핵심 매칭 토큰: 책가방

certification_diagnosis
  certification_type : 공급자적합성확인
  applied_standards  : ["어린이제품 공통안전기준", "아동용 섬유제품 안전기준"]
  judgement_level    : CONFIRMED
  source_refs        : ["certification_annex_rule:CHILD-A3-거", ...]

institution_guidance
  institution_required : false
  summary              : 공급자가 스스로 안전기준 적합성을 확인하는 제도. 지정기관 신고 불필요, 시험성적서 확보 필요.
  candidate_institutions: []

recall_reason_summary
  recall_count      : 198
  top_recall_reasons: ["프탈레이트계 가소제", "표시사항", "납", "끈/코드", ...]
  representative_cases: ["어린이 책가방 (20120928): ...", ...]
  prevention_points : 6개 (safety_standard_check_items 기반)

launch_checklist    : 7개 (Phase 3에서 생성)
source_refs         : 13개 (Phase 3+4+5 통합)
```

---

### Phase 6 — KC 유사 인증사례 검색

**수정 파일**: `app/main.py`, `app/services/kc_certification_service.py`

#### 데이터 파일

| 파일 | 레코드 수 | 비고 |
|---|---|---|
| `data/safety_json/kc_certification.json` | 353,861건 (226MB) | Git 커밋 제외 (.gitignore). 시작 시 집계 후 raw 폐기 |

#### 핵심 설계: 시작 시 집계 인덱스 구축 (compact index 패턴)

226MB 파일을 통째로 메모리에 유지하는 대신, 시작 시 1회 로드 후 `categoryName[2]` 기준으로 집계해 compact index만 유지.

```
kc_agg = {
  "완구": { total: 30851, valid: 457, top_organs: ["KCL"], samples: [...10개] },
  "학용품": { total: 6953, valid: 91, ... },
  ...  # 41개 카테고리
}
```

- 로드 + 집계 시간: ~3.2초 (226MB JSON)
- 집계 후 raw 즉시 del → 메모리 대부분 해제
- 파일 없으면 `kc_agg = {}` + warning log → 서버 정상 기동

#### main.py 변경

- `_build_kc_agg(raw_list)` 함수 추가 (lifespan 내부)
- `domestic_recall.json`은 기존대로 전체 로드 (11MB, recall_service에서 직접 사용)
- `kc_certification.json`은 집계 후 raw 폐기 → `app_data["kc_agg"]`에 저장

#### kc_certification_service.py — 3단계 법정 품목명 매칭

| 단계 | 방법 |
|---|---|
| 1. 정확 일치 | `legal_name in kc_agg` |
| 2. substring 포함 | `key in legal_name` or `legal_name in key` |
| 3. 정규화 후 비교 | "어린이용/유아용/아동용" 접두사 제거 후 재비교 (예: `아동용 섬유제품` → `유아용 섬유제품`) |

#### KC 인증 데이터 필드 활용

| 필드 | 활용 |
|---|---|
| `categoryName` | `> ` 기준 3번째 부분 = 법정 품목명 유사 카테고리 |
| `certOrganName` | 인증기관명 (`(약칭)` 추출 → top_organs) |
| `certState` | `적합`만 representative_models 샘플링 |
| `certNum` | 인증번호 |
| `certDate` | 인증일자 (`YYYYMMDD` → `YYYY-MM-DD` 포맷) |
| `modelName` | 모델명 |
| `importDiv` | 수입/제조 구분 |

#### 원칙 준수

- KC 인증정보는 보조 참고자료 (인증 가능 여부 확정 금지)
- 데이터에 없는 모델명·기관명·인증번호 생성 없음
- `note` 필드에 항상 보조 근거 안내 문구 포함

#### 테스트 케이스별 결과

| 케이스 | KC 매칭 카테고리 | KC count | top_organs |
|---|---|---|---|
| A 어린이용 책가방 | 유아용 섬유제품 (정규화 매칭) | 2,094건 | KCL |
| B 장난감 자동차 | 완구 (정확 매칭) | 30,851건 | KCL |
| C 어린이 색연필 세트 | 학용품 (정확 매칭) | 6,953건 | KCL |
| D 유아용 내의 | 유아용 섬유제품 (정확 매칭) | 2,094건 | KCL |
| E 정체불명 | 매칭 없음 | 0건 | — |

---

---

### Phase 7 — Markdown 보고서 품질 고도화 (템플릿 기반)

**수정 파일**: `app/services/report_service.py`

#### 개선 항목

| 섹션 | 변경 내용 |
|---|---|
| §2 법정 품목명 후보 | CONFIRMED/CANDIDATE/NEEDS_CONFIRMATION 별 한글 톤 설명 추가 |
| §3 예상 인증유형 | 인증유형별 한 줄 설명 추가 (안전확인/안전인증/공급자적합성확인/확인 전). raw source_refs ID 제거. |
| §4 기관 및 절차 | 공급자적합성확인 대상 자연스러운 설명: "지정기관 신고 없이 출시 가능하나 시험성적서·입증자료 5년 보관 의무" |
| §5 리콜 | "리콜 데이터 기반 출시 전 위험 포인트" 섹션명으로 프레이밍 강화 |
| §6 KC | count>0: "유사 인증사례는 최종 인증 가능성 아님" 주의 blockquote 추가. count=0: note만 표시 ("0건" 제거). |
| §7 체크리스트 | `_dedup_checklist()`: 어절 중복 비율(60% 이상)로 prevention_points와 겹치는 항목 자동 제거. checklist가 빈 경우 기본 3개 안내. |
| §8 추가 확인 | CONFIRMED 있으면 CANDIDATE 수준 확인 항목만 표시(노이즈 방지). 전부 NEEDS_CONFIRMATION이면 "입력 정보 보완 필요" + 4개 보완 항목. |
| §9 안내 문구 | "본 결과는 공공데이터와 기준 데이터 기반의 사전 진단 참고자료이며, 최종 법적 판단은 관계기관 또는 전문가 확인이 필요합니다." 고정 |

#### 테스트 결과 (docs/test_outputs/)

| 파일 | 케이스 | 특이사항 |
|---|---|---|
| A_bag_report.md | 책가방 (아동용 섬유제품, 공급자적합성확인) | §4 공급자확인 자연스러운 설명, KC 2,094건 |
| B_toy_car_report.md | 장난감 자동차 (완구, 안전확인) | §8 CONFIRMED → "확정됨" 메시지, KC 30,851건 |
| C_infant_inner_report.md | 유아용 내의 (CANDIDATE) | §2 CANDIDATE 톤, KC 2,094건 |
| D_kids_shirt_report.md | 아동용 티셔츠 (NEEDS_CONFIRMATION) | §3 "확인 전" blockquote, §8 보완 안내 |
| E_unknown_report.md | 정체불명 (NEEDS_CONFIRMATION, KC 0건) | §6 note만 표시, §8 입력 보완 4항목 |

---

## 현재 미구현 / 다음 단계 후보

| 단계 | 항목 | 관련 파일 |
|---|---|---|
| Phase 8 | LLM 보고서 생성 | `app/llm/hf_generator.py` + `app/llm/prompts.py` |
| 검색 고도화 | BM25 검색 | `app/search/bm25_search.py` |
| 리콜 검색 확장 | unmapped 2,935건 BM25/임베딩 검색 | `app/services/recall_service.py` |

---

## Git 브랜치 이력

| 브랜치 | 커밋 | 내용 |
|---|---|---|
| `main` | `c6dcce4` | MVP baseline + 데이터 로더 |
| `feat/conservative-matcher-phase4` | `ee640db` | Phase 4 + 보수적 매처 + 과확정 방지 |
| `feat/domestic-recall-summary` | `6582bf6` | Phase 5 국내 리콜 사유 검색 |
