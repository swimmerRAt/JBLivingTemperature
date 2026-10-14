PRAGMA foreign_keys = ON;

CREATE TABLE catalog_item (
    workbook_row INTEGER PRIMARY KEY,
    category TEXT NOT NULL,
    planned_indicator TEXT NOT NULL,
    recommended_source TEXT NOT NULL,
    provider TEXT NOT NULL,
    acquisition_form TEXT,
    planned_spatial_unit TEXT,
    planned_time_unit TEXT,
    processing_note TEXT,
    primary_url TEXT,
    alternative_url TEXT,
    caution TEXT,
    acquisition_status TEXT NOT NULL CHECK (acquisition_status IN
        ('ready', 'partial', 'missing', 'requires_key', 'derived')),
    status_note TEXT NOT NULL
);

CREATE TABLE source_file (
    id INTEGER PRIMARY KEY,
    path TEXT NOT NULL UNIQUE,
    source_family TEXT NOT NULL,
    original_url TEXT,
    provider TEXT,
    period_label TEXT,
    byte_count INTEGER NOT NULL CHECK (byte_count > 0),
    sha256 TEXT NOT NULL CHECK (length(sha256) = 64)
);

CREATE TABLE catalog_source_file (
    workbook_row INTEGER NOT NULL REFERENCES catalog_item(workbook_row),
    source_file_id INTEGER NOT NULL REFERENCES source_file(id),
    PRIMARY KEY (workbook_row, source_file_id)
);

-- API responses are preserved here until their table-specific dimensions are
-- validated. They must not be used as normalized indicators implicitly.
CREATE TABLE api_record (
    source_file_id INTEGER NOT NULL REFERENCES source_file(id),
    record_index INTEGER NOT NULL,
    provider TEXT NOT NULL CHECK (provider IN ('KOSIS', 'TAGO')),
    dataset_name TEXT NOT NULL,
    period_label TEXT,
    area_name TEXT,
    raw_json TEXT NOT NULL,
    PRIMARY KEY (source_file_id, record_index)
);

CREATE TABLE historical_population_age (
    year INTEGER NOT NULL,
    admin_code TEXT NOT NULL,
    area_name TEXT NOT NULL,
    area_level TEXT NOT NULL CHECK (area_level IN ('province', 'city', 'district', 'town')),
    city_name TEXT,
    age_start INTEGER NOT NULL,
    age_end INTEGER,
    population INTEGER NOT NULL CHECK (population >= 0),
    source_file_id INTEGER NOT NULL REFERENCES source_file(id),
    PRIMARY KEY (year, admin_code, age_start)
);

CREATE TABLE migration_annual_count (
    year INTEGER NOT NULL,
    area_code TEXT NOT NULL,
    area_name TEXT NOT NULL,
    sex_code TEXT NOT NULL,
    age_code TEXT NOT NULL,
    age_start INTEGER,
    age_end INTEGER,
    movement_code TEXT NOT NULL CHECK (movement_code IN ('T10', 'T20', 'T25')),
    people INTEGER NOT NULL,
    source_file_id INTEGER NOT NULL REFERENCES source_file(id),
    PRIMARY KEY (year, area_code, sex_code, age_code, movement_code)
);

CREATE TABLE migration_annual_rate (
    year INTEGER NOT NULL,
    area_code TEXT NOT NULL,
    area_name TEXT NOT NULL,
    sex_code TEXT NOT NULL,
    age_code TEXT NOT NULL,
    age_start INTEGER,
    age_end INTEGER,
    movement_code TEXT NOT NULL CHECK (movement_code IN ('T10', 'T20', 'T30')),
    rate_pct REAL,
    source_file_id INTEGER NOT NULL REFERENCES source_file(id),
    PRIMARY KEY (year, area_code, sex_code, age_code, movement_code)
);

CREATE TABLE business_2020 (
    area_code TEXT NOT NULL,
    area_name TEXT NOT NULL,
    area_level TEXT NOT NULL CHECK (area_level IN ('province', 'city', 'district', 'town')),
    city_name TEXT,
    industry_code TEXT NOT NULL,
    industry_name TEXT NOT NULL,
    establishment_count INTEGER CHECK (establishment_count >= 0),
    employee_count INTEGER CHECK (employee_count >= 0),
    source_file_id INTEGER NOT NULL REFERENCES source_file(id),
    PRIMARY KEY (area_code, industry_code)
);

