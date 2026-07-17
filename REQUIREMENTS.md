# ResearchFellow Plugin — Requirements v0.1 (2026-07-04)

> 무료 배포 계층. 유저의 LLM 호스트(Claude Code / Claude Desktop)에 설치되어
> **환자 데이터를 만지는 모든 작업을 로컬에서** 수행한다.
> 설계 근거: `../docs/entry-points-design_2026-07-04.md`,
> `../docs/material-classification-strategy_2026-07-04.md`,
> `../docs/business-model-mcp-plugin_2026-07-04.md`

## 0. 정체성 · 원칙

- ResearchFellow는 "한 명의 연구자로 승격한" 동료 — 위저드가 아니라 같이 자료를 읽고
  논의하는 공동연구자 페르소나.
- **무료만으로 12단계 완주가 가능해야 한다** (플러그인 = 퍼널).
- 환자 데이터(PHI)는 어떤 경로로도 로컬을 벗어나지 않는다.
- 무결성 가드레일은 전부 무료 계층에 포함된다.

## 1. 배포 타깃

| ID | 요구사항 |
|----|---------|
| DT-1 | Claude Code plugin 형식 (`.claude-plugin/plugin.json` + skills + `.mcp.json`) |
| DT-2 | Claude Desktop에서 동일 원격 MCP를 connector로 사용 가능해야 함 (스킬 계층만 별도 패키징) |
| DT-3 | 플러그인 설치 → 첫 `/rf` 호출까지 필수 설정 0개 (NCBI 키 등은 해당 기능 사용 시점에 안내) |

## 2. FR-E — 진입점 (Layer 1: 의도 기점)

| ID | 요구사항 |
|----|---------|
| FR-E1 | 신규 프로젝트 시작 시 "무엇을 하시겠어요?" 기점 5+1을 제시한다: S1 아이디어 / S2 데이터 제안 / S3 논문 새로 쓰기 / S4 논문 수정 / S5 리뷰 대응 / S0 재개 |
| FR-E2 | S1: 자유 텍스트를 받아 시스템이 명확도(명확/거침/막연)를 판정하고 대화 깊이를 조정한다. 유저에게 명확도 분류를 요구하지 않는다 |
| FR-E3 | S1 거침 판정 시: 2~4개의 좁히는 질문(exposure, outcome 임상 중요도, 데이터 접근성)으로 인터뷰 |
| FR-E4 | S1 막연 판정 시: 관심 분야·보유 데이터·최근 읽은 논문에서 출발하는 탐색 대화 → 필요시 S2로 자연 전환 |
| FR-E5 | S2: retrospective-autoresearch 계열의 Discovery 흐름으로 핸드오프 (포트폴리오 후보 선택 시 S1 파이프라인 합류) |
| FR-E6 | S0: 저장 지점 + "이어서: {다음 액션}" 동사형 라벨 + blocker 요약을 표시한다 |
| FR-E7 | 기점 선택은 `state.json`에 `entry_point`로 기록되고 audit에 `ENTRY_POINT` 이벤트를 남긴다 |

## 3. FR-M — 자료 인테이크 · 분류 (Layer 2)

