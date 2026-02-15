"""
modules/synthetic/social_graph.py

Builds and maintains a weighted social graph of staff relationships
inferred from behavioral signals. Computes centrality metrics, runs
cascade simulations, and exports visualization-ready snapshots.

The graph is the analytical engine behind SSE's "critical staff" feature.
It answers: "If we lose this person, what happens to the rest of the team?"

VISUALIZATION DESIGN PRINCIPLE:
    Every output from this module is shaped for a frontend graph visualization.
    Nodes carry color hints, size hints, and labels. Cascade simulations
    return before/after states so the frontend can animate the transition.
    The manager should be able to tap a node and SEE the ripple.

DEPENDENCIES: None beyond stdlib. Centrality algorithms are implemented
    inline for restaurant-scale graphs (5-40 nodes). No networkx needed.

DETERMINISM: All Monte Carlo simulations use hashlib-seeded randomness
    for reproducibility.
"""

from __future__ import annotations

import hashlib
import math
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Dict, List, Any, Optional, Tuple


# ---------------------------------------------------------------------------
# Deterministic randomness (same pattern as every other module)
# ---------------------------------------------------------------------------

def _det_float(seed_str: str) -> float:
    """Deterministic float in [0, 1) from an arbitrary seed string."""
    hash_val = int(hashlib.sha256(seed_str.encode()).hexdigest(), 16)
    return (hash_val % 1_000_000) / 1_000_000


# ---------------------------------------------------------------------------
# Color and visual mapping constants
# ---------------------------------------------------------------------------

# Mood -> hex color gradient (for node fill)
# 1=deep red (crisis), 2=orange (struggling), 3=yellow (neutral),
# 4=light green (good), 5=bright green (thriving)
MOOD_COLORS = {
    1: "#DC2626",  # red-600
    2: "#F97316",  # orange-500
    3: "#EAB308",  # yellow-500
    4: "#84CC16",  # lime-500
    5: "#22C55E",  # green-500
}

# Priority tier -> ring/border color (for node border)
TIER_COLORS = {
    "critical":  "#DC2626",  # red — lose them, lose the team
    "important": "#F97316",  # orange — real impact if they leave
    "standard":  "#3B82F6",  # blue — normal
    "low":       "#9CA3AF",  # gray — peripheral
}

# Edge type -> color (for edge rendering)
EDGE_COLORS = {
    "shift_cowork": "#94A3B8",  # slate-400 (common, subtle)
    "swap_pickup":  "#8B5CF6",  # violet-500 (favor = trust)
    "osm_pickup":   "#06B6D4",  # cyan-500 (extra effort)
    "mood_sync":    "#F59E0B",  # amber-500 (emotional bond)
}

# Role label -> icon hint (for frontend node badges)
ROLE_ICONS = {
    "glue_person": "heart",      # holds the team together
    "bridge":      "git-branch", # connects groups
    "hub":         "star",       # popular within group
    "peripheral":  "circle",     # low integration
}

# Cascade ripple order -> color (for cascade animation)
CASCADE_RIPPLE_COLORS = {
    0: "#DC2626",  # removed node — red
    1: "#F97316",  # first-order — orange
    2: "#EAB308",  # second-order — yellow
    3: "#FDE68A",  # third-order — light yellow
}


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class StaffNode:
    """A single staff member in the social graph."""
    staff_id: str
    persona: str
    tenure_days: int = 0
    current_mood: int = 3          # Latest mood_emoji (1-5 int)
    rolling_mood: float = 3.0      # 30-day rolling average
    rolling_safe_rate: float = 0.7
    rolling_fair_rate: float = 0.7
    rolling_respected_rate: float = 0.7
    is_active: bool = True

    # Computed by graph engine
    betweenness_centrality: float = 0.0
    eigenvector_centrality: float = 0.0
    degree_centrality: float = 0.0
    composite_criticality: float = 0.0
    cascade_risk: float = 0.0
    role_label: str = "peripheral"
    priority_tier: str = "standard"


@dataclass
class GraphEdge:
    """A weighted relationship between two staff members."""
    source_id: str
    target_id: str
    weight: float = 0.0
    edge_type_weights: Dict[str, float] = field(default_factory=dict)
    # Tracks accumulated weight per type: {"shift_cowork": 0.84, "swap_pickup": 0.30}
    last_interaction_day: int = 0


# ---------------------------------------------------------------------------
# Centrality algorithms (inline, no external deps)
# ---------------------------------------------------------------------------

