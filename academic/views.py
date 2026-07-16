# academic/views.py
from django.shortcuts import render
from django.db import transaction
from django.http import HttpResponse
from django.contrib.auth import get_user_model
from django.core import serializers
import json
import tablib
import datetime
from drf_yasg.utils import swagger_auto_schema
from drf_yasg import openapi
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from .models import RoutineEntry
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from django.db.models import Q


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
    TemporarySwapRequest,FixedClassSchedule,Notification,RoutineEntry, ActivityLog, SystemSetting

)
from .utils import generate_routine_algorithm, rollback_routine_algorithm
from .serializers import (
    DepartmentSerializer, SemesterSerializer, CourseSerializer, NotificationSerializer,
    TimeSlotSerializer, RoutineEntrySerializer, RoomSerializer,FixedClassScheduleSerializer,NoticeSerializer,Notice
)

from user_api.permissions import IsAdminUser

User = get_user_model()




from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated

from drf_yasg import openapi
from django.shortcuts import get_object_or_404



RESOURCE_MAP = {
    'user': UserResource,
    'course': CourseResource,
    'room': RoomResource,
    'department': DepartmentResource,
    'batch': BatchResource,
    'routine': RoutineEntryResource,
}


class FixedClassScheduleListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def is_admin(self, user):
        return getattr(user, 'role', '') == 'ADMIN' or user.is_staff

    @swagger_auto_schema(
        tags=['4. Admin Operations'],
        operation_description="**[ADMIN ONLY]** Get all fixed class schedules.",
        responses={200: FixedClassScheduleSerializer(many=True)}
    )
    def get(self, request):
        if not self.is_admin(request.user):
            return Response({"error": "Only admins can access this API."}, status=status.HTTP_403_FORBIDDEN)
        
        schedules = FixedClassSchedule.objects.all()
        serializer = FixedClassScheduleSerializer(schedules, many=True)
        return Response({"status": "success", "data": serializer.data}, status=status.HTTP_200_OK)

    @swagger_auto_schema(
        tags=['4. Admin Operations'],
        operation_description="**[ADMIN ONLY]** Create a new fixed class schedule.",
        request_body=FixedClassScheduleSerializer,
        responses={201: "Created Successfully", 400: "Bad Request"}
    )
    def post(self, request):
        if not self.is_admin(request.user):
            return Response({"error": "Only admins can perform this action."}, status=status.HTTP_403_FORBIDDEN)
        
        serializer = FixedClassScheduleSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save()
            return Response({"status": "success", "message": "Fixed class scheduled successfully!", "data": serializer.data}, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class FixedClassScheduleDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def is_admin(self, user):
        return getattr(user, 'role', '') == 'ADMIN' or user.is_staff

    @swagger_auto_schema(
        tags=['4. Admin Operations'],
        operation_description="**[ADMIN ONLY]** Delete a fixed class schedule.",
        responses={204: "Deleted Successfully", 403: "Forbidden"}
    )
    def delete(self, request, pk):
        if not self.is_admin(request.user):
            return Response({"error": "Only admins can perform this action."}, status=status.HTTP_403_FORBIDDEN)
        
        schedule = get_object_or_404(FixedClassSchedule, pk=pk)
        schedule.delete()
        return Response({"status": "success", "message": "Fixed class schedule deleted successfully!"}, status=status.HTTP_204_NO_CONTENT)

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
    queryset = Course.objects.select_related(
        'teacher', 'department', 'semester', 'fixed_room'
    ).filter(is_active=True)
    
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

# ==============================================================================
# ROUTINE GENERATION & MANAGEMENT APIs
# ==============================================================================

# class TeacherSwapRequestView(APIView):
#     permission_classes = [IsAuthenticated]

#     @swagger_auto_schema(
#         tags=['3. Teacher Operations'],
#         operation_description="**[TEACHER ONLY]** Request a temporary class swap (PROXY or MUTUAL).",
#         request_body=openapi.Schema(
#             type=openapi.TYPE_OBJECT,
#             required=['swap_type', 'target_teacher_id', 'requester_routine_id', 'swap_date'],
#             properties={
#                 'swap_type': openapi.Schema(type=openapi.TYPE_STRING, description="'PROXY' or 'MUTUAL'", default='PROXY'),
#                 'target_teacher_id': openapi.Schema(type=openapi.TYPE_INTEGER, description="ID of the teacher you are requesting", default=1),
#                 'requester_routine_id': openapi.Schema(type=openapi.TYPE_INTEGER, description="Your routine ID", default=10),
#                 'target_routine_id': openapi.Schema(type=openapi.TYPE_INTEGER, description="Target routine ID (Required only for MUTUAL)", default=15),
#                 'swap_date': openapi.Schema(type=openapi.TYPE_STRING, format=openapi.FORMAT_DATE, description="Format: YYYY-MM-DD", default="2026-06-25"),
#                 'reason': openapi.Schema(type=openapi.TYPE_STRING, description="Reason for the swap", default="Medical Emergency"),
#             }
#         ),
#         responses={200: "Success", 400: "Bad Request (Conflict/Errors)", 403: "Forbidden"}
#     )
#     def post(self, request):
#         user = request.user
#         if getattr(user, 'role', '') != 'TEACHER':
#             return Response({"error": "Only teachers can request swaps."}, status=status.HTTP_403_FORBIDDEN)

#         swap_type = request.data.get('swap_type')
#         target_teacher_id = request.data.get('target_teacher_id')
#         requester_routine_id = request.data.get('requester_routine_id')
#         target_routine_id = request.data.get('target_routine_id')
#         swap_date_str = request.data.get('swap_date')
#         reason = request.data.get('reason', '')

#         try:
#             swap_date = datetime.datetime.strptime(swap_date_str, '%Y-%m-%d').date()
#             req_routine = RoutineEntry.objects.get(id=requester_routine_id, is_active=True)
#             target_teacher = User.objects.get(id=target_teacher_id, role='TEACHER')

#             if req_routine.course.teacher != user:
#                 return Response({"error": "You can only swap your own classes."}, status=status.HTTP_403_FORBIDDEN)

#             if swap_type == 'PROXY':
#                 conflict = RoutineEntry.objects.filter(
#                     day=req_routine.day,
#                     time_slot=req_routine.time_slot,
#                     course__teacher=target_teacher,
#                     is_active=True
#                 ).exists()
#                 if conflict:
#                     return Response({"error": f"{target_teacher.username} already has a class at this time!"}, status=status.HTTP_400_BAD_REQUEST)

#                 req = TemporarySwapRequest.objects.create(
#                     swap_type='PROXY', requester=user, target_teacher=target_teacher,
#                     requester_routine=req_routine, swap_date=swap_date, reason=reason
#                 )

#             elif swap_type == 'MUTUAL':
#                 if not target_routine_id:
#                     return Response({"error": "target_routine_id is required for MUTUAL swap."}, status=status.HTTP_400_BAD_REQUEST)

#                 tgt_routine = RoutineEntry.objects.get(id=target_routine_id, is_active=True)
#                 if tgt_routine.course.teacher != target_teacher:
#                     return Response({"error": "Target routine does not belong to the target teacher."}, status=status.HTTP_400_BAD_REQUEST)

#                 conflict1 = RoutineEntry.objects.filter(
#                     day=tgt_routine.day, time_slot=tgt_routine.time_slot, course__teacher=user, is_active=True
#                 ).exists()
#                 conflict2 = RoutineEntry.objects.filter(
#                     day=req_routine.day, time_slot=req_routine.time_slot, course__teacher=target_teacher, is_active=True
#                 ).exists()

#                 if conflict1 or conflict2:
#                     return Response({"error": "Mutual swap causes a timetable conflict for one or both teachers."}, status=status.HTTP_400_BAD_REQUEST)

#                 req = TemporarySwapRequest.objects.create(
#                     swap_type='MUTUAL', requester=user, target_teacher=target_teacher,
#                     requester_routine=req_routine, target_routine=tgt_routine,
#                     swap_date=swap_date, reason=reason
#                 )
#             else:
#                 return Response({"error": "Invalid swap_type."}, status=status.HTTP_400_BAD_REQUEST)

#             return Response({"status": "Success", "message": "Swap request sent successfully!", "request_id": req.id})

#         except Exception as e:
#             return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

#     @swagger_auto_schema(
#         tags=['3. Teacher Operations'],
#         operation_description="**[TEACHER ONLY]** Accept or Reject a pending swap request.",
#         request_body=openapi.Schema(
#             type=openapi.TYPE_OBJECT,
#             required=['request_id', 'action'],
#             properties={
#                 'request_id': openapi.Schema(type=openapi.TYPE_INTEGER, description="ID of the swap request", default=1),
#                 'action': openapi.Schema(type=openapi.TYPE_STRING, description="'ACCEPT' or 'REJECT'", default="ACCEPT"),
#             }
#         ),
#         responses={200: "Success", 400: "Bad Request", 404: "Not Found"}
#     )
#     def put(self, request):
#         user = request.user
#         request_id = request.data.get('request_id')
#         action = request.data.get('action')

#         try:
#             swap_req = TemporarySwapRequest.objects.get(id=request_id, target_teacher=user, status='PENDING')
#             if action == 'ACCEPT':
#                 swap_req.status = 'ACCEPTED'
#                 swap_req.save()
#                 return Response({"status": "Success", "message": "Swap request ACCEPTED."})
#             elif action == 'REJECT':
#                 swap_req.status = 'REJECTED'
#                 swap_req.save()
#                 return Response({"status": "Success", "message": "Swap request REJECTED."})
#             else:
#                 return Response({"error": "Invalid action."}, status=status.HTTP_400_BAD_REQUEST)
#         except TemporarySwapRequest.DoesNotExist:
#             return Response({"error": "Pending request not found or you are not authorized."}, status=status.HTTP_404_NOT_FOUND)


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
                # ==========================================================
                # ACTIVITY LOG (Partial Generation / Warning)
                # ==========================================================
                ActivityLog.objects.create(
                    actor=request.user,
                    action_description=f"GENERATED partial routine for Dept ID: {department.id} (Conflict Warnings Ignored).",
                    severity='WARNING'
                )
                return Response(result, status=status.HTTP_409_CONFLICT)
                
            elif result.get("status") == "Locked":
                return Response(result, status=status.HTTP_403_FORBIDDEN)
                
            elif result.get("status") == "Error":
                return Response(result, status=status.HTTP_400_BAD_REQUEST)
                
            else:
                # ==========================================================
                # ACTIVITY LOG (Routine Generation Success)
                # ==========================================================
                ActivityLog.objects.create(
                    actor=request.user, 
                    action_description=f"GENERATED a new master routine for Dept ID: {department.id}.", 
                    severity='SUCCESS'
                )
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
            
            
            if result.get("status") == "error" or result.get("status") == "Error":
                return Response(result, status=status.HTTP_400_BAD_REQUEST)
                
            # ==========================================================
            # ACTIVITY LOG (Routine Rollback Success)
            # ==========================================================
            ActivityLog.objects.create(
                actor=request.user,
                action_description=f"ROLLED BACK the master routine to its previous state for Dept ID: {department.id}.",
                severity='WARNING' 
            )
            
            return Response(result, status=status.HTTP_200_OK)
            
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


class DepartmentRoutineView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        tags=['3. Teacher Dashboard'],
        operation_description="**[TEACHER ONLY]** Get the complete routine for the teacher's department (includes targeted courses, offered courses, and cross-department classes by colleagues).",
        responses={200: "Success"}
    )
    def get(self, request):
        user = request.user


        if user.role != 'TEACHER':
            return Response({"error": "Shudhumatro Teacher ra ei API access korte parben."}, status=status.HTTP_403_FORBIDDEN)
        
        if not user.department:
            return Response({"error": "Apnar profile e kono department assign kora nei."}, status=status.HTTP_400_BAD_REQUEST)

        teacher_dept = user.department

        routines = RoutineEntry.objects.filter(
            Q(course__department=teacher_dept) |                 
            Q(course__offering_department=teacher_dept) |         
            Q(course__teacher__department=teacher_dept)          
        ).select_related('day', 'time_slot', 'course', 'room').distinct()

        
        serializer = RoutineEntrySerializer(routines, many=True)

        return Response({
            "status": "success",
            "department": teacher_dept.name,
            "total_classes": routines.count(),
            "data": serializer.data
        }, status=status.HTTP_200_OK)
    



