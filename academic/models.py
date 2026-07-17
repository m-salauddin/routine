# academic/models.py
from django.db import models
from django.conf import settings


# ==============================================================================
# 0. MASTER BASE MODEL (For Audit Trails & Soft Delete)
# ==============================================================================
class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    is_active = models.BooleanField(
        default=True, 
        help_text="Soft Delete: Uncheck to archive/hide this record instead of permanently deleting."
    )

    class Meta:
        abstract = True


# ==============================================================================
# 1. CORE LOOKUP MODELS
# ==============================================================================
class Day(models.Model):
    name = models.CharField(max_length=15, unique=True, help_text="e.g., Sunday, Monday")
    order = models.PositiveIntegerField(default=0)

    def __str__(self):
        return self.name

    class Meta:
        ordering = ['order']

class TimeSlot(models.Model):
    start_time = models.TimeField()
    end_time = models.TimeField()
    is_lunch_break = models.BooleanField(default=False, help_text="Global lunch break flag.")

    def __str__(self):
        return f"{self.start_time.strftime('%I:%M %p')} - {self.end_time.strftime('%I:%M %p')}"


class RoomType(models.Model):
    name = models.CharField(max_length=100, unique=True, help_text="e.g., Theory, Lab")
    def __str__(self): return self.name

class RoomSubType(models.Model):
    main_type = models.ForeignKey(RoomType, on_delete=models.CASCADE, related_name='sub_types')
    name = models.CharField(max_length=100)
    def __str__(self): return f"{self.main_type.name} - {self.name}"


# ==============================================================================
# 2. UNIVERSITY STRUCTURE MODELS (Inheriting TimeStampedModel)
# ==============================================================================
class Department(TimeStampedModel):
    name = models.CharField(max_length=100, unique=True)
    def __str__(self): return self.name

class Semester(TimeStampedModel):
    name = models.CharField(max_length=100, unique=True)
    order = models.PositiveIntegerField(unique=True)
    def __str__(self): return self.name

# --- NEW: BATCH MODEL FOR ALUMNI & LIFECYCLE MANAGEMENT ---
class Batch(TimeStampedModel):
    STATUS_CHOICES = (
        ('ACTIVE', 'Active (Currently Studying)'),
        ('GRADUATED', 'Graduated / Alumni (Archived)'),
    )
    name = models.CharField(max_length=50, help_text="e.g., 25th Batch, Spring 2026")
    department = models.ForeignKey(Department, on_delete=models.CASCADE, related_name='batches')
    current_semester = models.ForeignKey(Semester, on_delete=models.SET_NULL, null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='ACTIVE')

    class Meta:
        verbose_name_plural = "Batches"
        # [NEW] এই লাইনের কারণে একই ডিপার্টমেন্টে একই নামের ব্যাচ দুইবার তৈরি করা যাবে না
        unique_together = ('name', 'department') 

    def __str__(self):
        sem_name = self.current_semester.name if self.current_semester else "No Semester"
        return f"{self.name} - {self.department.name} ({sem_name})"

    def save(self, *args, **kwargs):
        is_new = self.pk is None
        old_semester = None
        old_status = None
        
        if not is_new:
            old_batch = Batch.objects.filter(pk=self.pk).first()
            if old_batch:
                old_semester = old_batch.current_semester
                old_status = getattr(old_batch, 'status', None)

        super().save(*args, **kwargs)

        from django.contrib.auth import get_user_model
        User = get_user_model()

        if not is_new:
            # AUTOMATION 1: 
            if old_semester != self.current_semester:
                User.objects.filter(batch=self, role='STUDENT').update(semester=self.current_semester)

            # AUTOMATION 2: 
            if old_status == 'ACTIVE' and self.status == 'GRADUATED':
                # AUTOMATION 2: If batch is marked as GRADUATED, deactivate students and clear their semester
                User.objects.filter(batch=self, role='STUDENT').update(semester=None, is_active=False)

class Room(TimeStampedModel):
    room_number = models.CharField(max_length=50, unique=True)
    capacity = models.PositiveIntegerField(help_text="Student capacity of this room")
    room_type = models.ForeignKey(RoomType, on_delete=models.CASCADE)
    room_sub_type = models.ForeignKey(RoomSubType, on_delete=models.SET_NULL, null=True, blank=True)
    department = models.ForeignKey(Department, on_delete=models.SET_NULL, null=True, blank=True)

    def __str__(self):
        return f"{self.room_number} ({self.room_type.name} - Cap: {self.capacity})"


# ==============================================================================
# 3. ACADEMIC & COURSE MODELS (Cross-Department Architecture)
# ==============================================================================