| ID | 요구사항 |
|----|---------|
| FR-M1 | 기점 확정 후 자료를 요청한다 ("없으면 건너뛰어도 됩니다"). 복수 파일·붙여넣기(PMID/DOI/URL) 지원 |
| FR-M2 | 분류 파이프라인 Stage 0(형식: 확장자·MIME·매직바이트) → Stage 1(구조 스캔) → Stage 2(호스트 LLM 배치 분류)를 로컬에서 수행 |
| FR-M3 | 4축 분류: format × role(연구 온톨로지 21종) × confidence(high/medium/low) × lineage(버전·파생·중복) |
| FR-M4 | Stage 1 tabular 휴리스틱: 소행수+p-value/CI 컬럼→analysis_output, 대행수+ID 컬럼→raw_dataset |
| FR-M5 | Layer 1 기점을 분류 사전확률로 프롬프트에 주입한다 |
| FR-M6 | 원본은 `research/00_materials/`에 불변 보관, `research/.system/materials.json` 레지스트리로 관리. 확정 시 아티팩트로 승격(promote, `origin: imported`) |
| FR-M7 | 브리핑 3의제를 대화로 진행: ① 이 자료로 할 수 있는 것 ② 수준 평가(갭 리포트) ③ 구체적 시작점 제안 |
| FR-M8 | 갭이 크면 일방 진행하지 않고 인터뷰로 분기한다 ("문헌 검색부터 같이 할까요, 더 갖고 계신가요?") |
| FR-M9 | 파일마다 묻지 않는다: high는 조용히 확정, medium 이하만 브리핑에서 일괄 확인 |
| FR-M10 | PHI 스크리닝: tabular에서 이름·주민번호·연락처 패턴 규칙 스캔 → 감지 시 가명화 권장 경고, **값은 로그에 남기지 않음** |
| FR-M11 | analysis_output 감지 시 Intake Gate에서 provenance 문진(데이터 실재·QC 수행·코드 존재) 강제, `PROVENANCE_ATTESTED` 기록 |

## 4. FR-I — 인터랙션·가시성

| ID | 요구사항 |
|----|---------|
| FR-I1 | 모든 유저 접점은 인터랙션 프리미티브 7종(P1–P7)으로 기술된다. 정본: `references/interaction-model.md` |
| FR-I2 | `research_card`: state.json 필드. PICO 확정·변경 시 갱신하고, 주요 접점(브리핑·게이트·재개·현황)의 개막 문장으로 사용 |
| FR-I3 | 재진술 출처 3분류(전달받음/추론+근거/불확실). 역추출 확인 화면은 `_provenance`를 자료명·위치로 노출 |
| FR-I4 | 수집 루프: 접수(적재+1줄 이해+"더 있으신가요?") → 종료 선언 → 배치 스캔 1회 → 브리핑 |
| FR-I5 | 작업공표: 자율 작업 전 1줄(무엇/결과 위치/대략 소요 — 기존 duration 표 재사용) |
| FR-I6 | 보고(P5)에는 산출물 폴더 경로 필수. 단계 완료 시 `SUMMARY.md` 영속화 **(SUMMARY.md는 Phase 4; 현재 미구현)** |
| FR-I7 | `PROGRESS.md`·`RESEARCH_LOG.md`는 progress_renderer가 결정론적으로 전체 재생성하고, 모든 저장 지점에서 비차단으로 실행 |
| FR-I8 | 차단설명 4요소(무엇/왜-연구 언어/해제 조건/지금 가능한 것)를 모든 차단 순간에 적용 |
| FR-I9 | PROJECT_INIT 시 13단계 폴더+정적 README+`.gitignore`(materials·desk)를 사전 생성하고, 상태 표기는 PROGRESS.md 단일화 |
| FR-I10 | 표기 언어: 폴더·파일명·표·피겨·상태 라벨은 영어, 설명 산문은 한국어 (P-F) |

## 5. FR-W — 12단계 하네스 (상태머신 v3)

