"""Prepare KTDB March 2025 weekday bus timetable and OSM pedestrian graph.

KTDB codes city/rural/village buses as route_type=0 (not standard GTFS bus=3).
Its calendar's all-days flags do not make the representative weekday a weekend.
"""
from __future__ import annotations
import argparse
from collections import defaultdict
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import numpy as np
import osmium
import pandas as pd
from pyproj import Transformer
from scipy.sparse import csr_matrix, save_npz
from shapely.geometry import LineString, Point
from shapely.strtree import STRtree
from src.data_pipeline import load_units, database_path
from src.scheduled_accessibility import gtfs_seconds, CONNECTION_DTYPE


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024*1024), b''):
            h.update(chunk)
    return h.hexdigest()


def walk_directions(tags):
    """Return allowed pedestrian directions; car oneway does not prohibit walking."""
    highway = tags.get('highway', '')
    if not highway or highway in {'construction','proposed','motorway','motorway_link','raceway'}:
        return ()
    foot = tags.get('foot', '')
    allowed = {'footway','path','pedestrian','steps','living_street','residential','service','track','unclassified','road','primary','primary_link','secondary','secondary_link','tertiary','tertiary_link','trunk','trunk_link','cycleway','bridleway','platform'}
    if highway not in allowed:
        return ()
    if foot in {'no', 'private', 'use_sidepath'} or tags.get('area') == 'yes':
        return ()
    explicit = foot in {'yes','designated','permissive'}
    if (tags.get('access') in {'no','private'} or tags.get('motorroad') == 'yes'
            or highway in {'trunk','trunk_link'}) and not explicit:
        return ()
    if highway in {'cycleway','bridleway'} and not explicit:
        return ()
    direction = tags.get('oneway:foot', 'no')
    return (1,) if direction in {'yes','1','true'} else (-1,) if direction == '-1' else (1,-1)


class WalkingWays(osmium.SimpleHandler):
    def __init__(self, bbox):
        super().__init__()
        self.bbox = bbox
        self.locations = {}
        self.blocked = set()
        self.segments = {}

    def node(self, node):
        if not node.location.valid():
            return
        x,y = node.location.lon,node.location.lat
        a,b,c,d = self.bbox
        if a <= x <= c and b <= y <= d:
            self.locations[node.id] = (x,y)
            foot = node.tags.get('foot','')
            barrier = node.tags.get('barrier','')
            if foot in {'no','private'} or (node.tags.get('access','') in {'no','private'} and foot not in {'yes','designated','permissive'}) or barrier in {'wall','fence'}:
                self.blocked.add(node.id)

    def way(self, way):
        tags = dict(way.tags)
        directions = walk_directions(tags)
        if not directions:
            return
        previous = None
        for ref in way.nodes:
            node = ref.ref
            if previous is not None and previous in self.locations and node in self.locations and previous not in self.blocked and node not in self.blocked and previous != node:
                low, high = sorted((previous,node))
                forward = previous == low
                mask = 0
                for direction in directions:
                    mask |= 1 if (direction == 1) == forward else 2
                key=(low,high)
                self.segments[key] = self.segments.get(key,0) | mask
            previous = node


