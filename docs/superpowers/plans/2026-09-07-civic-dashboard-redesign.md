# Civic dashboard redesign Implementation Plan

Goal: Replace the broken demo-data dashboard with a Figma-aligned Streamlit dashboard backed by the repository's real regional data.

Architecture: A pure data-pipeline module discovers and normalizes the checked-in CSV/ZIP sources, a scoring module creates an explainable dashboard snapshot, and app.py renders that snapshot through Figma-inspired Streamlit sections. Existing chart/map helpers remain reusable but receive the new normalized schema.

Tech Stack: Python 3, pandas, Streamlit, Plotly, Folium, pytest.

Spec: docs/superpowers/specs/2026-09-07-civic-dashboard-redesign-design.md

## Global Constraints

- Preserve the existing Python/Streamlit runtime.
- Do not fabricate region-level raw values when a source is unavailable.
- Keep score components normalized to 0-100 and expose their contribution.
- Use Korean, sentence-case UI labels and visible data-source metadata.
- Every new pure function has a focused test written before implementation.

### Task 1: Establish the normalized regional data contract

Files:
- Create tests/test_data_pipeline.py
- Create src/data_pipeline.py
- Modify scripts/build_dataset.py

Interfaces:
- discover_sources(data_dir: Path) -> dict[str, Path]
- build_region_panel(data_dir: Path) -> tuple[pd.DataFrame, dict[str, object]]
- load_dashboard_data(data_dir: Path) -> tuple[pd.DataFrame, dict[str, object]]

Steps:
- Write failing tests for source discovery, real-region output, and missing source metadata.
- Run pytest tests/test_data_pipeline.py -q and confirm the expected failures.
- Implement source discovery and parsers for population, population change, vacancy, and healthcare tables, with a documented fallback for county-level values.
- Run the focused tests and confirm they pass.
- Update scripts/build_dataset.py to emit data/processed/region_panel.csv and data/processed/data_quality.json.

### Task 2: Make scoring and recommendations match the dashboard

Files:
- Create tests/test_dashboard_model.py
- Create src/dashboard_model.py
- Modify src/recommendation/recommender.py
- Modify src/recommendation/explanation.py
- Modify scripts/calculate_scores.py

Interfaces:
- build_dashboard_snapshot(regions: pd.DataFrame, quality: dict, selected_region: str | None = None, weights: dict[str, float] | None = None) -> dict[str, object]
- score_regions(regions: pd.DataFrame, weights: dict[str, float]) -> pd.DataFrame
- risk_grade(score: float) -> str

Steps:
- Write failing tests for score bounds, risk grades, selected-region breakdown, and recommendation ordering.
- Run pytest tests/test_dashboard_model.py -q and confirm the expected failures.
- Implement normalized component scoring, weighted livability score, risk grade, trend summary, and explainable recommendations.
- Run the focused tests and confirm they pass.
- Make scripts/calculate_scores.py use the new model and write data/processed/region_latest.csv.

### Task 3: Rebuild the Streamlit page around Figma sections

Files:
- Create src/ui.py
- Modify app.py
- Modify src/visualization/map.py
- Modify src/visualization/charts.py

Interfaces:
- render_header(snapshot) -> None
- render_status_cards(snapshot) -> None
- render_sidebar(regions) -> dict
- render_dashboard(snapshot) -> None

Steps:
- Write failing tests for display labels and empty-data behavior in pure UI helpers.
- Run focused tests and confirm the expected failures.
- Implement the sidebar, status cards, risk map, risk score, prediction panel, recommendations, indicators, and footer sections with Streamlit containers and CSS.
- Run focused tests and a Python compile check.
- Run the app smoke path with installed dependencies, or report the exact missing optional dependency if the environment lacks them.

### Task 4: Verify the complete flow

Files:
- Modify README.md
- Modify docs/data.md

Steps:
- Run pytest -q.
- Run python3 -m compileall app.py src scripts.
- Run the dataset and scoring scripts against the checked-in data/ directory.
- Inspect git diff --check and confirm no generated data files or unrelated changes are included.
- Update README with the new build/run flow and data-quality caveat.
