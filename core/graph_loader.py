"""Provide a small, explicitly synthetic network for the local MVP."""


def load_demo_graph():
    """Return a schematic graph; its connections are not real streets."""
    nodes = {
        "A": {"label": "Demo Start", "x": 70, "y": 155},
        "N": {"label": "North Junction", "x": 330, "y": 65},
        "C": {"label": "Central Junction", "x": 355, "y": 155},
        "S": {"label": "South Junction", "x": 330, "y": 250},
        "D": {"label": "Demo Destination", "x": 625, "y": 155},
    }

    branches = (
        ("A", "N", 280, {"historical_incidents": 0.9, "lighting_deficiency": 0.1,
                         "live_hazards": 0.1, "isolation": 0.1, "uncertainty": 0.1}),
        ("N", "D", 320, {"historical_incidents": 0.9, "lighting_deficiency": 0.1,
                         "live_hazards": 0.1, "isolation": 0.1, "uncertainty": 0.1}),
        ("A", "C", 350, {"historical_incidents": 0.1, "lighting_deficiency": 0.1,
                         "live_hazards": 0.2, "isolation": 0.2, "uncertainty": 0.1}),
        ("C", "D", 350, {"historical_incidents": 0.1, "lighting_deficiency": 0.1,
                         "live_hazards": 0.2, "isolation": 0.2, "uncertainty": 0.1}),
        ("A", "S", 450, {"historical_incidents": 0.1, "lighting_deficiency": 0.05,
                         "live_hazards": 0.0, "isolation": 0.05, "uncertainty": 0.1}),
        ("S", "D", 400, {"historical_incidents": 0.1, "lighting_deficiency": 0.05,
                         "live_hazards": 0.0, "isolation": 0.05, "uncertainty": 0.1}),
    )
    edges = [
        {
            "id": "{}-{}".format(source, target),
            "source": source,
            "target": target,
            "length_m": length_m,
            "risk_factors": factors,
        }
        for source, target, length_m, factors in branches
    ]
    return {"nodes": nodes, "edges": edges}