# ==============================================================================
# TEACHER PANEL: CANCEL, REACTIVATE & UPDATE CLASS
# ==============================================================================



def notify_students_about_class(entry, action, actor, custom_message=""):
    """
    Helper function to send bulk notifications to students when a class is cancelled, reactivated, or updated.
    """
    
    target_students = User.objects.filter(
        role='STUDENT',
        department=entry.course.department,
        semester=entry.course.semester,
        is_active=True
    )

    if not target_students.exists():
        return 

   
    course_info = f"{entry.course.course_code} ({entry.course.course_name})"
    day_time = f"{entry.day.name} at {entry.time_slot}"
    
    title = ""
    message = ""

    if action == 'cancel':
        title = f"Class Cancelled: {entry.course.course_code}"
        message = f"Your class for {course_info} on {day_time} has been cancelled.\nReason: {custom_message}"
    elif action == 'reactivate':
        title = f"Class Reactivated: {entry.course.course_code}"
        message = f"Good news! Your cancelled class for {course_info} on {day_time} has been reactivated and will be held as scheduled."
    elif action == 'update':
        title = f"Update on Cancelled Class: {entry.course.course_code}"
        message = f"There is an update regarding the cancelled class for {course_info} on {day_time}.\nUpdated Reason: {custom_message}"

    
    notifications_to_create = []
    for student in target_students:
        notifications_to_create.append(
            Notification(
                recipient=student,
                sender=actor,
                notification_type='CLASS_DEL',
                title=title,
                message=message,
                action_url="/dashboard/students-routine" 
            )
        )
    
    if notifications_to_create:
        Notification.objects.bulk_create(notifications_to_create)



