# academic/serializers.py
from rest_framework import serializers

# Importing all models including the new dynamic ones and Notification
from .models import ActivityLog, Day, RoomType, RoomSubType, Department, Semester, Course, TimeSlot, RoutineEntry, Room, Notification

from rest_framework import serializers
from .models import FixedClassSchedule

from rest_framework import serializers
from .models import FixedClassSchedule

class FixedClassScheduleSerializer(serializers.ModelSerializer):
    # showing related fields for better frontend display
    course_name = serializers.CharField(source='course.course_name', read_only=True)
    course_code = serializers.CharField(source='course.course_code', read_only=True)
    day_name = serializers.CharField(source='day.name', read_only=True)
    room_number = serializers.CharField(source='room.room_number', read_only=True)
    
    # Adding start_time and end_time fields to display the time slot details
    start_time = serializers.TimeField(source='time_slot.start_time', read_only=True)
    end_time = serializers.TimeField(source='time_slot.end_time', read_only=True)

    class Meta:
        model = FixedClassSchedule
        fields = [
            'id', 'course', 'course_name', 'course_code', 
            'day', 'day_name', 
            'time_slot', 'start_time', 'end_time', 
            'room', 'room_number', 'group_name'
        ]




# Added serializers for the new dynamic models so they can be accessed via API if needed
class DaySerializer(serializers.ModelSerializer):
    class Meta:
        model = Day
        fields = ['id', 'name', 'order']

class RoomTypeSerializer(serializers.ModelSerializer):
    class Meta:
        model = RoomType
        fields = ['id', 'name']

class RoomSubTypeSerializer(serializers.ModelSerializer):
    main_type_name = serializers.CharField(source='main_type.name', read_only=True)
    class Meta:
        model = RoomSubType
        fields = ['id', 'name', 'main_type', 'main_type_name']

class DepartmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Department
        fields = ['id', 'name']

class SemesterSerializer(serializers.ModelSerializer):
    class Meta:
        model = Semester
        fields = ['id', 'name', 'order']

# 1. Updated RoomSerializer
class RoomSerializer(serializers.ModelSerializer):
    department_name = serializers.CharField(source='department.name', read_only=True)
    
    # Fetching string names for frontend display
    room_type_name = serializers.CharField(source='room_type.name', read_only=True)
    room_sub_type_name = serializers.CharField(source='room_sub_type.name', read_only=True)
    
    class Meta:
        model = Room
        fields = [
            'id', 'room_number', 'capacity', 
            'room_type', 'room_type_name', 
            'room_sub_type', 'room_sub_type_name', 
            'department', 'department_name'
        ]


class CourseSerializer(serializers.ModelSerializer):
    teacher_name = serializers.CharField(source='teacher.username', read_only=True)
    department_name = serializers.CharField(source='department.name', read_only=True)
    semester_name = serializers.CharField(source='semester.name', read_only=True)
    
    # Fetching string names for frontend convenience
    fixed_room_number = serializers.CharField(source='fixed_room.room_number', read_only=True)
    course_type_name = serializers.CharField(source='course_type.name', read_only=True)
    course_sub_type_name = serializers.CharField(source='course_sub_type.name', read_only=True)

    # UPDATE: Removed 'fixed_day_name' completely as it no longer exists in the database.

    class Meta:
        model = Course
        fields = [
            'id', 'course_code', 'course_name', 'student_count',
            'credits', 
            'course_type', 'course_type_name', 
            'course_sub_type', 'course_sub_type_name',
            'teacher', 'teacher_name',
            'department', 'department_name',
            'semester', 'semester_name',
            'fixed_room', 'fixed_room_number'
         
        ]
    teacher_name = serializers.CharField(source='teacher.username', read_only=True)
    department_name = serializers.CharField(source='department.name', read_only=True)
    semester_name = serializers.CharField(source='semester.name', read_only=True)
    
    # Fetching string names for frontend convenience
    fixed_room_number = serializers.CharField(source='fixed_room.room_number', read_only=True)
    course_type_name = serializers.CharField(source='course_type.name', read_only=True)
    course_sub_type_name = serializers.CharField(source='course_sub_type.name', read_only=True)
    fixed_day_name = serializers.CharField(source='fixed_day.name', read_only=True)

    class Meta:
        model = Course
        fields = [
            'id', 'course_code', 'course_name', 'student_count',
            'credits', 
            'course_type', 'course_type_name', 
            'course_sub_type', 'course_sub_type_name',
            'teacher', 'teacher_name',
            'department', 'department_name',
            'semester', 'semester_name',
            'fixed_room', 'fixed_room_number', 
            'fixed_day', 'fixed_day_name', 
            'fixed_time_slot'
        ]



