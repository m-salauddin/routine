# academic/views.py
from django.shortcuts import render
from django.db import transaction
from django.http import HttpResponse
from django.contrib.auth import get_user_model
from django.core import serializers
import json
import tablib

from rest_framework import viewsets, status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.parsers import MultiPartParser, FormParser

# --- NEW: Swagger Imports ---
from drf_yasg.utils import swagger_auto_schema
from drf_yasg import openapi

from .admin import CourseResource, RoomResource, DepartmentResource, BatchResource, RoutineEntryResource, SemesterResource, UserResource
from user_api.admin import UserResource

from .models import (
    Department, Semester, Course, TimeSlot, RoutineEntry, Room, 
    RoomType, RoomSubType, Day, BatchTimeConstraint, SystemBackup, Batch
)
from .utils import generate_routine_algorithm, rollback_routine_algorithm
from .serializers import (
    DepartmentSerializer, SemesterSerializer, CourseSerializer, 
    TimeSlotSerializer, RoutineEntrySerializer, RoomSerializer
)

from user_api.permissions import IsAdminUser

User = get_user_model()

# ==============================================================================
# ROUTINE CONFLICT CHECKER (Helper Function)
# ==============================================================================
def check_routine_conflict(day_id, time_slot_id, room_id, course, exclude_entry_ids=None):
    base_query = RoutineEntry.objects.filter(
        day_id=day_id, 
        time_slot_id=time_slot_id, 
        is_active=True,
        is_cancelled=False
    )
    
    if exclude_entry_ids:
        base_query = base_query.exclude(id__in=exclude_entry_ids)

    if base_query.filter(room_id=room_id).exists():
        return "Error: Ei somoy ei Room ti already booked!"

    if course.teacher and base_query.filter(course__teacher=course.teacher).exists():
        return f"Error: {course.teacher.username} sir er ei somoy onno ekta class ase!"

    if base_query.filter(course__department=course.department, course__semester=course.semester).exists():
        return f"Error: Ei batch er students der ei somoy already arekta class ase!"

    return None

# ==============================================================================
# Model ViewSets
# ==============================================================================
class DepartmentViewSet(viewsets.ModelViewSet):
    queryset = Department.objects.filter(is_active=True)
    serializer_class = DepartmentSerializer
    permission_classes = [IsAdminUser]

class SemesterViewSet(viewsets.ModelViewSet):
    queryset = Semester.objects.filter(is_active=True).order_by('order')
    serializer_class = SemesterSerializer
    permission_classes = [IsAdminUser]

class CourseViewSet(viewsets.ModelViewSet):
    queryset = Course.objects.filter(is_active=True)
    serializer_class = CourseSerializer
    permission_classes = [IsAdminUser]

class TimeSlotViewSet(viewsets.ModelViewSet):
    queryset = TimeSlot.objects.all().order_by('start_time')
    serializer_class = TimeSlotSerializer
    permission_classes = [IsAdminUser]

class RoomViewSet(viewsets.ModelViewSet):
    queryset = Room.objects.filter(is_active=True)
    serializer_class = RoomSerializer
    permission_classes = [IsAdminUser]