class Course(TimeStampedModel):
    course_name = models.CharField(max_length=255)
    course_code = models.CharField(max_length=50, unique=True)
    
    # Target: The students who are taking this course (e.g., CSE students)
    department = models.ForeignKey(Department, related_name='targeted_courses', on_delete=models.CASCADE)
    semester = models.ForeignKey(Semester, on_delete=models.CASCADE)
    
    # --- NEW CROSS-DEPARTMENT FIELDS ---
    # Offering: The department that teaches it (e.g., Math Dept)
    offering_department = models.ForeignKey(
        Department, related_name='offered_courses', on_delete=models.SET_NULL, null=True, blank=True,
        help_text="Who teaches this? Leave blank if same as Target Department."
    )
    # Preferred Room: If Math Dept teaches CSE, but wants to use CSE rooms
    preferred_room_department = models.ForeignKey(
        Department, related_name='preferred_room_courses', on_delete=models.SET_NULL, null=True, blank=True,
        help_text="Force algorithm to look for rooms in this specific department first."
    )
    
    teacher = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, 
        limit_choices_to={'role': 'TEACHER'}
    )
    credits = models.IntegerField()
    student_count = models.IntegerField(default=0)
    
    course_type = models.ForeignKey(RoomType, on_delete=models.CASCADE)
    course_sub_type = models.ForeignKey(RoomSubType, on_delete=models.SET_NULL, null=True, blank=True)

    # Fixed Routine Constraints
    # UPDATE: Removed fixed_day and fixed_time_slot. Only fixed_room remains.
    fixed_room = models.ForeignKey(
        Room, on_delete=models.SET_NULL, null=True, blank=True,
        help_text="Force the algorithm to always use this specific room for all classes of this course."
    )

    def __str__(self):
        return f"{self.course_code} - {self.course_name}"

    def get_offering_dept(self):
        # Fallback to target department if offering department is not set
        return self.offering_department if self.offering_department else self.department

# ==============================================================================
# 4. ROUTINE & CONSTRAINT MODELS
# ==============================================================================

class RoutineEntry(TimeStampedModel):
    day = models.ForeignKey(Day, on_delete=models.CASCADE)
    time_slot = models.ForeignKey(TimeSlot, on_delete=models.CASCADE)
    course = models.ForeignKey(Course, on_delete=models.CASCADE)
    room = models.ForeignKey(Room, on_delete=models.CASCADE, null=True, blank=True)
    group_name = models.CharField(max_length=50, null=True, blank=True)

    is_cancelled = models.BooleanField(default=False)
    cancel_message = models.TextField(null=True, blank=True)
    
    #  Admin Pre-Assigned Flag ---
    is_fixed = models.BooleanField(default=False, help_text="True if this class is pre-assigned by Admin")

    class Meta:
        #  'group_name' to allow parallel lab groups for the same course
        unique_together = (
            ('day', 'time_slot', 'room'), 
            ('day', 'time_slot', 'course', 'group_name')
        )

    def __str__(self):
        fixed_mark = "[FIXED] " if self.is_fixed else ""
        return f"{fixed_mark}{self.day.name} | {self.time_slot} | {self.course.course_code} | Room: {self.room}"
    

# --- model to store fixed class schedules (pre-assigned by admin) ---
class FixedClassSchedule(TimeStampedModel):
    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name='fixed_schedules')
    day = models.ForeignKey(Day, on_delete=models.CASCADE)
    time_slot = models.ForeignKey(TimeSlot, on_delete=models.CASCADE)
    
    # room is optional; if not set, the algorithm can assign any available room of the correct type
    room = models.ForeignKey(Room, on_delete=models.CASCADE, null=True, blank=True, help_text="Optional: Fix a specific room too")
    group_name = models.CharField(max_length=50, null=True, blank=True)

    class Meta:
        # Ensure that each course can only have one fixed schedule per day and time slot
        unique_together = (('day', 'time_slot', 'course'),)
        ordering = ['day__order', 'time_slot__start_time']

    def __str__(self):
        return f"FIXED: {self.course.course_code} - {self.day.name} ({self.time_slot})"
class BatchTimeConstraint(TimeStampedModel):
    CONSTRAINT_CHOICES = (
        ('CLASS_OFF', 'Class Off / Blocked'),
        ('FORCE_ALLOW_LUNCH_CLASS', 'Force Allow Class During Lunch'),
    )
    department = models.ForeignKey(Department, on_delete=models.CASCADE)
    semester = models.ForeignKey(Semester, on_delete=models.CASCADE)
    
    # NEW: Link specifically to a Batch (Optional, for batch-specific blocks)
    batch = models.ForeignKey(Batch, on_delete=models.CASCADE, null=True, blank=True, help_text="Optional: Apply only to a specific batch")
    
    day = models.ForeignKey(Day, on_delete=models.CASCADE)
    time_slot = models.ForeignKey(TimeSlot, on_delete=models.CASCADE)
    constraint_type = models.CharField(max_length=50, choices=CONSTRAINT_CHOICES, default='CLASS_OFF')

    class Meta:
        unique_together = ('department', 'semester', 'batch', 'day', 'time_slot')

    def __str__(self):
        return f"Rule: {self.department.name} | {self.day.name} | {self.get_constraint_type_display()}"


