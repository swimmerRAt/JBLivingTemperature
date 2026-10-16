"""Compute administrative-center healthcare access using prepared OSM/KTDB data."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys
import time
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import numpy as np
import pandas as pd
from scipy.sparse import load_npz
from src.scheduled_accessibility import gtfs_seconds, scan_arrivals


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--network-dir',type=Path,default=ROOT/'data/processed/accessibility_network')
    parser.add_argument('--departures',nargs='+',default=['08:00:00','12:00:00','18:00:00'])
    parser.add_argument('--region',help='Optional one-region diagnostic run; never overwrites full output')
    parser.add_argument('--boarding-margin',type=int,default=30,help='Seconds allowed for each new boarding')
    args=parser.parse_args()
    if args.boarding_margin < 0:parser.error('Boarding margin must be nonnegative')
    for instant in args.departures:gtfs_seconds(instant)
    directory=args.network_dir
    manifest=json.loads((directory/'manifest.json').read_text(encoding='utf-8'))
    walk=load_npz(directory/'walk_graph.npz')
    connections=np.load(directory/'scheduled_connections.npy',mmap_mode='r')
    points=pd.read_csv(directory/'network_points.csv',dtype={'point_id':str,'region_code':str})
    origins=points[points.category.eq('origin')]
    if args.region:origins=origins[origins.region_code.eq(args.region)]
    if origins.empty:raise ValueError('No selected administrative center origin exists')
    facilities=points[points.category.isin(['hospital','pharmacy']) & points.network_node.ge(0)]
    nodes=facilities.network_node.to_numpy(dtype=int)
    hospital_mask=facilities.category.eq('hospital').to_numpy()
    pharmacy_mask=facilities.category.eq('pharmacy').to_numpy()
    rows=[]
    started=time.monotonic()
    for number,origin in enumerate(origins.itertuples(index=False),1):
        for instant in args.departures:
            record={'region_code':origin.region_code,'departure_time':instant,
                'travel_budget_minutes':30,'method':'ktdb_schedule_walk','status':'unmapped_origin',
                'accessible_facilities_30m':None,'accessible_medical_30m':None,'accessible_pharmacy_30m':None,
                'accessible_jobs_30m':None,'origin_connector_m':origin.connector_m,
                'origin_name':getattr(origin,'origin_name',None),
                'origin_source_url':getattr(origin,'source_url',None),
                'origin_lat':origin.lat,'origin_lon':origin.lon,
                'origin_basis':manifest.get('origin_basis','polygon_representative_point'),
                'mapped_facilities':len(facilities),'source_facilities':manifest['facility_records']}
            if getattr(origin,'origin_status',None)=='center_unconfirmed':
                record['status']='unconfirmed_center'
            if origin.network_node >= 0:
                departure=gtfs_seconds(instant)
                labels=scan_arrivals(walk,connections,int(origin.network_node),departure,
                                     boarding_margin_seconds=args.boarding_margin)
                reached=np.isfinite(labels[nodes])
                record.update(status='computed',accessible_facilities_30m=int(reached.sum()),
                    accessible_medical_30m=int((reached&hospital_mask).sum()),
                    accessible_pharmacy_30m=int((reached&pharmacy_mask).sum()))
            rows.append(record)
        if number%10==0 or number==len(origins):
            print(f'{number}/{len(origins)} origins, {time.monotonic()-started:.1f}s',flush=True)
    frame=pd.DataFrame(rows)
    target=directory/'diagnostic_results.csv' if args.region else ROOT/'data/processed/accessibility_departures.csv'
    temporary=target.with_suffix('.csv.tmp')
    frame.to_csv(temporary,index=False);temporary.replace(target)
    if not args.region:
        # Maintain the single-time legacy filename; the UI can select all samples.
        default=frame[frame.departure_time.eq(args.departures[0])]
        default.to_csv(ROOT/'data/processed/accessibility_30m.csv',index=False)
        metadata={**manifest,'departures':args.departures,'boarding_margin_seconds':args.boarding_margin,
            'result_rows':len(frame),'computed_origins':int(default.status.eq('computed').sum()),
            'unmapped_origins':int(default.status.ne('computed').sum()),
            'unconfirmed_centers':int(default.status.eq('unconfirmed_center').sum()),
            'centers_off_network':int(default.status.eq('unmapped_origin').sum()),
            'elapsed_seconds':time.monotonic()-started,
            'notes':manifest['notes']+['Healthcare counts only. Job data absent, never encoded as zero.',
                'Counts include mapped facilities only. Unmapped origins are unknown, not zero-access areas.',
                '2025.03 timetable/healthcare and 2026.10 OSM are mixed snapshots, not a current-service guarantee.']}
        (ROOT/'data/processed/accessibility_metadata.json').write_text(json.dumps(metadata,ensure_ascii=False,indent=2),encoding='utf-8')
    print(f'Saved {target}',flush=True)


if __name__=='__main__':main()
