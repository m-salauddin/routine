# academic/admin.py
import datetime
from django.contrib import admin
from import_export import resources, fields
from import_export.widgets import ForeignKeyWidget, Widget
from import_export.admin import ImportExportModelAdmin
from django.contrib.auth import get_user_model
from django.urls import path
from django.shortcuts import redirect
from django.contrib import messages
from django.http import HttpResponseRedirect
from django.urls import reverse
from .models import Notice

admin.site.register(Notice)


from .models import (
    Day, TimeSlot, RoomType, RoomSubType, Department, 
    Semester, Batch, Room, Course, RoutineEntry, 
    BatchTimeConstraint, SystemSetting, RoutineBackup,FixedClassSchedule
)

User = get_user_model()



@admin.register(FixedClassSchedule)
class FixedClassScheduleAdmin(admin.ModelAdmin):
    list_display = ('course', 'day', 'time_slot', 'room', 'group_name')
    list_filter = ('day', 'course__department', 'course__semester')
    search_fields = ('course__course_name', 'course__course_code')
    ordering = ('day__order', 'time_slot__start_time')
    
    # Admin form fieldsets to organize the fields in the admin interface
    fieldsets = (
        ('Course & Time', {
            'fields': ('course', 'day', 'time_slot')
        }),
        ('Optional Settings', {
            'fields': ('room', 'group_name'),
            'classes': ('collapse',),
        }),
    )


# CUSTOM WIDGETS (For Two-way Export & Import)

class TimeSlotWidget(Widget):
    """
    Time Slot ke Export ar Import korar Custom Logic.
    """
    def clean(self, value, row=None, *args, **kwargs):
        if not value or value == "N/A":
            return None
        try:
            # Excel theke "14:30 - 15:20" format e data asbe
            start_str = str(value).split('-')[0].strip()
            start_time = datetime.datetime.strptime(start_str, '%H:%M').time()
            return TimeSlot.objects.get(start_time=start_time)
        except Exception as e:
            raise ValueError(f"Time Slot er format thik nei: {value}. Import korar jonno 'HH:MM - HH:MM' format use korun.")

    # FIXED: Added **kwargs to accept unexpected arguments like 'export_fields'
    def render(self, value, obj=None, **kwargs):
        if not value:
            return "N/A"
        start = value.start_time.strftime('%H:%M')
        end = value.end_time.strftime('%H:%M')
        return f"{start} - {end}"
# ==============================================================================
# PROFESSIONAL RESOURCES
# ==============================================================================
class DepartmentResource(resources.ModelResource):
    class Meta:
        model = Department
        skip_unchanged = True
        import_id_fields = ('name',)


class SemesterResource(resources.ModelResource):
    class Meta:
        model = Semester
        skip_unchanged = True
        import_id_fields = ('name',)

class UserResource(resources.ModelResource):
    class Meta:
        model = User
        fields = ('id', 'username', 'first_name', 'last_name', 'email', 'is_staff', 'is_active')
        export_order = fields
        skip_unchanged = True
        import_id_fields = ('username',)

class BatchResource(resources.ModelResource):
    department = fields.Field(
        column_name='department',
        attribute='department',
        widget=ForeignKeyWidget(Department, 'name')
    )
    current_semester = fields.Field(
        column_name='current_semester',
        attribute='current_semester',
        widget=ForeignKeyWidget(Semester, 'name')
    )

    class Meta:
        model = Batch
        import_id_fields = ('name',)
        skip_unchanged = True
        report_skipped = True
        fields = ('id', 'name', 'department', 'current_semester', 'status', 'is_active')


class CourseResource(resources.ModelResource):
    department = fields.Field(
        column_name='department',
        attribute='department',
        widget=ForeignKeyWidget(Department, 'name')
    )
    semester = fields.Field(
        column_name='semester',
        attribute='semester',
        widget=ForeignKeyWidget(Semester, 'name')
    )
    teacher = fields.Field(
        column_name='teacher',
        attribute='teacher',
        widget=ForeignKeyWidget(User, 'username')
    )
    
    # UPDATE: Added Fixed Room for seamless Import/Export
    fixed_room = fields.Field(
        column_name='fixed_room',
        attribute='fixed_room',
        widget=ForeignKeyWidget(Room, 'room_number')
    )

    class Meta:
        model = Course
        import_id_fields = ('course_code',)
        skip_unchanged = True
        report_skipped = True
        
        # UPDATE: Explicitly defining fields to prevent import errors from old Excel sheets
        fields = (
            'id', 'course_name', 'course_code', 'department', 'semester', 
            'offering_department', 'preferred_room_department', 'teacher', 
            'credits', 'student_count', 'course_type', 'course_sub_type', 
            'fixed_room'
        )


class RoomResource(resources.ModelResource):
    department = fields.Field(
        column_name='department',
        attribute='department',
        widget=ForeignKeyWidget(Department, 'name')
    )
    room_type = fields.Field(
        column_name='room_type',
        attribute='room_type',
        widget=ForeignKeyWidget(RoomType, 'name')
    )

    class Meta:
        model = Room
        import_id_fields = ('room_number',)
        skip_unchanged = True
        report_skipped = True

class RoutineEntryResource(resources.ModelResource):
    day = fields.Field(
        column_name='Day',
        attribute='day',
        widget=ForeignKeyWidget(Day, 'name') 
    )
    
    # MAGIC: Ekhane amader notun banano TimeSlotWidget use kora hoyeche
    time_slot = fields.Field(
        column_name='Time Slot',
        attribute='time_slot',
        widget=TimeSlotWidget() 
    )
    
    course = fields.Field(
        column_name='Course Name',
        attribute='course',
        widget=ForeignKeyWidget(Course, 'course_name') 
    )
    
    room = fields.Field(
        column_name='Room',
        attribute='room',
        widget=ForeignKeyWidget(Room, 'room_number') 
    )

    class Meta:
        model = RoutineEntry
        fields = ('id', 'day', 'time_slot', 'course', 'room', 'group_name', 'is_cancelled')
        export_order = ('id', 'day', 'time_slot', 'course', 'room', 'group_name', 'is_cancelled')


