import numpy as np
import pytest
from scipy.sparse import csr_matrix
from src.scheduled_accessibility import gtfs_seconds, scan_arrivals, CONNECTION_DTYPE
from scripts.prepare_accessibility_sources import walk_directions


def graph():
    # Home->stop A 60 sec, stop B->clinic 60 sec; disconnected walking between stops.
    return csr_matrix(([60.,60.,60.,60.],([0,1,2,3],[1,0,3,2])),shape=(4,4))


def test_timetable_wait_and_boarding_margin():
    c=np.array([(8*3600+120,8*3600+600,1,2,0,0,0)],dtype=CONNECTION_DTYPE)
    labels=scan_arrivals(graph(),c,0,8*3600)
    assert labels[3]-8*3600==660
    # Arriving too late for boarding must not use the departed bus.
    assert not np.isfinite(scan_arrivals(graph(),c,0,8*3600+60)[3])


def test_stay_on_vehicle_through_unmapped_stop_without_second_wait():
    walk=csr_matrix((4,4))
    c=np.array([(100,110,0,-1,7,0,1),(110,120,-1,3,7,1,0)],dtype=CONNECTION_DTYPE)
    assert scan_arrivals(walk,c,0,50,boarding_margin_seconds=30)[3]==120
    # Cannot board a different trip at an unmapped stop.
    c[1]['trip']=8
    assert not np.isfinite(scan_arrivals(walk,c,0,50)[3])


def test_pickup_dropoff_and_transfer_walking():
    walk=csr_matrix(([60.],([1],[2])),shape=(4,4))
    c=np.array([(100,200,0,1,0,0,0),(300,400,2,3,1,0,0)],dtype=CONNECTION_DTYPE)
    assert scan_arrivals(walk,c,0,0)[3]==400
    c[0]['dropoff']=1
    assert not np.isfinite(scan_arrivals(walk,c,0,0)[3])
    c[0]['dropoff']=0;c[0]['pickup']=1
    assert not np.isfinite(scan_arrivals(walk,c,0,0)[3])


def test_route_choice_express_overtake_and_budget():
    c=np.array([(100,1000,0,1,0,0,0),(200,300,0,1,1,0,0)],dtype=CONNECTION_DTYPE)
    assert scan_arrivals(csr_matrix((2,2)),c,0,0)[1]==300
    assert not np.isfinite(scan_arrivals(csr_matrix((2,2)),c,0,0,budget_seconds=299)[1])


def test_gtfs_overnight_and_invalid_times():
    assert gtfs_seconds('25:10:00')==90600
    for value in ['12:60:00','-1:20:00','12:00','nan']:
        with pytest.raises(ValueError):gtfs_seconds(value)


def test_pedestrian_permissions_do_not_follow_car_oneway():
    assert walk_directions({'highway':'residential','oneway':'yes'})==(1,-1)
    assert walk_directions({'highway':'motorway'})==()
    assert walk_directions({'highway':'footway','access':'private'})==()
    assert walk_directions({'highway':'trunk','foot':'yes'})==(1,-1)
    assert walk_directions({'highway':'footway','oneway:foot':'-1'})==(-1,)


def test_scheduled_output_keeps_unknown_origins_and_jobs_missing(tmp_path):
    import pandas as pd
    from src.development import load_development_results, validate_accessibility
    frame=pd.DataFrame({'region_code':['001','002'],'departure_time':['08:00:00']*2,
        'travel_budget_minutes':[30]*2,'method':['ktdb_schedule_walk']*2,
        'status':['computed','unmapped_origin'], 'accessible_facilities_30m':[3,None],
        'accessible_medical_30m':[2,None],'accessible_pharmacy_30m':[1,None],
        'accessible_jobs_30m':[None,None]})
    validate_accessibility(frame)
    directory=tmp_path/'processed';directory.mkdir()
    frame.to_csv(directory/'accessibility_30m.csv',index=False)
    result=load_development_results(tmp_path)
    assert len(result['accessibility'])==2
    assert not result['warnings']
    frame.loc[1,'accessible_facilities_30m']=0
    with pytest.raises(ValueError,match='0개'): validate_accessibility(frame)


def test_invalid_connections_and_walk_costs_are_rejected():
    c=np.array([(100,99,0,1,0,0,0)],dtype=CONNECTION_DTYPE)
    with pytest.raises(ValueError,match='before'):scan_arrivals(csr_matrix((2,2)),c,0,0)
    bad=csr_matrix(([-1.],([0],[1])),shape=(2,2))
    with pytest.raises(ValueError,match='nonnegative'):scan_arrivals(bad,np.empty(0,dtype=CONNECTION_DTYPE),0,0)


def test_ktdb_preparation_uses_bus_zero_and_excludes_whole_implausible_trip(tmp_path):
    import pandas as pd
    from scripts.prepare_accessibility_sources import prepare_gtfs
    source=tmp_path/'gtfs';source.mkdir()
    output=tmp_path/'out';output.mkdir()
    pd.DataFrame({'stop_id':['a','b','c'],'stop_lat':[35.,35.,35.],
                  'stop_lon':[127.,127.001,129.]}).to_csv(source/'stops.txt',index=False)
    pd.DataFrame({'route_id':['city','intercity'],'route_type':[0,3]}).to_csv(source/'routes.txt',index=False)
    pd.DataFrame({'route_id':['city','city','intercity'],'service_id':['B1']*3,
                  'trip_id':['good','bad','not_city']}).to_csv(source/'trips.txt',index=False)
    rows=[]
    for trip,finish in [('good','b'),('bad','c'),('not_city','b')]:
        rows.extend([[trip,'08:00:00','08:00:00','a',1,0,0],
                     [trip,'08:01:00','08:01:00',finish,2,0,0]])
    pd.DataFrame(rows,columns=['trip_id','arrival_time','departure_time','stop_id',
        'stop_sequence','pickup_type','drop_off_type']).to_csv(source/'stop_times.txt',index=False)
    stops, timetable, info=prepare_gtfs(source,(126.9,34.9,127.1,35.1),output)
    assert set(timetable.trip_id)=={'good'}
    assert info['spatial_time_trips_excluded']==1
    assert pd.read_csv(output/'excluded_timetable_jumps.csv').trip_id.tolist()==['bad']