class AdminCancelClassView(APIView):
    permission_classes = [IsAdminUser]

    @swagger_auto_schema(
        tags=['4. Enterprise Operations'], 
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            properties={
                'action': openapi.Schema(
                    type=openapi.TYPE_STRING, 
                    description="Action type: 'cancel', 'reactivate', or 'update'"
                ),
                'cancel_message': openapi.Schema(
                    type=openapi.TYPE_STRING, 
                    description="Required for 'cancel' and 'update' actions."
                ),
            },
            required=['action']
        ),
        operation_description="**[ADMIN ONLY]** Temporarily cancel ANY class, reactivate an off class, or update the cancel message."
    )
    def post(self, request, entry_id):
        try:
            entry = RoutineEntry.objects.get(id=entry_id)
        except RoutineEntry.DoesNotExist:
            return Response({"error": "Routine entry not found."}, status=status.HTTP_404_NOT_FOUND)

        action = request.data.get('action')

        if action == 'cancel':
            cancel_message = request.data.get('cancel_message', 'Class temporarily cancelled by Administration.')
            entry.is_cancelled = True
            entry.cancel_message = cancel_message
            entry.save()
            
            ActivityLog.objects.create(actor=request.user, action_description=f"ADMIN CANCELLED class: {entry.course.course_name} on {entry.day.name}.", severity='WARNING')
            notify_students_about_class(entry, 'cancel', request.user, cancel_message) # [NEW] Notification Call
            
            return Response({"status": "success", "message": "Class cancelled successfully by Admin.", "cancel_message": cancel_message})

        elif action == 'reactivate':
            entry.is_cancelled = False
            entry.cancel_message = None
            entry.save()
            
            ActivityLog.objects.create(actor=request.user, action_description=f"ADMIN REACTIVATED class: {entry.course.course_name} on {entry.day.name}.", severity='SUCCESS')
            notify_students_about_class(entry, 'reactivate', request.user) # [NEW] Notification Call
            
            return Response({"status": "success", "message": "Class reactivated successfully by Admin."})

        elif action == 'update':
            if not entry.is_cancelled:
                return Response({"error": "Cannot update message. The class is not cancelled yet."}, status=status.HTTP_400_BAD_REQUEST)
            
            new_cancel_message = request.data.get('cancel_message', '')
            if not new_cancel_message:
                return Response({"error": "Cancel message cannot be empty for update action."}, status=status.HTTP_400_BAD_REQUEST)
                
            entry.cancel_message = new_cancel_message
            entry.save()
            
            ActivityLog.objects.create(actor=request.user, action_description=f"ADMIN UPDATED cancellation message for: {entry.course.course_name}.", severity='INFO')
            notify_students_about_class(entry, 'update', request.user, new_cancel_message) # [NEW] Notification Call
            
            return Response({"status": "success", "message": "Cancellation message updated successfully by Admin.", "cancel_message": new_cancel_message})

        else:
            return Response({"error": "Invalid action. Please use 'cancel', 'reactivate', or 'update'."}, status=status.HTTP_400_BAD_REQUEST)
        

