# Weekly Daily Detail Table Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a deterministic daily activity table and daily-report links to weekly Markdown reports while correctly merging same-day warm-up, main, and cool-down records.

**Architecture:** Extend weekly analysis with chronological activity metadata, non-overlapping workout phases, and one prepared daily summary per requested date. Keep aggregation and dominant-type decisions in `analysis/weekly.py`; keep `reporting/weekly.py` limited to formatting prepared values and safe relative links. Preserve metadata at the CLI conversion boundary without changing SQLite.

**Tech Stack:** Python 3.11+, dataclasses, Typer, pytest, Markdown, the existing FIT and single-activity analysis pipeline.

---

## Chunk 1: Daily Aggregation Model

### Task 1: Define daily summaries and aggregate same-day activities

**Files:**
- Modify: `src/garmin_runner/analysis/weekly.py`
- Modify: `tests/test_weekly_report.py`

- [ ] **Step 1: Write a failing test for seven daily summaries**

Create `test_weekly_analysis_builds_seven_daily_summaries`. Build a seven-day context with one E run created using `average_hr=134` and assert:

```python
assert len(analysis.daily_summaries) == 7
summary = analysis.daily_summaries[1]
assert summary.activity_date == date(2026, 6, 16)
assert summary.training_type == "E 跑"
assert summary.total_distance_km == 12
assert summary.total_duration_s == 3600
assert summary.combined_pace_s_per_km == 300
assert summary.average_hr == 134
assert summary.is_rest_day is False
assert analysis.daily_summaries[0].is_rest_day is True
```

Extend the test helper to accept `average_hr: float | None = None` and `start_time_local: datetime | None = None`, and pass those values into `WeeklyActivity`.

- [ ] **Step 2: Verify RED**

Run:

```bash
.venv/bin/pytest tests/test_weekly_report.py::test_weekly_analysis_builds_seven_daily_summaries -q
```

Expected: FAIL because `WeeklyAnalysis.daily_summaries` does not exist.

- [ ] **Step 3: Add immutable model types**

Add `WeeklyWorkoutPhase`, `DailyCompositionItem`, and `DailyTrainingSummary` to `analysis/weekly.py`. `DailyTrainingSummary` contains date, dominant type, prepared composition items, ordered activities, total distance/duration, combined pace, duration-weighted heart rate, and rest flag.

Extend `WeeklyActivity` with trailing defaults:

```python
start_time_local: datetime | None = None
workout_phases: tuple[WeeklyWorkoutPhase, ...] = ()
```

Add `daily_summaries: list[DailyTrainingSummary]` to `WeeklyAnalysis`.

- [ ] **Step 4: Implement calendar-range aggregation**

Add `_daily_summaries(context)` and call it from `analyze_week`. It must:

- Generate every date from `week_start` through `week_end`, inclusive.
- Sort same-day activities with `(item.start_time_local is None, item.start_time_local or datetime.max, item.activity_id)`: timestamped activities first, then missing timestamps, with activity id as the final deterministic tie-breaker.
- Sum distance and duration.
- Compute pace as total duration divided by total distance.
- Weight heart rate by duration using only non-null values.
- Produce rest summaries for missing dates.
- Select dominant type using the approved priority table.

Replace the existing `rest_days = max(0, 7 - running_days)` line with:

```python
period_days = max(0, (context.week_end - context.week_start).days + 1)
rest_days = max(0, period_days - running_days)
```

- [ ] **Step 5: Verify GREEN**

Run the focused test again and expect PASS.

- [ ] **Step 6: Write failing tests for grouped composition and edge cases**

Add the following exact focused tests:

- `test_weekly_analysis_groups_same_day_warmup_main_and_cooldown`: same-day records merge, dominant type is `稳态跑（含热身冷身）`, composition order is `热身`, `稳态跑`, `冷身`, and pace uses total duration/total distance.
- `test_weekly_analysis_weights_heart_rate_and_ignores_missing_values`: duration weighting excludes a missing-HR activity from numerator and denominator.
- `test_weekly_analysis_returns_none_for_all_missing_heart_rate`: all-missing heart rate returns `None`.
- `test_weekly_analysis_custom_range_uses_inclusive_day_count`: a three-day context creates three summaries and asserts `analysis.rest_days == 2` with one running day.
- `test_weekly_analysis_keeps_labels_for_auxiliary_only_day`: an all-auxiliary day keeps `热身/冷身` labels.