CREATE TABLE tago_stop (
    city_name TEXT NOT NULL,
    stop_id TEXT NOT NULL,
    stop_name TEXT NOT NULL,
    latitude REAL NOT NULL CHECK (latitude BETWEEN 33 AND 39),
    longitude REAL NOT NULL CHECK (longitude BETWEEN 124 AND 132),
    retrieved_at_utc TEXT NOT NULL,
    source_file_id INTEGER NOT NULL REFERENCES source_file(id),
    PRIMARY KEY (city_name, stop_id)
);

CREATE TABLE tago_route (
    city_name TEXT NOT NULL,
    route_id TEXT NOT NULL,
    route_number TEXT,
    route_type TEXT,
    start_stop_name TEXT,
    end_stop_name TEXT,
    retrieved_at_utc TEXT NOT NULL,
    source_file_id INTEGER NOT NULL REFERENCES source_file(id),
    PRIMARY KEY (city_name, route_id)
);

CREATE TABLE tago_route_stop (
    source_file_id INTEGER NOT NULL REFERENCES source_file(id),
    record_index INTEGER NOT NULL,
    city_name TEXT NOT NULL,
    route_id TEXT NOT NULL,
    stop_id TEXT,
    stop_name TEXT,
    stop_order INTEGER,
    updown_code TEXT,
    latitude REAL,
    longitude REAL,
    PRIMARY KEY (source_file_id, record_index),
    FOREIGN KEY (city_name, route_id) REFERENCES tago_route(city_name, route_id)
);

-- MOIS administrative codes changed when Jeonbuk became a special province.
-- The year is part of the key; old and new codes are never silently joined.
CREATE TABLE area_year (
    year INTEGER NOT NULL,
    admin_code TEXT NOT NULL,
    name TEXT NOT NULL,
    full_name TEXT NOT NULL,
    level TEXT NOT NULL CHECK (level IN ('province', 'city', 'district', 'town')),
    city_name TEXT,
    population_total INTEGER NOT NULL CHECK (population_total >= 0),
    source_file_id INTEGER NOT NULL REFERENCES source_file(id),
    PRIMARY KEY (year, admin_code)
);

CREATE TABLE population_age (
    year INTEGER NOT NULL,
    admin_code TEXT NOT NULL,
    age_start INTEGER NOT NULL,
    age_end INTEGER,
    population INTEGER NOT NULL CHECK (population >= 0),
    source_file_id INTEGER NOT NULL REFERENCES source_file(id),
    PRIMARY KEY (year, admin_code, age_start),
    FOREIGN KEY (year, admin_code) REFERENCES area_year(year, admin_code)
);

-- Women by five-year band, for 지방소멸위험지수 (women 20-39 / everyone 65+).
CREATE TABLE population_age_female (
    year INTEGER NOT NULL,
    admin_code TEXT NOT NULL,
    age_start INTEGER NOT NULL,
    age_end INTEGER,
    population INTEGER NOT NULL CHECK (population >= 0),
    source_file_id INTEGER NOT NULL REFERENCES source_file(id),
    PRIMARY KEY (year, admin_code, age_start),
    FOREIGN KEY (year, admin_code) REFERENCES area_year(year, admin_code)
);

CREATE TABLE vacant_housing (
    year INTEGER NOT NULL,
    city_name TEXT NOT NULL,
    vacant_count INTEGER,
    housing_count INTEGER,
    vacancy_pct REAL,
    source_file_id INTEGER NOT NULL REFERENCES source_file(id),
    PRIMARY KEY (year, city_name),
    CHECK (vacant_count IS NULL OR vacant_count >= 0),
    CHECK (housing_count IS NULL OR housing_count >= 0),
    CHECK (vacancy_pct IS NULL OR vacancy_pct BETWEEN 0 AND 100)
);

