# -*- coding: utf-8 -*-
"""시연용 demo_outputs/*.md 재생성 스크립트.

발표 직전 재생성에 사용. /diagnose 파이프라인을 그대로 실행하여
docs/demo_outputs/{01_bag,02_toy_car,03_unknown}_demo.md 를 갱신한다.

실행:
    python scripts/generate_demo_outputs.py
전제:
    - ENABLE_LLM=false (기본). 모델 다운로드 없음 → report_generation_mode=template
    - data/ 기준 JSON 및 rag_chunk_all_with_kc.jsonl 존재
"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

# 프로젝트 루트를 import path에 추가
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

from fastapi.testclient import TestClient  # noqa: E402
from app.main import app  # noqa: E402

DEMO_DIR = ROOT / "docs" / "demo_outputs"

# (파일명, 시연 타이틀, 입력 JSON)
CASES = [
    (
        "01_bag_demo.md",
        "어린이용 책가방",
        {
            "product_name": "책가방",
            "user_query": "초등학생용 책가방을 수입합니다.",
            "target_age": "7세",
            "material_text": "폴리에스터, 나일론",
            "power_type": None,
            "battery_included": False,
            "import_or_manufacture": "수입",
        },
    ),
    (
        "02_toy_car_demo.md",
        "장난감 자동차",
        {
            "product_name": "장난감 자동차",
            "user_query": "5세 어린이가 사용하는 건전지 장난감 자동차를 수입합니다.",
            "target_age": "5세",
            "material_text": "플라스틱, 금속 나사",
            "power_type": "건전지",
            "battery_included": True,
            "import_or_manufacture": "수입",
        },
    ),
    (
        "03_unknown_demo.md",
        "정체불명 어린이용 반짝이 물건",
        {
            "product_name": "정체불명 어린이용 반짝이 물건",
            "user_query": "어린이용 반짝이 물건인데 정확한 품목을 모르겠습니다.",
            "target_age": "7세",
            "material_text": None,
            "power_type": None,
            "battery_included": False,
            "import_or_manufacture": "수입",
        },
    ),
]


def _truncate(s: str, n: int) -> str:
    s = (s or "").replace("\n", " ").strip()
    return s + "..." if len(s) > n else s


def build_md(title: str, body: dict, d: dict) -> str:
    cands = d["legal_product_candidates"]
    cert = d["certification_diagnosis"]
    inst = d["institution_guidance"]
    recall = d["recall_reason_summary"]
    kc = d["kc_certification_summary"]
    checklist = d["launch_checklist"]

    md = f"# 시연 케이스: {title}\n\n"
    md += "> 생성 환경: template-only (ENABLE_LLM=false)  \n"
    md += f"> report_generation_mode: `{d['report_generation_mode']}`\n\n"
    md += "---\n\n"

    # 1. 입력 JSON
    md += "## 1. 입력 JSON\n\n```json\n"
    md += json.dumps(body, ensure_ascii=False, indent=2)
    md += "\n```\n\n"

    # 2. 법정 품목명 후보
    md += "## 2. 법정 품목명 후보\n\n"
    if not cands:
        md += "  (후보 없음)\n\n"
    else:
        for c in cands:
            md += f"  - **{c['legal_product_name']}** (`{c['confidence_level']}`, score={c['confidence_score']:.2f})\n"
            md += f"    - 매칭 근거: {c['match_basis']}\n"
        md += "\n"

    # 3. 인증유형 및 안전기준
    md += "## 3. 인증유형 및 안전기준\n\n"
    md += f"  - 인증유형: `{cert['certification_type']}`\n"
    md += f"  - 판단 수준: `{cert['judgement_level']}`\n"
    md += "  - 적용 안전기준:\n"
    if cert["applied_standards"]:
        for s in cert["applied_standards"]:
            md += f"  - {s}\n"
    else:
        md += "  (없음)\n"
    md += "\n"

    # 4. 시험기관/절차 안내
    md += "## 4. 시험기관/절차 안내\n\n"
    md += f"  - 기관 필요: {'예' if inst['institution_required'] else '아니오'}\n"
    md += f"  - 요약: {inst['summary']}\n"
    if inst["candidate_institutions"]:
        md += "  - 후보 기관:\n"
        for org in inst["candidate_institutions"]:
            short = f" ({org['short_name']})" if org.get("short_name") else ""
            md += f"    - **{org['institution_name']}{short}** (`{org.get('certification_type','')}`)\n"
    md += "\n"

    # 5. 국내 리콜 사유
    md += "## 5. 국내 리콜 사유\n\n"
    n = recall["recall_count"]
    md += f"  - 리콜 건수: {n}건\n"
    if n > 0:
        md += "  - 주요 사유:\n"
        for r in recall["top_recall_reasons"]:
            md += f"  - {r}\n"
        md += "  - 대표 사례:\n"
        for c in recall["representative_cases"][:3]:
            md += f"    - {_truncate(c, 110)}\n"
        if recall["prevention_points"]:
            md += "  - 사전 점검 포인트:\n"
            for pt in recall["prevention_points"]:
                md += f"  [ ] {pt}\n"
    else:
        md += "  - (품목군 미확정으로 직접 매칭되는 국내 리콜 사례가 없습니다.)\n"
    md += "\n"

    # 6. KC 동일 품목군 인증사례 참고
    md += "## 6. KC 동일 품목군 인증사례 참고\n\n"
    md += f"  - matched_category: `{kc.get('matched_category','')}`\n"
    md += f"  - 인증사례 수: {kc['similar_cert_count']:,}건\n"
    if kc["top_cert_organ_names"]:
        md += f"  - 주요 인증기관: {', '.join(kc['top_cert_organ_names'])}\n"
    if kc["representative_models"]:
        md += "  - 대표 인증사례:\n"
        for m in kc["representative_models"]:
            md += f"  - {m}\n"
    md += f"  - note: {kc['note']}\n\n"

    # 7. 출시 전 체크리스트
    md += "## 7. 출시 전 체크리스트\n\n"
    if checklist:
        for item in checklist:
            md += f"  [ ] {item}\n"
    else:
        md += "  (확인 필요 — 법정 품목명 미확정으로 체크리스트 생성 불가)\n"
    md += "\n"

    # 8. 검색 근거 요약 (RAG) — 내부 chunk ID/근거 출처 (사용자 보고서엔 미노출)
    md += "## 8. 검색 근거 요약 (RAG / source_refs)\n\n"
    used = d.get("used_rag_chunk_ids") or []
    refs = d.get("source_refs") or []
    if used:
        md += f"  - used_rag_chunk_ids ({len(used)}): {', '.join(used)}\n"
    else:
        md += "  - used_rag_chunk_ids: (없음 — 품목 미확정 시 근거 chunk 미수집)\n"
    if refs:
        md += f"  - source_refs ({len(refs)}건, 일부):\n"
        for r in refs[:12]:
            md += f"    - {r}\n"
    md += "\n"

    # 9. final_report_markdown 원문
    md += "## 9. final_report_markdown 원문\n\n```markdown\n"
    md += d["final_report_markdown"].strip()
    md += "\n```\n"

    return md


def main() -> None:
    DEMO_DIR.mkdir(parents=True, exist_ok=True)
    with TestClient(app) as client:
        for filename, title, body in CASES:
            resp = client.post("/diagnose", json=body)
            resp.raise_for_status()
            d = resp.json()
            md = build_md(title, body, d)
            out = DEMO_DIR / filename
            out.write_text(md, encoding="utf-8")
            print(
                f"[OK] {filename}: top="
                f"{(d['legal_product_candidates'][0]['legal_product_name'] if d['legal_product_candidates'] else '(없음)')}"
                f", recall={d['recall_reason_summary']['recall_count']}"
                f", rag={len(d.get('used_rag_chunk_ids') or [])}"
            )


if __name__ == "__main__":
    main()
