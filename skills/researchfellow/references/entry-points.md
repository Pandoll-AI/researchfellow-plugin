# Entry Points (FR-E) — Layer 1 conversation detail

> Read before routing any starting point. This is the executable detail behind the
> SKILL.md "Initialization routing" section. Intent frames; material (Layer 2) supplies
> coordinates. Never ask the user which step they are on, and never expose the internal
> clarity labels (clear/rough/vague).

## Routing recap

```
/research
  state.json exists            → S0 resume view (+ "새 프로젝트 시작")
  absent, free-text argument   → S1 directly (argument = the idea)
  absent, no usable argument   → 5+1 starting-point cards (AskUserQuestion, order fixed)
```

On any card selection or fresh S1: initialize `state.json` from
`templates/project-init.json`, set `project_name`, write `entry_point`, append an
`ENTRY_POINT` audit event, then run the entry path. See "entry_point recording" below.

---

## The 5+1 starting-point cards (verbatim Korean)

Single question, five cards + the resume list. Order is fixed.

> **무엇을 하시겠어요?**
>
> - **① 연구 아이디어를 이야기하고 싶어요** — 자유롭게 말씀해 주시면 PICO로 구조화해 드립니다. *(→ S1)*
> - **② 데이터로 뭘 할 수 있는지 제안받고 싶어요** — 데이터는 있는데 주제가 미정일 때. *(→ S2)*
> - **③ 논문을 새로 쓰기 시작할래요** — 연구·분석이 어느 정도 진행된 상태에서 시작. *(→ S3)*
> - **④ 쓰던 논문을 수정하고 싶어요** — 기존 원고를 진단하고 개선. *(→ S4)*
> - **⑤ 리뷰어 대응부터 할래요** — 제출 후 리비전. 코멘트를 파싱해 대응 계획. *(→ S5)*
>
> *(진행 중인 연구가 있으면 하단에 재개 목록으로 노출 — S0)*

Map: ①→S1, ②→S2, ③→S3, ④→S4, ⑤→S5.

---

## S1 — Idea interview (3-way, clarity-adaptive)

Take the free text and judge clarity by **how many of P·E·O are identifiable**
(Population, Exposure, Outcome). Record the verdict in `entry_point.s1_clarity` as
`clear` / `rough` / `vague`. Then adapt depth:

### clear (P·E·O all 3 identified)

1. Structure into PICO (`templates/pico-template.json` schema).
2. Restate on one screen, **explicitly naming any uncertain field** (mark it
   `"confidence": "low"` and say so).
3. Confirm once: "이렇게 이해했는데 맞나요? 틀린 부분만 짚어주세요."
4. Write `.research/idea.json`, mark step 1 `completed`, `ARTIFACT_CREATED` (idea).
5. Hand off to Layer 2 (offer to bring materials), then continue toward Step 2.

### rough (1–2 identified) — narrowing question bank

Ask **2–4** questions, **1–2 at a time**, and **skip any element already identified**.
The **promotion rule** overrides everything: *the moment P·E·O are all filled, jump to
the clear path immediately — do not exhaust the bank.* (Rigidity is the failure mode.)

- **Q1 (exposure):** "무엇의 효과를 보고 싶으신가요 — 특정 약제인가요, 시술인가요, 아니면
  진료 패턴 같은 것인가요?"
- **Q2 (outcome):** "결과 지표 중 임상적으로 가장 중요한 것은 무엇인가요? (예: 사망, 재입원,
  합병증 발생)"
- **Q3 (comparator):** "비교 대상이 있나요 — 다른 치료를 받은 군, 혹은 치료받지 않은 군?"
- **Q4 (data access):** "이 분석에 필요한 데이터에 접근하실 수 있나요? 어떤 형태인가요
  (EMR 추출, 레지스트리, 공개 데이터 등)?"

### vague (0 identified) — 3 probes → candidate directions

1. Run 3 probes:
   - **P1 (interest):** "요즘 어떤 임상 영역에 관심이 있으세요?"
   - **P2 (data on hand):** "지금 손에 있는 데이터가 있나요? 있다면 어떤 것인가요?"
   - **P3 (recent reading):** "최근에 인상 깊게 읽은 논문이나 주제가 있나요?"
2. Offer **2–3 candidate directions**, each as a one-line PICO sketch + a one-line
   feasibility note.
3. On pick → join the **rough** path.
4. If the probes reveal "데이터는 있는데 주제가 없음", propose switching to **S2**: update
   `entry_point` and re-append `ENTRY_POINT` (see recording rule).

---

## S2 — Data-first discovery (stub, requirement-satisfying)

P0 hands off rather than implementing discovery inline.

