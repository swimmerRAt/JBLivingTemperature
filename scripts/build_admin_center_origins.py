"""Extract named administrative welfare/community offices from the supplied OSM.

Match an office's explicit name and containing analysis unit. Never substitute
an arbitrary polygon representative point when the office is unconfirmed.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import re
import sqlite3
import sys
import unicodedata
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import osmium
import pandas as pd
from shapely.geometry import Point,Polygon
from src.data_pipeline import load_units,unit_name,database_path

SUFFIXES=('행정복지센터','주민센터','동사무소','읍사무소','면사무소','읍행정복지센터','면행정복지센터','동주민센터')


def office_town(name):
    value=unicodedata.normalize('NFC',str(name))
    value=re.sub(r'\s+','',value)
    for suffix in sorted(SUFFIXES,key=len,reverse=True):
        if value.endswith(suffix):
            town=value[:-len(suffix)]
            # 읍/면/동 are part of the unit, not removed suffixes.
            if suffix.startswith('읍') and town and not town.endswith('읍'):town+='읍'
            if suffix.startswith('면') and town and not town.endswith('면'):town+='면'
            if suffix=='동주민센터' and town and not town.endswith('동'):town+='동'
            return town
    return None


class Centers(osmium.SimpleHandler):
    def __init__(self,bbox):
        super().__init__();self.bbox=bbox;self.coords={};self.records=[]
    def node(self,node):
        if not node.location.valid():return
        lon,lat=node.location.lon,node.location.lat
        a,b,c,d=self.bbox
        if not a<=lon<=c or not b<=lat<=d:return
        self.coords[node.id]=(lon,lat)
        self.record('node',node.id,dict(node.tags),lon,lat,'mapped_point')
    def way(self,way):
        tags=dict(way.tags)
        if not office_town(tags.get('name','')):return
        coords=[self.coords.get(n.ref) for n in way.nodes]
        if any(c is None for c in coords) or len(coords)<3:return
        if coords[0]==coords[-1] and len(coords)>=4:
            polygon=Polygon(coords)
            if polygon.is_valid:
                point=polygon.representative_point()
                self.record('way',way.id,tags,point.x,point.y,'building_interior_point')
    def record(self,kind,identifier,tags,lon,lat,location_method):
        name=tags.get('name','')
        if office_town(name) and not any(word in name for word in ['옛','구청사','폐쇄','이전부지']):
            self.records.append({'center_name':name,'lon':lon,'lat':lat,'source_type':'osm',
                'source_url':f'https://www.openstreetmap.org/{kind}/{identifier}',
                'source_date':'2026-10-15','location_method':location_method,
                'osm_id':f'{kind}/{identifier}','address':tags.get('addr:full','')})


def match_centers(units,candidates):
    units=units.to_crs(4326)
    records=[]
    audit=[]
    for unit in units.itertuples():
        matches=[]
        for candidate in candidates:
            town=office_town(candidate['center_name'])
            # A prefix such as city/gu may precede the explicit town name.
            aliases=[unit.town]
            if unit.town=='금암동':aliases+=['금암1동','금암2동']
            if unit.town=='여의동':aliases+=['동산동']
            if unit.town=='교월동':aliases+=['교동월촌동']
            if unit.town=='초산동':aliases+=['시기3동']
            if unit.town=='용진읍':aliases+=['용진면']
            name_match=any(town==alias or (town and town.endswith(alias)) for alias in aliases)
            point=Point(candidate['lon'],candidate['lat'])
            if name_match and unit.geometry.covers(point):matches.append(candidate)
        # Node+building representations of one office count as one location.
        if matches:
            matches.sort(key=lambda r:(office_town(r['center_name'])!=unit.town,
                r['location_method']!='mapped_point',r['source_url']))
            selected=matches[0]
            far=[m for m in matches if Point(m['lon'],m['lat']).distance(Point(selected['lon'],selected['lat']))>.002]
            if far:
                # Do not guess between spatially distinct offices/old buildings.
                exact=[m for m in matches if office_town(m['center_name'])==unit.town]
                if exact and all(Point(m['lon'],m['lat']).distance(Point(exact[0]['lon'],exact[0]['lat']))<.002 for m in exact):
                    selected=exact[0]
                else:
                    audit.append({'region_code':unit.region_code,'reason':'multiple_distinct_centers','candidates':json.dumps(matches,ensure_ascii=False)})
                    matches=[]
        if unit.city=='전주시' and unit.town=='금암동':
            # Official 2025-02-24 announcement selects former Geumam 1 office.
            verified=[c for c in candidates if c.get('osm_id')=='node/7851838002']
            if verified:
                selected={**verified[0], 'center_name':'금암동 주민센터',
                    'official_source_url':'https://daum.jeonju.go.kr/main/menu?bwrite_id=3410&do=view&gc=1018WXIQ&page=4'}
                matches=verified
        if matches:
            records.append({'region_code':unit.region_code,'city':unit.city,'town':unit.town,
                'origin_status':('confirmed_official_selection' if selected.get('official_source_url') else 'confirmed_osm_name_and_boundary'),'origin_kind':'administrative_center',
                'match_method':('official_office_selection_with_osm_location' if selected.get('official_source_url') else 'office_name_and_current_boundary'),**selected})
        else:
            records.append({'region_code':unit.region_code,'city':unit.city,'town':unit.town,
                'origin_status':'center_unconfirmed','origin_kind':'administrative_center',
                'center_name':None,'lon':None,'lat':None,'source_url':None})
    return pd.DataFrame(records),pd.DataFrame(audit)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pbf',type=Path,default=ROOT/'data/raw/osm/south-korea-261015.osm.pbf')
    args=parser.parse_args()
    with sqlite3.connect(database_path(ROOT/'data')) as connection:units,_=load_units(connection)
    bbox=units.to_crs(5179).geometry.buffer(5000).to_crs(4326).total_bounds
    extractor=Centers(bbox);extractor.apply_file(str(args.pbf))
    directory=ROOT/'data/raw/admin_centers';directory.mkdir(parents=True,exist_ok=True)
    pd.DataFrame(extractor.records).to_csv(directory/'osm_candidates.csv',index=False)
    origins,audit=match_centers(units,extractor.records)
    origins.to_csv(directory/'origins.csv',index=False)
    audit.to_csv(directory/'ambiguous_matches.csv',index=False)
    print('Candidates:',len(extractor.records),'Confirmed:',int(origins.lat.notna().sum()),'Unconfirmed:',int(origins.lat.isna().sum()),flush=True)
    print(origins[origins.lat.isna()][['city','town']].to_string(index=False),flush=True)


if __name__=='__main__':main()
