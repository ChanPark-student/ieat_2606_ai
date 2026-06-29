# ieat_2606_ai — 작업 진행 내역 정리

---

## 프로젝트 개요

- **목적**: 신제품 출시 전 인증·리콜 리스크 진단 어시스턴트 AI MVP
- **스택**: Python 3.x · FastAPI · Pydantic v2 · uvicorn
- **데이터**: JSON/JSONL 파일 기반 (PostgreSQL 없음)
- **원칙**: 하드코딩 금지, 데이터 기반 조회, LLM은 문장 조립기 역할

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

### Phase 2 — category_matcher 구현 및 wire-up

**수정 파일**: `app/services/category_matcher.py`, `app/services/diagnosis_service.py`

#### category_matcher.py — 전면 재작성

| 항목 | 내용 |
|---|---|
| 함수 시그니처 | `match_category(request, index_data) → List[LegalProductCandidate]` |
| 검색 쿼리 구성 | `product_name + user_query + material_text` 결합 후 토큰화 |
| 토큰 필터 | 길이 2 미만 제거, 중복 제거 |
| 검색 대상 필드 | `legal_product_name`(3.0), `display_product_name`(3.0), `user_expression`(2.0), `normalized_expression`(2.0), `aliases`(2.0), `keywords`(1.0), `hazard_keywords`(1.0) |
| 보너스 점수 | `user_expression`/`aliases`가 `product_name`의 부분문자열이면 +8점 |
| 완전일치 보너스 | `legal/display_product_name` 완전 일치 시 +5점 |
| dedup | `legal_product_name` 기준으로 그룹핑 후 최고점 항목만 선택 (978개 중복 항목 통합) |
| 정규화 | `raw_score / max(max_raw, 8.0)` → `[0, 1]` |
| 매칭 없음 | `[]` 반환 (더미 추가 금지) |
| 방어 처리 | 잘못된 item은 warning 로그 후 skip, 예외 외부 전파 없음 |

#### diagnosis_service.py — matcher wire-up

```python
# Phase 2: product_category_index 기반 후보 매칭
index_data = app_data["master_json"]["product_category_index"]
legal_product_candidates = match_category(request, index_data)
```

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

### Phase 3 — 인증유형 및 안전기준 조회

**수정 파일**: `app/services/certification_service.py`, `app/services/diagnosis_service.py`, `app/services/report_service.py`

#### 실제 데이터 파일 구조 확인 결과

| 파일 | 레코드 수 | 핵심 필드 |
|---|---|---|
| `certification_annex_rule(DB 적재용 원본 JSON).json` | 38건 | `product_name`, `certification_type`, `common_safety_standard`, `product_safety_standard`, `rule_id`, `source_file` |
| `safety_standard_document.json` | 73건 | `product_name`, `certification_type`, `standard_doc_id`, `is_latest`, `is_active` |
| `safety_standard_check_items.json` | 241건 | `product_name`, `pre_launch_check_item`, `hazard_keyword`, `is_active` |
| `product_category_index.json` | 978건 | `legal_product_name`, `user_expression`, `certification_type`, `common/product_safety_standard` 포함 |

> 연결 키: **모든 파일이 `product_name` == `legal_product_name`** 으로 매핑

#### certification_service.py — 3단계 조회 구현

```
최우선 후보 선택 (CONFIRMED > CANDIDATE > NEEDS_CONFIRMATION, 동점이면 score 높은 순)
       ↓
Step 1: certification_annex_rule 조회
  → certification_type (가장 권위 있는 source, index 값 덮어씀)
  → common_safety_standard, product_safety_standard → applied_standards
  → rule_id, source_file → source_refs

Step 2: safety_standard_document 조회 (is_latest=True만)
  → standard_doc_id, source_file → source_refs

Step 3: safety_standard_check_items 조회 (is_active=True만)
  → pre_launch_check_item → launch_checklist (최대 15개, 중복 제거)
  → check_item_id → source_refs
```

#### diagnosis_service.py — Phase 3 wire-up

```python
cert_diagnosis, launch_checklist, cert_source_refs = diagnose_certification(
    legal_product_candidates, app_data
)
```

- `launch_checklist`, `source_refs` 응답에 반영
- 기존 빈 구조체 생성 코드 제거

#### report_service.py — 핸드오프 §10.2 기준 9섹션 구조로 재작성

| 섹션 | 내용 |
|---|---|
| §1 입력 제품 요약 | 한글 레이블로 표시 |
| §2 법정 품목명 후보 | confidence_level, score, match_basis 포함 |
| **§3 예상 인증유형 및 적용 안전기준** | **신규**: certification_type, applied_standards, source_refs |
| §4 기관 및 절차 안내 | institution_guidance |
| §5 국내 리콜 사유 요약 | recall_reason_summary |
| §6 KC 유사 인증사례 | kc_certification_summary |
| **§7 출시 전 확인 체크리스트** | **신규**: launch_checklist (safety_standard_check_items 기반) |
| §8 최종 확인 필요사항 | needs_user_confirmation 후보 목록 |
| §9 안내 문구 | disclaimer |

---

## 최종 상태 (테스트 입력 기준)

**입력**
```json
{
  "product_name": "어린이용 책가방",
  "user_query": "초등학생이 사용하는 책가방을 출시하려고 합니다.",
  "target_age": "8세",
  "material_text": "폴리에스터, 코팅 원단, 플라스틱 버클",
  "power_type": "없음",
  "battery_included": false,
  "import_or_manufacture": "수입"
}
```

**응답 핵심 필드**
```
legal_product_candidates[0]
  → legal_product_name : 아동용 섬유제품
  → confidence_level   : CONFIRMED (1.00)
  → certification_type : 공급자적합성확인

certification_diagnosis
  → certification_type  : 공급자적합성확인
  → applied_standards   : ["어린이제품 공통안전기준", "아동용 섬유제품 안전기준"]
  → judgement_level     : CONFIRMED
  → source_refs         : ["certification_annex_rule:CHILD-A3-거", ...]

launch_checklist (7건, safety_standard_check_items.json 기반)
  → 프탈레이트계 가소제 시험성적서 확인
  → 납 함유량 시험성적서 확인
  → 카드뮴 시험성적서 확인
  → 폼알데하이드 확인
  → 아릴아민/아조염료 확인
  → KC 표시사항 확인
  → 끈/코드 목 졸림 위험 확인
```

---

## 현재 미구현 / 다음 단계 후보

| 단계 | 항목 | 관련 파일 |
|---|---|---|
| Phase 4 | 기관 및 절차 안내 | `test_institution.json`, `institution_scope.json`, `certification_process_rule.json` |
| Phase 5 | 국내 리콜 사유 검색 | `safety_json/domestic_recall.json` |
| Phase 6 | KC 유사 인증사례 | `safety_json/kc_certification.json` (237MB, 경량 인덱스 고려) |
| Phase 7 | LLM 보고서 생성 | `app/llm/hf_generator.py` + `app/llm/prompts.py` |
| 검색 고도화 | BM25 검색 | `app/search/bm25_search.py` |
| 기술 부채 | recall_service 하드코딩 제거 | `safety_standard_check_items.json` 기반으로 대체 |
| 기술 부채 | institution_service 하드코딩 제거 | `certification_process_rule.json` 기반으로 대체 |