CREATE TABLE housing_type_area_annual (
    year INTEGER NOT NULL,
    area_code TEXT NOT NULL,
    area_name TEXT NOT NULL,
    area_level TEXT NOT NULL CHECK (area_level IN ('province', 'city', 'district', 'town')),
    city_name TEXT,
    housing_type_code TEXT NOT NULL,
    housing_type_name TEXT NOT NULL,
    housing_count INTEGER CHECK (housing_count >= 0),
    source_file_id INTEGER NOT NULL REFERENCES source_file(id),
    PRIMARY KEY (year, area_code, housing_type_code)
);

CREATE TABLE province_housing_observation (
    year INTEGER NOT NULL,
    housing_type TEXT NOT NULL,
    housing_count INTEGER NOT NULL CHECK (housing_count >= 0),
    source_file_id INTEGER NOT NULL REFERENCES source_file(id),
    PRIMARY KEY (year, housing_type)
);

CREATE TABLE province_medical_summary (
    period_label TEXT NOT NULL,
    facility_group TEXT NOT NULL,
    facility_type TEXT NOT NULL,
    facility_count INTEGER NOT NULL CHECK (facility_count >= 0),
    source_file_id INTEGER NOT NULL REFERENCES source_file(id),
    PRIMARY KEY (period_label, facility_group, facility_type)
);

CREATE TABLE medical_city_quarterly (
    period_code TEXT NOT NULL CHECK (length(period_code) = 6),
    area_code TEXT NOT NULL,
    area_name TEXT NOT NULL,
    facility_type_code TEXT NOT NULL,
    facility_type_name TEXT NOT NULL,
    facility_count INTEGER CHECK (facility_count >= 0),
    source_file_id INTEGER NOT NULL REFERENCES source_file(id),
    PRIMARY KEY (period_code, area_code, facility_type_code)
);

CREATE TABLE migration_observation (
    source_family TEXT NOT NULL,
    period_label TEXT NOT NULL,
    period_kind TEXT NOT NULL CHECK (period_kind IN ('month', 'quarter')),
    area_name TEXT NOT NULL,
    age_group TEXT NOT NULL DEFAULT '',
    sex TEXT NOT NULL DEFAULT '',
    movement_type TEXT NOT NULL,
    people INTEGER NOT NULL,
    source_file_id INTEGER NOT NULL REFERENCES source_file(id),
    PRIMARY KEY (source_family, period_label, area_name, age_group, sex, movement_type)
);

CREATE TABLE province_transport_observation (
    source_family TEXT NOT NULL,
    year INTEGER NOT NULL,
    category TEXT NOT NULL,
    original_value REAL NOT NULL,
    source_file_id INTEGER NOT NULL REFERENCES source_file(id),
    PRIMARY KEY (source_family, year, category)
);

CREATE TABLE province_transport_api (
    year INTEGER NOT NULL,
    metric_group TEXT NOT NULL CHECK (metric_group IN ('access_mode', 'access_time', 'trip_frequency')),
    table_id TEXT NOT NULL,
    item_code TEXT NOT NULL,
    category TEXT NOT NULL,
    original_value REAL,
    unit TEXT,
    source_file_id INTEGER NOT NULL REFERENCES source_file(id),
    PRIMARY KEY (year, metric_group, item_code)
);

CREATE TABLE medical_facility_snapshot (
    snapshot_period TEXT NOT NULL,
    facility_kind TEXT NOT NULL CHECK (facility_kind IN ('hospital', 'pharmacy')),
    encrypted_facility_id TEXT NOT NULL,
    facility_name TEXT NOT NULL,
    facility_type TEXT,
    source_city_name TEXT,
    city_name TEXT NOT NULL,
    dong_name TEXT,
    address TEXT,
    longitude REAL,
    latitude REAL,
    source_file_id INTEGER NOT NULL REFERENCES source_file(id),
    PRIMARY KEY (snapshot_period, facility_kind, encrypted_facility_id)
);

CREATE TABLE bus_stop (
    stop_id TEXT PRIMARY KEY,
    stop_name TEXT NOT NULL,
    city_name TEXT NOT NULL,
    source_city_name TEXT,
    source_city_code TEXT,
    latitude REAL NOT NULL CHECK (latitude BETWEEN 33 AND 39),
    longitude REAL NOT NULL CHECK (longitude BETWEEN 124 AND 132),
    collected_on TEXT,
    source_file_id INTEGER NOT NULL REFERENCES source_file(id)
);

