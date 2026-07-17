# Material Intake (FR-M) — Layer 2 pipeline detail

> Read before scanning or classifying any material. This is the executable detail behind
> the SKILL.md "Layer 2" section. The classifier is a librarian, not a reviewer:
> contradictions between materials are *not* judged here — they are events for Layer 3.
> Originals are immutable; classification never edits them.

## Receipt mode — collection loop (P1)

자료가 도착할 때는 먼저 접수 목록에만 적재합니다. 이 모드에서는 스캔·PHI 스크리닝·
분류·역추출을 하지 않습니다. 파일명과 확장자, 또는 붙여넣은 식별자만으로 rule hint 수준의
잠정 이해를 말하며, 내용에 관한 과잉 추론을 하지 않습니다.

각 도착 자료에는 다음처럼 한 줄로 응답합니다.

> "`{받은 것}`을 받았습니다. 파일명·확장자로 보면 `{잠정 역할}`로 보입니다. 더 주실 것이 있으신가요?"

이 문장은 분류 판정이나 추가 정보 요구가 아닌 접수 확인입니다. 따라서 FR-M9의
파일별 분류 질문 억제와 충돌하지 않습니다.

유저가 "이게 다예요"처럼 종료를 선언하면, 먼저 P3 작업공표를 한 줄로 합니다. 무엇을
일괄 스캔하는지, 결과가 `research/.system/scan-report.json`과 `research/00_materials/`에 생긴다는 점,
대략 소요를 함께 알립니다. 파일·디렉토리만 `--input` 반복 인자로 넘기고, 붙여넣은
PMID/DOI/URL은 쉼표로 모아 하나의 `--paste-refs` 값으로 넘깁니다. 식별자를 `--input`으로
넘기지 않습니다. 이어서 접수 목록 전체를 **한 번만** 배치 스캔하고, 아래 파이프라인의
Stage 2 분류와 브리핑으로 합류합니다.

분류나 브리핑 뒤에 자료가 더 도착하면 새 자료를 미니 인테이크로 접수한 뒤 종료 선언
시 재스캔하여 기존 흐름에 흡수합니다. `version_groups`는 그 미니 배치 안에서만 계산됩니다.
배치 간 중복·버전 lineage는 Stage 2가 기존 `materials.json` 레지스트리와 대조하여
`lineage_hints`로 판정합니다.

## Pipeline pseudo-flow

```
Stage 0  format detection       rules (ext + magic bytes)          material_scanner.py
Stage 1  structure scan         format-specific light extract       material_scanner.py
Stage 2  LLM batch classify     role · confidence · rationale       host LLM (this file's contract)
Stage 3  precise disambiguation low-confidence only, or a question  host LLM / user
```

Stage 0–1 are offline and free (rule + structure). Stage 2 is the only LLM cost — batch
it. Stage 3 is optional, low-confidence only.

## Scanner call (Stage 0–1)

Exactly as the CLI defines (`scripts/material_scanner.py`):

```
python3 ${CLAUDE_PLUGIN_ROOT}/skills/researchfellow/scripts/material_scanner.py \
    --input <file|dir> [--input <file|dir> ...] \
    [--paste-refs "PMID:38812345, 10.1001/jama.2024.1234"] \
    --project-dir research \
    [--no-copy] [--phi-screen] \
    --output research/.system/scan-report.json
```

- `--input` is repeatable (files or directories). `--paste-refs` accepts loose PMIDs/DOIs.
- `--phi-screen` shells out to `phi_screener.py` for **tabular files only** during the scan.
  Scanner-produced text excerpts (docx / md / txt / code, headings included) are masked
  through the `phi_detect` engine **always, independent of this flag** — hits are replaced
  with `[MASKED:<rule_id>]` placeholders before the excerpt enters the report, and intake
  continues (mask-only policy).
- Originals are copied to `00_materials/<sha256[:12]>_<name>` unless `--no-copy`.
- Output `scan-report.json`: `entries[]` (each with `format`, `structure`,
  `rule_role_hint{role,rule,certainty}`, `identifiers`, `lineage_pre`, `needs_llm`,
  `excerpt_source`, `phi`), `pasted_refs[]`, `version_groups{}`, `llm_batch_needed[]`.
