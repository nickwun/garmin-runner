# Weekly Daily Detail Table Design

## Goal

Add a complete seven-day activity table to the deterministic weekly Markdown report so the athlete can review each day's training type, composition, total distance, total duration, combined pace, and duration-weighted heart rate without opening every daily report.

## Scope

This change affects only weekly analysis and weekly Markdown rendering. It does not change activity synchronization, FIT parsing, single-activity classification, monthly reports, or coaching prompts.

## User Experience

The weekly report gains a `每日训练明细` section after `训练量` and before `强度结构`. For `--week` reports it contains Monday through Sunday in chronological order, including explicit rest-day rows. For custom `--since/--until` reports it contains one row for every calendar date in the inclusive requested range; custom ranges are not normalized to seven days.

The table columns are:

| Column | Meaning |
| --- | --- |
| 日期 | ISO date plus Chinese weekday label |
| 训练类型 | Dominant deterministic classification for the day |
| 训练组成 | All activities on that date summarized in chronological order |
| 总距离 | Sum of all activity distances for the date |
| 总时长 | Sum of all activity durations for the date |
| 综合配速 | Total duration divided by total distance |
| 加权心率 | Duration-weighted mean of available activity average-heart-rate values |

Each active day is followed by a compact line of relative links to its generated daily reports. Links stay outside the table to keep the table readable. A day with no activity displays `休息` and `-` values and has no links.

## Daily Grouping Rules

Activities are grouped by `activity_date` and sorted within the day by a new `start_time_local` value copied from the SQLite row and parsed as a local datetime. Activity id provides a deterministic tie-breaker when timestamps are equal or missing. A normal ISO-week range produces exactly seven daily summaries, even for an empty week; a custom range produces exactly `(week_end - week_start) + 1` summaries.

### Aggregate Metrics

- Distance is the sum of all activity distances.
- Duration is the sum of all activity durations.
- Combined pace is total duration divided by total distance. It is `N/A` when distance or duration is zero.
- Heart rate is weighted by activity duration, using only activities with a non-null average heart rate. It is `N/A` when every activity lacks heart rate.
- Individual distances and durations remain unchanged in the underlying `WeeklyActivity` objects.

### Dominant Training Type

The dominant type is selected by this priority. If multiple activities share the highest priority, the displayed type is the type of the longest-duration activity, with earlier start time and then activity id as deterministic tie-breakers:

1. 比赛
2. 长距离
3. 阈值间歇、间歇课、阈值课
4. 马配桥梁、稳态跑、中长有氧 / 稍稳有氧、轻松跑跑成稳态
5. MAF 跑、E 跑
6. 恢复跑
7. 热身/冷身

The displayed type adds `（含热身冷身）` when the day includes one or more auxiliary warm-up/cool-down activities alongside a dominant main activity.

### Training Composition

Composition expands any existing structured `workout_breakdown` into its non-zero, mutually exclusive `warmup`, `main`, and `cooldown` phases, then lists the remaining activities in chronological order using classification and distance. The overlapping `quality` phase is deliberately omitted from daily composition so its distance is not displayed twice. Add a small immutable `WeeklyWorkoutPhase` value object and preserve phase name, distance, and duration when `_weekly_activity_from_row` converts `SingleActivityAnalysis` into `WeeklyActivity`.

For separate short records classified as `热身/冷身`, all activities at the day's highest non-auxiliary priority form the main-activity span. Auxiliary records before the first main activity display as `热身`; records after the last main activity display as `冷身`; records between two main activities display as `辅助跑`. If the day contains only `热身/冷身` activities and therefore has no non-auxiliary main activity, every record keeps the `热身/冷身` label.

The resulting composition is, for example:

`热身 3.0 + 稳态跑 7.0 + 冷身 5.1 km`

Standalone activities use their classification directly, for example `E 跑 12.1 km`.

## Data Model

Add an immutable `DailyTrainingSummary` value object to `analysis/weekly.py` with:

- date
- dominant training type
- ordered activities
- total distance
- total duration
- combined pace
- duration-weighted heart rate
- rest-day flag

Extend `WeeklyActivity` with:

- `start_time_local: datetime | None`
- `workout_phases: tuple[WeeklyWorkoutPhase, ...]`

The CLI conversion reads `start_time_local` from the SQLite activity row and converts `analysis.workout_breakdown` into non-zero phase records. Existing test helpers may use defaults for both fields.

Add `daily_summaries` to `WeeklyAnalysis`. `analyze_week` builds this list from the already analyzed `WeeklyActivity` values. The reporting layer only formats the prepared values and does not perform aggregation or classification.

No SQLite schema change is required. Average pace is derived from duration and distance; average heart rate and report paths already exist on `WeeklyActivity`.

## Report Links

Daily report links use the fixed sibling-directory contract of the report writers: weekly reports are stored under `<reports_dir>/weekly/`, daily reports under `<reports_dir>/daily/`, so the rendered target is always `../daily/<report_path.name>`. The renderer never uses or exposes the absolute `report_path` parent. Link labels include the activity type and activity id so multiple records on the same date remain distinguishable.

The renderer must not expose summary JSON, FIT, database, token, cookie, or credential paths.

## Empty and Missing Data

- An empty ISO week renders seven rest rows. An empty custom range renders one rest row per requested calendar date.
- `rest_days` is calculated as inclusive period length minus distinct running days. This preserves the current seven-day result for ISO weeks and makes custom-range metrics consistent with their detail rows.
- Missing heart rate renders `N/A` without lowering report generation reliability.
- Zero distance or zero duration renders combined pace as `N/A`.
- Existing behavior for missing summary JSON or FIT remains unchanged: weekly generation fails with the current clear local-data error.

## Testing

Add focused tests for:

1. A single-activity day produces one daily summary with correct metrics.
2. Warm-up, main workout, and cool-down records merge into one day with the correct dominant type and composition.
3. Combined pace uses total duration divided by total distance.
4. Heart rate uses duration weighting and ignores null heart-rate values.
5. Missing heart rate renders `N/A`.
6. Rest days appear so the weekly table always has seven rows.
7. Empty weeks render seven rest rows.
8. Daily report links are relative, clickable Markdown links.
9. Structured composition omits the overlapping quality phase.
10. A custom three-day range renders three rows and calculates rest days from three, not seven.
11. Existing weekly volume, intensity, risk, and advice tests continue to pass unchanged.

## Verification

Run the full project checks required for a completed phase:

- `pytest`
- `python -m compileall src`
- `garmin-runner --help`
- `garmin-runner report weekly --help`
- Generate the current real-data weekly report and inspect the daily table.
- Confirm `reports/`, `data/`, credentials, FIT files, and SQLite remain ignored and untracked.

Only code, tests, README updates if needed, and this design/plan documentation may be committed. Local reports and personal training data must remain untracked.
