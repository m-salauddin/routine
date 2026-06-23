# academic/views.py
from django.shortcuts import render
from django.db import transaction
from django.http import HttpResponse
from django.contrib.auth import get_user_model
from django.core import serializers
import json
import tablib
import datetime

from rest_framework import viewsets, status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.parsers import MultiPartParser, FormParser

from drf_yasg.utils import swagger_auto_schema
from drf_yasg import openapi

from .admin import CourseResource, RoomResource, DepartmentResource, BatchResource, RoutineEntryResource, SemesterResource, UserResource
from user_api.admin import UserResource

from .models import (
    Department, Semester, Course, TimeSlot, RoutineEntry, Room, 
    RoomType, RoomSubType, Day, BatchTimeConstraint, SystemBackup, Batch,
    TemporarySwapRequest
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
# TEMPORARY SWAP REQUEST MANAGEMENT
# ==============================================================================
class TeacherSwapRequestView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        tags=['3. Teacher Operations'],
        operation_description="**[TEACHER ONLY]** Request a temporary class swap (PROXY or MUTUAL).",
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            required=['swap_type', 'target_teacher_id', 'requester_routine_id', 'swap_date'],
            properties={
                'swap_type': openapi.Schema(type=openapi.TYPE_STRING, description="'PROXY' or 'MUTUAL'", default='PROXY'),
                'target_teacher_id': openapi.Schema(type=openapi.TYPE_INTEGER, description="ID of the teacher you are requesting", default=1),
                'requester_routine_id': openapi.Schema(type=openapi.TYPE_INTEGER, description="Your routine ID", default=10),
                'target_routine_id': openapi.Schema(type=openapi.TYPE_INTEGER, description="Target routine ID (Required only for MUTUAL)", default=15),
                'swap_date': openapi.Schema(type=openapi.TYPE_STRING, format=openapi.FORMAT_DATE, description="Format: YYYY-MM-DD", default="2026-06-25"),
                'reason': openapi.Schema(type=openapi.TYPE_STRING, description="Reason for the swap", default="Medical Emergency"),
            }
        ),
        responses={200: "Success", 400: "Bad Request (Conflict/Errors)", 403: "Forbidden"}
    )
    def post(self, request):
        user = request.user
        if getattr(user, 'role', '') != 'TEACHER':
            return Response({"error": "Only teachers can request swaps."}, status=status.HTTP_403_FORBIDDEN)

        swap_type = request.data.get('swap_type')
        target_teacher_id = request.data.get('target_teacher_id')
        requester_routine_id = request.data.get('requester_routine_id')
        target_routine_id = request.data.get('target_routine_id')
        swap_date_str = request.data.get('swap_date')
        reason = request.data.get('reason', '')

        try:
            swap_date = datetime.datetime.strptime(swap_date_str, '%Y-%m-%d').date()
            req_routine = RoutineEntry.objects.get(id=requester_routine_id, is_active=True)
            target_teacher = User.objects.get(id=target_teacher_id, role='TEACHER')

            if req_routine.course.teacher != user:
                return Response({"error": "You can only swap your own classes."}, status=status.HTTP_403_FORBIDDEN)

            if swap_type == 'PROXY':
                conflict = RoutineEntry.objects.filter(
                    day=req_routine.day,
                    time_slot=req_routine.time_slot,
                    course__teacher=target_teacher,
                    is_active=True
                ).exists()
                if conflict:
                    return Response({"error": f"{target_teacher.username} already has a class at this time!"}, status=status.HTTP_400_BAD_REQUEST)

                req = TemporarySwapRequest.objects.create(
                    swap_type='PROXY', requester=user, target_teacher=target_teacher,
                    requester_routine=req_routine, swap_date=swap_date, reason=reason
                )

            elif swap_type == 'MUTUAL':
                if not target_routine_id:
                    return Response({"error": "target_routine_id is required for MUTUAL swap."}, status=status.HTTP_400_BAD_REQUEST)

                tgt_routine = RoutineEntry.objects.get(id=target_routine_id, is_active=True)
                if tgt_routine.course.teacher != target_teacher:
                    return Response({"error": "Target routine does not belong to the target teacher."}, status=status.HTTP_400_BAD_REQUEST)

                conflict1 = RoutineEntry.objects.filter(
                    day=tgt_routine.day, time_slot=tgt_routine.time_slot, course__teacher=user, is_active=True
                ).exists()
                conflict2 = RoutineEntry.objects.filter(
                    day=req_routine.day, time_slot=req_routine.time_slot, course__teacher=target_teacher, is_active=True
                ).exists()

                if conflict1 or conflict2:
                    return Response({"error": "Mutual swap causes a timetable conflict for one or both teachers."}, status=status.HTTP_400_BAD_REQUEST)

                req = TemporarySwapRequest.objects.create(
                    swap_type='MUTUAL', requester=user, target_teacher=target_teacher,
                    requester_routine=req_routine, target_routine=tgt_routine,
                    swap_date=swap_date, reason=reason
                )
            else:
                return Response({"error": "Invalid swap_type."}, status=status.HTTP_400_BAD_REQUEST)

            return Response({"status": "Success", "message": "Swap request sent successfully!", "request_id": req.id})

        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @swagger_auto_schema(
        tags=['3. Teacher Operations'],
        operation_description="**[TEACHER ONLY]** Accept or Reject a pending swap request.",
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            required=['request_id', 'action'],
            properties={
                'request_id': openapi.Schema(type=openapi.TYPE_INTEGER, description="ID of the swap request", default=1),
                'action': openapi.Schema(type=openapi.TYPE_STRING, description="'ACCEPT' or 'REJECT'", default="ACCEPT"),
            }
        ),
        responses={200: "Success", 400: "Bad Request", 404: "Not Found"}
    )
    def put(self, request):
        user = request.user
        request_id = request.data.get('request_id')
        action = request.data.get('action')

        try:
            swap_req = TemporarySwapRequest.objects.get(id=request_id, target_teacher=user, status='PENDING')
            if action == 'ACCEPT':
                swap_req.status = 'ACCEPTED'
                swap_req.save()
                return Response({"status": "Success", "message": "Swap request ACCEPTED."})
            elif action == 'REJECT':
                swap_req.status = 'REJECTED'
                swap_req.save()
                return Response({"status": "Success", "message": "Swap request REJECTED."})
            else:
                return Response({"error": "Invalid action."}, status=status.HTTP_400_BAD_REQUEST)
        except TemporarySwapRequest.DoesNotExist:
            return Response({"error": "Pending request not found or you are not authorized."}, status=status.HTTP_404_NOT_FOUND)