- `entry.phi` has two shapes: tabular entries carry the subprocess screen result
  (`severity`, `finding_count`, `report` = phi-report file path); document/code entries
  carry the inline masking result (`backend`, `target: "excerpt"`, `severity`,
  `finding_count`). Either shape feeds the same `flags` channel (`phi_suspect`).
- `rule_role_hint.certainty == "strong"` ⇒ `needs_llm:false` — the rule already decided;
  do **not** spend an LLM call on it. Classify only the `llm_batch_needed` set in Stage 2.
- PDFs carry `excerpt_source: "host_llm_read"` and no extracted text — you Read the first
  1–2 pages yourself when you need the excerpt (the scanner never extracts PDF text).
- `excerpt_source: "unscreened"` means the masking engine was unavailable or errored and
  the excerpt was **withheld** (fail-closed, empty string). The entry also carries a
  `needs_full_read` flag — Read the original yourself only after confirming with the user,
  and never paste identifier values into the conversation.

## PHI screener call (standalone)

```
python3 ${CLAUDE_PLUGIN_ROOT}/skills/researchfellow/scripts/phi_screener.py \
    --data-path <csv|xlsx|text> \
    --output research/.system/phi-report_<material_id>.json
```

Exit `0 clean / 1 warning / 2 critical`. Report holds only
`{column, rule_id, severity, match_count, match_rate, example_rows(row numbers only)}`.

Detection logic lives in the `phi_detect` engine (`scripts/phi_detect.py`), selected via
env `RF_PHI_BACKEND` (default `rules`). An unsupported backend value is an **explicit
error** (`UnsupportedPHIBackendError`) — never a silent fallback: phi_screener exits
non-zero, and material_scanner withholds excerpts per file (fail-closed). Future
detection upgrades (NER / Presidio adapters) plug in as new backends without touching
the callers.

**PHI warning protocol:** on warning/critical, warn the user and recommend
pseudonymization, but **never quote or reproduce a matched value** — reference the column
name and row numbers only. Include "스크리닝은 보조 검사이며 최종 확인 책임은 사용자에게
있습니다." Audit as `PHI_DETECTED {material, rules[], counts[]}` — again, no values.

---

## Role ontology (Stage 2 classification target)

The scanner emits a coarse `bibliographic` role for reference files; Stage 2 refines every
material into one of these semantic roles. Discriminators are 1–2 lines each.

### 데이터 계열
| role | 판별 단서 |
|---|---|
| `raw_dataset` | 대행수 + 고유 ID 컬럼. 환자·레코드 단위 원자료. 결과표가 아님. |
| `codebook` | 변수명·정의·코딩값을 나열한 사전. "코드북"·"데이터 사전"·variable dictionary. |
| `data_schema` | 컬럼/테이블 구조 정의(타입·관계)만, 값 없음. DDL·스키마 문서. |
| `extraction_query` | SQL `SELECT ... FROM`, 코호트 추출 쿼리. |

### 문헌 계열
| role | 판별 단서 |
|---|---|
| `bibliographic` | (coarse) .ris/.bib/.nbib 참고문헌 파일 — Stage 2에서 아래 세부 역할로 정제. |
| `background_paper` | 주제 배경을 다루는 선행 논문. novelty 위협 아님. |
| `competing_paper` | 같은/유사 설계의 경쟁·선행 연구 — novelty 포지셔닝에 영향. |
| `methods_paper` | 방법론 참고(통계 기법·설계 방법)용 논문. |
| `review_paper` | 종설/체계적 문헌고찰. |

### 프로젝트 산출물 계열
| role | 판별 단서 |
|---|---|
| `idea_note` | 연구 아이디어 메모·초안. 구조화 전 자유 텍스트. |
| `protocol` | 연구 프로토콜(배경·목적·방법·코호트 정의). 제목에 "Protocol". |
| `sap` | 통계분석계획서. 제목/헤딩에 "Statistical Analysis Plan". |
| `irb_approval` | IRB/윤리위 승인서·심의 문서. |
| `analysis_output` | 표·그림·로그(효과추정치·CI·p-value). 소행수 결과표. **provenance 문진 트리거.** |
| `analysis_code` | R/py/SAS 분석 코드(survival·glm·statsmodels 시그니처). |
| `manuscript_draft` | 원고 초안(IMRD 구조, Abstract/Methods/Results 헤딩). |
| `reviewer_comments` | 리뷰어 코멘트·심사 의견서. |
| `response_letter` | 리뷰 대응 레터(point-by-point). |

