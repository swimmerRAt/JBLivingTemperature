"""Connection-scan routing on walking links and dated scheduled bus connections.

Walk times are estimates; bus times and waits follow the supplied timetable.
No headway average, car speed, or invented job location is substituted.
"""
from __future__ import annotations
import heapq
import math
import numpy as np
from scipy.sparse import csr_matrix


def gtfs_seconds(value: str) -> int:
    """GTFS permits hours >=24, but not invalid minutes/seconds or negatives."""
    parts = str(value).split(':')
    if len(parts) != 3 or any(not p.isdigit() for p in parts):
        raise ValueError(f'Invalid GTFS time: {value}')
    hour, minute, second = map(int, parts)
    if minute >= 60 or second >= 60:
        raise ValueError(f'Invalid GTFS time: {value}')
    return hour * 3600 + minute * 60 + second


def scan_arrivals(walk: csr_matrix, connections: np.ndarray, origin: int,
                  departure: int, budget_seconds: int = 1800,
                  boarding_margin_seconds: int = 30) -> np.ndarray:
    """Earliest absolute arrivals; connections sorted by departure, trip and sequence.

    Connections have fields departure, arrival, from_node, to_node, trip,
    pickup, dropoff. -1 represents an unmapped stop, usable only while onboard.
    Transfer walking is a real path on the supplied graph. A configurable margin
    applies to each new boarding, never to a passenger staying on the same trip.
    """
    if not 0 <= origin < walk.shape[0] or budget_seconds <= 0 or boarding_margin_seconds < 0:
        raise ValueError('Invalid origin, budget or boarding margin')
    if len(connections) and np.any(np.diff(connections['departure']) < 0):
        raise ValueError('Connections must be sorted by departure')
    if len(connections) and np.any(connections['arrival'] < connections['departure']):
        raise ValueError('A connection cannot arrive before it departs')
    if not np.isfinite(walk.data).all() or (walk.data < 0).any():
        raise ValueError('Walking times must be finite and nonnegative')
    limit = departure + budget_seconds
    labels = np.full(walk.shape[0], np.inf)
    indptr, indices, times = walk.indptr, walk.indices, walk.data

    def relax(start, instant):
        if instant >= labels[start] or instant > limit:
            return
        labels[start] = instant
        heap = [(instant, start)]
        while heap:
            at, node = heapq.heappop(heap)
            if at != labels[node]:
                continue
            for i in range(indptr[node], indptr[node+1]):
                candidate = at + times[i]
                target = indices[i]
                if candidate <= limit and candidate < labels[target]:
                    labels[target] = candidate
                    heapq.heappush(heap, (candidate, target))

    relax(origin, departure)
    onboard = set()
    first = np.searchsorted(connections['departure'], departure)
    last = np.searchsorted(connections['departure'], limit, side='right')
    for connection in connections[first:last]:
        dep, arr, source, target, trip, pickup, dropoff = connection
        if trip not in onboard:
            if source < 0 or pickup != 0 or labels[source] + boarding_margin_seconds > dep:
                continue
            onboard.add(trip)
        if target >= 0 and dropoff == 0:
            relax(int(target), int(arr))
    return labels


CONNECTION_DTYPE = np.dtype([('departure', 'i4'), ('arrival', 'i4'),
    ('from_node', 'i4'), ('to_node', 'i4'), ('trip', 'i4'), ('pickup', 'i1'), ('dropoff', 'i1')])