def prepare_gtfs(folder, bbox, output):
    stops = pd.read_csv(folder/'stops.txt',dtype={'stop_id':str})
    routes = pd.read_csv(folder/'routes.txt',dtype={'route_id':str})
    trips = pd.read_csv(folder/'trips.txt',dtype=str)
    bus_routes = set(routes.loc[routes.route_type.eq(0),'route_id'])
    bus_trips = set(trips.loc[trips.route_id.isin(bus_routes),'trip_id'])
    a,b,c,d=bbox
    local_stops = stops[stops.stop_lon.between(a,c)&stops.stop_lat.between(b,d)].copy()
    ids = set(local_stops.stop_id)
    chosen=set()
    columns=['trip_id','arrival_time','departure_time','stop_id','stop_sequence','pickup_type','drop_off_type']
    print('Scanning national GTFS for regional bus trips',flush=True)
    for chunk in pd.read_csv(folder/'stop_times.txt',dtype={'trip_id':str,'stop_id':str},usecols=['trip_id','stop_id'],chunksize=500000):
        chosen.update(chunk.loc[chunk.stop_id.isin(ids)&chunk.trip_id.isin(bus_trips),'trip_id'])
    if not chosen:
        raise ValueError('No city/rural bus trips found in the regional bounds')
    print(f'Regional trips: {len(chosen)}; reading their complete stop sequences',flush=True)
    parts=[]
    for chunk in pd.read_csv(folder/'stop_times.txt',dtype={'trip_id':str,'stop_id':str},usecols=columns,chunksize=500000):
        part=chunk[chunk.trip_id.isin(chosen)]
        if not part.empty: parts.append(part.copy())
    timetable=pd.concat(parts,ignore_index=True).sort_values(['trip_id','stop_sequence'])
    if timetable.duplicated(['trip_id','stop_sequence']).any():
        raise ValueError('Duplicate trip/stop sequence in GTFS')
    timetable['arrival']=timetable.arrival_time.map(gtfs_seconds)
    timetable['departure']=timetable.departure_time.map(gtfs_seconds)
    for column in ['pickup_type','drop_off_type']:
        if not timetable[column].isin([0,1]).all(): raise ValueError('Unknown pickup/dropoff type')
    previous=timetable.groupby('trip_id').departure.shift()
    bad=(timetable.departure.lt(timetable.arrival)|timetable.arrival.lt(previous))
    bad_trips=set(timetable.loc[bad,'trip_id'])
    timetable=timetable[~timetable.trip_id.isin(bad_trips)].copy()
    if timetable.empty: raise ValueError('All regional trips have invalid chronological times')
    # A conservative data-quality screen, not a statutory speed limit: reject
    # entire trips with long coordinate jumps requiring >130 km/h even in a
    # straight line. Preserve the original source; never invent slower times.
    lat = timetable.stop_id.map(stops.set_index('stop_id').stop_lat)
    lon = timetable.stop_id.map(stops.set_index('stop_id').stop_lon)
    next_lat = lat.groupby(timetable.trip_id).shift(-1)
    next_lon = lon.groupby(timetable.trip_id).shift(-1)
    next_arrival = timetable.groupby('trip_id').arrival.shift(-1)
    delta = next_arrival - timetable.departure
    radians = np.radians
    hav = np.sin(radians(next_lat-lat)/2)**2 + np.cos(radians(lat))*np.cos(radians(next_lat))*np.sin(radians(next_lon-lon)/2)**2
    metres = 6371000*2*np.arctan2(np.sqrt(hav.clip(0,1)),np.sqrt((1-hav).clip(0,1)))
    speed = metres / delta.replace(0,np.nan) * 3.6
    suspicious = ((metres > 1000) & (speed > 130)) | ((metres > 100) & delta.eq(0))
    audit = timetable.loc[suspicious,['trip_id','stop_id','stop_sequence']].copy()
    audit['straight_distance_m'] = metres[suspicious]
    audit['implied_speed_kmh'] = speed[suspicious]
    audit.to_csv(output/'excluded_timetable_jumps.csv',index=False)
    suspicious_trips = set(audit.trip_id)
    timetable = timetable[~timetable.trip_id.isin(suspicious_trips)]
    if timetable.empty: raise ValueError('No trips remain after spatial/time quality screening')
    local_stops=local_stops[local_stops.stop_id.isin(timetable.stop_id)]
    timetable.to_parquet(output/'regional_stop_times.parquet',index=False)
    local_stops.to_csv(output/'regional_stops.csv',index=False)
    return local_stops,timetable,{'trips':int(timetable.trip_id.nunique()),'stop_times':len(timetable),
        'stops':len(local_stops),'invalid_time_trips_excluded':len(bad_trips), 'spatial_time_trips_excluded':len(suspicious_trips),
        'quality_screen':'Exclude full trips requiring >130 km/h straight-line over >1km, or >100m in zero time',
        'route_type':0}