| ID | 요구사항 |
|----|---------|
| FR-W1 | 12단계 워크플로우 + Step 13(Revision Loop, 순환 가능한 유일한 단계) |
| FR-W2 | step status에 `imported` 추가 (pending/in_progress/completed/skipped/blocked/imported) |
| FR-W3 | 진입 조건을 "선행 step 완료"가 아닌 "필요 아티팩트 존재·유효"로 판정 (artifact DAG). 12단계 순서는 기본 경로일 뿐 |
| FR-W4 | 중간 진입 시 하류 자산에서 상류 아티팩트를 역추출(reverse-fill)하고 draft(`imported(unverified)`)로 등록 |
| FR-W5 | Intake Gate: 역추출 draft 일괄 확인 + 건너뛴 gate 소급 처리(`retroactive: true`). 단 real-data gate 3종(feasibility/protocol/qc)은 개별 확인 유지 |
| FR-W6 | gate ID를 순번이 아닌 의미 기반으로 재정의 (`gate.go-no-go`, `gate.novelty`, `gate.endpoint`, `gate.feasibility`, `gate.protocol`, `gate.qc`, `gate.results`, `gate.manuscript`) |
| FR-W7 | gate 이원화: real-data 3종 = hard gate(차단), 나머지 = soft confirm(단계 요약에 묻어가는 확인) |
| FR-W8 | 상류 아티팩트 재실행·gate 번복 시 기존 invalidation cascade 규칙 유지 |
| FR-W9 | 모든 상태 전이는 `audit.jsonl`에 append-only 기록 (기존 이벤트 + `ARTIFACT_IMPORTED`, `GATE_RETROACTIVE`, `MATERIAL_RECLASSIFIED`) |

## 6. FR-T — 로컬 도구 (scripts)

| ID | 도구 | 요구사항 |
|----|------|---------|
| FR-T1 | `pubmed_search` | 유저 본인 NCBI 키/이메일로 검색, 결과 로컬 저장 (기존 스크립트 승계) |
| FR-T2 | `qc_checker` | outcome-index 시간축, 결측률, 분포 이상, 중복 검사 (승계) |
| FR-T3 | `dsl_compiler` | Cohort DSL → SQL (승계) |
| FR-T4 | `analysis_runner` | synthetic/real 모드, real은 hard gate 검증 후 실행 (승계) |
| FR-T5 | `material_scanner` (신규) | 분류 Stage 0~1: 형식 판별 + 구조 스캔 + tabular 프로파일 + DOI/PMID 추출 |
| FR-T6 | `phi_screener` (신규) | FR-M10의 규칙 스캔. 독립 실행 가능 (분류와 분리) |
| FR-T7 | 모든 스크립트는 stdlib 우선. 네트워크 접근은 `pubmed_search`(문헌 검색)와 `telemetry`(동의 기반 퍼널 카운터, fire-and-forget — FR-P)만. 그 외 오프라인 동작 |
| FR-T8 | `progress_renderer` | state.json·audit.jsonl에서 `PROGRESS.md`·`RESEARCH_LOG.md`를 결정론적으로 전체 재생성하며, 어떤 오류에도 exit 0으로 비차단 동작 |
| FR-T9 | `project_layout` | schema v3의 보이는 13단계 스캐폴드를 만들고 legacy 프로젝트를 안전하게 lazy migration |

## 7. FR-G — 가드레일 (전부 무료)

기존 guardrails.md 승계 + 다음 명시:

| ID | 요구사항 |
|----|---------|
| FR-G1 | 합성 결과의 원고 Results/Conclusions/Abstract 삽입 금지 |
| FR-G2 | 수치 주장은 표/그림 출처 참조 필수, novelty 주장은 PMID 근거 필수 |
| FR-G3 | pre-specified vs exploratory 라벨 상시 구분 |
| FR-G4 | hard gate 미승인·QC critical 시 real 분석 차단 |
| FR-G5 | imported 자산 기반 주장에는 provenance 상태를 원고 limitation에 자동 반영 |

## 8. FR-X — 원격 MCP 통합

| ID | 요구사항 |
|----|---------|
| FR-X1 | `.mcp.json`으로 researchfellow-mcp 원격 서버 등록 (Streamable HTTP) |
| FR-X2 | 워크플로우 자연 지점에서 원격 도구를 호출한다: Step 3→`novelty_check`, Step 6→`methodology_advisor`, Step 11→`checklist_map`, Step 12→`integrity_report`, Step 13→`reviewer_playbook` |
| FR-X3 | **(2026-07-16 개정)** 원격 도구는 인증 여부와 무관하게 항상 동일한(full) 결과를 반환한다. 업그레이드·가격 안내 문구는 어디에도 표시하지 않는다 |
| FR-X4 | 원격 도구에 전송하는 입력은 비식별 파생물만: PICO, 스키마(컬럼명·타입), 집계 통계, 텍스트 초안. **raw 데이터 행 전송 금지** — 전송 전 FR-T6로 확인 |
| FR-X5 | 원격 서버 불가용 시 해당 기능만 건너뛰고 워크플로우는 계속된다 (오프라인 완주 보장) |