# ==============================================================================
# ROUTINE GENERATION & MANAGEMENT APIs
# ==============================================================================
class GenerateRoutineView(APIView):
    permission_classes = [IsAdminUser]
    
    @swagger_auto_schema(
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            required=['department_id'],
            properties={
                'department_id': openapi.Schema(type=openapi.TYPE_INTEGER, description='Department ID'),
                'semester_id': openapi.Schema(type=openapi.TYPE_INTEGER, description='Semester ID (Optional)'),
                'ignore_warnings': openapi.Schema(type=openapi.TYPE_BOOLEAN, description='Ignore warnings? (Optional)'),
            }
        )
    )
    def post(self, request):
        department_id = request.data.get('department_id')
        semester_id = request.data.get('semester_id')
        ignore_warnings = request.data.get('ignore_warnings', False)
        
        if isinstance(ignore_warnings, str):
            ignore_warnings = ignore_warnings.lower() == 'true'
        
        if not department_id:
            return Response({"status": "error", "message": "department_id is required."}, status=status.HTTP_400_BAD_REQUEST)
            
        try:
            department = Department.objects.get(id=department_id, is_active=True)
            if semester_id:
                Semester.objects.get(id=semester_id, is_active=True)
            
            result = generate_routine_algorithm(
                department_id=department.id, 
                semester_id=semester_id, 
                ignore_warnings=ignore_warnings
            )
            
            if result.get("status") == "Warning":
                return Response(result, status=status.HTTP_409_CONFLICT)
            elif result.get("status") == "Locked":
                return Response(result, status=status.HTTP_403_FORBIDDEN)
            elif result.get("status") == "Error":
                return Response(result, status=status.HTTP_400_BAD_REQUEST)
            else:
                return Response(result, status=status.HTTP_200_OK)
            
        except Department.DoesNotExist:
            return Response({"status": "error", "message": "Department not found or is inactive."}, status=status.HTTP_404_NOT_FOUND)
        except Semester.DoesNotExist:
            return Response({"status": "error", "message": "Semester not found or is inactive."}, status=status.HTTP_404_NOT_FOUND)
        except Exception as e:
            return Response({"status": "error", "message": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class RollbackRoutineView(APIView):
    permission_classes = [IsAdminUser]
    
    @swagger_auto_schema(
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            required=['department_id'],
            properties={
                'department_id': openapi.Schema(type=openapi.TYPE_INTEGER, description='Department ID'),
            }
        )
    )
    def post(self, request):
        department_id = request.data.get('department_id')
        if not department_id:
            return Response({"status": "error", "message": "department_id is required."}, status=status.HTTP_400_BAD_REQUEST)
            
        try:
            department = Department.objects.get(id=department_id, is_active=True)
            result = rollback_routine_algorithm(department.id)
            return Response(result)
            
        except Department.DoesNotExist:
            return Response({"status": "error", "message": "Department not found."}, status=status.HTTP_404_NOT_FOUND)
        except Exception as e:
            return Response({"status": "error", "message": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class RoutineListView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        manual_parameters=[
            openapi.Parameter('day', openapi.IN_QUERY, description="Day ID", type=openapi.TYPE_INTEGER),
            openapi.Parameter('department_id', openapi.IN_QUERY, description="Department ID", type=openapi.TYPE_INTEGER),
            openapi.Parameter('semester_id', openapi.IN_QUERY, description="Semester ID", type=openapi.TYPE_INTEGER),
        ]
    )
    def get(self, request):
        user = request.user
        queryset = RoutineEntry.objects.filter(is_active=True)
    
        if user.role == 'TEACHER':
            queryset = queryset.filter(course__teacher=user)
        elif user.role == 'STUDENT':
            if user.department and user.semester:
                queryset = queryset.filter(
                    course__department=user.department,
                    course__semester=user.semester
                )
            else:
                return Response({"error": "Student profile incomplete"}, status=status.HTTP_400_BAD_REQUEST)
      
        day = request.query_params.get('day')
        department_id = request.query_params.get('department_id')
        semester_id = request.query_params.get('semester_id')

        if day:
            queryset = queryset.filter(day_id=day)
        if department_id:
            queryset = queryset.filter(course__department_id=department_id)
        if semester_id:
            queryset = queryset.filter(course__semester_id=semester_id)

        queryset = queryset.order_by('day__order', 'time_slot__start_time')
        serializer = RoutineEntrySerializer(queryset, many=True)
        return Response(serializer.data)
    

class TeacherCancelClassView(APIView):
    permission_classes = [IsAuthenticated]
    
    @swagger_auto_schema(
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            required=['routine_id', 'cancel_message'],
            properties={
                'routine_id': openapi.Schema(type=openapi.TYPE_INTEGER),
                'cancel_message': openapi.Schema(type=openapi.TYPE_STRING),
            }
        )
    )
    def post(self, request):
        user = request.user
        if getattr(user, 'role', '') != 'TEACHER':
            return Response({"error": "Only teachers can cancel classes."}, status=status.HTTP_403_FORBIDDEN)

        routine_id = request.data.get('routine_id')
        cancel_message = request.data.get('cancel_message')

        if not routine_id or not cancel_message:
            return Response({"error": "routine_id and cancel_message are required."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            routine = RoutineEntry.objects.get(id=routine_id, is_active=True)
            if routine.course.teacher != user:
                return Response({"error": "You can only cancel your own classes."}, status=status.HTTP_403_FORBIDDEN)

            routine.is_cancelled = True
            routine.cancel_message = cancel_message
            routine.save()

            return Response({
                "status": "Success",
                "message": f"Class '{routine.course.course_name}' has been cancelled successfully."
            })
        except RoutineEntry.DoesNotExist:
            return Response({"error": "Routine entry not found or inactive."}, status=status.HTTP_404_NOT_FOUND)


class ManualRoutineUpdateView(APIView):
    @swagger_auto_schema(
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            required=['day_id', 'time_slot_id'],
            properties={
                'day_id': openapi.Schema(type=openapi.TYPE_INTEGER),
                'time_slot_id': openapi.Schema(type=openapi.TYPE_INTEGER),
                'room_id': openapi.Schema(type=openapi.TYPE_INTEGER, description="Room ID (Optional)"),
            }
        )
    )
    def put(self, request, entry_id):
        try:
            entry = RoutineEntry.objects.get(id=entry_id)
        except RoutineEntry.DoesNotExist:
            return Response({"error": "Routine entry found hoyni!"}, status=status.HTTP_404_NOT_FOUND)

        new_day_id = request.data.get('day_id', entry.day.id)
        new_time_slot_id = request.data.get('time_slot_id', entry.time_slot.id)
        new_room_id = request.data.get('room_id', entry.room.id if entry.room else None)

        conflict_msg = check_routine_conflict(new_day_id, new_time_slot_id, new_room_id, entry.course, exclude_entry_ids=[entry.id])
        
        if conflict_msg:
            return Response({"status": "error", "message": conflict_msg}, status=status.HTTP_400_BAD_REQUEST)

        entry.day_id = new_day_id
        entry.time_slot_id = new_time_slot_id
        entry.room_id = new_room_id
        entry.save()

        return Response({"status": "success", "message": "Routine successfully update kora hoyeche!"})


class RoutineSwapView(APIView):
    @swagger_auto_schema(
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            required=['entry1_id', 'entry2_id'],
            properties={
                'entry1_id': openapi.Schema(type=openapi.TYPE_INTEGER),
                'entry2_id': openapi.Schema(type=openapi.TYPE_INTEGER),
            }
        )
    )
    def post(self, request):
        entry1_id = request.data.get('entry1_id')
        entry2_id = request.data.get('entry2_id')

        if not entry1_id or not entry2_id:
            return Response({"error": "Duti routine entry er ID dite hobe."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            entry1 = RoutineEntry.objects.get(id=entry1_id)
            entry2 = RoutineEntry.objects.get(id=entry2_id)
        except RoutineEntry.DoesNotExist:
            return Response({"error": "Kono ekte routine entry pawa jayni."}, status=status.HTTP_404_NOT_FOUND)

        ignore_ids = [entry1.id, entry2.id]
        
        conflict1 = check_routine_conflict(
            day_id=entry2.day.id, time_slot_id=entry2.time_slot.id, 
            room_id=entry1.room.id if entry1.room else None, course=entry1.course, exclude_entry_ids=ignore_ids
        )
        
        conflict2 = check_routine_conflict(
            day_id=entry1.day.id, time_slot_id=entry1.time_slot.id, 
            room_id=entry2.room.id if entry2.room else None, course=entry2.course, exclude_entry_ids=ignore_ids
        )

        if conflict1 or conflict2:
            return Response({
                "status": "error", "message": "Swap kora jabe na, karon conflict ache!",
                "details": {"entry1_issue": conflict1, "entry2_issue": conflict2}
            }, status=status.HTTP_400_BAD_REQUEST)

        with transaction.atomic():
            temp_day = entry1.day
            temp_time = entry1.time_slot
            
            entry1.day = entry2.day
            entry1.time_slot = entry2.time_slot
            entry1.save()

            entry2.day = temp_day
            entry2.time_slot = temp_time
            entry2.save()

        return Response({"status": "success", "message": "Duti class er shudhu somoy successfully swap kora hoyeche!"})

# ==============================================================================
# DYNAMIC EXCEL IMPORT & EXPORT APIs (Master API)
# ==============================================================================

RESOURCE_MAP = {
    'user': UserResource,
    'course': CourseResource,
    'room': RoomResource,
    'department': DepartmentResource,
    'batch': BatchResource,
    'routine': RoutineEntryResource,
}

class ExcelImportView(APIView):
    permission_classes = [IsAdminUser]
    parser_classes = (MultiPartParser, FormParser)

    @swagger_auto_schema(
        manual_parameters=[
            openapi.Parameter('model_name', openapi.IN_FORM, description="Model Name (e.g., user, course)", type=openapi.TYPE_STRING, required=True),
            openapi.Parameter('file', openapi.IN_FORM, description="Excel File", type=openapi.TYPE_FILE, required=True),
        ]
    )
    def post(self, request):
        model_name = request.data.get('model_name')
        excel_file = request.FILES.get('file')

        if not model_name or not excel_file:
            return Response({"error": "model_name and file are required in form-data."}, status=status.HTTP_400_BAD_REQUEST)

        model_name = model_name.lower()
        if model_name not in RESOURCE_MAP:
            return Response({"error": f"Invalid model_name. Allowed options: {list(RESOURCE_MAP.keys())}"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            dataset = tablib.Dataset()
            dataset.load(excel_file.read(), format='xlsx')

            resource_class = RESOURCE_MAP[model_name]
            resource = resource_class()

            result = resource.import_data(dataset, dry_run=True)

            if result.has_errors() or result.has_validation_errors():
                error_details = [f"Row {error[0]}: {str(error[1].error)}" for error in result.row_errors()]
                error_details.extend([f"Row {invalid_row.number}: {invalid_row.error_dict}" for invalid_row in result.invalid_rows])

                return Response({
                    "status": "error", "message": "Data validation failed! Please check your Excel file.", "details": error_details
                }, status=status.HTTP_400_BAD_REQUEST)

            resource.import_data(dataset, dry_run=False)
            return Response({"status": "success", "message": f"{model_name.capitalize()} data imported successfully!"}, status=status.HTTP_200_OK)

        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

class ExcelExportView(APIView):
    permission_classes = [IsAdminUser]

    @swagger_auto_schema(
        manual_parameters=[
            openapi.Parameter('model_name', openapi.IN_QUERY, description="Model Name (e.g., user, course, routine)", type=openapi.TYPE_STRING, required=True),
        ]
    )
    def get(self, request):
        model_name = request.query_params.get('model_name')

        if not model_name:
            return Response({"error": "model_name is required as a query parameter."}, status=status.HTTP_400_BAD_REQUEST)

        model_name = model_name.lower()
        if model_name not in RESOURCE_MAP:
            return Response({"error": f"Invalid model_name. Allowed options: {list(RESOURCE_MAP.keys())}"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            resource_class = RESOURCE_MAP[model_name]
            resource = resource_class()
            dataset = resource.export()

            response = HttpResponse(dataset.xlsx, content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
            response['Content-Disposition'] = f'attachment; filename="{model_name}_backup.xlsx"'
            return response
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

# ==============================================================================
# ENTERPRISE: MULTI-SHEET EXCEL SYNC (All Tables)
# ==============================================================================
class SystemExcelSyncView(APIView):
    permission_classes = [IsAdminUser]
    # File upload er jonne parser add kora holo jeno Swagger a Choose File button ashe
    parser_classes = (MultiPartParser, FormParser)

    def get(self, request):
        try:
            databook = tablib.Databook()
            export_sequence = [
                ('Users', UserResource()), ('Departments', DepartmentResource()),
                ('Semesters', SemesterResource()), ('Rooms', RoomResource()),
                ('Batches', BatchResource()), ('Courses', CourseResource()),
                ('Routine', RoutineEntryResource())
            ]

            for sheet_name, resource in export_sequence:
                dataset = resource.export()
                dataset.title = sheet_name
                databook.add_sheet(dataset)

            response = HttpResponse(databook.xlsx, content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
            response['Content-Disposition'] = 'attachment; filename="Full_System_Backup.xlsx"'
            return response
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @swagger_auto_schema(
        manual_parameters=[
            openapi.Parameter('file', openapi.IN_FORM, description="Full System Excel Backup File", type=openapi.TYPE_FILE, required=True),
        ]
    )
    def post(self, request):
        file = request.FILES.get('file')
        if not file:
            return Response({"error": "Excel file upload kora proyojon"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            databook = tablib.Databook()
            databook.xlsx = file.read()

            import_sequence = [
                ('Users', UserResource()), ('Departments', DepartmentResource()),
                ('Semesters', SemesterResource()), ('Rooms', RoomResource()),
                ('Batches', BatchResource()), ('Courses', CourseResource()),
                ('Routine', RoutineEntryResource())
            ]

            with transaction.atomic():
                for sheet_name, resource in import_sequence:
                    try:
                        dataset = databook.get_sheet(sheet_name)
                    except (ValueError, KeyError):
                        continue

                    result = resource.import_data(dataset, dry_run=False, raise_errors=True)
                    if result.has_errors():
                        raise ValueError(f"{sheet_name} sheet er data te somossa ache.")

            return Response({"status": "success", "message": "Sob gulo sheet er data safolbhabe import hoyeche!"})
        except Exception as e:
            return Response({"error": f"Import fail hoyeche, system rollback kora hoyeche. Reason: {str(e)}"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

# ==============================================================================
# ENTERPRISE: POINT-IN-TIME BACKUP & RESTORE (JSON Snapshots)
# ==============================================================================
class SystemSnapshotView(APIView):
    permission_classes = [IsAdminUser]

    @swagger_auto_schema(
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            required=['action'],
            properties={
                'action': openapi.Schema(type=openapi.TYPE_STRING, description="Action: 'backup' or 'restore'"),
                'name': openapi.Schema(type=openapi.TYPE_STRING, description="Backup Name (if action is backup)"),
                'backup_id': openapi.Schema(type=openapi.TYPE_INTEGER, description="Backup ID (if action is restore)"),
            }
        )
    )
    def post(self, request):
        action = request.data.get('action')
        
        if action == 'backup':
            backup_name = request.data.get('name', 'Auto Backup')
            try:
                models_to_backup = [Department, Semester, Batch, Room, Course, RoutineEntry, BatchTimeConstraint]
                
                full_data = []
                for model in models_to_backup:
                    qs = model.objects.all()
                    data = json.loads(serializers.serialize('json', qs))
                    full_data.extend(data)

                backup_obj = SystemBackup.objects.create(
                    name=backup_name, backup_data=json.dumps(full_data), created_by=request.user
                )
                
                return Response({
                    "status": "success", "message": f"Snapshot '{backup_name}' created successfully!", "backup_id": backup_obj.id
                })
            except Exception as e:
                return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        elif action == 'restore':
            backup_id = request.data.get('backup_id')
            if not backup_id:
                return Response({"error": "backup_id is required for restore"}, status=status.HTTP_400_BAD_REQUEST)

            try:
                backup_obj = SystemBackup.objects.get(id=backup_id)
                objects_to_restore = list(serializers.deserialize("json", backup_obj.backup_data))
                
                with transaction.atomic():
                    RoutineEntry.objects.all().delete()
                    BatchTimeConstraint.objects.all().delete()
                    Course.objects.all().delete()
                    Batch.objects.all().delete()
                    Room.objects.all().delete()
                    Semester.objects.all().delete()
                    Department.objects.all().delete()

                    for obj in objects_to_restore:
                        obj.save()


                return Response({"status": "success", "message": "System successfully restored to previous state!"})
            except SystemBackup.DoesNotExist:
                return Response({"error": "Backup not found!"}, status=status.HTTP_404_NOT_FOUND)
            except Exception as e:
                return Response({"error": f"Restore failed, system rolled back safely. Reason: {str(e)}"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        
        return Response({"error": "Invalid action. Use 'backup' or 'restore'"}, status=status.HTTP_400_BAD_REQUEST)