# ==============================================================================
# ROUTINE GENERATION & MANAGEMENT APIs
# ==============================================================================
class GenerateRoutineView(APIView):
    permission_classes = [IsAdminUser]
    
    @swagger_auto_schema(
        tags=['1. Routine Engine'],
        operation_description="**[ADMIN ONLY]** Automatically generate a completely new conflict-free routine for a department/semester.",
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            required=['department_id'],
            properties={
                'department_id': openapi.Schema(type=openapi.TYPE_INTEGER, description='Department ID', default=1),
                'semester_id': openapi.Schema(type=openapi.TYPE_INTEGER, description='Semester ID (Optional)', default=1),
                'ignore_warnings': openapi.Schema(type=openapi.TYPE_BOOLEAN, description='Ignore warnings to save partial routine?', default=False),
            }
        ),
        responses={200: "Success", 409: "Warning (Partial Schedule)", 403: "Forbidden"}
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
        tags=['1. Routine Engine'],
        operation_description="**[ADMIN ONLY]** Restore the routine to its previous state before the last generation.",
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            required=['department_id'],
            properties={
                'department_id': openapi.Schema(type=openapi.TYPE_INTEGER, description='Department ID', default=1),
            }
        ),
        responses={200: "Success", 400: "Error/No Backup"}
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
        tags=['1. Routine Engine'],
        operation_description="**[ALL USERS]** Get the routine. Output is role-specific (Teachers see their classes, Students see their batch). Automatically applies Accepted Temporary Swaps based on reference_date.",
        manual_parameters=[
            openapi.Parameter('day', openapi.IN_QUERY, description="Filter by Day ID", type=openapi.TYPE_INTEGER),
            openapi.Parameter('department_id', openapi.IN_QUERY, description="Filter by Department ID", type=openapi.TYPE_INTEGER),
            openapi.Parameter('semester_id', openapi.IN_QUERY, description="Filter by Semester ID", type=openapi.TYPE_INTEGER),
            openapi.Parameter('reference_date', openapi.IN_QUERY, description="Target Date (YYYY-MM-DD). Defaults to today if blank.", type=openapi.TYPE_STRING),
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
        data = serializer.data

        # ======================================================================
        # THE MAGIC OVERLAY: DYNAMIC DATE PROJECTION & SWAP INJECTION
        # ======================================================================
        reference_date_str = request.query_params.get('reference_date')
        if reference_date_str:
            try:
                ref_date = datetime.datetime.strptime(reference_date_str, '%Y-%m-%d').date()
            except ValueError:
                ref_date = datetime.date.today()
        else:
            ref_date = datetime.date.today()

        idx = (ref_date.weekday() + 1) % 7 
        sunday_date = ref_date - datetime.timedelta(days=idx)

        days_qs = Day.objects.all().order_by('order')
        day_date_map = {}
        for i, d in enumerate(days_qs):
            day_date_map[d.name] = sunday_date + datetime.timedelta(days=i)

        week_start = sunday_date
        week_end = sunday_date + datetime.timedelta(days=6)

        accepted_swaps = TemporarySwapRequest.objects.filter(
            status='ACCEPTED',
            swap_date__range=[week_start, week_end]
        ).select_related('requester_routine', 'target_routine', 'target_teacher')

        proxy_swaps = {s.requester_routine_id: s for s in accepted_swaps if s.swap_type == 'PROXY'}
        mutual_swaps = {}
        for s in accepted_swaps:
            if s.swap_type == 'MUTUAL':
                mutual_swaps[s.requester_routine_id] = s
                if s.target_routine_id:
                    mutual_swaps[s.target_routine_id] = s

        modified_data = []
        for item in data:
            entry_id = item['id']
            day_name = item['day_name']
            exact_date = day_date_map.get(day_name)

            item['date'] = exact_date.strftime('%Y-%m-%d') if exact_date else None
            item['is_temporary_proxy'] = False
            item['is_temporary_mutual'] = False

            if entry_id in proxy_swaps and proxy_swaps[entry_id].swap_date == exact_date:
                item['teacher_name'] = proxy_swaps[entry_id].target_teacher.username
                item['is_temporary_proxy'] = True

            elif entry_id in mutual_swaps and mutual_swaps[entry_id].swap_date == exact_date:
                swap = mutual_swaps[entry_id]
                
                if swap.requester_routine_id == entry_id and swap.target_routine:
                    t_routine = swap.target_routine
                    item['day'] = t_routine.day.id
                    item['day_name'] = t_routine.day.name
                    item['start_time'] = t_routine.time_slot.start_time.strftime('%H:%M:%S')
                    item['end_time'] = t_routine.time_slot.end_time.strftime('%H:%M:%S')
                    item['room_number'] = t_routine.room.room_number if t_routine.room else "N/A"
                    item['is_temporary_mutual'] = True
                    
                elif swap.target_routine_id == entry_id:
                    r_routine = swap.requester_routine
                    item['day'] = r_routine.day.id
                    item['day_name'] = r_routine.day.name
                    item['start_time'] = r_routine.time_slot.start_time.strftime('%H:%M:%S')
                    item['end_time'] = r_routine.time_slot.end_time.strftime('%H:%M:%S')
                    item['room_number'] = r_routine.room.room_number if r_routine.room else "N/A"
                    item['is_temporary_mutual'] = True

            modified_data.append(item)

        return Response(modified_data)


class TeacherCancelClassView(APIView):
    permission_classes = [IsAuthenticated]
    
    @swagger_auto_schema(
        tags=['3. Teacher Operations'],
        operation_description="**[TEACHER ONLY]** Send a cancellation notice to students for a specific class.",
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            required=['routine_id', 'cancel_message'],
            properties={
                'routine_id': openapi.Schema(type=openapi.TYPE_INTEGER, default=1),
                'cancel_message': openapi.Schema(type=openapi.TYPE_STRING, default="Class cancelled due to meeting."),
            }
        ),
        responses={200: "Success", 403: "Forbidden"}
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
        tags=['2. Manual Operations'],
        operation_description="**[ADMIN ONLY]** Manually update a specific class to a new day, time, or room.",
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            required=['day_id', 'time_slot_id'],
            properties={
                'day_id': openapi.Schema(type=openapi.TYPE_INTEGER, default=1),
                'time_slot_id': openapi.Schema(type=openapi.TYPE_INTEGER, default=2),
                'room_id': openapi.Schema(type=openapi.TYPE_INTEGER, description="Room ID (Optional)", default=3),
            }
        ),
        responses={200: "Success", 400: "Conflict Error"}
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
        tags=['2. Manual Operations'],
        operation_description="**[ADMIN ONLY]** Permanently swap the time slots of two classes in the master database.",
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            required=['entry1_id', 'entry2_id'],
            properties={
                'entry1_id': openapi.Schema(type=openapi.TYPE_INTEGER, default=1),
                'entry2_id': openapi.Schema(type=openapi.TYPE_INTEGER, default=2),
            }
        ),
        responses={200: "Success", 400: "Conflict Error"}
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
        tags=['4. Enterprise Operations'],
        operation_description="**[ADMIN ONLY]** Import data for a specific model using an Excel file.",
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
        tags=['4. Enterprise Operations'],
        operation_description="**[ADMIN ONLY]** Export data of a specific model to an Excel file.",
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
    parser_classes = (MultiPartParser, FormParser)

    @swagger_auto_schema(
        tags=['4. Enterprise Operations'],
        operation_description="**[ADMIN ONLY]** Download a Multi-sheet Excel file containing the entire database.",
    )
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
        tags=['4. Enterprise Operations'],
        operation_description="**[ADMIN ONLY]** Upload a Multi-sheet Excel file to sync the entire database sequentially.",
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
        tags=['4. Enterprise Operations'],
        operation_description="**[ADMIN ONLY]** Create a JSON snapshot of the system or restore from a previous snapshot.",
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            required=['action'],
            properties={
                'action': openapi.Schema(type=openapi.TYPE_STRING, description="'backup' or 'restore'", default='backup'),
                'name': openapi.Schema(type=openapi.TYPE_STRING, description="Backup Name (e.g., Before Finals)", default='System Auto Backup'),
                'backup_id': openapi.Schema(type=openapi.TYPE_INTEGER, description="Backup ID (if action is restore)"),
            }
        ),
        responses={200: "Success", 400: "Bad Request", 404: "Not Found"}
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