def _build_adjacency(
    nodes: Dict[str, StaffNode],
    edges: Dict[Tuple[str, str], GraphEdge],
) -> Dict[str, Dict[str, float]]:
    """
    Build undirected weighted adjacency dict from edges.
    Only includes active nodes.
    """
    adj: Dict[str, Dict[str, float]] = defaultdict(dict)
    active_ids = {sid for sid, n in nodes.items() if n.is_active}

    for (s, t), edge in edges.items():
        if s in active_ids and t in active_ids and edge.weight > 0:
            # Undirected: add both directions
            adj[s][t] = edge.weight
            adj[t][s] = edge.weight

    # Ensure all active nodes appear even if isolated
    for sid in active_ids:
        if sid not in adj:
            adj[sid] = {}

    return dict(adj)


def _compute_degree_centrality(adj: Dict[str, Dict[str, float]]) -> Dict[str, float]:
    """Degree centrality: sum of edge weights, normalized by max possible."""
    n = len(adj)
    if n <= 1:
        return {sid: 0.0 for sid in adj}

    raw = {}
    for sid, neighbors in adj.items():
        raw[sid] = sum(neighbors.values())

    max_degree = max(raw.values()) if raw else 1.0
    if max_degree == 0:
        return {sid: 0.0 for sid in adj}

    return {sid: val / max_degree for sid, val in raw.items()}


def _compute_betweenness_centrality(adj: Dict[str, Dict[str, float]]) -> Dict[str, float]:
    """
    Brandes algorithm for weighted betweenness centrality.
    Adapted for weighted shortest paths (lower weight = stronger connection,
    so we invert: distance = 1/weight).

    For restaurant graphs (5-40 nodes), this is instantaneous.
    """
    nodes = list(adj.keys())
    n = len(nodes)
    if n <= 2:
        return {sid: 0.0 for sid in nodes}

    betweenness = {sid: 0.0 for sid in nodes}

    for source in nodes:
        # Dijkstra-based single-source shortest paths
        stack = []
        predecessors = {sid: [] for sid in nodes}
        sigma = {sid: 0.0 for sid in nodes}  # number of shortest paths
        sigma[source] = 1.0
        dist = {sid: float("inf") for sid in nodes}
        dist[source] = 0.0

        # Priority queue as sorted list (fine for tiny graphs)
        queue = [(0.0, source)]
        visited = set()

        while queue:
            # Pop minimum distance node
            queue.sort(key=lambda x: x[0])
            d_v, v = queue.pop(0)

            if v in visited:
                continue
            visited.add(v)
            stack.append(v)

            for w, edge_weight in adj.get(v, {}).items():
                if edge_weight <= 0:
                    continue
                # Distance = inverse of weight (strong connection = short path)
                edge_dist = 1.0 / edge_weight
                new_dist = d_v + edge_dist

                if new_dist < dist[w] - 1e-10:
                    dist[w] = new_dist
                    sigma[w] = sigma[v]
                    predecessors[w] = [v]
                    queue.append((new_dist, w))
                elif abs(new_dist - dist[w]) < 1e-10:
                    sigma[w] += sigma[v]
                    predecessors[w].append(v)

        # Back-propagation
        delta = {sid: 0.0 for sid in nodes}
        while stack:
            w = stack.pop()
            for v in predecessors[w]:
                if sigma[w] > 0:
                    delta[v] += (sigma[v] / sigma[w]) * (1.0 + delta[w])
            if w != source:
                betweenness[w] += delta[w]

    # Normalize
    max_bc = max(betweenness.values()) if betweenness else 1.0
    if max_bc > 0:
        betweenness = {sid: val / max_bc for sid, val in betweenness.items()}

    return betweenness


def _compute_eigenvector_centrality(
    adj: Dict[str, Dict[str, float]],
    max_iter: int = 100,
    tolerance: float = 1e-6,
) -> Dict[str, float]:
    """
    Power iteration for eigenvector centrality.
    Weighted: a node connected to high-centrality nodes via strong edges
    gets higher centrality.
    """
    nodes = list(adj.keys())
    n = len(nodes)
    if n == 0:
        return {}
    if n == 1:
        return {nodes[0]: 1.0}

    # Initialize uniformly
    score = {sid: 1.0 / n for sid in nodes}

    for _ in range(max_iter):
        new_score = {}
        for sid in nodes:
            total = 0.0
            for neighbor, weight in adj.get(sid, {}).items():
                total += weight * score.get(neighbor, 0.0)
            new_score[sid] = total

        # Normalize
        norm = math.sqrt(sum(v * v for v in new_score.values()))
        if norm > 0:
            new_score = {sid: val / norm for sid, val in new_score.items()}

        # Check convergence
        diff = sum(abs(new_score[sid] - score[sid]) for sid in nodes)
        score = new_score
        if diff < tolerance:
            break

    # Normalize to [0, 1]
    max_val = max(score.values()) if score else 1.0
    if max_val > 0:
        score = {sid: val / max_val for sid, val in score.items()}

    return score