def prepare_walk(pbf,bbox,points,output,speed,max_connector):
    handler=WalkingWays(bbox)
    print('Reading OSM pedestrian ways',flush=True)
    handler.apply_file(str(pbf))
    if not handler.segments: raise ValueError('No pedestrian network found')
    used=sorted({node for segment in handler.segments for node in segment})
    lookup={node:i for i,node in enumerate(used)}
    project=Transformer.from_crs(4326,5179,always_xy=True)
    xy=np.array(project.transform(*zip(*(handler.locations[node] for node in used)))).T
    keys=list(handler.segments)
    lines=[LineString([xy[lookup[a]],xy[lookup[b]]]) for a,b in keys]
    tree=STRtree(lines)
    px,py=project.transform(points.lon.to_numpy(),points.lat.to_numpy())
    projected=[Point(x,y) for x,y in zip(px,py)]
    nearest=tree.nearest(projected)
    splits=defaultdict(list)
    next_node=len(used)
    mapping=[]
    for point,geometry,index in zip(points.to_dict('records'),projected,nearest):
        segment=lines[index]
        distance=geometry.distance(segment)
        record={**point,'connector_m':distance,'network_node':-1}
        if distance <= max_connector:
            offset=segment.project(geometry)
            network_node=next_node
            next_node+=1
            splits[int(index)].append((offset,network_node))
            record['network_node']=network_node
        mapping.append(record)
    rows,cols,costs=[],[],[]
    def edge(a,b,seconds):
        rows.append(a);cols.append(b);costs.append(max(float(seconds),0.))
    for index,((a,b),line) in enumerate(zip(keys,lines)):
        chain=[(0.,lookup[a]),*sorted(splits[index]),(line.length,lookup[b])]
        mask=handler.segments[(a,b)]
        for (offset,u),(following,v) in zip(chain,chain[1:]):
            seconds=(following-offset)/speed*60
            if mask&1:edge(u,v,seconds)
            if mask&2:edge(v,u,seconds)
    # Dedicated access-point nodes include the estimated off-network connector
    # once per entry/exit, not once per adjacent road segment.
    for record in mapping:
        if record['network_node'] < 0: continue
        road_node=record['network_node']
        access_node=next_node;next_node+=1
        edge(access_node,road_node,record['connector_m']/speed*60)
        edge(road_node,access_node,record['connector_m']/speed*60)
        record['network_node']=access_node
    walk=csr_matrix((np.array(costs), (rows,cols)),shape=(next_node,next_node))
    save_npz(output/'walk_graph.npz',walk)
    mapped=pd.DataFrame(mapping)
    mapped.to_csv(output/'network_points.csv',index=False)
    return mapped,{'nodes':next_node,'directed_edges':walk.nnz,'road_segments':len(lines),
                   'walking_metres_per_minute':speed,'max_connector_m':max_connector,
                   'unmapped_points':mapped[mapped.network_node.lt(0)].groupby('category').size().to_dict()}


