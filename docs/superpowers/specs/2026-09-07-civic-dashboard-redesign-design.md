# Civic dashboard redesign design

## Goal

Rebuild the Jeonbuk living-area Streamlit dashboard around the Figma Make
information architecture while replacing the missing demo-data dependency with
real data files already checked into data/.

## User flow

1. Select a Jeonbuk region in the sidebar.
2. See a concise status summary for population, infrastructure risk, and
   car-free livability.
3. Inspect the risk map and the selected region's score breakdown.
4. Review the model's forecast signal and the top alternative regions.
5. Open supporting indicators and source metadata before making a decision.

## Architecture

Keep Streamlit as the runtime and keep the analysis modules pure-Python/dataframe
oriented. Add a data-pipeline layer that normalizes the raw KOSIS and
healthcare CSV/ZIP inputs into one region-level dataframe, then expose a
dashboard snapshot object with the fields required by Header, Sidebar,
Indicators, RiskMap, RiskScore, PredictionPanel, Recommendations, and
DataFooter.

The scoring model will be explicit and explainable: normalize transport,
medical, housing, youth/population, and infrastructure signals to 0-100,
combine them with scenario weights, and derive a risk grade from the same
snapshot. Missing raw inputs are represented as NaN and surfaced as data
quality metadata rather than silently converted into fabricated values.

## Visual direction

- Base: deep navy shell with cool blue surfaces and a warm amber signal for
  attention; use restrained borders and compact radius.
- Type: system sans stack for Korean readability; large numeric scores carry
  the hierarchy.
- Layout: persistent narrow sidebar, wide dashboard canvas, two-column analysis
  grid, then recommendation and source sections.

    [sidebar] [header: selected area / refresh / quality]
              [risk] [population] [youth flow] [infrastructure]
              [risk map / comparison] [score drivers / forecast]
              [recommendations + indicators + sources]

## Data contract

The dashboard snapshot must provide:

- regions: one row per Jeonbuk city/county with scores and raw indicators.
- selected_region: the selected row and its score-driver contribution table.
- recommendations: ranked rows with score, grade, and reason text.
- trend: year/period values for population, youth share, vacancy, and
  healthcare access where available.
- quality: source names, latest periods, missing-field warnings, and row
  counts.

## Testing

Add tests for raw-file discovery, normalized region output, bounded score
calculation, recommendation ordering, and the empty/missing-data path. The
Streamlit layer is smoke-tested by importing its pure helper functions and by
running the app entrypoint only when optional UI dependencies are installed.