## 8b. FR-P — 프라이버시·텔레메트리 (2026-07-16 신설)

| ID | 요구사항 |
|----|---------|
| FR-P1 | **동의 게이트**: 첫 `/rf` 실행 시(모든 라우팅·상태 확인 이전) 토큰 발급 + 단계별 사용 이력 수집에 대한 동의를 받는다. 미동의 시 진행하지 않는다 — "막지 말고 얕게"의 유일한 명시적 예외 |
| FR-P2 | **수집 항목은 퍼널 카운터뿐**: 이벤트명(8종)·단계 번호(1-13)·진입점(S1-S5)·플러그인 버전·익명 토큰·프로젝트 해시(uuid4의 sha256). 자유 텍스트·아티팩트·대화는 스키마상 전송 불가 |
| FR-P3 | **유예(폐쇄망)**: 토큰 발급 실패 시 로컬 임시 ID(`local:<uuid4>`)로 시작을 허용하고 이벤트는 로컬 큐(캡 500)에 적재, 온라인 복구 시 정식 토큰으로 치환 후 소급 전송 |
| FR-P4 | **비차단**: telemetry.py는 어떤 경우에도 exit 0 (타임아웃 1.5s, fire-and-forget). 텔레메트리 실패가 워크플로우를 지연·차단하지 않는다 |
| FR-P5 | **철회**: `telemetry.py revoke` — 서버 이벤트 삭제 + 토큰 revoked + 로컬 동의 파일 제거. 절차는 공개 프라이버시 문서(web/privacy.html)에 명시 |
| FR-P6 | 설치 단위 상태는 `~/.researchfellow/`(config.json, queue.jsonl) — 프로젝트 단위 `research/`와 구분되는 유일한 글로벌 상태 |
| FR-P7 | `state.json.project_id`는 `telemetry.py new-project-id`가 생성한 실제 uuid4 — `project_name`(자유 문자열)은 절대 해시 원본으로 쓰지 않는다 |

## 9. 파일 레이아웃 (유저 프로젝트)

```
research/
├── 00_materials/       # 원본 불변 보관
├── 01_pico/ … 13_revision/  # 13개 단계의 정적 README와 산출물
├── rehearsal/          # NOT REAL DATA, DAG 밖
└── .system/            # state.json, audit.jsonl, materials.json, desk/, 보고서·체크리스트
```

## 10. NFR

| ID | 요구사항 |
|----|---------|
| NFR-1 | 오프라인(원격 MCP 없이) 12단계 완주 가능 |
| NFR-2 | PHI 로컬 불변: 어떤 기능도 raw 데이터를 외부 전송하지 않음 |
| NFR-3 | 한국어/영어 대화 지원 (유저 언어 추종) |
| NFR-4 | 상태 파일은 사람이 읽을 수 있는 JSON/JSONL/MD — 벤더 락인 없음 (이탈 시에도 자산은 유저 것) |
| NFR-5 | v1/v2 프로젝트를 판독하고, v2 `.research/` 프로젝트는 P3 작업공표와 P4 확인 뒤 v3 `research/`로 lazy 마이그레이션한다 (gate 순번→의미 ID 매핑 내장) |

## 11. 포팅 노트

원형: `~/Projects/research-bot/.research-skill/` (SKILL.md, references 5종, scripts 4종,
templates 6종). FR-E/FR-M(3층 진입점·분류)이 신규 구현, FR-W는 상태머신 v2 개정,
나머지는 승계·정비.
