# 전북 생활권 원천 데이터베이스

기준: 2026-10-12. 엑셀 `전북_생활권_에이전트_데이터_다운로드_출처.xlsx`의 5~29행을 기준으로 실제 원천의 기간·지역·항목을 확인했다. SQLite 파일은 `data/processed/living_areas.sqlite`, 스키마는 `db/schema.sql`, 빌더는 `scripts/build_living_database.py`다. `.env`와 `data/`는 Git에서 제외한다.

## 확보한 자료

| 자료 | 범위 | 주요 테이블·뷰 | 해석 시 주의 |
| --- | --- | --- | --- |
| 주민등록 인구 | KOSIS 2001~2007, 행안부 2008~2025, 전북 시군·읍면동과 5세 연령대 | `historical_population_age`, `area_year`, `population_age`, `city_population_series`, `population_age_series` | 행정구역 코드가 연도별로 달라질 수 있다. 시군 시계열 뷰는 14개 시군×25년이다. |
| 전입·전출·순이동 | KOSIS 2001~2025, 전북 시군, 연간 성별·5세 연령대 | `migration_annual_count`, `city_age20_39_net_migration` | `20~39세` 뷰는 네 5세 계층의 순이동 **인원**을 합산한다. `청년층(19~34세)` 등 별도 공표 계층도 원본에 있다. |
| 이동률 | KOSIS 2001~2025, 시군·연령대 | `migration_annual_rate` | 이동률(%)은 이동자 수(명)와 구분하고, 서로 다른 연령대의 비율을 더하지 않는다. |
| 사업체·종사자 | 2020 경제총조사, 전북 읍면동·산업대분류 | `business_2020` | `X`와 `-`는 결측으로 저장했다. 한 해만 있어 연간 증감률은 만들 수 없다. |
| 빈집 | 2015~2025, 전북 14개 시군 | `vacant_housing` | 기존 2021~2025 CSV와 새 API 응답의 중복 값은 빌드 때 일치 여부를 확인한다. |
| 주택 유형 | 2015~2025 | `housing_type_area_annual`, `province_housing_observation` | 읍면동은 2015·2020·2025년에만 제공된다. 그 외 연도는 시군 단위다. |
| 병의원·약국 | HIRA 개별시설 공개 ZIP, 2023.12·2024.12 및 2025.3~2026.6 분기 | `medical_facility_snapshot`, `medical_city_snapshot_counts` | 공개 스냅샷을 월별 연속 이력으로 해석할 수 없다. |
| 의료기관 집계 | KOSIS HIRA 분기 표, 전북 시군구·기관종별 2009~2026; 기존 시도 CSV도 보존 | `medical_city_quarterly`, `medical_city_quarterly_totals`, `province_medical_summary` | 전주시는 완산구·덕진구를 합산한다. HIRA 시설 ZIP과 집계 기준이 다르므로 직접 대조하지 않는다. |
| 버스 정류장·노선 | TAGO 현재 스냅샷, 전북 13개 시군 | `tago_stop`, `tago_stop_city_counts`, `tago_route`, `tago_route_stop` | TAGO 도시코드 목록에 **완주군이 없다**. 노선별 경유 정류소는 별도 다수 호출이 필요하다. 기존 전국 CSV는 `bus_stop`으로 보존한다. |
| 대중교통 접근·이용 | KOSIS API, 전북 시도 2014~2024; 기존 2020~2024 CSV도 보존 | `province_transport_api`, `province_transport_observation` | 시군별 비교 자료가 아니다. 2018년 전후 통계표와 일부 항목 구간이 달라진다. |

모든 원본 파일은 `source_file`에서 경로·출처·SHA-256으로 추적하고, 엑셀 항목과의 연결은 `catalog_source_file`에 저장한다. API 원문은 `api_record`에도 보존한다. `data_issue`에는 남은 품질·범위 문제가 기록된다.

## 원본 링크 검증 결과

