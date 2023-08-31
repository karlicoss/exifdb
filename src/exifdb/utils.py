from functools import lru_cache

import timezonefinder


# cache since it should be a singleton
@lru_cache(None)
def timezone_finder(fast: bool):
    if fast:
        # less precise, but faster
        return timezonefinder.TimezoneFinderL(in_memory=True)
    else:
        return timezonefinder.TimezoneFinder(in_memory=True)