class TeacherCancelClassView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        tags=['3. Teacher Panel'],
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            properties={
                'action': openapi.Schema(
                    type=openapi.TYPE_STRING, 
                    description="Action type: 'cancel', 'reactivate', or 'update'"
                ),
                'cancel_message': openapi.Schema(
                    type=openapi.TYPE_STRING, 
                    description="Required for 'cancel' and 'update' actions."
                ),
            },
            required=['action']
        ),
        operation_description="Cancel a class, reactivate an off class, or update the cancel message."
    )
    def post(self, request, entry_id):
        try:
            entry = RoutineEntry.objects.get(id=entry_id, course__teacher=request.user)
        except RoutineEntry.DoesNotExist:
            return Response({"error": "Routine entry not found or you don't have permission to modify this class."}, status=status.HTTP_404_NOT_FOUND)

        action = request.data.get('action')

        if action == 'cancel':
            cancel_message = request.data.get('cancel_message', 'Class cancelled by teacher.')
            entry.is_cancelled = True
            entry.cancel_message = cancel_message
            entry.save()
            
            ActivityLog.objects.create(actor=request.user, action_description=f"CANCELLED class: {entry.course.course_name} on {entry.day.name}.", severity='WARNING')
            notify_students_about_class(entry, 'cancel', request.user, cancel_message) # [NEW] Notification Call
            
            return Response({"status": "success", "message": "Class cancelled successfully.", "cancel_message": cancel_message})

        elif action == 'reactivate':
            entry.is_cancelled = False
            entry.cancel_message = None 
            entry.save()
            
            ActivityLog.objects.create(actor=request.user, action_description=f"REACTIVATED class: {entry.course.course_name} on {entry.day.name}.", severity='SUCCESS')
            notify_students_about_class(entry, 'reactivate', request.user) # [NEW] Notification Call
            
            return Response({"status": "success", "message": "Class reactivated successfully. The cancellation message has been removed."})

        elif action == 'update':
            if not entry.is_cancelled:
                return Response({"error": "Cannot update message. The class is not cancelled yet."}, status=status.HTTP_400_BAD_REQUEST)
            
            new_cancel_message = request.data.get('cancel_message', '')
            if not new_cancel_message:
                return Response({"error": "Cancel message cannot be empty for update action."}, status=status.HTTP_400_BAD_REQUEST)
                
            entry.cancel_message = new_cancel_message
            entry.save()
            
            ActivityLog.objects.create(actor=request.user, action_description=f"UPDATED cancellation message for: {entry.course.course_name}.", severity='INFO')
            notify_students_about_class(entry, 'update', request.user, new_cancel_message) # [NEW] Notification Call
            
            return Response({"status": "success", "message": "Cancellation message updated successfully.", "cancel_message": new_cancel_message})

        else:
            return Response({"error": "Invalid action. Please use 'cancel', 'reactivate', or 'update'."}, status=status.HTTP_400_BAD_REQUEST)

# class AdminCancelClassView(APIView):
#     permission_classes = [IsAdminUser]

#     @swagger_auto_schema(
#         tags=['2. Manual Operations'],
#         request_body=openapi.Schema(
#             type=openapi.TYPE_OBJECT,
#             properties={
#                 'action': openapi.Schema(
#                     type=openapi.TYPE_STRING, 
#                     description="Action type: 'cancel', 'reactivate', or 'update'"
#                 ),
#                 'cancel_message': openapi.Schema(
#                     type=openapi.TYPE_STRING, 
#                     description="Required for 'cancel' and 'update' actions."
#                 ),
#             },
#             required=['action']
#         ),
#         operation_description="**[ADMIN ONLY]** Temporarily cancel any class, reactivate an off class, or update the cancel message for any routine entry."
#     )
#     def post(self, request, entry_id):
#         try:
#             # admin can access any routine entry, so no teacher filter is applied
#             entry = RoutineEntry.objects.get(id=entry_id)
#         except RoutineEntry.DoesNotExist:
#             return Response(
#                 {"error": "Routine entry not found."}, 
#                 status=status.HTTP_404_NOT_FOUND
#             )

#         action = request.data.get('action')

#         # ১. Class Cancel Logic
#         if action == 'cancel':
#             # if admin does not provide a custom message, use a default cancellation message
#             cancel_message = request.data.get('cancel_message', 'Class temporarily cancelled by Administration.')
#             entry.is_cancelled = True
#             entry.cancel_message = cancel_message
#             entry.save()
            
#             # ==========================================================
#             # ACTIVITY LOG (Admin Cancel)
#             # ==========================================================
#             ActivityLog.objects.create(
#                 actor=request.user,
#                 action_description=f"ADMIN CANCELLED class: {entry.course.course_code} ({entry.course.course_name}) on {entry.day.name}.", 
#                 severity='WARNING'
#             )
#             return Response({
#                 "status": "success",
#                 "message": "Class cancelled successfully by Admin.", 
#                 "cancel_message": cancel_message
#             })
        
#         # ২. Class Reactivate Logic 
#         elif action == 'reactivate':
#             entry.is_cancelled = False
#             entry.cancel_message = None  
#             entry.save()
            
#             # ==========================================================
#             # ACTIVITY LOG (Admin Reactivate)
#             # ==========================================================
#             ActivityLog.objects.create(
#                 actor=request.user, 
#                 action_description=f"ADMIN REACTIVATED class: {entry.course.course_code} ({entry.course.course_name}) on {entry.day.name}.", 
#                 severity='SUCCESS'
#             )
#             return Response({
#                 "status": "success",
#                 "message": "Class reactivated successfully by Admin."
#             })

#         # ৩. Cancel Message Update Logic
#         elif action == 'update':
#             if not entry.is_cancelled:
#                 return Response(
#                     {"error": "Cannot update message. The class is not cancelled yet."}, 
#                     status=status.HTTP_400_BAD_REQUEST
#                 )
            
#             new_cancel_message = request.data.get('cancel_message', '')
#             if not new_cancel_message:
#                 return Response(
#                     {"error": "Cancel message cannot be empty for update action."}, 
#                     status=status.HTTP_400_BAD_REQUEST
#                 )
                
#             entry.cancel_message = new_cancel_message
#             entry.save()
            
#             # ==========================================================
#             # ACTIVITY LOG (Admin Update Message)
#             # ==========================================================
#             ActivityLog.objects.create(
#                 actor=request.user, 
#                 action_description=f"ADMIN UPDATED cancellation message for: {entry.course.course_code}.", 
#                 severity='INFO'
#             )
#             return Response({
#                 "status": "success",
#                 "message": "Cancellation message updated successfully by Admin.", 
#                 "cancel_message": new_cancel_message
#             })

#         # if the action is none of the above, return an error
#         else:
#             return Response(
#                 {"error": "Invalid action. Please use 'cancel', 'reactivate', or 'update'."}, 
#                 status=status.HTTP_400_BAD_REQUEST
#             )
#     permission_classes = [IsAdminUser]  