def build_connections(timetable,mapped,output):
    stop_nodes=dict(zip(mapped.loc[mapped.category.eq('stop'),'point_id'],mapped.loc[mapped.category.eq('stop'),'network_node']))
    frame=timetable.copy()
    frame['network_node']=frame.stop_id.map(stop_nodes).fillna(-1).astype(int)
    following=frame.groupby('trip_id').shift(-1)
    valid=following.arrival.notna()
    frame=frame.loc[valid]
    following=following.loc[valid]
    array=np.empty(len(frame),dtype=CONNECTION_DTYPE)
    array['departure']=frame.departure
    array['arrival']=following.arrival
    array['from_node']=frame.network_node
    array['to_node']=following.network_node
    array['trip']=pd.factorize(frame.trip_id,sort=True)[0]
    array['pickup']=frame.pickup_type
    array['dropoff']=following.drop_off_type
    # Stable ordering preserves within-trip order for zero-time connections.
    order=np.argsort(array['departure'],kind='stable')
    array=array[order]
    np.save(output/'scheduled_connections.npy',array)
    return len(array)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pbf',type=Path,default=ROOT/'data/raw/osm/south-korea-261015.osm.pbf')
    parser.add_argument('--gtfs',type=Path,default=ROOT/'data/raw/ktdb/202503/202503_GTFS_DataSet')
    parser.add_argument('--output',type=Path,default=ROOT/'data/processed/accessibility_network')
    parser.add_argument('--facilities-csv',type=Path,default=ROOT/'data/processed/living_facilities.csv',
                        help='Optional everyday facilities from scripts/download_living_facilities.py')
    parser.add_argument('--origins-csv',type=Path,default=ROOT/'data/raw/admin_centers/origins.csv',help='Verified administrative center locations; no polygon fallback')
    parser.add_argument('--walking-speed',type=float,default=80,help='Metres/minute; an assumption, not observation')
    parser.add_argument('--max-connector',type=float,default=100,help='Max estimated straight connector to a real road edge, metres')
    args=parser.parse_args()
    if not 0<args.walking_speed<=120 or not 0<args.max_connector<=200:parser.error('Invalid walking speed/connector limit')
    args.output.mkdir(parents=True,exist_ok=True)
    with sqlite3.connect(database_path(ROOT/'data')) as connection:
        units,_=load_units(connection)
        facilities=pd.read_sql_query('''SELECT encrypted_facility_id AS point_id, facility_kind AS category,
            longitude AS lon,latitude AS lat FROM medical_facility_snapshot
            WHERE snapshot_period='2025.3' ''',connection)
    living=pd.DataFrame(columns=['point_id','category','lon','lat'])
    if args.facilities_csv.exists():
        living=pd.read_csv(args.facilities_csv,dtype={'facility_id':str})
        living=living.rename(columns={'facility_id':'point_id'})[['point_id','category','lon','lat']]
        facilities=pd.concat([facilities.assign(point_id=facilities.category+':'+facilities.point_id.astype(str)),
                              living.assign(point_id=living.category+':'+living.point_id)],ignore_index=True)
    else:
        facilities['point_id']=facilities.category+':'+facilities.point_id
    # Neighbouring stops/roads are retained within a 5km bounding-box buffer.
    extent=units.to_crs(5179).geometry.buffer(5000).to_crs(4326).total_bounds
    bbox=tuple(float(v) for v in extent)
    stops,timetable,gtfs_info=prepare_gtfs(args.gtfs,bbox,args.output)
    centers=pd.read_csv(args.origins_csv,dtype={'region_code':str})
    required={'region_code','center_name','lon','lat','source_url','origin_status'}
    if not required <= set(centers) or centers.region_code.isna().any() or centers.region_code.duplicated().any():
        raise ValueError('Invalid administrative-center origin table')
    origins=units[['region_code']].merge(centers,on='region_code',how='left',validate='one_to_one')
    confirmed=origins[['lat','lon']].notna().all(axis=1)
    if origins.loc[confirmed,['center_name','source_url']].isna().any(axis=None):
        raise ValueError('Named center and source required for every confirmed coordinate')
    origins['origin_status']=origins.origin_status.fillna('center_unconfirmed')
    origins=origins.rename(columns={'center_name':'origin_name'})
    origins['point_id']='origin:'+origins.region_code
    origins['category']='origin'
    stop_points=stops.rename(columns={'stop_id':'point_id','stop_lat':'lat','stop_lon':'lon'})
    stop_points['category']='stop'
    # Facility IDs are namespaced by category to avoid collisions with stop/origin identifiers.
    valid_facilities=facilities.dropna(subset=['lat','lon']).copy()
    valid_facilities=valid_facilities[valid_facilities.lat.between(-90,90)&valid_facilities.lon.between(-180,180)]
    points=pd.concat([origins,
                     stop_points[['point_id','category','lat','lon']],
                     valid_facilities[['point_id','category','lat','lon']]],ignore_index=True)
    valid_points=points[points.lat.notna() & points.lon.notna()].copy()
    unknown=points[points.lat.isna() | points.lon.isna()].copy()
    mapped,walk_info=prepare_walk(args.pbf,bbox,valid_points,args.output,args.walking_speed,args.max_connector)
    if not unknown.empty:
        unknown['network_node']=-1
        unknown['connector_m']=np.nan
        mapped=pd.concat([mapped,unknown],ignore_index=True)
        mapped.to_csv(args.output/'network_points.csv',index=False)
    walk_info['unconfirmed_center_count']=int(len(unknown))
    connections=build_connections(timetable,mapped,args.output)
    manifest={'method':'ktdb_schedule_walk','timetable_basis':'2025-03 representative weekday',
        'medical_basis':'2025.3','osm_basis':'2026-10-15','bounds_wgs84':bbox,
        'gtfs':gtfs_info,'walking':walk_info,'connections':connections,
        'facility_records':int(facilities.category.isin(['hospital','pharmacy']).sum()),
        'facility_category_records':{k:int(v) for k,v in facilities.category.value_counts().items()},'facility_invalid_coordinates':len(facilities)-len(valid_facilities),
        'origins':len(origins),'origin_basis':'administrative_center','confirmed_centers':int(confirmed.sum()),'jobs_available':False,
        'source_sha256':{str(args.origins_csv.relative_to(ROOT)):digest(args.origins_csv),
            str(database_path(ROOT/'data').relative_to(ROOT)):digest(database_path(ROOT/'data')),
            str(args.pbf.relative_to(ROOT)):digest(args.pbf),
            **({str(args.facilities_csv.relative_to(ROOT)):digest(args.facilities_csv)} if args.facilities_csv.exists() else {}),
            **{str(p.relative_to(ROOT)):digest(p) for p in sorted((ROOT/'data/raw/facilities').glob('*.json'))},
            **{str(p.relative_to(ROOT)):digest(p) for p in sorted(args.gtfs.glob('*.txt'))}},
        'notes':['KTDB route_type=0 is city/rural/village bus; other modes excluded.',
                 'calendar all-days flags are synthetic; do not interpret as weekend/current service.',
                 'Walk times assume speed; connectors up to configured maximum are straight estimates.',
                 'Origins are named administrative welfare/community centers, not homes; missing centers have no polygon fallback.',
                 'OSM foot restrictions applied; detailed crossings, slopes and wheelchair access not verified.']}
    (args.output/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({**gtfs_info,**walk_info,'connections':connections},ensure_ascii=False),flush=True)


if __name__=='__main__':main()
