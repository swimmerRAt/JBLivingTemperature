import geopandas as gpd
from shapely.geometry import box
from scripts.build_admin_center_origins import match_centers, office_town


def units():
    return gpd.GeoDataFrame({'region_code':['1'],'city':['테스트시'],'town':['가동']},geometry=[box(127,35,128,36)],crs=4326)


def candidate(lon=127.5, name='가동 행정복지센터'):
    return {'center_name':name,'lon':lon,'lat':35.5,'source_url':'https://www.openstreetmap.org/node/1','location_method':'mapped_point'}


def test_names_and_boundary_required_without_centroid_fallback():
    assert office_town('용진읍 행정복지센터')=='용진읍'
    for candidates in [[],[candidate(129)],[candidate(name='나동 주민센터')]]:
        result,_=match_centers(units(),candidates)
        assert result.iloc[0].origin_status=='center_unconfirmed'
        assert result.lat.isna().all()
    result,_=match_centers(units(),[candidate()])
    assert result.iloc[0].lon==127.5


def test_distinct_centers_are_not_arbitrarily_selected():
    result,audit=match_centers(units(),[candidate(),candidate(127.6)])
    assert result.lat.isna().all()
    assert len(audit)==1
