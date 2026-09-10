from django.contrib import admin

from .models import (
    AgentStatus,
    BreakTime,
    CurrentActivity,
    HierarchicalLevel,
    Holiday,
    JobActivity,
    Journey,
    Location,
    NotifyEntry,
    OccurrenceType,
    OperationalOccurrence,
    OperationalAlertEvent,
    RequestType,
    Schedule,
    ScheduleRequest,
    ScheduleToday,
    StatusEvent,
    StatusType,
)

admin.site.register(StatusType)
admin.site.register(Location)
admin.site.register(JobActivity)
admin.site.register(Journey)
admin.site.register(HierarchicalLevel)
admin.site.register(BreakTime)
admin.site.register(RequestType)
admin.site.register(Schedule)
admin.site.register(ScheduleToday)
admin.site.register(AgentStatus)
admin.site.register(StatusEvent)
admin.site.register(CurrentActivity)
admin.site.register(ScheduleRequest)
admin.site.register(Holiday)
admin.site.register(NotifyEntry)
admin.site.register(OccurrenceType)
admin.site.register(OperationalOccurrence)
admin.site.register(OperationalAlertEvent)