1. Record `entry_point.id = "S2"`.
2. Tell the user, verbatim intent:
   > "데이터로 어떤 연구가 가능한지 폭넓게 발굴하려면 **`/retrospective-autoresearch`**
   > 스킬을 사용하세요. 데이터셋에서 10~20개의 가설 후보 포트폴리오를 자동으로 뽑아 줍니다.
   > 그 스킬에서 유망한 방향을 고르신 뒤 다시 오시면, 그 지점부터 이어서 설계해 드릴게요."
3. On return with a chosen hypothesis → join the **S1 clear path**.

---

## S3 — New manuscript (thin full implementation)

Almost no dedicated logic — S3 rides the common Layer 2 pipeline.

1. Record `entry_point.id = "S3"`.
2. Request materials (Layer 2). Scan → classify → reverse-fill.
3. The **arrival step is computed from materials** (typically Step 6 SAP … Step 11
   Manuscript), via reverse-fill + `can-enter`. See `references/material-intake.md`
   (role→step map) and the Intake Gate.
4. Confirm the starting point in the briefing → land in the workflow at `arrival_step`.

## S4 — Revise existing manuscript (partial)

1. Record `entry_point.id = "S4"`.
2. Intake the draft; register it as `manuscript_draft` → reverse-fill idea / protocol
   (Methods summary) / sap (statistical section) as `draft`; import `manuscript` and set
   step 11 `imported`.
3. Re-enter **Step 11** for revision.
4. *The full manuscript-diagnosis report is deferred to P1* — for P0, do the intake +
   re-entry and note that a deeper diagnosis is a paid-tier depth.

## S5 — Reviewer response (arrival mapping only)

1. Record `entry_point.id = "S5"`.
2. Intake the reviewer comments as a `reviewer_comments` material.
3. Create `revision/round-1/`, append a round entry to `steps.13.rounds`, set step 13
   `in_progress`.
4. Proceed with Step 13 (Revision Loop) per `references/workflow-steps.md`.

---

## S0 — Resume view (FR-E6)

Render from `state.json`. If `next_action` is present, use its `label`/`step`. If absent
(v1 file or never set), derive the first enterable step by trying
`state_tool can-enter --step N` in ascending order and use the first that returns exit 0
(or the lowest exit-2 step as the blocked target), labeled from the fixed table below.

**Verb-form label table (fixed, 13 entries):**

| Step | 라벨 |
|---|---|
| 1 | PICO 확정 |
| 2 | 문헌 검색 |
| 3 | 근거표 작성 |
| 4 | 변수 정의 |
| 5 | 프로토콜 작성 |
| 6 | SAP 작성 |
| 7 | 표·그림 틀 만들기 |
| 8 | 합성 드라이런 |
| 9 | 데이터 QC |
| 10 | 실 분석 실행 |
| 11 | 원고 작성 |
| 12 | 제출 패키지 정리 |
| 13 | 리뷰 대응 (round N) |

**Render layout:**

```
📋 {project_name}
   이어서: {label} (Step {n})
   완료 {x}/12 · 반입 {y}단계
   ⚠ {blocker 요약, 최대 3줄}
```

Then offer: `[이어서 진행] [상태 자세히] [다른 작업]`, and always also allow
"새 프로젝트 시작". `상태 자세히` → full dashboard (all step statuses + gate states,
summarized). `다른 작업` → surface the 5+1 cards for a new project.

`next_action` must be re-derived and saved at **every** state-change save point (step
completion, gate handling) — this is the silent 4th move of Step Transitions.

---

## entry_point recording & ENTRY_POINT audit

On selection (or S1 start), set `state.json.entry_point`:

```json
"entry_point": { "id": "S1", "label": "연구 아이디어를 이야기하고 싶어요",
                 "selected_at": "ISO8601", "s1_clarity": "rough",
                 "arrival_step": null, "notes": null }
```

- `s1_clarity` only for S1 (`clear`/`rough`/`vague`), else `null`.
- `arrival_step` filled at Intake Gate finalize (for S3/S4/S5), else set as the entry
  path completes.
- Audit: append `ENTRY_POINT` with `{id, label, s1_clarity, arrival_step}`.
- **A mid-flow switch (e.g. S1→S2) rewrites `entry_point` and appends a *new* `ENTRY_POINT`
  event** — never edit the prior audit line.

---

## Entry point → arrival mapping

| Entry | Arrival |
|---|---|
| S1 idea | Step 1–2 (interview result becomes the `idea.json` draft) |
| S2 discovery | `/retrospective-autoresearch` → on return, join S1 clear path → Step 1 |
| S3 new manuscript | material-driven, Step 6 (SAP) … Step 11 (Manuscript) |
| S4 revise | Step 11 re-entry (manuscript registered `imported`) |
| S5 reviewer | Step 13 Revision Loop (round 1) |
| S0 resume | saved `next_action` step |
