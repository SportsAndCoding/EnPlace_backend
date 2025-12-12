"""
modules/network_intelligence

Pattern matching engine for En Place's predictive retention system.
"""

from .pattern_matcher import (
    extract_signatures,
    score_staff_flight_risk,
    calculate_network_percentile,
    print_signature_report,
    signatures_to_dict,
    QuitterSignature,
    FlightRiskScore,
    EmotionalSignature,
    TENURE_BUCKETS,
    get_tenure_bucket,
)

__all__ = [
    "extract_signatures",
    "score_staff_flight_risk", 
    "calculate_network_percentile",
    "print_signature_report",
    "signatures_to_dict",
    "QuitterSignature",
    "FlightRiskScore",
    "EmotionalSignature",
    "TENURE_BUCKETS",
    "get_tenure_bucket",
]