# ==============================================================================
# IMPORT/EXPORT ADMIN CLASSES
# ==============================================================================
@admin.register(Department)
class DepartmentAdmin(ImportExportModelAdmin):
    resource_class = DepartmentResource
    list_display = ('name', 'is_active', 'created_at')
    search_fields = ('name',)
    list_filter = ('is_active',)

@admin.register(Semester)
class SemesterAdmin(ImportExportModelAdmin):
    list_display = ('name', 'order', 'is_active')
    ordering = ('order',)

@admin.register(Batch)
class BatchAdmin(ImportExportModelAdmin):
    resource_class = BatchResource
    list_display = ('name', 'department', 'current_semester', 'status', 'is_active')
    list_filter = ('department', 'status', 'is_active')
    search_fields = ('name',)

@admin.register(Room)
class RoomAdmin(ImportExportModelAdmin):
    resource_class = RoomResource
    list_display = ('room_number', 'capacity', 'room_type', 'department', 'is_active')
    list_filter = ('room_type', 'department', 'is_active')
    search_fields = ('room_number',)




@admin.register(Course)
class CourseAdmin(ImportExportModelAdmin):
    resource_class = CourseResource
    # UPDATE: Added 'fixed_room' at the end to easily spot fixed classes in the admin table
    list_display = ('course_code', 'course_name', 'credits', 'course_type', 'department', 'teacher', 'fixed_room', 'is_active')
    
    # UPDATE: Added 'fixed_room' to the filter so admins can filter courses that have fixed rooms
    list_filter = ('department', 'semester', 'course_type', 'fixed_room', 'is_active')
    
    search_fields = ('course_name', 'course_code', 'teacher__username')

# academic/admin.py er vitorer RoutineEntryAdmin class ti update korun


@admin.register(RoutineEntry)
class RoutineEntryAdmin(ImportExportModelAdmin):
    resource_class = RoutineEntryResource
    
    list_display = ('get_day_name', 'time_slot', 'get_course_name', 'group_name', 'get_department', 'get_room_number', 'get_teacher', 'is_cancelled', 'is_active')
    list_filter = ('day', 'course__department', 'course__semester', 'is_cancelled', 'is_active')
    search_fields = ('course__course_code', 'room__room_number')

    # --------------------------------------------------------------------------
    # MANUALLY ADD CUSTOM URLS FOR DJANGO ADMIN BUTTONS
    # --------------------------------------------------------------------------
    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path('global-excel-export/', self.admin_site.admin_view(self.global_excel_export_action), name='global-excel-export'),
        ]
        return custom_urls + urls

    def global_excel_export_action(self, request):
        """ Admin panel er button e click korle direct amader views er logic trigger hobe """
        from .views import SystemExcelSyncView
        # View call kore response return kora
        view_instance = SystemExcelSyncView.as_view()
        # GET request call hobe export er jonno
        return view_instance(request)

    # --------------------------------------------------------------------------
    # Model Display Methods (Ager motoi)
    # --------------------------------------------------------------------------
    def get_day_name(self, obj): return obj.day.name if obj.day else "N/A"
    get_day_name.short_description = 'Day'

    def get_course_name(self, obj): return obj.course.course_name
    get_course_name.short_description = 'Course'

    def get_department(self, obj): return obj.course.department.name
    get_department.short_description = 'Department'

    def get_room_number(self, obj): return obj.room.room_number if obj.room else "N/A"
    get_room_number.short_description = 'Room No'

    def get_teacher(self, obj): return obj.course.teacher.username if obj.course.teacher else "N/A"
    get_teacher.short_description = 'Teacher'
@admin.register(BatchTimeConstraint)
class BatchTimeConstraintAdmin(ImportExportModelAdmin):
    list_display = ('department', 'semester', 'day', 'time_slot', 'constraint_type', 'is_active')

admin.site.register(Day)
admin.site.register(TimeSlot)
admin.site.register(RoomType)
admin.site.register(RoomSubType)

# ==============================================================================
# SECURE/READ-ONLY ADMIN CLASSES
# ==============================================================================
@admin.register(SystemSetting)
class SystemSettingAdmin(admin.ModelAdmin):
    list_display = ('__str__', 'last_updated')
    
    def has_add_permission(self, request):
        if self.model.objects.exists():
            return False
        return super().has_add_permission(request)

@admin.register(RoutineBackup)
class RoutineBackupAdmin(admin.ModelAdmin):
    list_display = ('department', 'created_at')
    readonly_fields = ('department', 'created_at', 'backup_data')
    
    def has_add_permission(self, request):
        return False
    
    def has_change_permission(self, request, obj=None):
        return False
    


# academic/admin.py
from django.contrib import admin
from .models import AlgorithmConfig

@admin.register(AlgorithmConfig)
class AlgorithmConfigAdmin(admin.ModelAdmin):
    list_display = ('__str__', 'parallel_bonus', 'edge_slot_penalty', 'zero_gap_bonus')

    # শুধু একটি ডাটা থাকবে, তাই নতুন করে Add করার অপশন অফ করে দিচ্ছি (যদি অলরেডি একটা থাকে)
    def has_add_permission(self, request):
        if AlgorithmConfig.objects.exists():
            return False
        return True