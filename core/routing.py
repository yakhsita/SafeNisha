"""Distance-aware routing over a small walking graph."""

import heapq
import math

from core.risk_engine import calculate_risk


def find_route(graph, start, destination, beta=0.0, weights=None):
    """Find a route minimizing length_m * (1 + beta * edge risk)."""
    nodes = graph["nodes"]
    if start not in nodes or destination not in nodes:
        raise ValueError("Start and destination must be graph nodes.")
    if not isinstance(beta, (int, float)) or not math.isfinite(beta) or beta < 0:
        raise ValueError("beta must be a finite, non-negative number.")
    if start == destination:
        raise ValueError("Start and destination must be different.")

    adjacency = {node_id: [] for node_id in nodes}
    for edge in graph["edges"]:
        length = edge["length_m"]
        if not isinstance(length, (int, float)) or not math.isfinite(length) or length <= 0:
            raise ValueError("Each edge must have a positive, finite length.")
        if edge["source"] not in nodes or edge["target"] not in nodes:
            raise ValueError("Each edge endpoint must exist in the graph.")
        risk = calculate_risk(edge["risk_factors"], weights)
        cost = length * (1.0 + beta * risk)
        adjacency[edge["source"]].append((edge["target"], edge, cost, risk))
        adjacency[edge["target"]].append((edge["source"], edge, cost, risk))

    distances = {start: 0.0}
    previous = {}
    queue = [(0.0, start)]
    while queue:
        cost_so_far, node_id = heapq.heappop(queue)
        if cost_so_far > distances.get(node_id, float("inf")):
            continue
        if node_id == destination:
            break
        for neighbor, edge, edge_cost, risk in adjacency[node_id]:
            candidate = cost_so_far + edge_cost
            if candidate < distances.get(neighbor, float("inf")):
                distances[neighbor] = candidate
                previous[neighbor] = (node_id, edge, risk)
                heapq.heappush(queue, (candidate, neighbor))

    if destination not in previous:
        raise ValueError("No walking route connects the selected locations.")

    route_nodes = [destination]
    route_edges = []
    route_risks = []
    cursor = destination
    while cursor != start:
        prior_node, edge, risk = previous[cursor]
        route_edges.append(edge)
        route_risks.append(risk)
        route_nodes.append(prior_node)
        cursor = prior_node
    route_nodes.reverse()
    route_edges.reverse()
    route_risks.reverse()

    distance_m = sum(edge["length_m"] for edge in route_edges)
    estimated_risk = sum(edge["length_m"] * risk for edge, risk
                         in zip(route_edges, route_risks)) / distance_m
    return {
        "nodes": route_nodes,
        "edges": [edge["id"] for edge in route_edges],
        "distance_m": distance_m,
        "risk_indicator": estimated_risk,
        "generalized_cost": distances[destination],
    }


def compare_routes(graph, start, destination, beta, weights=None):
    """Return shortest-distance and beta-preferred routes."""
    shortest = find_route(graph, start, destination, beta=0.0, weights=weights)
    preferred = find_route(graph, start, destination, beta=beta, weights=weights)
    return {"shortest": shortest, "preferred": preferred}
