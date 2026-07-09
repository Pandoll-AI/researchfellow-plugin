"""checklist_map.py — design-aware reporting coverage screen (free guardrail).

Pins guideline selection (incl. the RECORD auto-pull and its false-positive fix)
and the anchored coverage judgement.
"""

from __future__ import annotations

import checklist_map as cm


def test_cohort_selects_strobe():
    assert cm.select_guidelines("cohort", "plain text", False) == ["strobe"]


def test_prediction_selects_tripod():
    assert cm.select_guidelines("prediction", "a prediction model", False) == ["tripod"]


def test_record_autopull_on_emr_wording():
    names = cm.select_guidelines("cohort", "cohort built from electronic health record data", False)
    assert names == ["strobe", "record"]


def test_record_not_pulled_by_the_word_claims():
    # Regression: "numeric claims" (assertions) must NOT pull in RECORD.
    names = cm.select_guidelines("cohort", "all numeric claims reference the output tables", False)
    assert names == ["strobe"]


def test_forced_routinely_collected_flag():
    assert cm.select_guidelines("cohort", "no data-source wording", True) == ["strobe", "record"]


def test_item_covered_by_anchor():
    report = cm.screen("## Methods\nAdjusted via multivariable Cox regression with IPTW.", ["strobe"])
    status = {i["id"]: i["status"] for g in report["guidelines"] for i in g["items"]}
    assert status["STROBE-12a"] == "covered"


def test_required_item_missing_is_reported():
    report = cm.screen("# Title only\n", ["strobe"])
    assert "STROBE-12a" in report["summary"]["required_missing"]
    assert report["summary"]["missing"] > 0


def test_section_present_but_no_anchor_is_unclear():
    report = cm.screen("## Methods\nWe did some things.", ["strobe"])
    status = {i["id"]: i["status"] for g in report["guidelines"] for i in g["items"]}
    # STROBE-12a section is Methods (present) but no stats anchor -> unclear.
    assert status["STROBE-12a"] == "unclear"


def test_anchor_comments_do_not_count_as_coverage():
    # A section with only a REPORTING anchor comment (no authored prose) must be
    # unclear/missing, not covered — else an unfilled template scores as reported.
    ms = "## Methods\n<!-- REPORTING: STROBE-12a adjusted multivariable regression propensity -->\n"
    report = cm.screen(ms, ["strobe"])
    status = {i["id"]: i["status"] for g in report["guidelines"] for i in g["items"]}
    assert status["STROBE-12a"] != "covered"


def test_blank_template_is_not_fully_covered(references_dir):
    template = (references_dir.parent / "templates" / "manuscript-template.md").read_text(encoding="utf-8")
    report = cm.screen(template, ["strobe"])
    # The scaffold has section headers but no authored content → many required gaps.
    assert report["summary"]["required_missing"], "blank template should have required gaps"


def test_cli_exit2_when_required_missing(run_script, tmp_path):
    ms = tmp_path / "m.md"
    ms.write_text("# Title\nempty manuscript", encoding="utf-8")
    out = tmp_path / "report.json"
    proc = run_script("checklist_map.py", "--design", "cohort", "--manuscript", str(ms), "--output", str(out))
    assert proc.returncode == 2
    assert "REQUIRED" in proc.stdout