-- SGIS national establishment survey by eup/myeon/dong. Each year uses its own
-- KSIC revision: section letters before 2007 (KSIC 8) name different industries.
CREATE TABLE sgis_business_town (
    year INTEGER NOT NULL CHECK (year BETWEEN 2000 AND 2100),
    town_code TEXT NOT NULL CHECK (length(town_code) = 8),
    town_name TEXT NOT NULL,
    city_name TEXT NOT NULL,
    industry_code TEXT NOT NULL,
    ksic_basis TEXT NOT NULL CHECK (ksic_basis IN ('KSIC8', 'KSIC9+')),
    industry_name TEXT NOT NULL,
    establishment_count INTEGER NOT NULL CHECK (establishment_count >= 0),
    employee_count INTEGER CHECK (employee_count >= 0),
    source_file_id INTEGER NOT NULL REFERENCES source_file(id),
    PRIMARY KEY (year, town_code, industry_code)
);

-- SGIS register-based census population re-aggregated on current town
-- boundaries. It counts residents (foreigners included), so its levels differ
-- from resident registration; use it for boundary-consistent change.
CREATE TABLE sgis_population_town (
    year INTEGER NOT NULL CHECK (year BETWEEN 2000 AND 2100),
    town_code TEXT NOT NULL CHECK (length(town_code) = 8),
    town_name TEXT NOT NULL,
    city_name TEXT NOT NULL,
    age_group TEXT NOT NULL,
    age_start INTEGER,
    age_end INTEGER,
    population INTEGER CHECK (population >= 0),
    source_file_id INTEGER NOT NULL REFERENCES source_file(id),
    PRIMARY KEY (year, town_code, age_group)
);

-- TAGO files are keyed by the city code used to query them; Jeonju's feed also
-- serves Wanju. This table places each stop in the SGIS boundary it falls in.
CREATE TABLE tago_stop_admin (
    feed_city_name TEXT NOT NULL,
    stop_id TEXT NOT NULL,
    admin_city_name TEXT,
    town_code TEXT,
    town_name TEXT,
    boundary_year INTEGER NOT NULL,
    match_method TEXT NOT NULL CHECK (match_method IN ('within', 'nearest_100m', 'outside_jeonbuk')),
    boundary_source_file_id INTEGER NOT NULL REFERENCES source_file(id),
    PRIMARY KEY (feed_city_name, stop_id),
    FOREIGN KEY (feed_city_name, stop_id) REFERENCES tago_stop(city_name, stop_id)
);

-- HIRA coordinates placed in the same SGIS town boundary as the TAGO stops.
CREATE TABLE medical_facility_admin (
    snapshot_period TEXT NOT NULL,
    facility_kind TEXT NOT NULL,
    encrypted_facility_id TEXT NOT NULL,
    admin_city_name TEXT,
    town_code TEXT,
    town_name TEXT,
    boundary_year INTEGER NOT NULL,
    match_method TEXT NOT NULL CHECK (match_method IN ('within', 'nearest_100m', 'outside_jeonbuk')),
    boundary_source_file_id INTEGER NOT NULL REFERENCES source_file(id),
    PRIMARY KEY (snapshot_period, facility_kind, encrypted_facility_id),
    FOREIGN KEY (snapshot_period, facility_kind, encrypted_facility_id)
        REFERENCES medical_facility_snapshot(snapshot_period, facility_kind, encrypted_facility_id)
);

CREATE TABLE data_issue (
    issue_key TEXT PRIMARY KEY,
    severity TEXT NOT NULL CHECK (severity IN ('info', 'warning', 'blocker')),
    description TEXT NOT NULL
);