class TimeSlotSerializer(serializers.ModelSerializer):
    class Meta:
        model = TimeSlot
        # Ekhane 'is_lunch_break' field ta notun kore add kora holo
        fields = ['id', 'start_time', 'end_time', 'is_lunch_break']



from rest_framework import serializers
# ... আপনার মডেল ইমপোর্টগুলো 

class RoutineEntrySerializer(serializers.ModelSerializer):
    day_name = serializers.CharField(source='day.name', read_only=True)
    course_name = serializers.CharField(source='course.course_name', read_only=True)
    course_code = serializers.CharField(source='course.course_code', read_only=True)

    course_type = serializers.SerializerMethodField()
    
    teacher_id = serializers.SerializerMethodField()  # <-- ১. নতুন ফিল্ড যোগ করা হলো
    teacher_name = serializers.SerializerMethodField()
    department_name = serializers.SerializerMethodField()
    semester_name = serializers.SerializerMethodField()
    
    room_number = serializers.CharField(source='room.room_number', read_only=True)
    start_time = serializers.TimeField(source='time_slot.start_time', read_only=True)
    end_time = serializers.TimeField(source='time_slot.end_time', read_only=True)
    credits = serializers.IntegerField(source='course.credits', read_only=True)

    class Meta:
        model = RoutineEntry
        fields = [
            'id',             # এটি হলো requester_routine_id বা target_routine_id
            'day', 
            'day_name',
            'start_time', 
            'end_time',
            'course_name', 
            'course_code', 
            'course_type',    
            'credits',       
            'teacher_id',     # <-- ২. fields লিস্টে যোগ করা হলো (এটি হলো target_teacher_id)
            'teacher_name', 
            'department_name', 
            'semester_name', 
            'room_number',
            'group_name',
            'is_cancelled',   
            'cancel_message'  
        ]

    def get_course_type(self, obj):
        return obj.course.course_type.name if obj.course and obj.course.course_type else "N/A"

    # <-- ৩. টিচারের আইডি বের করার লজিক (Error Free)
    def get_teacher_id(self, obj):
        return obj.course.teacher.id if obj.course and obj.course.teacher else None

    def get_teacher_name(self, obj):
        return obj.course.teacher.username if obj.course and obj.course.teacher else "No Teacher"

    def get_department_name(self, obj):
        return obj.course.department.name if obj.course and obj.course.department else "N/A"

    def get_semester_name(self, obj):
        return obj.course.semester.name if obj.course and obj.course.semester else "N/A"


# ==============================================================================
# 5. NOTIFICATION SERIALIZER
# ==============================================================================
class NotificationSerializer(serializers.ModelSerializer):
    sender_name = serializers.CharField(source='sender.username', read_only=True, default="System")
    created_at_formatted = serializers.DateTimeField(source='created_at', format="%Y-%m-%d %I:%M %p", read_only=True)

    class Meta:
        model = Notification
        fields = [
            'id', 'recipient', 'sender', 'sender_name', 
            'notification_type', 'title', 'message', 
            'action_url', 'is_read', 'created_at', 'created_at_formatted'
        ]



# ==============================================================================
# 6. ACTIVITY LOG SERIALIZER
# ==============================================================================
class ActivityLogSerializer(serializers.ModelSerializer):
    actor_name = serializers.CharField(source='actor.username', read_only=True, default="System")
    created_at_formatted = serializers.DateTimeField(source='created_at', format="%Y-%m-%d %I:%M %p", read_only=True)

    class Meta:
        model = ActivityLog
        fields = [
            'id', 'actor', 'actor_name', 'action_description', 
            'severity', 'created_at', 'created_at_formatted'
        ]