# ---------------------------------------------------------------------------
# Role classification
# ---------------------------------------------------------------------------

def _classify_role(
    betweenness: float,
    eigenvector: float,
    degree: float,
) -> str:
    """
    Classify staff role based on centrality metric patterns.

    glue_person:  High in BOTH betweenness AND eigenvector
                  (connected to important people AND bridges groups)
    bridge:       High betweenness, lower eigenvector
                  (connects otherwise disconnected clusters)
    hub:          High eigenvector + degree, lower betweenness
                  (popular within their cluster)
    peripheral:   Low across the board
    """
    high_bc = betweenness >= 0.5
    high_ev = eigenvector >= 0.5
    high_deg = degree >= 0.4

    if high_bc and high_ev:
        return "glue_person"
    if high_bc:
        return "bridge"
    if high_ev or high_deg:
        return "hub"
    return "peripheral"


def _classify_priority_tier(retention_score: float) -> str:
    """Map retention score to priority tier."""
    if retention_score >= 0.75:
        return "critical"
    if retention_score >= 0.50:
        return "important"
    if retention_score >= 0.25:
        return "standard"
    return "low"


# ---------------------------------------------------------------------------
# Main graph class
# ---------------------------------------------------------------------------

class StaffGraph:
    """
    Social network graph for one restaurant's staff.

    Maintains nodes (staff members) and weighted edges (inferred relationships).
    Edges accumulate strength from daily pairwise events and decay without
    reinforcement.
    """

    def __init__(
        self,
        restaurant_id: int,
        decay_rate: float = 0.02,
        min_edge_weight: float = 0.05,
    ):
        """
        Parameters
        ----------
        restaurant_id : int
        decay_rate : float
            Fraction of edge weight lost per day without interaction.
            At 0.02, an unreinforced edge loses ~45% strength in 30 days.
        min_edge_weight : float
            Edges decayed below this threshold are pruned.
        """
        self.restaurant_id = restaurant_id
        self.decay_rate = decay_rate
        self.min_edge_weight = min_edge_weight

        self.nodes: Dict[str, StaffNode] = {}
        # Edges keyed by tuple(sorted(source, target)) for undirected storage
        # Directed events still accumulate into the same edge — directionality
        # is tracked in edge_type_weights for analytics, but the graph itself
        # is undirected for centrality computation.
        self.edges: Dict[Tuple[str, str], GraphEdge] = {}

        self._centrality_stale: bool = True
        self._last_centrality_day: int = -1
        self._current_day: int = 0

    # ------------------------------------------------------------------
    # Node management
    # ------------------------------------------------------------------

    def add_node(self, staff_id: str, persona: str) -> None:
        """Register a new staff member in the graph."""
        if staff_id not in self.nodes:
            self.nodes[staff_id] = StaffNode(staff_id=staff_id, persona=persona)
            self._centrality_stale = True

    def remove_node(self, staff_id: str) -> None:
        """
        Mark a staff member as inactive (exited).
        Connected edges are removed. Centrality metrics marked stale.
        """
        if staff_id in self.nodes:
            self.nodes[staff_id].is_active = False
            self._centrality_stale = True

        # Remove all edges touching this node
        to_remove = [
            key for key in self.edges
            if staff_id in key
        ]
        for key in to_remove:
            del self.edges[key]

    def update_node_state(
        self,
        staff_id: str,
        *,
        persona: Optional[str] = None,
        tenure_days: Optional[int] = None,
        current_mood: Optional[int] = None,
        rolling_mood: Optional[float] = None,
        rolling_safe_rate: Optional[float] = None,
        rolling_fair_rate: Optional[float] = None,
        rolling_respected_rate: Optional[float] = None,
    ) -> None:
        """Update a node's metadata without triggering centrality recompute."""
        node = self.nodes.get(staff_id)
        if node is None:
            return
        if persona is not None:
            node.persona = persona
        if tenure_days is not None:
            node.tenure_days = tenure_days
        if current_mood is not None:
            node.current_mood = current_mood
        if rolling_mood is not None:
            node.rolling_mood = rolling_mood
        if rolling_safe_rate is not None:
            node.rolling_safe_rate = rolling_safe_rate
        if rolling_fair_rate is not None:
            node.rolling_fair_rate = rolling_fair_rate
        if rolling_respected_rate is not None:
            node.rolling_respected_rate = rolling_respected_rate

    # ------------------------------------------------------------------
    # Edge management
    # ------------------------------------------------------------------

    def _edge_key(self, id_a: str, id_b: str) -> Tuple[str, str]:
        """Canonical edge key: sorted tuple for undirected storage."""
        return tuple(sorted([id_a, id_b]))

    def _get_or_create_edge(self, id_a: str, id_b: str) -> GraphEdge:
        """Get existing edge or create new one."""
        key = self._edge_key(id_a, id_b)
        if key not in self.edges:
            self.edges[key] = GraphEdge(source_id=key[0], target_id=key[1])
        return self.edges[key]

    # ------------------------------------------------------------------
    # Daily update cycle
    # ------------------------------------------------------------------

    def update_daily(
        self,
        day_index: int,
        pairwise_events: List[Dict[str, Any]],
    ) -> None:
        """
        Ingest one day of pairwise events and update edge weights.

        Steps:
        1. Decay all existing edge weights
        2. Prune edges below min_edge_weight
        3. Add weight from today's pairwise events
        4. Mark centrality as stale
        """
        self._current_day = day_index

        # Step 1: Decay all edges
        for edge in list(self.edges.values()):
            days_since = day_index - edge.last_interaction_day
            if days_since > 0:
                decay_factor = (1.0 - self.decay_rate) ** days_since
                edge.weight *= decay_factor
                # Decay per-type weights too
                for etype in edge.edge_type_weights:
                    edge.edge_type_weights[etype] *= decay_factor

        # Step 2: Prune weak edges
        to_prune = [
            key for key, edge in self.edges.items()
            if edge.weight < self.min_edge_weight
        ]
        for key in to_prune:
            del self.edges[key]

        # Step 3: Accumulate from today's events
        for event in pairwise_events:
            source_id = event["source_id"]
            target_id = event["target_id"]
            weight = event["weight"]
            event_type = event["event_type"]

            # Skip events involving inactive or unknown nodes
            if source_id not in self.nodes or target_id not in self.nodes:
                continue
            if not self.nodes[source_id].is_active or not self.nodes[target_id].is_active:
                continue

            edge = self._get_or_create_edge(source_id, target_id)
            edge.weight += weight
            edge.edge_type_weights[event_type] = (
                edge.edge_type_weights.get(event_type, 0.0) + weight
            )
            edge.last_interaction_day = day_index

        # Step 4: Mark stale
        self._centrality_stale = True

    # ------------------------------------------------------------------
    # Centrality computation
    # ------------------------------------------------------------------

    def compute_centrality(self) -> None:
        """
        Calculate centrality metrics for all active nodes.
        Updates node objects in-place.
        """
        adj = _build_adjacency(self.nodes, self.edges)

        if not adj:
            self._centrality_stale = False
            return

        bc = _compute_betweenness_centrality(adj)
        ec = _compute_eigenvector_centrality(adj)
        dc = _compute_degree_centrality(adj)

        for sid, node in self.nodes.items():
            if not node.is_active:
                continue

            node.betweenness_centrality = bc.get(sid, 0.0)
            node.eigenvector_centrality = ec.get(sid, 0.0)
            node.degree_centrality = dc.get(sid, 0.0)

            # Composite criticality: betweenness weighted highest
            # because the "bridge" person is the most damaging single
            # point of failure in a restaurant team
            node.composite_criticality = (
                0.45 * node.betweenness_centrality +
                0.35 * node.eigenvector_centrality +
                0.20 * node.degree_centrality
            )

            node.role_label = _classify_role(
                node.betweenness_centrality,
                node.eigenvector_centrality,
                node.degree_centrality,
            )

        self._centrality_stale = False
        self._last_centrality_day = self._current_day

    def ensure_centrality(self) -> None:
        """Recompute centrality only if stale."""
        if self._centrality_stale:
            self.compute_centrality()

    # ------------------------------------------------------------------
    # Cascade simulation
    # ------------------------------------------------------------------

    def simulate_cascade(
        self,
        removed_staff_id: str,
        iterations: int = 200,
        max_hops: int = 3,
    ) -> Dict[str, Any]:
        """
        Monte Carlo simulation: "If we lose this person, what happens?"

        For each iteration:
        1. Remove the target node
        2. For each connected neighbor, compute elevated exit probability
        3. Propagate to neighbors of those who exit (up to max_hops)
        4. Record total cascade size

        Returns a visualization-ready cascade report with before/after
        node states for frontend animation.
        """
        self.ensure_centrality()

        target_node = self.nodes.get(removed_staff_id)
        if target_node is None or not target_node.is_active:
            return self._empty_cascade(removed_staff_id)

        # Build adjacency for simulation
        adj = _build_adjacency(self.nodes, self.edges)

        # Get neighbors and edge weights for the removed node
        target_neighbors = adj.get(removed_staff_id, {})
        if not target_neighbors:
            return self._empty_cascade(removed_staff_id)

        # Track cascade outcomes across iterations
        exit_counts = []  # total additional exits per iteration
        staff_exit_freq: Dict[str, int] = defaultdict(int)  # how often each person exits
        staff_hop_sum: Dict[str, int] = defaultdict(int)  # sum of hop distances

        for iteration in range(iterations):
            exited_this_run = set()
            frontier = [(removed_staff_id, 0)]  # (exited_id, hop)
            visited_sources = {removed_staff_id}

            while frontier:
                exited_id, hop = frontier.pop(0)
                if hop >= max_hops:
                    continue

                # Get neighbors of the person who just exited
                neighbors = adj.get(exited_id, {})

                for neighbor_id, edge_weight in neighbors.items():
                    if neighbor_id in exited_this_run or neighbor_id == removed_staff_id:
                        continue
                    if neighbor_id in visited_sources:
                        continue

                    neighbor_node = self.nodes.get(neighbor_id)
                    if neighbor_node is None or not neighbor_node.is_active:
                        continue

                    # Calculate follow probability
                    follow_prob = self._cascade_follow_probability(
                        neighbor_node=neighbor_node,
                        edge_weight=edge_weight,
                        hop=hop + 1,
                    )

                    # Deterministic roll per iteration + neighbor
                    roll = _det_float(
                        f"cascade:{self.restaurant_id}:{removed_staff_id}"
                        f":{neighbor_id}:{iteration}:{hop}"
                    )

                    if roll < follow_prob:
                        exited_this_run.add(neighbor_id)
                        staff_exit_freq[neighbor_id] += 1
                        staff_hop_sum[neighbor_id] += (hop + 1)
                        frontier.append((neighbor_id, hop + 1))

                    visited_sources.add(neighbor_id)

            exit_counts.append(len(exited_this_run))

        # Aggregate results
        exit_counts.sort()
        mean_exits = sum(exit_counts) / len(exit_counts) if exit_counts else 0.0
        p95_index = min(len(exit_counts) - 1, int(len(exit_counts) * 0.95))
        worst_case = exit_counts[p95_index] if exit_counts else 0

        # Severity classification
        if mean_exits >= 3.0:
            severity = "critical"
        elif mean_exits >= 1.5:
            severity = "high"
        elif mean_exits >= 0.5:
            severity = "moderate"
        else:
            severity = "low"

        # Build per-staff follow data for visualization
        at_risk_staff = []
        for sid, freq in sorted(staff_exit_freq.items(), key=lambda x: -x[1]):
            node = self.nodes[sid]
            follow_probability = freq / iterations
            avg_hop = staff_hop_sum[sid] / freq if freq > 0 else 0
            at_risk_staff.append({
                "staff_id": sid,
                "persona": node.persona,
                "follow_probability": round(follow_probability, 3),
                "avg_ripple_hop": round(avg_hop, 1),
                "current_mood": node.current_mood,
                "tenure_days": node.tenure_days,
                # Visualization: color by ripple order
                "ripple_color": CASCADE_RIPPLE_COLORS.get(
                    round(avg_hop), CASCADE_RIPPLE_COLORS[3]
                ),
            })

        # Store cascade risk on the target node
        target_node.cascade_risk = mean_exits

        # Build before/after snapshot for frontend animation
        node_states_before = {}
        node_states_after = {}

        for sid, node in self.nodes.items():
            if not node.is_active:
                continue

            # BEFORE state: current reality
            node_states_before[sid] = {
                "mood_color": MOOD_COLORS.get(node.current_mood, "#9CA3AF"),
                "tier_color": TIER_COLORS.get(node.priority_tier, "#9CA3AF"),
                "size_factor": 0.5 + node.composite_criticality * 0.5,
                "opacity": 1.0,
            }

            # AFTER state: what changes if target leaves
            if sid == removed_staff_id:
                node_states_after[sid] = {
                    "mood_color": "#1F2937",    # gray-800 (gone)
                    "tier_color": "#1F2937",
                    "size_factor": 0.3,
                    "opacity": 0.25,            # faded out
                }
            elif sid in staff_exit_freq:
                follow_prob = staff_exit_freq[sid] / iterations
                avg_hop = staff_hop_sum[sid] / staff_exit_freq[sid]
                # Shift color toward red proportional to follow probability
                node_states_after[sid] = {
                    "mood_color": self._blend_to_red(
                        MOOD_COLORS.get(node.current_mood, "#9CA3AF"),
                        follow_prob,
                    ),
                    "tier_color": CASCADE_RIPPLE_COLORS.get(
                        round(avg_hop), "#FDE68A"
                    ),
                    "size_factor": 0.5 + node.composite_criticality * 0.5,
                    "opacity": max(0.4, 1.0 - follow_prob * 0.5),
                    "shake_intensity": follow_prob,  # frontend can use for animation
                }
            else:
                # Unaffected staff
                node_states_after[sid] = node_states_before[sid].copy()

        return {
            "removed_staff_id": removed_staff_id,
            "removed_persona": target_node.persona,
            "removed_criticality": round(target_node.composite_criticality, 3),
            "removed_role": target_node.role_label,
            "expected_additional_exits": round(mean_exits, 2),
            "worst_case_exits": worst_case,
            "cascade_severity": severity,
            "severity_color": TIER_COLORS.get(severity, "#9CA3AF"),
            "at_risk_staff": at_risk_staff[:10],  # top 10 most at-risk
            # Visualization animation data
            "node_states_before": node_states_before,
            "node_states_after": node_states_after,
            # Edge changes: edges connected to removed node fade out
            "removed_edges": [
                self._edge_key(removed_staff_id, sid)
                for sid in adj.get(removed_staff_id, {})
            ],
        }

    def _cascade_follow_probability(
        self,
        neighbor_node: StaffNode,
        edge_weight: float,
        hop: int,
    ) -> float:
        """
        Probability that a neighbor follows when a connected person exits.

        Factors:
        - Edge weight (stronger connection = more impact)
        - Neighbor's current mood (low mood = more vulnerable)
        - Neighbor's persona sensitivity
        - Hop distance (signal attenuates with each hop)
        """
        # Base: edge weight normalized (typical strong edge ~2-4)
        base = min(1.0, edge_weight / 4.0) * 0.3

        # Mood vulnerability: low mood = more susceptible
        # mood=1 -> 1.6x, mood=3 -> 1.0x, mood=5 -> 0.4x
        mood_factor = 1.0 + (3.0 - neighbor_node.rolling_mood) * 0.3

        # Persona sensitivity to team disruption
        persona_sensitivity = {
            "social_glue":          1.4,
            "burned_idealist":      1.3,
            "emerging_leader":      1.2,
            "enthusiastic_rookie":  1.1,
            "overwhelmed_rookie":   1.1,
            "snarky_rookie":        0.9,
            "flight_risk_veteran":  1.3,
            "ghoster_in_training":  0.8,
            "lazy_rookie":          0.7,
            "workhorse":            0.6,
            "quiet_pro":            0.5,
            "cynical_anchor":       0.3,
        }.get(neighbor_node.persona, 0.8)

        # Hop attenuation: signal weakens with distance
        # hop=1: 1.0x, hop=2: 0.5x, hop=3: 0.25x
        hop_factor = 1.0 / (2 ** (hop - 1))

        prob = base * mood_factor * persona_sensitivity * hop_factor
        return min(0.5, max(0.0, prob))  # cap at 50% per neighbor per hop

    def _empty_cascade(self, staff_id: str) -> Dict[str, Any]:
        """Return an empty cascade result for isolated/unknown nodes."""
        return {
            "removed_staff_id": staff_id,
            "removed_persona": "unknown",
            "removed_criticality": 0.0,
            "removed_role": "peripheral",
            "expected_additional_exits": 0.0,
            "worst_case_exits": 0,
            "cascade_severity": "low",
            "severity_color": TIER_COLORS["low"],
            "at_risk_staff": [],
            "node_states_before": {},
            "node_states_after": {},
            "removed_edges": [],
        }

    @staticmethod
    def _blend_to_red(hex_color: str, intensity: float) -> str:
        """
        Blend a hex color toward red (#DC2626) by intensity (0-1).
        Used for cascade visualization: as follow_probability increases,
        the node color shifts toward red.
        """
        # Parse hex
        hex_color = hex_color.lstrip("#")
        r1 = int(hex_color[0:2], 16)
        g1 = int(hex_color[2:4], 16)
        b1 = int(hex_color[4:6], 16)

        # Target: red-600
        r2, g2, b2 = 220, 38, 38

        # Blend
        intensity = min(1.0, max(0.0, intensity))
        r = int(r1 + (r2 - r1) * intensity)
        g = int(g1 + (g2 - g1) * intensity)
        b = int(b1 + (b2 - b1) * intensity)

        return f"#{r:02X}{g:02X}{b:02X}"

    # ------------------------------------------------------------------
    # Criticality ranking
    # ------------------------------------------------------------------

    def get_criticality_ranking(self) -> List[Dict[str, Any]]:
        """
        Rank all active staff by retention importance.

        Combines graph criticality, cascade risk, flight risk signals,
        and replacement difficulty into a single retention score.
        """
        self.ensure_centrality()

        rankings = []
        active_nodes = [n for n in self.nodes.values() if n.is_active]

        if not active_nodes:
            return []

        # Compute cascade risk for all nodes (if not already done)
        for node in active_nodes:
            if node.cascade_risk == 0.0 and node.composite_criticality > 0.3:
                cascade = self.simulate_cascade(node.staff_id, iterations=50)
                node.cascade_risk = cascade["expected_additional_exits"]

        # Normalize cascade risk
        max_cascade = max((n.cascade_risk for n in active_nodes), default=1.0)
        if max_cascade == 0:
            max_cascade = 1.0

        for node in active_nodes:
            cascade_norm = node.cascade_risk / max_cascade

            # Flight risk: inverse of mood + persona risk profile
            flight_risk = max(0.0, (3.5 - node.rolling_mood) / 3.5)
            if node.persona in ("flight_risk_veteran", "burned_idealist", "ghoster_in_training"):
                flight_risk = min(1.0, flight_risk * 1.5)

            # Replacement difficulty: tenure-based (experienced staff are harder to replace)
            replacement_diff = min(1.0, node.tenure_days / 365.0)
            if node.persona in ("quiet_pro", "workhorse", "emerging_leader"):
                replacement_diff = min(1.0, replacement_diff * 1.3)

            retention_score = (
                0.30 * cascade_norm +
                0.25 * node.composite_criticality +
                0.25 * flight_risk +
                0.20 * replacement_diff
            )

            node.priority_tier = _classify_priority_tier(retention_score)

            rankings.append({
                "staff_id": node.staff_id,
                "persona": node.persona,
                "retention_score": round(retention_score, 3),
                "priority_tier": node.priority_tier,
                "tier_color": TIER_COLORS[node.priority_tier],
                "cascade_risk": round(node.cascade_risk, 2),
                "graph_criticality": round(node.composite_criticality, 3),
                "flight_risk": round(flight_risk, 3),
                "replacement_difficulty": round(replacement_diff, 3),
                "role_label": node.role_label,
                "role_icon": ROLE_ICONS.get(node.role_label, "circle"),
                "current_mood": node.current_mood,
                "tenure_days": node.tenure_days,
                "mood_color": MOOD_COLORS.get(node.current_mood, "#9CA3AF"),
            })

        rankings.sort(key=lambda x: -x["retention_score"])
        return rankings

    # ------------------------------------------------------------------
    # Graph snapshot for visualization
    # ------------------------------------------------------------------

    def get_graph_snapshot(self, day_index: Optional[int] = None) -> Dict[str, Any]:
        """
        Export current graph state for frontend visualization.

        The snapshot contains everything the frontend needs to render
        an interactive network graph:
        - Nodes with size, color, label, and all metrics
        - Edges with thickness, color, and type
        - Global metadata for header/stats display

        Frontend rendering notes:
        - Use node.size_factor for relative node radius
        - Use node.mood_color for fill, node.tier_color for border/ring
        - Use edge.thickness for stroke width
        - Use edge.color for stroke color
        - Tooltip: show persona, mood, tenure, role_label, criticality
        """
        self.ensure_centrality()

        day = day_index if day_index is not None else self._current_day
        active_nodes = [n for n in self.nodes.values() if n.is_active]

        # Build node list
        viz_nodes = []
        for node in active_nodes:
            viz_nodes.append({
                "staff_id": node.staff_id,
                "persona": node.persona,
                "tenure_days": node.tenure_days,

                # Current state
                "current_mood": node.current_mood,
                "rolling_mood": round(node.rolling_mood, 2),

                # Graph metrics
                "composite_criticality": round(node.composite_criticality, 3),
                "betweenness": round(node.betweenness_centrality, 3),
                "eigenvector": round(node.eigenvector_centrality, 3),
                "degree": round(node.degree_centrality, 3),
                "cascade_risk": round(node.cascade_risk, 2),
                "role_label": node.role_label,
                "priority_tier": node.priority_tier,

                # Visualization directives
                "size_factor": round(0.4 + node.composite_criticality * 0.6, 3),
                "mood_color": MOOD_COLORS.get(node.current_mood, "#9CA3AF"),
                "tier_color": TIER_COLORS.get(node.priority_tier, "#9CA3AF"),
                "role_icon": ROLE_ICONS.get(node.role_label, "circle"),
            })

        # Build edge list
        viz_edges = []
        for (s, t), edge in self.edges.items():
            if edge.weight < self.min_edge_weight:
                continue

            # Determine primary edge type (highest accumulated weight)
            primary_type = "shift_cowork"
            if edge.edge_type_weights:
                primary_type = max(edge.edge_type_weights, key=edge.edge_type_weights.get)

            viz_edges.append({
                "source": s,
                "target": t,
                "weight": round(edge.weight, 3),
                "primary_type": primary_type,

                # Visualization directives
                "thickness": round(min(6.0, 0.5 + edge.weight * 1.5), 2),
                "color": EDGE_COLORS.get(primary_type, "#94A3B8"),
                "opacity": round(min(1.0, 0.3 + edge.weight * 0.2), 2),
            })

        # Global stats
        n_active = len(active_nodes)
        max_possible_edges = n_active * (n_active - 1) / 2 if n_active > 1 else 1
        density = len(viz_edges) / max_possible_edges if max_possible_edges > 0 else 0

        criticalities = [n["composite_criticality"] for n in viz_nodes]
        avg_crit = sum(criticalities) / len(criticalities) if criticalities else 0

        moods = [n["current_mood"] for n in viz_nodes]
        avg_mood = sum(moods) / len(moods) if moods else 3.0

        return {
            "nodes": viz_nodes,
            "edges": viz_edges,
            "metadata": {
                "restaurant_id": self.restaurant_id,
                "day_index": day,
                "active_staff_count": n_active,
                "edge_count": len(viz_edges),
                "graph_density": round(density, 3),
                "avg_criticality": round(avg_crit, 3),
                "avg_mood": round(avg_mood, 2),
            },
            # Legend data for frontend rendering
            "legend": {
                "mood_colors": MOOD_COLORS,
                "tier_colors": TIER_COLORS,
                "edge_colors": EDGE_COLORS,
                "role_icons": ROLE_ICONS,
                "cascade_ripple_colors": CASCADE_RIPPLE_COLORS,
            },
        }

    # ------------------------------------------------------------------
    # Analytics helpers
    # ------------------------------------------------------------------

    def get_edge_between(self, id_a: str, id_b: str) -> Optional[Dict[str, Any]]:
        """Return edge data between two staff, or None if no connection."""
        key = self._edge_key(id_a, id_b)
        edge = self.edges.get(key)
        if edge is None:
            return None
        return {
            "source": edge.source_id,
            "target": edge.target_id,
            "weight": round(edge.weight, 3),
            "edge_types": {k: round(v, 3) for k, v in edge.edge_type_weights.items()},
            "last_interaction_day": edge.last_interaction_day,
        }

    def get_node_neighbors(self, staff_id: str) -> List[Dict[str, Any]]:
        """Return all neighbors of a staff member with edge info."""
        neighbors = []
        for (s, t), edge in self.edges.items():
            other = None
            if s == staff_id:
                other = t
            elif t == staff_id:
                other = s
            if other is None:
                continue

            node = self.nodes.get(other)
            if node is None or not node.is_active:
                continue

            primary_type = "shift_cowork"
            if edge.edge_type_weights:
                primary_type = max(edge.edge_type_weights, key=edge.edge_type_weights.get)

            neighbors.append({
                "staff_id": other,
                "persona": node.persona,
                "edge_weight": round(edge.weight, 3),
                "primary_type": primary_type,
                "mood": node.current_mood,
            })

        neighbors.sort(key=lambda x: -x["edge_weight"])
        return neighbors

    @property
    def active_count(self) -> int:
        """Number of currently active staff in the graph."""
        return sum(1 for n in self.nodes.values() if n.is_active)