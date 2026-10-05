"""Weekly planner rollout flags, read per call so operators can toggle them.

Every flag defaults on; set the variable to ``false`` to turn one off.
"""

import os

CATALOG_PROJECTIONS = "CATALOG_PROJECTIONS_ENABLED"
CATALOG_PUBLICATION_FENCING = "CATALOG_PUBLICATION_FENCING_ENABLED"
WEEKLY_PLANNER_SHORT_GENERATION = "WEEKLY_PLANNER_SHORT_GENERATION"
CATALOG_DURABLE_PREPARATION = "CATALOG_DURABLE_PREPARATION_ENABLED"
CATALOG_PREPARATION_IN_PROCESS = "CATALOG_PREPARATION_IN_PROCESS_ENABLED"
_TRUE_VALUES = frozenset({"true", "1", "yes"})


def planner_flag_enabled(name: str) -> bool:
    return os.getenv(name, "true").strip().casefold() in _TRUE_VALUES
