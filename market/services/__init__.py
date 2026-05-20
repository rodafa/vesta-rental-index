# Lazy re-export of aggregation classes so existing imports keep working:
#   from market.services import DailyMarketStatsAggregator
# These are deferred until actually accessed to avoid importing Django models
# at package-load time (before django.setup() runs).

_AGGREGATION_NAMES = {
    "DailyMarketStatsAggregator",
    "DailySegmentStatsAggregator",
    "ListingCycleTracker",
    "MonthlyMarketReportAggregator",
    "MonthlySegmentStatsAggregator",
    "PriceChangeDetector",
    "WeeklyLeasingSummaryAggregator",
}


def __getattr__(name):
    if name in _AGGREGATION_NAMES:
        from market.services import aggregation
        return getattr(aggregation, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