#     @swagger_auto_schema(
#         tags=['4. Enterprise Operations'], 
#         request_body=openapi.Schema(
#             type=openapi.TYPE_OBJECT,
#             properties={
#                 'action': openapi.Schema(
#                     type=openapi.TYPE_STRING, 
#                     description="Action type: 'cancel', 'reactivate', or 'update'"
#                 ),
#                 'cancel_message': openapi.Schema(
#                     type=openapi.TYPE_STRING, 
#                     description="Required for 'cancel' and 'update' actions."
#                 ),
#             },
#             required=['action']
#         ),
#         operation_description="**[ADMIN ONLY]** Temporarily cancel ANY class, reactivate an off class, or update the cancel message."
#     )
#     def post(self, request, entry_id):
#         try:
#             # Admin can access any routine entry, so no teacher filter is applied
#             entry = RoutineEntry.objects.get(id=entry_id)
#         except RoutineEntry.DoesNotExist:
#             return Response(
#                 {"error": "Routine entry not found."}, 
#                 status=status.HTTP_404_NOT_FOUND
#             )

#         action = request.data.get('action')

#         # ১. class cancel logic (Admin)
#         if action == 'cancel':
#             # if no custom message is provided, use a default admin cancellation message
#             cancel_message = request.data.get('cancel_message', 'Class temporarily cancelled by Administration.')
#             entry.is_cancelled = True
#             entry.cancel_message = cancel_message
#             entry.save()
            
#             ActivityLog.objects.create(
#                 actor=request.user,
#                 action_description=f"ADMIN CANCELLED class: {entry.course.course_name} on {entry.day.name}.", 
#                 severity='WARNING'
#             )
#             return Response({
#                 "status": "success",
#                 "message": "Class cancelled successfully by Admin.", 
#                 "cancel_message": cancel_message
#             })

#         # ২. again class re-activate (Admin)
#         elif action == 'reactivate':
#             entry.is_cancelled = False
#             entry.cancel_message = None
#             entry.save()
            
#             ActivityLog.objects.create(
#                 actor=request.user, 
#                 action_description=f"ADMIN REACTIVATED class: {entry.course.course_name} on {entry.day.name}.", 
#                 severity='SUCCESS'
#             )
#             return Response({
#                 "status": "success",
#                 "message": "Class reactivated successfully by Admin."
#             })

#         # ৩. off class message update logic (Admin)
#         elif action == 'update':
#             if not entry.is_cancelled:
#                 return Response(
#                     {"error": "Cannot update message. The class is not cancelled yet."}, 
#                     status=status.HTTP_400_BAD_REQUEST
#                 )
            
#             new_cancel_message = request.data.get('cancel_message', '')
#             if not new_cancel_message:
#                 return Response(
#                     {"error": "Cancel message cannot be empty for update action."}, 
#                     status=status.HTTP_400_BAD_REQUEST
#                 )
                
#             entry.cancel_message = new_cancel_message
#             entry.save()
            
#             ActivityLog.objects.create(
#                 actor=request.user, 
#                 action_description=f"ADMIN UPDATED cancellation message for: {entry.course.course_name}.", 
#                 severity='INFO'
#             )
#             return Response({
#                 "status": "success",
#                 "message": "Cancellation message updated successfully by Admin.", 
#                 "cancel_message": new_cancel_message
#             })

#         # if the action is none of the above
#         else:
#             return Response(
#                 {"error": "Invalid action. Please use 'cancel', 'reactivate', or 'update'."}, 
#                 status=status.HTTP_400_BAD_REQUEST
#             )
        



# class TeacherCancelClassView(APIView):
#     permission_classes = [IsAuthenticated]

#     @swagger_auto_schema(
#         tags=['3. Teacher Panel'],
#         request_body=openapi.Schema(
#             type=openapi.TYPE_OBJECT,
#             properties={
#                 'action': openapi.Schema(
#                     type=openapi.TYPE_STRING, 
#                     description="Action type: 'cancel', 'reactivate', or 'update'"
#                 ),
#                 'cancel_message': openapi.Schema(
#                     type=openapi.TYPE_STRING, 
#                     description="Required for 'cancel' and 'update' actions."
#                 ),
#             },
#             required=['action']
#         ),
#         operation_description="Cancel a class, reactivate an off class, or update the cancel message."
#     )
#     def post(self, request, entry_id):
#         try:
#             # Check if the entry exists and belongs to the logged-in teacher
#             entry = RoutineEntry.objects.get(id=entry_id, course__teacher=request.user)
#         except RoutineEntry.DoesNotExist:
#             return Response(
#                 {"error": "Routine entry not found or you don't have permission to modify this class."}, 
#                 status=status.HTTP_404_NOT_FOUND
#             )

#         action = request.data.get('action')

#         # ১. class cancel logic
#         if action == 'cancel':
#             cancel_message = request.data.get('cancel_message', 'Class cancelled by teacher.')
#             entry.is_cancelled = True
#             entry.cancel_message = cancel_message
#             entry.save()
#             ActivityLog.objects.create(actor=request.user,action_description=f"CANCELLED class: {entry.course.course_name} on {entry.day.name}.", severity='WARNING')
#             return Response({
#                 "status": "success",
#                 "message": "Class cancelled successfully.", 
#                 "cancel_message": cancel_message
#             })
        

#         # ২. again class re-activate
#         elif action == 'reactivate':
#             entry.is_cancelled = False
#             entry.cancel_message = None  # Remove the cancellation message when reactivating
#             entry.save()
#             ActivityLog.objects.create(actor=request.user, action_description=f"REACTIVATED class: {entry.course.course_name} on {entry.day.name}.", severity='SUCCESS')
#             return Response({
#                 "status": "success",
#                 "message": "Class reactivated successfully. The cancellation message has been removed."
#             })

#         # ৩. off class massage update logic
#         elif action == 'update':
#             if not entry.is_cancelled:
#                 return Response(
#                     {"error": "Cannot update message. The class is not cancelled yet."}, 
#                     status=status.HTTP_400_BAD_REQUEST
#                 )
            
#             new_cancel_message = request.data.get('cancel_message', '')
#             if not new_cancel_message:
#                 return Response(
#                     {"error": "Cancel message cannot be empty for update action."}, 
#                     status=status.HTTP_400_BAD_REQUEST
#                 )
                