- [ ] **Step 7: Verify RED**

Run:

```bash
.venv/bin/pytest tests/test_weekly_report.py::test_weekly_analysis_groups_same_day_warmup_main_and_cooldown tests/test_weekly_report.py::test_weekly_analysis_weights_heart_rate_and_ignores_missing_values tests/test_weekly_report.py::test_weekly_analysis_returns_none_for_all_missing_heart_rate tests/test_weekly_report.py::test_weekly_analysis_custom_range_uses_inclusive_day_count tests/test_weekly_report.py::test_weekly_analysis_keeps_labels_for_auxiliary_only_day -q
```

Expected: at least one FAIL because composition rules are incomplete.

- [ ] **Step 8: Implement dominant type and composition rules**

Add helpers for priority ranking, deterministic tie-breaking, main-activity span, positional relabeling of separate auxiliary records, and expansion of stored structured phases.

Composition must follow these exact rules:

- If an activity has `workout_phases`, use those phase names and distances as its composition items and do not also add an item for the activity's overall type/distance.
- If it has no stored phases, add one item using the activity type and activity distance.
- Stored phase labels are the existing deterministic phase names without remapping.
- External `热身/冷身` records are relabeled only by their position around the main span.
- Daily aggregate distance always sums original activity distances; composition is descriptive and must not feed aggregate totals.
- Never store or render the overlapping quality phase.

- [ ] **Step 9: Verify weekly analysis tests GREEN**

Run:

```bash
.venv/bin/pytest tests/test_weekly_report.py -q
```

Expected: all weekly tests PASS.

### Task 2: Preserve activity order and structured phases at the CLI boundary

**Files:**
- Modify: `src/garmin_runner/cli.py`
- Modify: `tests/test_weekly_report.py`

- [ ] **Step 1: Write a failing conversion-boundary test**

Test `_weekly_activity_from_row` with a row containing `start_time_local="2026-06-19T06:10:00"`, temporary summary/FIT placeholders, and monkeypatches for FIT decoding, analysis, and daily report writing. The mocked `SingleActivityAnalysis.basic.activity_date` remains a date and is not used as a timestamp; the SQLite row is the sole source for `WeeklyActivity.start_time_local`.

Construct a real `WorkoutBreakdown` mock with warm-up, main, cooldown, and overlapping quality `WorkoutPhase` values. Assert that `start_time_local == datetime(2026, 6, 19, 6, 10)` and that `workout_phases` contains warm-up, main, and cooldown but omits quality.

- [ ] **Step 2: Verify RED**

Run:

```bash
.venv/bin/pytest tests/test_weekly_report.py::test_weekly_activity_conversion_preserves_start_time_and_non_overlapping_phases -q
```

Expected: FAIL because conversion does not populate the new fields.

- [ ] **Step 3: Implement pure conversion helpers**

In `cli.py`:

- Parse only `activity["start_time_local"]` with `datetime.fromisoformat`; return `None` when the row value is missing or empty.
- Convert `analysis.workout_breakdown.warmup`, `.main`, and `.cooldown` into `WeeklyWorkoutPhase` only when both `distance_km > 0` and `duration_s > 0`; treat missing values as zero and omit that phase.
- Do not convert `.quality`.
- Pass both values to `WeeklyActivity`.

- [ ] **Step 4: Verify GREEN**

Run the focused conversion test again and expect PASS.

- [ ] **Step 5: Verify Chunk 1**

Run:

```bash
.venv/bin/pytest tests/test_weekly_report.py -q
.venv/bin/python -m compileall src
```

Expected: all weekly tests pass and compileall exits 0.

## Chunk 2: Markdown Rendering and Real-Data Validation

### Task 3: Render the daily table and safe report links

**Files:**
- Modify: `src/garmin_runner/reporting/weekly.py`
- Modify: `tests/test_weekly_report.py`

- [ ] **Step 1: Write failing renderer tests**

Add these exact tests:

