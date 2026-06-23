# academic/urls.py
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    DepartmentViewSet, SemesterViewSet, CourseViewSet, SystemSnapshotView, TeacherSwapRequestView, 
    TimeSlotViewSet, RoomViewSet, GenerateRoutineView, 
    RoutineListView, RollbackRoutineView, TeacherCancelClassView ,ExcelImportView, ExcelExportView,
    ManualRoutineUpdateView, RoutineSwapView,SystemExcelSyncView,
    SystemSnapshotView
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
    path('cancel-class/', TeacherCancelClassView.as_view(), name='cancel-class'), # নতুন এন্ডপয়েন্ট
    path('view-routine/', RoutineListView.as_view(), name='view-routine'),
    path('import-excel/', ExcelImportView.as_view(), name='import-excel'),
    path('export-excel/', ExcelExportView.as_view(), name='export-excel'),
    path('routine/update/<int:entry_id>/', ManualRoutineUpdateView.as_view(), name='manual-routine-update'),
    path('routine/swap/', RoutineSwapView.as_view(), name='routine-swap'),
    path('sync/excel/', SystemExcelSyncView.as_view(), name='system-excel-sync'),
    path('sync/snapshot/', SystemSnapshotView.as_view(), name='system-snapshot'),
    path('swap-request/', TeacherSwapRequestView.as_view(), name='swap-request'),
   
 
]