#             entry.cancel_message = new_cancel_message
#             entry.save()
#             ActivityLog.objects.create(actor=request.user, action_description=f"UPDATED cancellation message for: {entry.course.course_name}.", severity='INFO')
#             return Response({
#                 "status": "success",
#                 "message": "Cancellation message updated successfully.", 
#                 "cancel_message": new_cancel_message
#             })

#         # if the action is none of the above, return an error
#         else:
#             return Response(
#                 {"error": "Invalid action. Please use 'cancel', 'reactivate', or 'update'."}, 
#                 status=status.HTTP_400_BAD_REQUEST
#             )



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

        # ==========================================================
        # ACTIVITY LOG (Manual Routine Update)
        # ==========================================================
        ActivityLog.objects.create(
            actor=request.user,
            action_description=f"MANUALLY UPDATED class schedule for Entry ID: {entry.id}.",
            severity='WARNING'
        )

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

            # ==========================================================
            # ACTIVITY LOG (Permanent Admin Swap)
            # ==========================================================
            ActivityLog.objects.create(
                actor=request.user,
                action_description=f"PERMANENTLY SWAPPED time slots between Class ID: {entry1.id} and Class ID: {entry2.id}.",
                severity='WARNING' 
            )

        return Response({"status": "success", "message": "Duti class er shudhu somoy successfully swap kora hoyeche!"})
# ==============================================================================
# DYNAMIC EXCEL IMPORT & EXPORT APIs (Master API)
# ==============================================================================




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
            
            # ==========================================================
            # ৩. ACTIVITY LOG (Excel Upload)
            # ==========================================================
            ActivityLog.objects.create(
                actor=request.user,
                action_description=f"IMPORTED academic data for '{model_name.capitalize()}' via Excel sync.",
                severity='SUCCESS'
            )

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
    


# ==============================================================================
# 6. SYSTEM NOTIFICATIONS API
# ==============================================================================

# academic/views.py

from drf_yasg.utils import swagger_auto_schema
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
# নিশ্চিত করুন যে Notice, NoticeSerializer, Notification এবং User আপনার ফাইলে ইমপোর্ট করা আছে

class NoticeListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        tags=['Notices'],
        operation_description="Get a list of all notices.",
        responses={200: "Success"}
    )
    def get(self, request):
        notices = Notice.objects.all()
        serializer = NoticeSerializer(notices, many=True)
        return Response(serializer.data)

    @swagger_auto_schema(
        tags=['Notices'],
        operation_description="**[ADMIN & TEACHER ONLY]** Create a new notice and auto-send notifications to target users.",
        request_body=NoticeSerializer,  # এই লাইনের কারণেই সোয়াগারে ইনপুট বক্সগুলো আসবে
        responses={201: "Success", 400: "Bad Request", 403: "Forbidden"}
    )
    def post(self, request):
        if request.user.role not in ['ADMIN', 'TEACHER']: 
            return Response({"error": "You do not have permission to create notices."}, status=status.HTTP_403_FORBIDDEN)

        serializer = NoticeSerializer(data=request.data)
        if serializer.is_valid():
            notice = serializer.save(sender=request.user)
            
            # Auto-generate Notifications
            target_users = self.get_users_for_notice(notice)
            
            notifications_to_create = []
            for user in target_users:
                if user.id != request.user.id:
                    notifications_to_create.append(
                        Notification(
                            recipient=user,
                            sender=request.user,
                            related_notice=notice, 
                            notification_type='NOTICE',
                            title=notice.title,
                            message=notice.message,
                            action_url="/notices" # ফ্রন্টএন্ডের নোটিশ পেজের লিংক
                        )
                    )
            
            if notifications_to_create:
                Notification.objects.bulk_create(notifications_to_create)

            return Response({"status": "success", "message": "Notice created and notifications sent.", "data": serializer.data}, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def get_users_for_notice(self, notice):
        if notice.notice_type == 'GLOBAL':
            return User.objects.filter(is_active=True)
        
        dept_ids = notice.target_departments.values_list('id', flat=True)
        batch_ids = notice.target_batches.values_list('id', flat=True) 
        
        users = User.objects.filter(is_active=True)
        
        if dept_ids:
            users = users.filter(department_id__in=dept_ids)
        if batch_ids:
            users = users.filter(batch_id__in=batch_ids) 
            
        return users.distinct()

class NoticeDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get_object(self, pk):
        try:
            return Notice.objects.get(pk=pk)
        except Notice.DoesNotExist:
            return None

    def get(self, request, pk):
        notice = self.get_object(pk)
        if not notice:
            return Response({"error": "Notice not found."}, status=status.HTTP_404_NOT_FOUND)
        serializer = NoticeSerializer(notice)
        return Response(serializer.data)

    def put(self, request, pk):
        notice = self.get_object(pk)
        if not notice:
            return Response({"error": "Notice not found."}, status=status.HTTP_404_NOT_FOUND)

     
        if request.user.role != 'ADMIN' and notice.sender != request.user:
            return Response({"error": "You do not have permission to update this notice."}, status=status.HTTP_403_FORBIDDEN)

        serializer = NoticeSerializer(notice, data=request.data, partial=True)
        if serializer.is_valid():
            updated_notice = serializer.save()
            
          
            Notification.objects.filter(related_notice=updated_notice).update(
                title=updated_notice.title,
                message=updated_notice.message
            )
            return Response({"status": "success", "message": "Notice updated successfully.", "data": serializer.data})
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def delete(self, request, pk):
        notice = self.get_object(pk)
        if not notice:
            return Response({"error": "Notice not found."}, status=status.HTTP_404_NOT_FOUND)

       
        if request.user.role != 'ADMIN' and notice.sender != request.user:
            return Response({"error": "You do not have permission to delete this notice."}, status=status.HTTP_403_FORBIDDEN)

        
        notice.delete() 
        return Response({"status": "success", "message": "Notice and all related notifications deleted successfully."})


# academic/views.py

class NotificationListView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        tags=['5. Notifications'],
        operation_description="**[ALL USERS]** Get all notifications, including system notices."
    )
    def get(self, request):
        notifications = Notification.objects.filter(recipient=request.user).select_related('related_notice')
        serializer = NotificationSerializer(notifications, many=True)
        return Response(serializer.data)




