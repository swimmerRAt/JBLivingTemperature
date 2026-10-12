# 전북 생활권 인사이트

전북 지역의 인구 구조, 주거 공실, 의료기관, 대중교통 지표를 한 화면에서
살펴보고, 생활권 점수와 추천 지역을 비교하는 Streamlit 대시보드입니다.

## 목표

- 지역별 생활 온도와 위험 등급 계산
- 지도 기반 지역 비교
- 조건별 가중치 추천
- 점수 기여도와 핵심 지표 확인
- 원천 데이터와 누락 필드 상태 확인

## 핵심 원칙

이번 버전은 "완성된 예측 모델"이 아니라 **원천 데이터를 정규화해 설명 가능한
지역 비교를 제공하는 분석 도구**를 목표로 합니다.

- 데이터는 저장소의 KOSIS CSV와 의료기관 ZIP을 사용
- 점수 계산은 0-100 정규화와 사용자 가중치 기반으로 동작
- 의료기관 수는 2026년 6월 의료기관 파일 기준
- 현재 시군구 순이동 원천 데이터는 다음 연결 대상이며 화면에 누락 상태를 표시
- 이후 ML 모델로 교체할 수 있도록 data pipeline과 dashboard model을 분리

## 빠른 실행

    python -m pip install -r requirements.txt
    python scripts/build_dataset.py
    python scripts/calculate_scores.py
    streamlit run app.py

엑셀 출처 목록에 맞춘 별도 SQLite 원천 DB는 `python scripts/build_living_database.py`로 생성합니다. 확보 범위, 인증키 입력 및 후속 수집 절차는 [DB 구축 상태](docs/database.md)에 있습니다.

앱은 data/processed/region_panel.csv가 없으면 원천 파일을 읽어 처음 한 번
지역 패널을 만들고, data_quality.json에 연결된 자료와 누락 경고를 저장합니다.

## 폴더 구조

- data/*.csv, data/*.zip: 원천 지역·인구·주거·의료·교통 데이터
- data/processed/region_panel.csv: 정규화한 지역 패널
- data/processed/region_latest.csv: 점수와 위험 등급을 포함한 대시보드 테이블
- src/data_pipeline.py: 원천 파일 탐색과 지역 패널 생성
- src/dashboard_model.py: 점수, 위험 등급, 추천 스냅샷 생성
- src/ui.py: Figma 기반 Streamlit 레이아웃과 화면 컴포넌트
- src/analysis: 분석 모듈
- src/recommendation: 추천 및 설명 모듈
- src/visualization: 지도와 차트 시각화
- app.py: Streamlit 서비스

실행 전 원천 데이터 파일이 충분하지 않으면 화면은 해당 지표를 "데이터 없음"으로
표시하고, 품질 경고를 하단에 보여줍니다. 지도 좌표는 분석값이 아니라 지도
표시를 위한 시군구 중심 좌표입니다.
