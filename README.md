# 전북 생활권 인사이트

전북 읍면동의 인구 구조, 사업체, 의료기관, 버스 정류장·노선 지표를 한 화면에서
살펴보고, 생활 점수와 추천 지역을 비교하는 Streamlit 대시보드입니다.

## 목표

- 읍면동별 생활 점수와 상대 등급(A~E) 계산
- 지도 기반 지역 비교
- 조건별 가중치 추천
- 점수 기여도와 핵심 지표 확인
- 원천 데이터와 누락 필드 상태 확인

## 핵심 원칙

이번 버전은 "완성된 예측 모델"이 아니라 **원천 데이터를 정규화해 설명 가능한
지역 비교를 제공하는 분석 도구**입니다.

- 데이터는 원천 SQLite DB(`data/processed/living_areas.sqlite`)에서 읽습니다.
- 분석 단위는 SGIS 2024년 경계 기준 전북 읍면동 242곳입니다(금암1·2동은 금암동으로 합침).
- 점수는 읍면동 간 백분위 순위(0~100)를 사용자 가중치로 합한 상대 지표입니다.
- 등급은 현재 지표와 3년 예측(미래 안정성)을 합한 상대 순위입니다.
- 3년 예측: 청년(20~39세) 인구는 LightGBM 패널 모델(해밀턴-페리 코호트 추계와 비교 검증), 사업체는 읍면동×업종별 감소 확률 모델입니다. 성능과 근거는 [예측 모델 보고서](docs/model_report.md)에 있습니다.
- 빈집 비율과 청년 순이동은 시군 단위 자료라 같은 시군의 읍면동에 같은 값이 들어갑니다.

## 빠른 실행

처음 설치·DB 구축·시연 절차는 [실행 매뉴얼](docs/실행_매뉴얼.md)에 단계별로 정리돼 있습니다.

    python -m venv --system-site-packages .venv
    .venv/bin/python -m pip install -r requirements.txt
    .venv/bin/python scripts/build_living_database.py   # 원천 DB (data/ 아래 원본 필요)
    .venv/bin/python scripts/train_models.py            # 1~3단계 예측 학습·검증 + 보고서 + 대시보드 패널 (약 1분)
    .venv/bin/python -m streamlit run app.py

`train_models.py` 한 번으로 청년 순이동 추정 → 사업체 감소 위험 → 청년 인구 예측 → 대시보드 패널 갱신이 차례로 실행되고,
결과는 `docs/model_report.md`, `models/model_report.json`, `data/processed/forecast_*.csv`에 저장됩니다.

서비스가 제공하는 기능과 판단 기준은 [서비스 명세](docs/서비스_명세.md)에 정리돼 있고, 작업할 때마다 함께 갱신합니다.
원천 데이터 수집, 인증키 입력, DB 구성은 [DB 구축 상태](docs/database.md)에 있습니다.
앱은 `data/processed/region_panel.csv`가 없거나 DB보다 오래되었으면 DB에서 패널을 다시 만듭니다.

## 폴더 구조

- data/raw, data/*.zip: 원천 API 응답·파일 (Git 제외)
- data/processed/living_areas.sqlite: 출처를 추적하는 원천 DB
- data/processed/region_panel.csv: 읍면동 최신 지표 (대시보드 입력)
- data/processed/town_year_panel.csv: 읍면동×연도 패널 (주민등록 인구 2008~2025, 현재 경계 등록센서스 인구 2015~2024, 사업체 2000~2024, 시군 빈집·순이동)
- scripts/: 원천 다운로드, DB 빌드, 패널 생성
- src/data_pipeline.py: DB에서 읍면동 단위와 패널 생성
- src/forecast/: 예측 모델 (data.py 입력, youth.py 청년 순이동·인구 예측, business.py 업종별 감소 위험)
- scripts/train_models.py: 예측 학습·백테스트·보고서 생성
- src/dashboard_model.py: 점수, 상대 등급, 3년 위험 단계, 추천 스냅샷 생성
- src/recommendation/explanation.py: 추천 이유 문장
- src/ui.py, src/visualization/map.py: Streamlit 화면과 지도
- app.py: Streamlit 서비스

지도 좌표는 분석값이 아니라 읍면동 경계 안의 대표점입니다.