class UnreadNotificationCountView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        tags=['5. Notifications'],
        operation_description="**[ALL USERS]** Get the total count of unread notifications to show on the bell icon badge."
    )
    def get(self, request):
        count = Notification.objects.filter(recipient=request.user, is_read=False).count()
        return Response({"unread_count": count})

class MarkNotificationReadView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        tags=['5. Notifications'],
        operation_description="**[ALL USERS]** Mark a specific notification as read when the user clicks on it."
    )
    def patch(self, request, pk):
        try:
            notification = Notification.objects.get(pk=pk, recipient=request.user)
            notification.is_read = True
            notification.save()
            return Response({"status": "success", "message": "Notification marked as read."})
        except Notification.DoesNotExist:
            return Response({"error": "Notification not found."}, status=status.HTTP_404_NOT_FOUND)
        


# class NoticeListCreateView(APIView):
#     permission_classes = [IsAuthenticated]

#     def get(self, request):
#         notices = Notice.objects.all()
#         serializer = NoticeSerializer(notices, many=True)
#         return Response(serializer.data)

#     def post(self, request):
       
#         if request.user.role not in ['ADMIN', 'TEACHER']: 
#             return Response({"error": "You do not have permission to create notices."}, status=status.HTTP_403_FORBIDDEN)

#         serializer = NoticeSerializer(data=request.data)
#         if serializer.is_valid():
#             notice = serializer.save(sender=request.user)
            
#             # Auto-generate Notifications
#             target_users = self.get_users_for_notice(notice)
            
#             notifications_to_create = []
#             for user in target_users:
#                 if user.id != request.user.id:
#                     notifications_to_create.append(
#                         Notification(
#                             recipient=user,
#                             sender=request.user,
#                             related_notice=notice, 
#                             notification_type='NOTICE',
#                             title=notice.title,
#                             message=notice.message,
#                             action_url="/notices"
#                         )
#                     )
            
#             if notifications_to_create:
#                 Notification.objects.bulk_create(notifications_to_create)

#             return Response({"status": "success", "message": "Notice created and notifications sent.", "data": serializer.data}, status=status.HTTP_201_CREATED)
#         return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

#     def get_users_for_notice(self, notice):
#         if notice.notice_type == 'GLOBAL':
#             return User.objects.filter(is_active=True)
        
      
#         dept_ids = notice.target_departments.values_list('id', flat=True)
#         sem_ids = notice.target_semesters.values_list('id', flat=True)
#         batch_ids = notice.target_batches.values_list('id', flat=True) 
        
#         users = User.objects.filter(is_active=True)
        
#         if dept_ids:
#             users = users.filter(department__id__in=dept_ids)
#         if sem_ids:
#             users = users.filter(semester__id__in=sem_ids)
#         if batch_ids:
#             users = users.filter(batch__id__in=batch_ids) 
            
#         return users.distinct()





# ==============================================================================
# 7. SYSTEM LOGS API (AUDIT TRAIL)
# ==============================================================================
from django.db.models import Q
from .models import ActivityLog
from .serializers import ActivityLogSerializer

class BaseActivityLogView(APIView):
    permission_classes = [IsAuthenticated]

    def get_filtered_logs(self, request):
        user = request.user
        # 1. if the user is an admin or superuser, return all logs
        if user.is_staff or user.is_superuser:
            logs = ActivityLog.objects.all()
        # 2. normal user case: only logs where the user is the actor or related user
        else:
            logs = ActivityLog.objects.filter(Q(actor=user) | Q(related_users=user)).distinct()
        
        # 3. user hide log filter
        logs = logs.exclude(hidden_by=user)
        return logs

class RecentActivityLogView(BaseActivityLogView):
    @swagger_auto_schema(tags=['6. Activity Logs'], operation_description="Get 10 most recent logs.")
    def get(self, request):
        # last 10 history logs
        logs = self.get_filtered_logs(request)[:10]
        serializer = ActivityLogSerializer(logs, many=True)
        return Response(serializer.data)

class AllActivityLogView(BaseActivityLogView):
    @swagger_auto_schema(tags=['6. Activity Logs'], operation_description="Get all history logs.")
    def get(self, request):
        # all history logs 
        logs = self.get_filtered_logs(request)
        serializer = ActivityLogSerializer(logs, many=True)
        return Response(serializer.data)

class HideActivityLogView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(tags=['6. Activity Logs'], operation_description="Hide a log from user's dashboard.")
    def post(self, request, pk):
        try:
            log = ActivityLog.objects.get(pk=pk)
            # log do not delete, just add the user to hidden_by
            log.hidden_by.add(request.user)
            return Response({"status": "success", "message": "Log dismissed successfully."})
        except ActivityLog.DoesNotExist:
            return Response({"error": "Log not found."}, status=status.HTTP_404_NOT_FOUND)
        


class SystemSettingView(APIView):
    permission_classes = [IsAdminUser]

    @swagger_auto_schema(
        tags=['4. Enterprise Operations'],
        operation_description="**[ADMIN ONLY]** Get current system settings (Lock/Unlock status).",
        responses={200: "Success"}
    )
    def get(self, request):

        setting, created = SystemSetting.objects.get_or_create(id=1)
        return Response({
            "is_routine_locked": setting.is_routine_locked,
            "last_updated": setting.last_updated
        }, status=status.HTTP_200_OK)

    @swagger_auto_schema(
        tags=['4. Enterprise Operations'],
        operation_description="**[ADMIN ONLY]** Lock or Unlock the routine system.",
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            required=['is_routine_locked'],
            properties={
                'is_routine_locked': openapi.Schema(type=openapi.TYPE_BOOLEAN, description='Set True to lock, False to unlock'),
            }
        ),
        responses={200: "Success"}
    )
    def post(self, request):
        is_locked = request.data.get('is_routine_locked')
        
        if is_locked is None:
            return Response({"error": "is_routine_locked value pathate hobe (True/False)."}, status=status.HTTP_400_BAD_REQUEST)

        setting, created = SystemSetting.objects.get_or_create(id=1)
        
       
        if setting.is_routine_locked != is_locked:
            setting.is_routine_locked = is_locked
            setting.save()

            # ==========================================================
            # ACTIVITY LOG (System Lock/Unlock)
            # ==========================================================
            status_text = "LOCKED" if is_locked else "UNLOCKED"
            log_severity = 'WARNING' if is_locked else 'INFO'
            
            ActivityLog.objects.create(
                actor=request.user,
                action_description=f"{status_text} the master routine system.",
                severity=log_severity
            )

        return Response({
            "status": "success", 
            "message": f"Routine system successfully {'locked' if is_locked else 'unlocked'}!",
            "is_routine_locked": setting.is_routine_locked
        }, status=status.HTTP_200_OK)
    


