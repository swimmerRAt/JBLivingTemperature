"""Expected-time walk/bus routing on measured, directed network edges.

No straight-line distances or assumed bus speeds are substituted for missing data.
Boarding uses half the supplied headway; staying on a route adds no second wait.
"""
from __future__ import annotations

from collections import defaultdict
import heapq
import math

import pandas as pd


def reachable_times(edges: pd.DataFrame, origin: str, budget: float = 30,
                    wait_factor: float = 0.5) -> dict[str, float]:
    required = {'from_node', 'to_node', 'mode', 'minutes', 'route_id', 'headway_minutes'}
    if required - set(edges):
        raise ValueError(f'Missing network columns: {sorted(required - set(edges))}')
    if not math.isfinite(budget) or budget <= 0 or not 0 <= wait_factor <= 1:
        raise ValueError('Invalid time budget or wait factor')
    graph = defaultdict(list)
    for row in edges.itertuples(index=False):
        duration = float(row.minutes)
        if row.mode not in {'walk', 'bus'} or not math.isfinite(duration) or duration < 0:
            raise ValueError('Edges require walk/bus mode and finite nonnegative minutes')
        if row.mode == 'bus':
            if pd.isna(row.route_id) or not str(row.route_id).strip():
                raise ValueError('Bus edges require route_id')
            interval = float(row.headway_minutes)
            if not math.isfinite(interval) or interval <= 0:
                raise ValueError('Bus edges require positive measured headway_minutes')
        else:
            interval = 0
        graph[str(row.from_node)].append((str(row.to_node), row.mode, duration,
                                         str(row.route_id), interval))
    distances = {(str(origin), ''): 0.0}
    queue = [(0.0, str(origin), '')]
    result = {}
    while queue:
        elapsed, node, route = heapq.heappop(queue)
        if elapsed != distances.get((node, route)):
            continue
        result[node] = min(result.get(node, math.inf), elapsed)
        for target, mode, duration, next_route, interval in graph[node]:
            on_route = next_route if mode == 'bus' else ''
            wait = interval * wait_factor if mode == 'bus' and route != next_route else 0
            candidate = elapsed + duration + wait
            state = (target, on_route)
            if candidate <= budget and candidate < distances.get(state, math.inf):
                distances[state] = candidate
                heapq.heappush(queue, (candidate, target, on_route))
    return result


def summarize_accessibility(edges: pd.DataFrame, origins: pd.DataFrame,
                            facilities: pd.DataFrame, budget: float = 30,
                            wait_factor: float = 0.5) -> pd.DataFrame:
    """One representative origin per town; count distinct destination facilities."""
    if not {'region_code', 'node_id'} <= set(origins):
        raise ValueError('Origins require region_code and node_id')
    if not {'facility_id', 'node_id', 'category'} <= set(facilities):
        raise ValueError('Facilities require facility_id, node_id and category')
    if origins.empty or facilities.empty or edges.empty:
        raise ValueError('Network, origins and facilities must be nonempty')
    if origins[['region_code', 'node_id']].isna().any(axis=None) or origins.region_code.duplicated().any():
        raise ValueError('Exactly one nonmissing origin per region is required')
    if facilities[['facility_id', 'node_id', 'category']].isna().any(axis=None):
        raise ValueError('Facility identifiers, nodes and categories cannot be missing')
    nodes = set(edges.from_node.astype(str)) | set(edges.to_node.astype(str))
    if not set(origins.node_id.astype(str)) <= nodes or not set(facilities.node_id.astype(str)) <= nodes:
        raise ValueError('Origin/facility nodes must exist in the network')
    records = []
    for origin in origins.itertuples(index=False):
        times = reachable_times(edges, str(origin.node_id), budget, wait_factor)
        reached = facilities[facilities.node_id.astype(str).isin(times)]
        records.append({'region_code': str(origin.region_code),
                        'accessible_facilities_30m': reached.facility_id.nunique(),
                        'accessible_medical_30m': reached.loc[reached.category.eq('medical'), 'facility_id'].nunique(),
                        'accessible_jobs_30m': reached.loc[reached.category.eq('jobs'), 'facility_id'].nunique(),
                        'travel_budget_minutes': budget, 'wait_factor': wait_factor})
    return pd.DataFrame(records)
