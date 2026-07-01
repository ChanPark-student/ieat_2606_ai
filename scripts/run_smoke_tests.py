# -*- coding: utf-8 -*-
"""공식 smoke test — A~F 케이스로 핵심 불변식 검증.

실행:
    python scripts/run_smoke_tests.py
종료코드 0 = 전체 통과, 1 = 실패. CI/발표 직전 빠른 점검용.
ENABLE_LLM=false 기본 (모델 다운로드 없음).
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

from fastapi.testclient import TestClient  # noqa: E402
from app.main import app  # noqa: E402

CASES = {
    "A_bag": {
        "body": {"product_name": "어린이용 책가방", "user_query": "초등학생이 사용하는 책가방을 출시하려고 합니다.", "target_age": "8세", "material_text": "폴리에스터, 코팅 원단, 플라스틱 버클", "power_type": "없음", "battery_included": False, "import_or_manufacture": "수입"},
        "expect": {"top_legal": "아동용 섬유제품", "cert": "공급자적합성확인", "confirmed": True, "rag_nonempty": True},
    },
    "B_toy_car": {
        "body": {"product_name": "장난감 자동차", "user_query": "5세 어린이가 사용하는 건전지 장난감 자동차를 수입하려고 합니다.", "target_age": "5세", "material_text": "플라스틱, 금속 나사", "power_type": "건전지", "battery_included": True, "import_or_manufacture": "수입"},
        "expect": {"top_legal": "완구", "cert": "안전확인", "confirmed": True, "recall_count": 224, "rag_nonempty": True},
    },
    "C_pencil": {
        "body": {"product_name": "어린이 색연필 세트", "user_query": "초등학생이 사용하는 24색 색연필 세트를 출시하려고 합니다.", "target_age": "8세", "material_text": "목재, 안료, 종이 포장재", "power_type": "없음", "battery_included": False, "import_or_manufacture": "제조"},
        "expect": {"top_legal": "학용품", "cert": "안전확인", "confirmed": True, "rag_nonempty": True},
    },
    "D_underwear": {
        "body": {"product_name": "유아용 내의", "user_query": "12개월 아기가 입는 면 소재 내의를 제조해서 판매하려고 합니다.", "target_age": "12개월", "material_text": "면 100%, 봉제 원단, 고무 밴드", "power_type": "없음", "battery_included": False, "import_or_manufacture": "제조"},
        "expect": {"top_legal": "유아용 섬유제품", "cert": "안전확인", "kc_not": "아동용 섬유제품"},
    },
    "E_unknown": {
        "body": {"product_name": "정체불명 어린이용 반짝이 물건", "user_query": "어린이가 사용하는 반짝이는 물건을 출시하려고 합니다.", "target_age": "6세", "material_text": "플라스틱, 반짝이 코팅", "power_type": "없음", "battery_included": False, "import_or_manufacture": "수입"},
        "expect": {"confirmed": False, "cert": "확인 전", "recall_count": 0, "supp_empty": True, "rag_empty": True, "kc_zero": True},
    },
    "F_doll": {
        "body": {"product_name": "벨리곰 인형", "user_query": "핑크색 곰인형입니다.", "target_age": "5세부터 15세까지", "material_text": "극세사 원단, 솜, 실, 플라스틱 단추", "power_type": "없음", "battery_included": False, "import_or_manufacture": "제조"},
        "expect": {
            "top_legal": "완구", "cert": "안전확인", "confirmed": True, "recall_count": 224, "rag_nonempty": True,
            "checklist_heat_low": True, "checklist_strap_low": True, "checklist_fabric_hazard_present": True,
            "battery_shown_no": True,
        },
    },
}

_TECH_TERMS = ["BM25", "retriever", "retrieved_chunk", "top-k", "token overlap"]


def check(label: str, cond: bool, detail: str = "") -> bool:
    mark = "PASS" if cond else "FAIL"
    print(f"   [{mark}] {label}" + (f" — {detail}" if detail and not cond else ""))
    return cond


def main() -> int:
    ok = True
    with TestClient(app) as client:
        health = client.get("/health").json()
        print("[/health]", health.get("loaded"))
        ok &= check("/health status ok", health.get("status") == "ok")

        for name, spec in CASES.items():
            print(f"\n=== {name} ===")
            r = client.post("/diagnose", json=spec["body"])
            ok &= check("HTTP 200", r.status_code == 200, str(r.status_code))
            if r.status_code != 200:
                continue
            d = r.json()
            exp = spec["expect"]
            cands = d["legal_product_candidates"]
            top = cands[0] if cands else None
            cert = d["certification_diagnosis"]["certification_type"]
            recall = d["recall_reason_summary"]
            kc = d["kc_certification_summary"]
            used = d.get("used_rag_chunk_ids") or []

            ok &= check("report_generation_mode=template", d["report_generation_mode"] == "template")

            if "top_legal" in exp:
                ok &= check(f"top={exp['top_legal']}", bool(top) and top["legal_product_name"] == exp["top_legal"],
                            top["legal_product_name"] if top else "(없음)")
            if "cert" in exp:
                ok &= check(f"cert={exp['cert']}", cert == exp["cert"], cert)
            if exp.get("confirmed") is True:
                ok &= check("CONFIRMED/CANDIDATE 존재", any(c["confidence_level"] in ("CONFIRMED", "CANDIDATE") for c in cands))
            if exp.get("confirmed") is False:
                ok &= check("CONFIRMED 0개", sum(1 for c in cands if c["confidence_level"] == "CONFIRMED") == 0)
            if "recall_count" in exp:
                ok &= check(f"recall_count={exp['recall_count']}", recall["recall_count"] == exp["recall_count"], str(recall["recall_count"]))
            if exp.get("supp_empty"):
                ok &= check("supplemental_cases==[]", recall.get("supplemental_cases") == [])
            if exp.get("rag_nonempty"):
                ok &= check("used_rag_chunk_ids 비어있지 않음", len(used) > 0, str(len(used)))
            if exp.get("rag_empty"):
                ok &= check("used_rag_chunk_ids==[] (과확정 차단)", used == [])
            if exp.get("kc_zero"):
                ok &= check("KC==0", kc["similar_cert_count"] == 0)
            if "kc_not" in exp:
                ok &= check(f"KC 교차매칭 금지 (!= {exp['kc_not']})", kc.get("matched_category") != exp["kc_not"], repr(kc.get("matched_category")))

            md = d["final_report_markdown"]
            leaked = [t for t in _TECH_TERMS if t in md]
            ok &= check("보고서 기술용어 미노출", not leaked, ",".join(leaked))

            # §7 체크리스트 순서 검증 (봉제완구/섬유 소재 우선순위 재정렬)
            i7 = md.find("## 7. 출시 전 확인 체크리스트")
            i8 = md.find("## 8.")
            checklist_block = md[i7:i8] if i7 >= 0 and i8 >= 0 else ""
            checklist_lines = [l for l in checklist_block.split("\n") if l.strip().startswith("- [ ]")]
            n = len(checklist_lines)

            if exp.get("checklist_heat_low") and n:
                heat_idx = next((i for i, l in enumerate(checklist_lines) if "온열" in l), None)
                ok &= check(
                    "온열 항목이 하위 절반 이내 (제거되지 않되 후순위)",
                    heat_idx is not None and heat_idx >= n // 2,
                    f"idx={heat_idx}/{n}",
                )
            if exp.get("checklist_strap_low") and n:
                strap_idx = next((i for i, l in enumerate(checklist_lines) if "끈과 코드" in l), None)
                ok &= check(
                    "끈/코드 항목이 최하위 (입력에 관련 신호 없음)",
                    strap_idx is not None and strap_idx == n - 1,
                    f"idx={strap_idx}/{n}",
                )
            if exp.get("checklist_fabric_hazard_present"):
                has_formaldehyde = any("폼알데하이드" in l for l in checklist_lines)
                has_azo = any("아릴아민" in l or "아조염료" in l for l in checklist_lines)
                ok &= check(
                    "폼알데하이드·아릴아민/아조염료 항목이 체크리스트에 유지됨 (섬유 소재 맥락)",
                    has_formaldehyde and has_azo,
                )
            if exp.get("battery_shown_no"):
                ok &= check("§1에 배터리 포함: 아니오 명시", "배터리 포함**: 아니오" in md)

            if "checklist_heat_low" in exp or "checklist_strap_low" in exp:
                print(f"   (§7 체크리스트 {n}개 순서: " + " | ".join(
                    l.strip()[6:36] for l in checklist_lines) + ")")

    print("\n" + "=" * 50)
    print("SMOKE TEST:", "ALL PASS ✅" if ok else "FAILURES ❌")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
