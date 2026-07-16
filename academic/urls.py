# academic/urls.py
from django.urls import path, include
from rest_framework.routers import DefaultRouter


from .views import (
    AdminCancelClassView, DepartmentRoutineView, DepartmentViewSet, SemesterViewSet, CourseViewSet, SystemSettingView, SystemSnapshotView,TeacherSwapRequestView, 
    TimeSlotViewSet, RoomViewSet, GenerateRoutineView, 
    RoutineListView, RollbackRoutineView, TeacherCancelClassView ,ExcelImportView, ExcelExportView,
    ManualRoutineUpdateView, RoutineSwapView,SystemExcelSyncView,
    SystemSnapshotView,NotificationListView, UnreadNotificationCountView, MarkNotificationReadView,RecentActivityLogView, AllActivityLogView,
    HideActivityLogView,FixedClassScheduleListCreateView, FixedClassScheduleDetailView,NoticeDetailView,NoticeListCreateView
)





router = DefaultRouter()
router.register(r'departments', DepartmentViewSet)
router.register(r'semesters', SemesterViewSet)
router.register(r'courses', CourseViewSet)
router.register(r'timeslots', TimeSlotViewSet)
router.register(r'rooms', RoomViewSet) 

urlpatterns = [
    path('', include(router.urls)),
    path('generate-routine/', GenerateRoutineView.as_view(), name='generate-routine'),
    path('rollback-routine/', RollbackRoutineView.as_view(), name='rollback-routine'),
    path('cancel-class/<int:entry_id>/', TeacherCancelClassView.as_view(), name='cancel-class'),
    path('view-routine/', RoutineListView.as_view(), name='view-routine'),
    path('import-excel/', ExcelImportView.as_view(), name='import-excel'),
    path('export-excel/', ExcelExportView.as_view(), name='export-excel'),
    path('routine/update/<int:entry_id>/', ManualRoutineUpdateView.as_view(), name='manual-routine-update'),
    path('routine/swap/', RoutineSwapView.as_view(), name='routine-swap'),
    path('sync/excel/', SystemExcelSyncView.as_view(), name='system-excel-sync'),
    path('sync/snapshot/', SystemSnapshotView.as_view(), name='system-snapshot'),
    path('swap-request/', TeacherSwapRequestView.as_view(), name='swap-request'),
    path('notifications/', NotificationListView.as_view(), name='notification-list'),
    path('notifications/unread-count/', UnreadNotificationCountView.as_view(), name='notification-unread-count'),
    path('notifications/<int:pk>/mark-read/', MarkNotificationReadView.as_view(), name='notification-mark-read'),
    path('logs/recent/', RecentActivityLogView.as_view(), name='logs-recent'),
    path('logs/all/', AllActivityLogView.as_view(), name='logs-all'),
    path('logs/<int:pk>/hide/', HideActivityLogView.as_view(), name='logs-hide'),
    path('system-settings/', SystemSettingView.as_view(), name='system-settings'),
    path('department-routine/', DepartmentRoutineView.as_view(), name='department-routine'),
    path('admin/routine/<int:entry_id>/cancel/', AdminCancelClassView.as_view(), name='admin-cancel-class'),
    path('api/fixed-schedules/', FixedClassScheduleListCreateView.as_view(), name='fixed-schedules-list'),
    path('api/fixed-schedules/<int:pk>/', FixedClassScheduleDetailView.as_view(), name='fixed-schedules-detail'),
    path('notices/', NoticeListCreateView.as_view(), name='notice-list-create'),
    path('notices/<int:pk>/', NoticeDetailView.as_view(), name='notice-detail'),
 
 
]