### 기타
| role | 판별 단서 |
|---|---|
| `journal_guideline` | 저널 투고 규정·가이드라인. |
| `conference_abstract` | 학회 초록. |
| `unknown` | 상충 신호 또는 발췌만으로 불명 → 질문행. |

**Role → step map (starting-point derivation):**

| 보유 역할 | 충족/재료 단계 | 함의 |
|---|---|---|
| `idea_note` | Step 1 재료 | PICO 역추출 대상 |
| `background_paper`/`competing_paper`/`review_paper` | Step 2–3 재료 | 근거표 증분 |
| `methods_paper` | Step 6 재료 | 방법론 참고 |
| `codebook`/`data_schema` | Step 4 재료 | 변수 매핑 |
| `protocol` | Step 1–5 충족(역추출) | Step 6부터 제안 |
| `sap` | Step 6 충족 | Step 7–8부터 |
| `raw_dataset` (QC 이력 없음) | Step 9 진입 재료 | real-data gate 문진 필요 |
| `analysis_output` + `analysis_code` | Step 10 충족 후보 | **provenance 문진 필수** |
| `manuscript_draft` | Step 11 재료 | 원고 진단·재진입 |
| `reviewer_comments` | Step 13 재료 | 리비전 루프 |

---

## Stage 2 — LLM batch classification prompt contract

**Context to assemble:** ① the role ontology above ② the entry-point prior table below
③ the current PICO (if any) ④ existing `materials.json` summary of `(material_id, role,
filename)` for cross-batch lineage comparison. **Input:** scan-report excerpts for the
`llm_batch_needed` set + (for PDFs) the first 1–2 pages you Read. **Batch ≤ 10 materials
per call.**

### Entry-point priors (FR-M5)

Priors **break ties only. Content evidence always wins.**

| 기점 | 사전확률이 높은 역할 |
|---|---|
| S1 | `idea_note`, `background_paper`, `competing_paper` |
| S2 | `raw_dataset`, `codebook`, `data_schema` |
| S3 | `protocol`, `sap`, `analysis_output`, `analysis_code`, `raw_dataset` |
| S4 | `manuscript_draft`, `protocol`, `sap` |
| S5 | `reviewer_comments`, `manuscript_draft`, `response_letter` |

### Output contract (JSON only)

```json
[
  { "material_id": "m-003",
    "role": "protocol",
    "confidence": "high",
    "rationale": "제목에 'Study Protocol', 헤딩에 코호트 정의·목적",
    "lineage_hints": { "version_group_with": ["m-004"], "derived_from": [], "duplicate_of": null },
    "flags": [] }
]
```

`flags` domain (exactly these): `phi_suspect` | `plan_data_mismatch_suspect` |
`competing_paper_novelty` | `needs_full_read`. Flags route to Layer 3; they are not
classification verdicts. Note: `phi_suspect` and `needs_full_read` can arrive from the
scanner layer too (rule-based masking / engine fail-closed), not only from this Stage 2
output — treat both origins identically.

### Confidence handling (FR-M9)

- **high** → silently `confirmed`. Do not ask.
- **medium** → a **confirm row** in the briefing inventory, batched with others.
- **low** → Stage 3 re-read; if still unclear, a **question row** in the briefing.

Write each verdict into `materials.json` (`templates/materials-registry-template.json`):
`classified_by: rule|llm|user`, `status: unconfirmed|confirmed|rejected|reclassified`.
`promoted_to` links to the artifact registry id (bidirectional with state.json
`artifacts.*.source`). Reclassification after promotion invalidates the promoted artifact
and appends `MATERIAL_RECLASSIFIED` (cascade per state-machine.md).

---

## Briefing — the 3 agendas