CREATE INDEX area_year_city ON area_year(city_name, year, level);
CREATE INDEX population_age_group ON population_age(year, age_start);
CREATE INDEX medical_facility_city_period ON medical_facility_snapshot(city_name, snapshot_period);
CREATE INDEX medical_city_quarterly_area ON medical_city_quarterly(area_name, period_code);
CREATE INDEX bus_stop_city ON bus_stop(city_name);
CREATE INDEX migration_period ON migration_observation(area_name, period_label);
CREATE INDEX province_transport_api_group ON province_transport_api(metric_group, year);
CREATE INDEX api_record_dataset ON api_record(provider, dataset_name, period_label);
CREATE INDEX historical_population_city ON historical_population_age(city_name, year, age_start);
CREATE INDEX migration_annual_area ON migration_annual_count(area_name, year, age_code);
CREATE INDEX migration_rate_area ON migration_annual_rate(area_name, year, age_code);
CREATE INDEX business_city ON business_2020(city_name, industry_code);
CREATE INDEX housing_area_city ON housing_type_area_annual(city_name, year, housing_type_code);
CREATE INDEX tago_route_stop_lookup ON tago_route_stop(city_name, route_id, stop_order);
CREATE INDEX sgis_business_city ON sgis_business_town(city_name, year, industry_code);
CREATE INDEX tago_stop_admin_city ON tago_stop_admin(admin_city_name, town_code);
CREATE INDEX medical_facility_admin_town ON medical_facility_admin(snapshot_period, town_code);

CREATE VIEW city_year_population AS
SELECT year, admin_code, name AS city_name, population_total, source_file_id
FROM area_year WHERE level = 'city';

CREATE VIEW city_population_series AS
SELECT year, admin_code, area_name AS city_name, population AS population_total,
       source_file_id, 'KOSIS' AS source_name
FROM historical_population_age WHERE area_level = 'city' AND age_start = -1
UNION ALL
SELECT year, admin_code, city_name, population_total, source_file_id, 'MOIS'
FROM city_year_population;

CREATE VIEW population_age_series AS
SELECT year, admin_code, area_name, city_name, age_start, age_end,
       population, source_file_id, 'KOSIS' AS source_name
FROM historical_population_age WHERE age_start >= 0
UNION ALL
SELECT p.year, p.admin_code, a.name, a.city_name, p.age_start, p.age_end,
       p.population, p.source_file_id, 'MOIS'
FROM population_age p JOIN area_year a
  ON a.year = p.year AND a.admin_code = p.admin_code;

CREATE VIEW city_age20_39_net_migration AS
SELECT year, area_code, area_name AS city_name, SUM(people) AS net_migration,
       MIN(source_file_id) AS source_file_id
FROM migration_annual_count
WHERE sex_code = '0' AND age_code IN ('120', '130', '150', '160') AND movement_code = 'T25'
  AND length(area_code) = 5
GROUP BY year, area_code, area_name;

CREATE VIEW medical_city_snapshot_counts AS
SELECT snapshot_period, city_name, facility_kind, COUNT(*) AS facility_count
FROM medical_facility_snapshot
GROUP BY snapshot_period, city_name, facility_kind;

CREATE VIEW medical_city_quarterly_totals AS
SELECT period_code,
       CASE WHEN area_name LIKE '전주시 %구' THEN '전주시' ELSE area_name END AS city_name,
       SUM(facility_count) AS facility_count
FROM medical_city_quarterly
WHERE facility_type_code = 'A001' AND area_name <> '전북'
GROUP BY period_code, city_name;

CREATE VIEW bus_stop_city_counts AS
SELECT city_name, COUNT(*) AS observed_stop_count
FROM bus_stop GROUP BY city_name;

CREATE VIEW tago_stop_city_counts AS
SELECT city_name, COUNT(*) AS observed_stop_count
FROM tago_stop GROUP BY city_name;

-- A stop listed in two city feeds is counted once per administrative city.
CREATE VIEW tago_stop_admin_city_counts AS
SELECT admin_city_name AS city_name, COUNT(DISTINCT stop_id) AS observed_stop_count
FROM tago_stop_admin WHERE admin_city_name IS NOT NULL
GROUP BY admin_city_name;

CREATE VIEW sgis_business_city_annual AS
SELECT year, city_name, industry_code, ksic_basis, industry_name,
       SUM(establishment_count) AS establishment_count,
       SUM(employee_count) AS employee_count_known,
       SUM(employee_count IS NULL) AS towns_with_suppressed_employees
FROM sgis_business_town
GROUP BY year, city_name, industry_code;
