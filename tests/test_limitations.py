import sqlite3
import pandas as pd
import pytest
from src.limitations import medical_history, medical_changes, weight_sensitivity, component_evidence
from src.dashboard_model import score_regions
from tests.test_dashboard_model import sample_regions


def test_medical_counts_distinguish_missing_mapping_from_zero(tmp_path):
    folder=tmp_path/'processed'
    folder.mkdir()
    with sqlite3.connect(folder/'living_areas.sqlite') as c:
        c.executescript('''
        CREATE TABLE medical_facility_snapshot(snapshot_period TEXT,facility_kind TEXT);
        CREATE TABLE medical_facility_admin(snapshot_period TEXT,facility_kind TEXT,town_code TEXT, admin_city_name TEXT, town_name TEXT);
        CREATE TABLE sgis_business_town(town_code TEXT,industry_code TEXT,year INTEGER,city_name TEXT,town_name TEXT);
        INSERT INTO sgis_business_town VALUES('001','ALL',2024,'전주시','금암동'),('002','ALL',2024,'전주시','여의동');
        INSERT INTO medical_facility_snapshot VALUES('2024.6','hospital'),('2025.6','hospital'),('2025.6','pharmacy');
        INSERT INTO medical_facility_admin VALUES('2024.6','hospital','old01','전주시','금암1동'),('2025.6','hospital','old02','전주시','여의동');
        ''')
    history=medical_history(tmp_path)
    changes=medical_changes(history,'001').set_index('kind')
    assert changes.loc['hospital','change']==-1
    assert changes.loc['hospital','change_pct']==-100
    assert pd.isna(changes.loc['pharmacy','current_count'])
    assert pd.isna(changes.loc['pharmacy','change'])
    other=medical_changes(history,'002').set_index('kind')
    assert other.loc['hospital','change']==1
    assert pd.isna(other.loc['hospital','change_pct'])


def test_medical_comparison_requires_at_least_a_year():
    frame=pd.DataFrame({'region_code':['1','1'],'facility_kind':['hospital']*2,
        'period':pd.to_datetime(['2025-12-01','2026-06-01']), 'snapshot_period':['2025.12','2026.6'],
        'count':[1,2],'mapping_coverage':[1,1]})
    assert pd.isna(medical_changes(frame,'1').iloc[0]['change'])


def test_weight_sensitivity_includes_current_and_treats_zero_as_unavailable():
    scored=score_regions(sample_regions())
    result=weight_sensitivity(scored)
    assert result.scenario_count.eq(13).all()
    assert result.best_rank.le(result.current_rank).all()
    assert result.worst_rank.ge(result.current_rank).all()
    assert weight_sensitivity(scored,dict.fromkeys(['transport','medical','business','vacancy','youth','stability'],0)).empty
    with pytest.raises(ValueError): weight_sensitivity(scored,fraction=1)


def test_evidence_exposes_neutral_missing_values_and_city_scope():
    evidence=component_evidence({}).set_index('평가 항목')
    assert evidence.loc['대중교통 접근성','입력 상태']=='모두 결측 → 중립 50점'
    assert evidence.loc['주거 안정성(시군)','자료 범위']=='시군'
    assert evidence.loc['미래 안정성(3년 예측)','자료 범위']=='읍면동 과거 추세'


def test_evidence_does_not_claim_trend_fallback_for_missing_row_in_forecast_dataset():
    evidence=component_evidence({}, forecast_connected=True).set_index('평가 항목')
    assert evidence.loc['미래 안정성(3년 예측)','자료 범위']=='읍면동 모델 예측'
    assert evidence.loc['미래 안정성(3년 예측)','입력 상태']=='모두 결측 → 중립 50점'