- 엑셀의 인구 표 `211/DT_B31`의 지역 차원은 강원 지역이었다. 2001~2007년 전북 인구에는 KOSIS `101/DT_1B04005`를 사용했다. 2008년 이후는 이미 확보한 행안부 연말 파일을 사용한다.
- 엑셀의 `101/DT_1B26006`은 **이동률** 표다. 연령별 이동 **인원**은 `101/DT_1B26001`, 성별 이동 인원은 `101/DT_1B26001_A02`에서 받았다.
- 엑셀의 `101/DT_2KI2011`은 2020년 경제총조사 한 해뿐이다. [KOSIS 공식 답변](https://kosis.kr/civilComplaint/qnaDetail.do?boardIdx=17353)은 전국사업체조사 읍면동별 연도 자료를 KOSIS **온라인간행물**에서 제공한다고 안내한다. 공식 검색 화면에서 [2024년 XLS](https://kosis.kr/upsHtml/online/downSrvcFile.do?PUBCODE=ZY&FILE_NAME=/ZY/0224.xlsx&SEQ=539), [2023년 XLS](https://kosis.kr/upsHtml/online/downSrvcFile.do?PUBCODE=ZY&FILE_NAME=/ZY/0223.xlsx&SEQ=496), [2022년 XLS](https://kosis.kr/upsHtml/online/downSrvcFile.do?PUBCODE=ZY&FILE_NAME=/ZY/0222.xlsx&SEQ=475), [2021년 XLS](https://kosis.kr/upsHtml/online/downSrvcFile.do?PUBCODE=ZY&FILE_NAME=/ZY/0221.xlsx&SEQ=471) 링크를 확인했다. 파일 자체는 아직 내려받지 못했고 형식·내용도 확인 전이다.
- 엑셀의 대중교통 보조 링크 `116/DT_MLTM_5725`는 현재 조회한 항목이 접근수단·시간이 아니라 **교통비 구간**이다. 접근수단은 `DT_MLTM_5720`(2014~2017)과 `DT_MLTM_6196`(2018~2024), 접근시간은 `DT_MLTM_5721`, 주간 이용횟수는 `DT_MLTM_5719`(2014~2017)와 `DT_MLTM_7149`(2018~2024)를 사용했다. 이용횟수 첫 구간은 이전 표의 `1~5회`와 이후 표의 `4~5회`로 달라 직접 연결하면 안 된다.

## 다시 수집하고 빌드하기

루트 `.env`에 `KOSIS_API_KEY`와 `DATA_GO_KR_API_KEY`를 넣는다. 키 값은 출력하거나 Git에 넣지 않는다. 다운로드 스크립트는 이미 받은 JSON을 다시 요청하지 않으며, 필요하면 `--force`로 갱신한다.

```bash
.venv/bin/python scripts/download_kosis_api.py --table registered_population --start 2001 --end 2007
.venv/bin/python scripts/download_kosis_api.py --table migration_age --start 2001 --end 2025
.venv/bin/python scripts/download_kosis_api.py --table migration_sex --start 2001 --end 2025
.venv/bin/python scripts/download_kosis_api.py --table migration_rate --start 2001 --end 2025
.venv/bin/python scripts/download_kosis_api.py --table business_town --start 2020 --end 2020
.venv/bin/python scripts/download_kosis_api.py --table vacant_housing --start 2015 --end 2025
.venv/bin/python scripts/download_kosis_api.py --table housing_types --start 2015 --end 2025
.venv/bin/python scripts/download_hira_quarterly.py
.venv/bin/python scripts/download_transport_api.py
.venv/bin/python scripts/download_tago_api.py --dataset stops
.venv/bin/python scripts/download_tago_api.py --dataset routes
.venv/bin/python scripts/download_tago_api.py --dataset route_stops
.venv/bin/python scripts/build_living_database.py
```

버스 도착·차량 위치 API는 실시간 자료다. 과거 운행 이력을 소급할 수 없으므로, 호출할 정류소·노선과 주기를 결정하고 그 시점부터 별도로 적재해야 한다. TAGO의 도시코드 목록에 없는 완주군은 지자체 원본 등 다른 공급원이 필요하다.

## 남은 원천 범위

1. **읍면동 사업체 연속연도**: KOSIS 온라인간행물의 전국사업체조사 파일을 연도별로 확보하고, 2020 경제총조사와 산업분류·조사기준이 비교 가능한지 확인해야 한다. 현재 `business_2020`만으로 사업체 증감률을 만들지 않는다.
2. **완주군 정류장·노선**: TAGO 도시코드 목록에 없으므로 완주군 또는 전주·완주 교통정보시스템의 별도 공개 원본이 필요하다. 13개 시군 자료로 14개 시군 순위를 만들지 않는다.
3. **실시간 운행 이력**: 버스 도착·위치 API는 현재 상태만 응답한다. 저장 주기와 대상 노선·정류장을 정한 뒤 새 이력을 축적해야 한다.
4. **기간·해상도 차이**: 의료시설 개별 목록은 월별 연속 공개본이 아니다. 대중교통 접근·이용 지표는 2014~2024년을 확보했지만 전북 시도 단위이며, 시기별 항목 정의가 일부 다르다.

출처: [KOSIS OpenAPI](https://kosis.kr/openapi/devGuide/devGuide_0201List.do), [행안부 주민등록 인구](https://jumin.mois.go.kr/ageStatMonth.do), [TAGO 정류소 API](https://www.data.go.kr/data/15098534/openapi.do), [TAGO 노선 API](https://www.data.go.kr/data/15098529/openapi.do), [HIRA 공개 목록](https://opendata.hira.or.kr/op/opc/selectOpenData.do?sno=11925).