class SystemSetting(models.Model):
    is_routine_locked = models.BooleanField(default=False)
    last_updated = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        if not self.pk and SystemSetting.objects.exists(): return
        super(SystemSetting, self).save(*args, **kwargs)


class RoutineBackup(models.Model):
    department = models.ForeignKey(Department, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)
    backup_data = models.JSONField()

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"Backup: {self.department.name} | {self.created_at}"
    

# academic/models.py (Add at the bottom)

class SystemBackup(models.Model):
    name = models.CharField(max_length=255, help_text="Backup er ekta nam din (e.g., Before Midterm)")
    backup_data = models.TextField(help_text="Full database snapshot in JSON format")
    created_at = models.DateTimeField(auto_now_add=True)
    
   
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.name} - {self.created_at.strftime('%Y-%m-%d %H:%M')}"
    


# ==============================================================================
# 5. TEMPORARY SWAP & PROXY MANAGEMENT
# ==============================================================================

class TemporarySwapRequest(TimeStampedModel):
    SWAP_TYPE_CHOICES = (
        ('PROXY', 'Proxy / Substitution (Teacher B takes Teacher A\'s class)'),
        ('MUTUAL', 'Mutual Time Swap (Exchange of class times)'),
    )
    STATUS_CHOICES = (
        ('PENDING', 'Pending'),
        ('ACCEPTED', 'Accepted'),
        ('REJECTED', 'Rejected'),
        ('CANCELLED', 'Cancelled by Requester'),
    )

    swap_type = models.CharField(max_length=10, choices=SWAP_TYPE_CHOICES, default='PROXY')
    
    # Teachers involved
    requester = models.ForeignKey(settings.AUTH_USER_MODEL, related_name='swap_requests_sent', on_delete=models.CASCADE)
    target_teacher = models.ForeignKey(settings.AUTH_USER_MODEL, related_name='swap_requests_received', on_delete=models.CASCADE)
    
    # Routines involved
    requester_routine = models.ForeignKey(RoutineEntry, related_name='swap_requests_as_requester', on_delete=models.CASCADE)
    target_routine = models.ForeignKey(
        RoutineEntry, related_name='swap_requests_as_target', 
        on_delete=models.CASCADE, null=True, blank=True,
        help_text="Only required if swap_type is MUTUAL"
    )
    
    swap_date = models.DateField(help_text="The specific date for this temporary swap")
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='PENDING')
    reason = models.TextField(null=True, blank=True, help_text="Reason for the swap request")

    class Meta:
        ordering = ['-swap_date', '-created_at']

    def __str__(self):
        return f"{self.get_swap_type_display()} | {self.requester.username} -> {self.target_teacher.username} | Date: {self.swap_date} | {self.status}"
    




# ==============================================================================
# 6. SYSTEM NOTIFICATIONS (Event-Driven Alerts)
# ==============================================================================




class Notice(TimeStampedModel):
    NOTICE_TYPES = (
        ('GLOBAL', 'Global Notice (All Users)'),
        ('TARGETED', 'Targeted Notice (Specific Dept/Batch)'), 
    )
    
    sender = models.ForeignKey(settings.AUTH_USER_MODEL, related_name='sent_notices', on_delete=models.CASCADE)
    notice_type = models.CharField(max_length=20, choices=NOTICE_TYPES, default='TARGETED')
    title = models.CharField(max_length=255)
    message = models.TextField()
    
   
    target_departments = models.ManyToManyField('Department', blank=True, related_name='notices')
    target_batches = models.ManyToManyField('Batch', blank=True, related_name='notices')
    
    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.notice_type} - {self.title} by {self.sender.username}"

