# 전북 생활권 에이전트 MVP

이 프로젝트는 전북 읍면동 단위 생활권 점수와 추천 로직을 보여주는 데모입니다.

## 목표

- 전북 지역별 생활권 점수 계산
- 지도 기반 시각화
- 지역별 상세 분석
- 사용자 조건에 따른 추천
- 청년 유출과 인프라 안정성 요인 탐색

## 핵심 원칙

이번 MVP는 "완성된 예측 모델"이 아니라 **실제 데이터 파이프라인이 작동하는 데모**를 목표로 합니다.

- 데이터는 시나리오 기반의 데모 데이터로 구성
- 점수 계산은 정형 가중치 기반 heuristics 사용
- 미래 인프라 안정성은 baseline risk 점수로 표현
- 이후 단계에서 ML 모델로 교체 가능하도록 함수 인터페이스 유지

## 빠른 실행

```bash
python -m pip install -r requirements.txt
python scripts/build_dataset.py
python scripts/calculate_scores.py
streamlit run app.py
```

## 폴더 구조

- `data/demo/demo_regions.csv`: 데모 지역 데이터
- `data/processed/region_panel.csv`: 통합 지역 패널
- `data/processed/region_latest.csv`: 최종 분석 테이블
- `src/analysis`: 점수 계산 모듈
- `src/recommendation`: 추천 및 설명 모듈
- `src/visualization`: 지도/차트 시각화
- `app.py`: Streamlit 서비스

## 참고

현재 단계는 MVP 베이스라인으로, 실제 데이터가 확보되면 `predict_infrastructure_risk` 같은 인터페이스를 실제 모델로 교체하면 됩니다.
