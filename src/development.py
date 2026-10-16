"""Load optional development results without weakening core data requirements."""
from pathlib import Path
import json
import math
import sqlite3
import pandas as pd
from src.limitations import medical_history


def load_development_results(data_dir: Path) -> dict:
    directory = Path(data_dir) / 'processed'
    access_path = directory / 'accessibility_30m.csv'
    analysis_path = directory / 'infrastructure_analysis.json'
    result = {'accessibility': pd.DataFrame(), 'analysis': None, 'warnings': []}
    for path, key, multiple in [(access_path, 'accessibility', False),
            (directory / 'accessibility_departures.csv', 'accessibility_samples', True)]:
        if not path.exists():
            continue
        try:
            frame = pd.read_csv(path, dtype={'region_code': str})
            validate_accessibility(frame, multiple)
            result[key] = frame
        except (ValueError, OSError) as exc:
            result['warnings'].append(f'30분 생활권 결과를 읽지 못했습니다: {exc}')
    metadata_path = directory / 'accessibility_metadata.json'
    if metadata_path.exists():
        try:
            result['accessibility_metadata'] = json.loads(metadata_path.read_text(encoding='utf-8'))
        except (ValueError, OSError) as exc:
            result['warnings'].append(f'생활권 출처 설명을 읽지 못했습니다: {exc}')
    if analysis_path.exists():
        try:
            analysis = json.loads(analysis_path.read_text(encoding='utf-8'))
            if not isinstance(analysis, dict) or not isinstance(analysis.get('coefficients'), list):
                raise ValueError('분석 결과 구조가 올바르지 않습니다')
            weights = analysis.get('suggested_weights', {})
            allowed = {'transport', 'medical', 'business', 'vacancy'}
            for coefficient in analysis['coefficients']:
                if not isinstance(coefficient, dict) or coefficient.get('component') not in allowed:
                    raise ValueError('분석 계수 항목이 올바르지 않습니다')
                for key in ('coefficient', 'p_value', 'ci_low', 'ci_high'):
                    value = coefficient.get(key)
                    if not isinstance(value, (float, int)) or not math.isfinite(value):
                        raise ValueError('분석 계수와 구간은 유한한 숫자여야 합니다')
            if not isinstance(weights, dict) or not set(weights) <= allowed:
                raise ValueError('알 수 없는 가중치 항목입니다')
            if any(not isinstance(v, (float, int)) or not 0 < v <= 1 for v in weights.values()):
                raise ValueError('제안 가중치는 0 초과 1 이하여야 합니다')
            result['analysis'] = analysis
        except (ValueError, OSError) as exc:
            result['warnings'].append(f'패널 분석 결과를 읽지 못했습니다: {exc}')
    try:
        result['medical_history'] = medical_history(data_dir)
    except (OSError, ValueError, sqlite3.DatabaseError) as exc:
        result['medical_history'] = pd.DataFrame()
        result['warnings'].append(f'의료시설 이력을 읽지 못했습니다: {exc}')
    return result


def validate_accessibility(frame: pd.DataFrame, multiple: bool = False) -> None:
    required = ['accessible_facilities_30m', 'accessible_medical_30m']
    if not {'region_code', 'travel_budget_minutes', *required} <= set(frame):
        raise ValueError('필수 열이 없습니다')
    scheduled = 'method' in frame and frame.method.eq('ktdb_schedule_walk').all()
    keys = ['region_code', 'departure_time'] if multiple else ['region_code']
    if not set(keys) <= set(frame) or frame.empty or frame[keys].isna().any(axis=None) or frame.duplicated(keys).any():
        raise ValueError('지역 코드·출발 시간이 비어 있거나 중복됩니다')
    if scheduled:
        if not {'status','accessible_pharmacy_30m','accessible_jobs_30m','departure_time'} <= set(frame):
            raise ValueError('시간표 결과의 필수 열이 없습니다')
        if not frame.status.isin(['computed','unmapped_origin','unconfirmed_center']).all():
            raise ValueError('알 수 없는 계산 상태입니다')
        numeric = required + ['accessible_pharmacy_30m']
        valid = frame.status.eq('computed')
        if frame.loc[~valid,numeric].notna().any(axis=None):
            raise ValueError('위치 미연결 지역을 0개로 표시할 수 없습니다')
        if frame.accessible_jobs_30m.notna().any():
            raise ValueError('일자리 데이터가 없는 분석입니다')
        values = frame.loc[valid,numeric].apply(pd.to_numeric, errors='raise')
        if not frame.loc[valid,'accessible_facilities_30m'].eq(
                frame.loc[valid,'accessible_medical_30m'] + frame.loc[valid,'accessible_pharmacy_30m']).all():
            raise ValueError('전체 시설 수와 병의원·약국 합계가 다릅니다')
    else:
        required += ['accessible_jobs_30m']
        if not {*required, 'wait_factor'} <= set(frame) or not frame.wait_factor.between(0,1).all():
            raise ValueError('대기시간 가정을 확인하세요')
        values = frame[required].apply(pd.to_numeric, errors='raise')
    if values.isna().any(axis=None) or (values < 0).any(axis=None) or (values % 1 != 0).any(axis=None):
        raise ValueError('시설 수는 0 이상의 정수여야 합니다')
    if not frame.travel_budget_minutes.eq(30).all():
        raise ValueError('30분 예산을 확인하세요')