- `test_weekly_report_renders_daily_detail_table_and_rest_rows`: render a normal ISO week containing a grouped steady day, a missing-heart-rate day, and rest days. Assert seven table date rows in chronological order, Chinese weekday labels from `周一` through `周日`, and rest rows containing `休息` plus `-` metric values.
- `test_weekly_report_renders_custom_range_dates`: render a three-day custom context and assert exactly those three chronological dates, not seven.
- `test_weekly_report_renders_relative_daily_links_without_private_paths`: assert safe relative links and absence of private paths.

The daily-detail test must also assert that Markdown contains:

```python
assert "## 每日训练明细" in content
assert "| 日期 | 训练类型 | 训练组成 | 总距离 | 总时长 | 综合配速 | 加权心率 |" in content
assert "稳态跑（含热身冷身）" in content
assert "热身 3.0 + 稳态跑 7.0 + 冷身 5.0 km" in content
assert "5:00 /km" in content
assert "N/A" in content
assert "[稳态跑 123](../daily/123.md)" in content
```

Also assert that no absolute temporary directory, FIT, summary, SQLite, token, or cookie path appears.

- [ ] **Step 2: Verify RED**

Run:

```bash
.venv/bin/pytest tests/test_weekly_report.py::test_weekly_report_renders_daily_detail_table_and_rest_rows tests/test_weekly_report.py::test_weekly_report_renders_custom_range_dates tests/test_weekly_report.py::test_weekly_report_renders_relative_daily_links_without_private_paths -q
```

Expected: FAIL because the section does not exist.

- [ ] **Step 3: Implement renderer helpers**

Add formatting for Chinese weekday labels, rest rows with `-` values, composition items, combined pace, optional heart rate, and links using only `../daily/{activity.report_path.name}`. Iterate `analysis.daily_summaries` as provided so ISO weeks render seven chronological rows and custom ranges render their inclusive dates. Insert the section after training volume and before intensity structure. Keep links in a compact list below the table.

- [ ] **Step 4: Verify GREEN**

Run the same three explicit renderer test node IDs again and expect PASS.

- [ ] **Step 5: Run full automated verification**

Run:

```bash
.venv/bin/pytest -q
.venv/bin/python -m compileall src
.venv/bin/garmin-runner --help
.venv/bin/garmin-runner report weekly --help
```

Expected: all tests pass; compileall and help commands exit 0.

### Task 4: Validate with private Garmin data and deliver

**Files:**
- Modify only if a real-data bug is found: the smallest relevant source/test file
- Local-only output: `reports/weekly/2026-W29.md`

- [ ] **Step 1: Generate the current real-data weekly report**

Run:

```bash
.venv/bin/garmin-runner report weekly --week current
```

Inspect seven dates, one merged row per training day, rest days, daily totals, and relative links. Confirm multi-record interval, MAF, and steady days are not double-counted.

- [ ] **Step 2: Fix real-data defects with TDD if needed**

For any defect, add a failing regression test first, implement the smallest correction, and rerun the full Task 3 verification.

- [ ] **Step 3: Perform sensitive-file checks**

Run:

```bash
git check-ignore .env config/athlete.yaml config/running_background.md data reports
git ls-files | rg '(\.env$|athlete\.yaml$|running_background\.md$|\.sqlite$|\.fit$|\.tcx$|\.gpx$|(^|/)(data|reports)/|token|cookie)' || true
git branch --show-current
git remote get-url origin
git status --short
```

Expected: all private paths are ignored, no sensitive file is tracked, branch is `main`, origin is `https://github.com/nickwun/garmin-runner.git`, and only intended source/test/plan files are modified.

- [ ] **Step 4: Review, commit, and push**

Run:

```bash
git diff --check
git diff -- src/garmin_runner/analysis/weekly.py src/garmin_runner/reporting/weekly.py src/garmin_runner/cli.py tests/test_weekly_report.py docs/superpowers/plans/2026-07-19-weekly-daily-detail-table-plan.md
git add src/garmin_runner/analysis/weekly.py src/garmin_runner/reporting/weekly.py src/garmin_runner/cli.py tests/test_weekly_report.py docs/superpowers/plans/2026-07-19-weekly-daily-detail-table-plan.md
git commit -m "feat: add daily training details to weekly reports"
git push origin main
git status --short
```

Expected: commit and push succeed; final `git status --short` is empty.