import datetime
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from drf_yasg.utils import swagger_auto_schema
from drf_yasg import openapi

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
            # [UPDATED HERE] datetime.datetime.strptime ব্যবহার করা হয়েছে
            swap_date = datetime.datetime.strptime(swap_date_str, '%Y-%m-%d').date()
            req_routine = RoutineEntry.objects.get(id=requester_routine_id, is_active=True)
            target_teacher = User.objects.get(id=target_teacher_id, role='TEACHER')

            if req_routine.course.teacher != user:
                return Response({"error": "You can only swap your own classes."}, status=status.HTTP_403_FORBIDDEN)

            
            req_course_info = f"{req_routine.course.course_code} on {req_routine.day.name} at {req_routine.time_slot.start_time.strftime('%I:%M %p')}"

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

                # PROXY Notification
                msg = f"{user.username} requested you to take their class ({req_course_info}) on Date: {swap_date}. Reason: {reason}"
                title = f"PROXY Swap Request from {user.username}"

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

                # MUTUAL Notification
                tgt_course_info = f"{tgt_routine.course.course_code} at {tgt_routine.time_slot.start_time.strftime('%I:%M %p')}"
                msg = f"{user.username} proposed a MUTUAL swap. Exchange your class ({tgt_course_info}) with their class ({req_course_info}) on Date: {swap_date}. Reason: {reason}"
                title = f"MUTUAL Swap Request from {user.username}"

            else:
                return Response({"error": "Invalid swap_type."}, status=status.HTTP_400_BAD_REQUEST)

            
            Notification.objects.create(
                recipient=target_teacher,
                sender=user,
                notification_type='SWAP_REQ',
                title=title,
                message=msg,
                action_url=f"/dashboard/swap-requests?request_id={req.id}"
            )

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
            course_code = swap_req.requester_routine.course.course_code

            if action == 'ACCEPT':
                swap_req.status = 'ACCEPTED'
                swap_req.save()

                
                Notification.objects.create(
                    recipient=swap_req.requester,
                    sender=user,
                    notification_type='SWAP_ACC',
                    title='Swap Request Accepted 🎉',
                    message=f"{user.username} has ACCEPTED your {swap_req.swap_type} swap request for {course_code} on {swap_req.swap_date}.",
                    action_url="/dashboard/teachers-routine"
                )
                return Response({"status": "Success", "message": "Swap request ACCEPTED."})
            
            elif action == 'REJECT':
                swap_req.status = 'REJECTED'
                swap_req.save()

                
                Notification.objects.create(
                    recipient=swap_req.requester,
                    sender=user,
                    notification_type='SWAP_REJ',
                    title='Swap Request Rejected ❌',
                    message=f"{user.username} has REJECTED your {swap_req.swap_type} swap request for {course_code} on {swap_req.swap_date}.",
                    action_url="/dashboard/teachers-routine"
                )
                return Response({"status": "Success", "message": "Swap request REJECTED."})
            
            else:
                return Response({"error": "Invalid action."}, status=status.HTTP_400_BAD_REQUEST)
        except TemporarySwapRequest.DoesNotExist:
            return Response({"error": "Pending request not found or you are not authorized."}, status=status.HTTP_404_NOT_FOUND)





# class NoticeListCreateView(APIView):
#     permission_classes = [IsAuthenticated]

#     def get(self, request):
#         notices = Notice.objects.all()
#         serializer = NoticeSerializer(notices, many=True)
#         return Response(serializer.data)

#     def post(self, request):
       
#         if request.user.role not in ['ADMIN', 'TEACHER']: 
#             return Response({"error": "You do not have permission to create notices."}, status=status.HTTP_403_FORBIDDEN)

#         serializer = NoticeSerializer(data=request.data)
#         if serializer.is_valid():
#             notice = serializer.save(sender=request.user)
            
#             # Auto-generate Notifications
#             target_users = self.get_users_for_notice(notice)
            
#             notifications_to_create = []
#             for user in target_users:
#                 if user.id != request.user.id:
#                     notifications_to_create.append(
#                         Notification(
#                             recipient=user,
#                             sender=request.user,
#                             related_notice=notice, 
#                             notification_type='NOTICE',
#                             title=notice.title,
#                             message=notice.message,
#                             action_url="/notices"
#                         )
#                     )
            
#             if notifications_to_create:
#                 Notification.objects.bulk_create(notifications_to_create)

#             return Response({"status": "success", "message": "Notice created and notifications sent.", "data": serializer.data}, status=status.HTTP_201_CREATED)
#         return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

#     def get_users_for_notice(self, notice):
#         if notice.notice_type == 'GLOBAL':
#             return User.objects.filter(is_active=True)
        
      
#         dept_ids = notice.target_departments.values_list('id', flat=True)
#         sem_ids = notice.target_semesters.values_list('id', flat=True)
#         batch_ids = notice.target_batches.values_list('id', flat=True) 
        
#         users = User.objects.filter(is_active=True)
        
#         if dept_ids:
#             users = users.filter(department__id__in=dept_ids)
#         if sem_ids:
#             users = users.filter(semester__id__in=sem_ids)
#         if batch_ids:
#             users = users.filter(batch__id__in=batch_ids) 
            
#         return users.distinct()


