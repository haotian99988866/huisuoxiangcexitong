from datetime import datetime, time, timedelta

DAY_SHIFT_START = time(6, 0)
NIGHT_SHIFT_START = time(18, 0)

# 这些会所的出勤/照片按班次窗口展示：白班 06:00-18:00，晚班 18:00-次日 06:00
SHIFT_VENUE_IDS = frozenset({1, 2, 4, 5, 6})


def current_shift_window(now=None):
    """返回 (窗口开始, 窗口结束, 班次名称)，时间均为本机本地时间。"""
    now = now or datetime.now()
    day_start = datetime.combine(now.date(), DAY_SHIFT_START)
    night_start = datetime.combine(now.date(), NIGHT_SHIFT_START)
    if day_start <= now < night_start:
        return day_start, night_start, "白班"
    if now >= night_start:
        return night_start, day_start + timedelta(days=1), "晚班"
    return night_start - timedelta(days=1), day_start, "晚班"