Render a collapsible inventory by family (confirmed = ✓ one-liner; medium/low + version
representatives gathered into one batch confirm row; questions last), then discuss:

`research_card`가 있으면 브리핑 첫 줄에 한 문장으로 보여 줍니다. 값이 없거나 `null`이면
조용히 생략합니다.

```
받은 자료 9개를 정리했습니다.

  데이터        ▸ cohort_2024.csv (12,483행) · codebook.xlsx        ✓ 확정
  문헌 (5)      ▸ 배경 4편 · 유사 선행연구 1편 ⚠ novelty 검토 필요     ✓ 확정
  프로토콜      ▸ protocol_v3.docx (v1·v2는 이전 버전으로 묶음)        ✓ 확정
  확인 필요 (1) ▸ old_notes.docx — 아이디어 메모인가요, 프로토콜 초안인가요?
               [메모예요] [프로토콜이에요] [열어서 같이 보기]
```

1. **할 수 있는 것** — "이 자료로 가능한 작업: 코호트 연구 설계(데이터 충분), 문헌
   근거표(논문 6편), 원고 Methods 초안(프로토콜 기반)."
2. **자료 수준 평가 (갭 리포트)** — 충분/부족. "배경 문헌이 2편뿐입니다. 근거표를 만들려면
   10편 이상 권장." / "코드북이 없어 변수 의미를 추정해야 합니다 — 코드북이 있나요?"
3. **시작점 제안** — "제안: Step 6(SAP)부터. 프로토콜에서 PICO·변수 정의를 역추출해
   두었으니 확인해 주세요." (역추출 완료 고지)

### Gap-verdict decision rule (FR-M8)

For a proposed arrival step N, after reverse-fill, run
`state_tool can-enter --project-dir research --step N`. **If it still returns exit 2, the
gap is large** — do not proceed unilaterally; force the interactive interview branch:

> "문헌이 부족하니 **문헌 검색부터 같이 할까요**, 아니면 가지고 계신 논문이 더 있나요?"

The interview's conclusion *is* the Intake Gate.

---

## Intake Gate (FR-W5) — 4 stages

One body with the briefing's "confirm the starting point". Procedure is authoritative in
`references/state-machine.md` (§Intake Gate) — apply it exactly:

1. **Batch confirm** reverse-filled `draft` artifacts on one screen ("맞으면 한 번에 확정,
   틀린 항목만 짚어주세요") → `draft→valid`, `step→imported`, `ARTIFACT_IMPORTED × N`.
2. **Retroactive soft gates, batched** — one rationale sentence each, approve in one pass →
   `approved` + `retroactive:true` + `GATE_RETROACTIVE × N`. Contested gates stay pending +
   blocker.
3. **Real-data 3 gates, individually** (arrival ≥ 9 or `analysis_output` confirmed) —
   `feasibility` and `protocol` individually; `qc` requires the provenance interview below.
   **Retroactive is forbidden here.** If unapproved: `execution_mode` stays `planning`,
   Step 10 deterministically blocked.
4. **Finalize** — record `arrival_step`, `focus_step`, `next_action`.

### Provenance interview (FR-M11) — 3 questions

Ask when `analysis_output` is confirmed (verbatim):

1. "이 결과가 나온 **실제 환자 데이터가 존재**하나요?"
2. "그 데이터에 대해 **QC(품질 검증)를 수행**하셨나요?"
3. "분석을 **재현할 수 있는 코드**가 있나요?"

| 응답 | 판정 |
|---|---|
| 전부 예 | `gate.qc` 정식 승인 가능 + `real_results` `imported` + `PROVENANCE_ATTESTED` |
| 데이터 실재 = 아니오 | 결과는 **참고자료로만**. 원고 Results 사용 금지 + FR-G5 limitation 예약 |
| QC = 아니오 | `gate.qc` pending, Step 9 권고, 수치 주장 차단 |

---

## Safety recap

- Materials are optional: "없으면 건너뛰어도 됩니다."
- PHI: never quote a matched value; column + row numbers only; screening is advisory,
  user holds final responsibility.
- `analysis_output` always triggers the provenance interview — an imported result can
  never silently bypass the real-data hard gates.
