import json
import numpy as np
import pandas as pd
import pytest

from src.accessibility import reachable_times, summarize_accessibility
from src.development import load_development_results
from src.panel_analysis import fit_infrastructure_panel


def network():
    return pd.DataFrame([
        ['home', 'a', 'walk', 5, '', None],
        ['a', 'b', 'bus', 5, 'r1', 10],
        ['b', 'c', 'bus', 5, 'r1', 10],
        ['b', 'd', 'bus', 5, 'r2', 20],
        ['c', 'clinic', 'walk', 5, '', None],
    ], columns=['from_node', 'to_node', 'mode', 'minutes', 'route_id', 'headway_minutes'])


def test_wait_once_per_boarding_and_directed_travel():
    times = reachable_times(network(), 'home')
    assert times['clinic'] == 25
    assert times['d'] == 30
    assert 'd' not in reachable_times(network(), 'home', 29)
    assert reachable_times(network(), 'clinic') == {'clinic': 0}
    assert reachable_times(network(), 'home', wait_factor=1)['c'] == 25


def test_missing_headway_never_becomes_zero_wait():
    edges = network()
    edges.loc[1, 'headway_minutes'] = None
    with pytest.raises(ValueError, match='headway'):
        reachable_times(edges, 'home')


def test_access_counts_distinct_facilities_and_rejects_disconnected_origin():
    origins = pd.DataFrame({'region_code': ['001'], 'node_id': ['home']})
    facilities = pd.DataFrame({'facility_id': ['h', 'h'], 'node_id': ['clinic', 'clinic'], 'category': ['medical']*2})
    result = summarize_accessibility(network(), origins, facilities)
    assert result.iloc[0].accessible_medical_30m == 1
    origins.loc[0, 'node_id'] = 'unknown'
    with pytest.raises(ValueError, match='exist'):
        summarize_accessibility(network(), origins, facilities)


def synthetic_panel():
    rng = np.random.default_rng(41)
    records = []
    for region in range(15):
        prior = 0
        for year in range(2010, 2020):
            value = float(rng.normal())
            records.append([str(region), year, region * 2 + year - 2010 + 3 * prior + rng.normal(0, .1), value])
            prior = value
    return pd.DataFrame(records, columns=['region_code', 'year', 'youth_net_migration_rate', 'transport_access'])


def test_panel_recovers_lagged_association_with_entity_and_year_effects():
    result = fit_infrastructure_panel(synthetic_panel())
    assert result['coefficients'][0]['coefficient'] > 2
    assert result['suggested_weights'] == {'transport': 1}
    assert result['observations'] == 135


def test_panel_does_not_lag_across_missing_years():
    panel = synthetic_panel()
    panel = panel[~panel.year.eq(2014)]
    assert fit_infrastructure_panel(panel)['observations'] == 105


def test_panel_rejects_copied_current_snapshots():
    panel = synthetic_panel()
    panel['transport_access'] = panel.region_code.astype(float)
    with pytest.raises(ValueError, match='absorbed'):
        fit_infrastructure_panel(panel)


def test_development_missing_and_invalid_results_are_visible(tmp_path):
    assert load_development_results(tmp_path)['analysis'] is None
    directory = tmp_path / 'processed'
    directory.mkdir()
    (directory / 'infrastructure_analysis.json').write_text(json.dumps({'coefficients': [], 'suggested_weights': {'invalid': 1}}))
    assert load_development_results(tmp_path)['warnings']


def test_tago_history_fetches_new_data_and_keeps_latest_files(tmp_path, monkeypatch):
    import sys
    from scripts import download_tago_api as collector
    monkeypatch.setattr(collector, 'ROOT', tmp_path)
    monkeypatch.setattr(collector, 'read_key', lambda: 'test-key')
    monkeypatch.setattr(collector.time, 'sleep', lambda _: None)
    monkeypatch.setattr(collector, 'fetch', lambda operation, key, **kwargs:
                        {'items': {'item': [{'cityname': '전주시', 'citycode': '1'}]}}
                        if operation == 'city_codes' else
                        {'items': {'item': {'routeid': 'r1', 'intervaltime': 20}}})
    monkeypatch.setattr(collector, 'fetch_pages', lambda operation, key, code, **kwargs:
                        ([{'routeid': 'r1'}], 1))
    current = tmp_path / 'data/raw/tago/stops_전주시.json'
    current.parent.mkdir(parents=True)
    current.write_text('existing snapshot')
    monkeypatch.setattr(sys, 'argv', ['collector', '--history', '--city', '전주시'])
    collector.main()
    collector.main()
    runs = list((current.parent / 'history').iterdir())
    assert len(runs) == 2
    assert current.read_text() == 'existing snapshot'
    for run in runs:
        manifest = json.loads((run / 'manifest.json').read_text())
        assert manifest['cities'] == ['전주시']
        info = list(run.glob('route_info_*.json'))
        assert len(info) == 1
        assert json.loads(info[0].read_text())['items'][0]['intervaltime'] == 20
        assert info[0].name in manifest['files']