class Notification(TimeStampedModel):
    NOTIFICATION_TYPES = (
        ('SWAP_REQ', 'Swap Request Received'),
        ('SWAP_ACC', 'Swap Request Accepted'),
        ('SWAP_REJ', 'Swap Request Rejected'),
        ('CLASS_DEL', 'Class Cancelled'),
        ('ADMIN_MSG', 'Admin Message'),
        ('NOTICE', 'System Notice'),
    )

    recipient = models.ForeignKey(settings.AUTH_USER_MODEL, related_name='notifications', on_delete=models.CASCADE)
    sender = models.ForeignKey(
        settings.AUTH_USER_MODEL, related_name='sent_notifications', 
        on_delete=models.SET_NULL, null=True, blank=True
    )
    
   
    related_notice = models.ForeignKey(Notice, on_delete=models.CASCADE, null=True, blank=True, related_name='generated_notifications')
    
    notification_type = models.CharField(max_length=20, choices=NOTIFICATION_TYPES)
    title = models.CharField(max_length=255)
    message = models.TextField()
    action_url = models.CharField(
        max_length=255, null=True, blank=True, 
        help_text="Frontend URL to redirect when user clicks the notification"
    )
    is_read = models.BooleanField(default=False)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"To {self.recipient.username} - {self.title} [Read: {self.is_read}]"
# #academic/models.py
# class Notice(TimeStampedModel):
#     NOTICE_TYPES = (
#         ('GLOBAL', 'Global Notice (All Users)'),
#         ('TARGETED', 'Targeted Notice (Specific Dept/Semester/Batch)'),
#     )
    
#     sender = models.ForeignKey(settings.AUTH_USER_MODEL, related_name='sent_notices', on_delete=models.CASCADE)
#     notice_type = models.CharField(max_length=20, choices=NOTICE_TYPES, default='TARGETED')
#     title = models.CharField(max_length=255)
#     message = models.TextField()
    
#     # Target Audience (M2M fields)
#     target_departments = models.ManyToManyField('Department', blank=True, related_name='notices')
#     target_semesters = models.ManyToManyField('Semester', blank=True, related_name='notices')
#     target_batches = models.ManyToManyField('Batch', blank=True, related_name='notices') # [NEW] ব্যাচ টার্গেট করার জন্য
    
#     class Meta:
#         ordering = ['-created_at']

#     def __str__(self):
#         return f"{self.notice_type} - {self.title} by {self.sender.username}"


# class Notification(TimeStampedModel):
#     NOTIFICATION_TYPES = (
#         ('SWAP_REQ', 'Swap Request Received'),
#         ('SWAP_ACC', 'Swap Request Accepted'),
#         ('SWAP_REJ', 'Swap Request Rejected'),
#         ('CLASS_DEL', 'Class Cancelled'),
#         ('ADMIN_MSG', 'Admin Message'),
#         ('NOTICE', 'System Notice'), 
#     )

#     recipient = models.ForeignKey(settings.AUTH_USER_MODEL, related_name='notifications', on_delete=models.CASCADE)
#     sender = models.ForeignKey(
#         settings.AUTH_USER_MODEL, related_name='sent_notifications', 
#         on_delete=models.SET_NULL, null=True, blank=True
#     )
    
    
#     related_notice = models.ForeignKey(Notice, on_delete=models.CASCADE, null=True, blank=True, related_name='generated_notifications')
    
#     notification_type = models.CharField(max_length=20, choices=NOTIFICATION_TYPES)
#     title = models.CharField(max_length=255)
#     message = models.TextField()
#     action_url = models.CharField(
#         max_length=255, null=True, blank=True, 
#         help_text="Frontend URL to redirect when user clicks the notification"
#     )
#     is_read = models.BooleanField(default=False)

#     class Meta:
#         ordering = ['-created_at']

#     def __str__(self):
#         return f"To {self.recipient.username} - {self.title} [Read: {self.is_read}]"


# ==============================================================================
# 7. SYSTEM ACTIVITY LOGS (Audit Trail)
# ==============================================================================
class ActivityLog(models.Model):
    SEVERITY_CHOICES = (
        ('INFO', 'Information'),       # General actions (e.g., Logins, viewing)
        ('SUCCESS', 'Success'),        # Positive actions (e.g., Swap accepted)
        ('WARNING', 'Warning'),        # Cautious actions (e.g., Class cancelled)
        ('DANGER', 'Danger'),          # Critical actions (e.g., Deletions, Reset)
    )

    # who performed the action (e.g., admin, teacher)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, related_name='performed_logs', 
        on_delete=models.SET_NULL, null=True, blank=True
    )
    
    # who is affected by this action (e.g., students, teachers)
    related_users = models.ManyToManyField(
        settings.AUTH_USER_MODEL, related_name='related_logs', blank=True
    )
    
    action_description = models.CharField(max_length=500)
    severity = models.CharField(max_length=20, choices=SEVERITY_CHOICES, default='INFO')
    
    # who can see this log and who has hidden it
    hidden_by = models.ManyToManyField(
        settings.AUTH_USER_MODEL, related_name='hidden_logs', blank=True
    )
    
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        actor_name = self.actor.username if self.actor else "System"
        return f"[{self.severity}] {actor_name}: {self.action_description}"