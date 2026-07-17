# Interaction Model (FR-I) — user-facing grammar

> Read before receiving material, restating the study, announcing autonomous work,
> requesting a decision, reporting completion, rendering the resume view, or explaining a
> blocker. This document is the canonical interaction grammar; it changes conversation
> copy only, never the state-machine judgment or gate semantics.

## Primitives (P1–P7)

| Primitive | Definition | Required elements |
|---|---|---|
| P1 Receipt | 자료·텍스트가 도착했을 때의 접수 | 받은 것 + 1줄 잠정 이해 + "더 주실 것이 있으신가요?" |
| P2 Restate | 행동 전에 연구를 어떻게 이해했는지 재진술 | research card + 출처 3분류 + 불확실한 점 |
| P3 Announce | 자율 작업으로 잠시 응답이 없기 직전의 작업공표 | 무엇을 할지 + 결과 위치 + 대략 소요 |
| P4 Confirm | 유저의 결정을 요청 | 한 화면 한 결정; soft는 배치, hard는 개별 |
| P5 Report | 작업 완료 뒤의 보고 | 요약 + 산출물 폴더 경로 + 다음 1문장 |
| P6 Orient | 현재 위치를 묻거나 재개할 때의 현황 | 13-step 좌표 + blocker + 다음 액션 |
| P7 Blocker | 진행할 수 없는 순간의 차단설명 | 무엇이 막혔는지 + 연구 언어의 이유 + 해제 조건 + 지금 가능한 것 |

## Touchpoint mapping

| Touchpoint | Primitive | Copy rule |
|---|---|---|
| Telemetry consent (`SKILL.md`) | P4 | 동의 선택 전에 연구 내용은 전혀 전송하지 않고, 어느 단계에서 막히는지만 익명으로 세어 개선에 쓴다는 가치 1줄을 먼저 설명한다 |
| 5+1 starting-point cards (`entry-points.md`) | P4 | 기존 카드 순서와 선택지는 유지한다 |
| S1 interview (`entry-points.md`) | P2 + P4 | 재진술에는 출처 3분류를 적용하고, 확인은 한 번에 요청한다 |
| Material request (FR-M1) | P1 | 접수 모드의 수집 루프를 따른다 |
| Confidence confirmation (`medium`/`low`) | P4 | FR-M9대로 파일별이 아니라 브리핑에서 일괄 확인한다 |
| Briefing — 3 agendas | P2 + P5 | research card 1줄로 시작하고, 역추출 완료를 알린다 |
| Intake Gate — 4 stages | P2 + P4 | 이해진술과 역추출 근거를 먼저 보여 준 뒤 배치 확정을 요청한다 |
| Provenance interview | P4 | 실제 데이터 관련 세 질문은 기존처럼 개별 확인한다 |
| Gate approval — 3 choices | P4 | 선택지 앞에 현재 연구 이해를 1줄로 진술한다 |
| Step transition — 3 moves | P5 | 완료 보고에 산출물 폴더 경로를 포함한다 |
| S0 resume + participation | P6 | research card가 있으면 1줄로 먼저 보여 주고 13-step 현황을 렌더한다 |
| Desk forms (`desk-interface.md`) | P2 + P6 | 기존 라벨과 상태 표현을 사용해 이해와 현황을 함께 보여 준다 |
| PHI warning / fail-closed / can-enter block | P7 | 차단설명 4요소를 같은 순서로 적용한다 |

## Research card and provenance

`research_card`는 연구를 한 문장으로 요약한 상태 필드입니다. PICO를 확정하거나
변경할 때 갱신합니다. 브리핑, Intake Gate, gate approval, S0 resume의 개막에 이
문장을 사용합니다. 값이 없거나 `null`이면 표시하지 않고 계속합니다.

모든 P2 재진술에는 다음 출처를 문장 안에 드러냅니다.

- **전달받음** — 유저가 직접 말씀하시거나 제공한 내용: "말씀해 주신 대로 —"
- **추론** — 자료에서 읽어낸 내용과 근거: "제가 읽어낸 것 — `{자료명}`의 `{위치}`에서 …"
- **불확실** — 아직 확인되지 않은 해석: "확실치 않은 것 — …"

역추출 재진술은 `_provenance`의 자료명과 위치를 함께 노출합니다. 근거가 없는
추론은 전달받음처럼 말하지 않습니다.

## Announce, report, and blocker copy

P3 작업공표는 긴 자율 작업 직전에 한 문장으로 합니다. 무엇을 처리하는지, 결과를
어느 경로에서 확인하는지, 대략 얼마나 걸리는지를 함께 말합니다. 실제 생성될
산출물만 경로로 약속합니다.

P5 보고는 만든 내용의 짧은 요약 뒤에 산출물 폴더 경로와 다음 행동 한 문장을
붙입니다. 파일명만으로 보고를 끝내지 않습니다. 단계 완료 시 첫 무브의 요약은 해당 단계
폴더의 `SUMMARY.md`에도 한국어 산문으로 전체 재작성해 저장하며, 이전 요약을 덮어씁니다.

P7 차단설명은 다음 네 요소를 같은 순서로 포함합니다.

1. 무엇이 막혔는지
2. 왜 막혔는지 — 시스템 용어 대신 연구 언어로 설명
3. 무엇이 갖춰지면 풀리는지
4. 그동안 지금 할 수 있는 일

예: "QC 확인이 아직 없어 실분석을 진행할 수 없습니다. QC 없이 수치를 쓰면
리뷰어가 결과를 신뢰하기 어렵습니다. QC 보고서가 확인되면 풀립니다. 그동안
프로토콜과 분석 계획은 계속 다듬을 수 있습니다."

## Collection loop

P1의 수집 루프 실행 규칙은 `references/material-intake.md`의 **Receipt mode**를
따릅니다. 이 문서는 수집 절차를 중복 정의하지 않습니다.
