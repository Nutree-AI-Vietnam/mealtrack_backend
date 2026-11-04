"""Fixed limits for writing a week's dishes with a model."""

from __future__ import annotations

# The longest a weekly_plan job's attempt may run.
#
# A week is twenty-one dishes: one naming call per meal type and one recipe
# call per slot, then every ingredient line priced. Measured end to end
# against the real model, with pricing stubbed out, six runs took 80, 90,
# 92, 100, 103 and 121 seconds — so the worker's default 90 second deadline
# killed most of them, and all four attempts would have died the same way.
#
# Real pricing is slower than the stub: roughly 150 ingredient lines per
# week, each one a food_reference read and, when that misses, a FatSecret
# call or an AI estimate. This is set well above the measured ceiling
# because the cost of being wrong is a user watching a spinner while four
# attempts burn a week of tokens each, and nothing waits on this job
# synchronously. A worker that dies outright is bounded by the lease, not
# by this.
WEEKLY_PLAN_DEADLINE_SECONDS = 420.0
