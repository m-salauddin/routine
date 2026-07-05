# academic/utils.py
import random
import math
from django.db import transaction
from .models import (
    Day, Course, TimeSlot, RoutineEntry, Room, 
    SystemSetting, RoutineBackup, BatchTimeConstraint, FixedClassSchedule
)

class ScheduleConstraint:
    def __init__(self, days, time_slots, batch_constraints_dict, teacher_totals, batch_totals):
        self.teacher_occupied = set()
        self.room_occupied = set()
        self.batch_occupied = set()
        self.course_daily_tracker = set()
        self.teacher_batch_interaction = {}

        self.day_loads = {day.id: 0 for day in days}
        self.teacher_daily_count = {}
        self.batch_daily_count = {}
        
        self.batch_day_loads = {}
        
        self.batch_constraints = batch_constraints_dict
        
        total_days = max(1, len(days))
        self.teacher_limits = {
            tid: math.ceil(total / total_days) + 1 
            for tid, total in teacher_totals.items()
        }
        self.batch_limits = {
            bid: math.ceil(total / total_days) + 2 
            for bid, total in batch_totals.items()
        }

        self.slot_index_map = {slot.id: idx for idx, slot in enumerate(time_slots)}
        self.teacher_schedule_map = {} 
        self.batch_schedule_map = {}   

    def can_schedule_daily(self, day_id, course, duration):
        if course.teacher:
            t_limit = self.teacher_limits.get(course.teacher.id, 4)
            current_t_load = self.teacher_daily_count.get((course.teacher.id, day_id), 0)
            if current_t_load + duration > t_limit:
                return False
                
        batch_key = (course.department.id, course.semester.id)
        b_limit = self.batch_limits.get(batch_key, 6)
        current_b_load = self.batch_daily_count.get((batch_key[0], batch_key[1], day_id), 0)
        if current_b_load + duration > b_limit:
            return False
            
        return True

    def can_schedule_continuous(self, day_id, start_idx, duration, course):
        MAX_CONTINUOUS = 3

        batch_key = (day_id, course.department.id, course.semester.id)
        batch_occupied = self.batch_schedule_map.get(batch_key, set())

        left_idx = start_idx - 1
        left_count = 0
        while left_idx in batch_occupied:
            left_count += 1
            left_idx -= 1

        right_idx = start_idx + duration
        right_count = 0
        while right_idx in batch_occupied:
            right_count += 1
            right_idx += 1

        if left_count + duration + right_count > MAX_CONTINUOUS:
            return False

        if course.teacher:
            teacher_key = (day_id, course.teacher.id)
            teacher_occupied = self.teacher_schedule_map.get(teacher_key, set())

            left_idx = start_idx - 1
            left_count = 0
            while left_idx in teacher_occupied:
                left_count += 1
                left_idx -= 1

            right_idx = start_idx + duration
            right_count = 0
            while right_idx in teacher_occupied:
                right_count += 1
                right_idx += 1

            if left_count + duration + right_count > MAX_CONTINUOUS:
                return False

        return True

    def is_conflict(self, day, slot, course, room, group_name=None):
        day_id = day.id
        constraint_type = self.batch_constraints.get((course.department.id, course.semester.id, day_id, slot.id))
        
        if constraint_type == 'CLASS_OFF':
            return True
        if slot.is_lunch_break and constraint_type != 'FORCE_ALLOW_LUNCH_CLASS':
            return True
        if course.teacher and (day_id, slot.id, course.teacher.id) in self.teacher_occupied:
            return True
        if room and (day_id, slot.id, room.id) in self.room_occupied:
            return True
        if (day_id, slot.id, course.department.id, course.semester.id) in self.batch_occupied:
            return True

        is_lab = course.course_type and 'lab' in course.course_type.name.lower()
        if not is_lab and (course.id, group_name, day_id) in self.course_daily_tracker:
            return True

        if course.teacher:
            tb_key = (day_id, course.teacher.id, course.department.id, course.semester.id)
            if tb_key in self.teacher_batch_interaction and self.teacher_batch_interaction[tb_key] != course.id:
                return True

        return False

    def assign(self, day, slot, course, room, group_name=None):
        day_id = day.id
        slot_idx = self.slot_index_map[slot.id]

        if course.teacher:
            self.teacher_occupied.add((day_id, slot.id, course.teacher.id))
            self.teacher_batch_interaction[(day_id, course.teacher.id, course.department.id, course.semester.id)] = course.id
            self.teacher_daily_count[(course.teacher.id, day_id)] = self.teacher_daily_count.get((course.teacher.id, day_id), 0) + 1
            
            t_key = (day_id, course.teacher.id)
            if t_key not in self.teacher_schedule_map:
                self.teacher_schedule_map[t_key] = set()
            self.teacher_schedule_map[t_key].add(slot_idx)
        
        if room:
            self.room_occupied.add((day_id, slot.id, room.id))
        
        self.batch_occupied.add((day_id, slot.id, course.department.id, course.semester.id))
        self.course_daily_tracker.add((course.id, group_name, day_id))
        self.day_loads[day_id] += 1
        self.batch_daily_count[(course.department.id, course.semester.id, day_id)] = self.batch_daily_count.get((course.department.id, course.semester.id, day_id), 0) + 1
        
        self.batch_day_loads[(course.department.id, course.semester.id, day_id)] = self.batch_day_loads.get((course.department.id, course.semester.id, day_id), 0) + 1

        b_key = (day_id, course.department.id, course.semester.id)
        if b_key not in self.batch_schedule_map:
            self.batch_schedule_map[b_key] = set()
        self.batch_schedule_map[b_key].add(slot_idx)

# UPDATE: Added fixed_counts dictionary to handle remaining credits properly
def prepare_prioritized_sessions(courses, fixed_counts=None):
    if fixed_counts is None:
        fixed_counts = {}
        
    all_sessions = []
    for course in courses:
        total_credits = course.credits if course.credits > 0 else 1
        
        # Calculate remaining credits after deducting fixed classes
        remaining_credits = course.credits - fixed_counts.get(course.id, 0)
        if remaining_credits <= 0:
            continue  # Course is fully scheduled via Fixed Classes
            
        credits_filled = fixed_counts.get(course.id, 0)
        is_lab_course = course.course_type and 'lab' in course.course_type.name.lower()
        fixed_bonus = 1000 if (course.fixed_day or course.fixed_time_slot or course.fixed_room) else 0

        if is_lab_course:
            while remaining_credits >= 2:
                credits_filled += 2
                all_sessions.append({'course': course, 'duration': 2, 'priority_score': (credits_filled / total_credits) + fixed_bonus, 'is_lab': True})
                remaining_credits -= 2
            if remaining_credits > 0:
                credits_filled += 1
                all_sessions.append({'course': course, 'duration': 1, 'priority_score': (credits_filled / total_credits) + fixed_bonus, 'is_lab': True})
        else:
            for _ in range(remaining_credits):
                credits_filled += 1
                all_sessions.append({'course': course, 'duration': 1, 'priority_score': (credits_filled / total_credits) + fixed_bonus, 'is_lab': False})

    random.shuffle(all_sessions)
    all_sessions.sort(key=lambda x: (x['priority_score'], -x['duration']), reverse=True)
    return all_sessions

def get_valid_rooms_for_course(course, all_active_rooms, is_lab):
    if course.fixed_room and course.fixed_room.is_active:
        return [course.fixed_room]

    base_matching_rooms = [
        r for r in all_active_rooms
        if r.room_type_id == course.course_type_id 
        and (not course.course_sub_type_id or r.room_sub_type_id == course.course_sub_type_id)
    ]

    dept_to_search = course.preferred_room_department or course.offering_department or course.department
    dept_rooms = [r for r in base_matching_rooms if r.department == dept_to_search]
    valid_rooms = dept_rooms if dept_rooms else base_matching_rooms

    rooms_fitting_capacity = [r for r in valid_rooms if r.capacity >= course.student_count]

    if rooms_fitting_capacity:
        return sorted(rooms_fitting_capacity, key=lambda x: x.capacity)
    
    if is_lab and valid_rooms:
        valid_rooms.sort(key=lambda x: x.capacity, reverse=True)
        return [valid_rooms[0]]
    
    return []

def generate_routine_algorithm(department_id, semester_id=None, ignore_warnings=False):
    setting = SystemSetting.objects.first()
    if setting and setting.is_routine_locked:
        return {"status": "Locked", "message": "System is locked. Cannot generate routine."}

    with transaction.atomic():
        base_courses = Course.objects.select_related(
            'teacher', 'department', 'semester', 'course_type', 'course_sub_type', 
            'fixed_day', 'fixed_time_slot', 'fixed_room', 'preferred_room_department', 'offering_department'
        ).filter(department_id=department_id, is_active=True)

        if semester_id:
            courses_to_schedule = list(base_courses.filter(semester_id=semester_id))
            old_routines = RoutineEntry.objects.filter(course__department_id=department_id, course__semester_id=semester_id)
        else:
            courses_to_schedule = list(base_courses)
            old_routines = RoutineEntry.objects.filter(course__department_id=department_id)

        # UPDATE: Include is_fixed in Backup
        if old_routines.exists():
            backup_list = [{
                'day_id': e.day_id, 'time_slot_id': e.time_slot_id, 
                'course_id': e.course_id, 'room_id': e.room_id, 
                'group_name': e.group_name, 'is_fixed': getattr(e, 'is_fixed', False)
            } for e in old_routines]
            RoutineBackup.objects.create(department_id=department_id, backup_data=backup_list)

        # Delete all old routines
        old_routines.delete()

        # =====================================================================
        # NEW CORE LOGIC: Copy from FixedClassSchedule to RoutineEntry
        # =====================================================================
        if semester_id:
            fixed_schedules = FixedClassSchedule.objects.filter(course__department_id=department_id, course__semester_id=semester_id)
        else:
            fixed_schedules = FixedClassSchedule.objects.filter(course__department_id=department_id)
            
        fixed_routines_to_insert = []
        fixed_counts = {}
        for fs in fixed_schedules:
            fixed_routines_to_insert.append(RoutineEntry(
                day=fs.day,
                time_slot=fs.time_slot,
                course=fs.course,
                room=fs.room,
                group_name=fs.group_name,
                is_fixed=True
            ))
            fixed_counts[fs.course_id] = fixed_counts.get(fs.course_id, 0) + 1
            
        if fixed_routines_to_insert:
            RoutineEntry.objects.bulk_create(fixed_routines_to_insert)
        # =====================================================================

        days = list(Day.objects.all().order_by('order'))
        time_slots = list(TimeSlot.objects.all().order_by('start_time'))
        total_slots = len(time_slots)
        all_active_rooms = list(Room.objects.filter(is_active=True))

        constraints_qs = BatchTimeConstraint.objects.filter(is_active=True)
        batch_constraints_dict = {}
        for c in constraints_qs:
            key = (c.department_id, c.semester_id, c.day_id, c.time_slot_id)
            if key in batch_constraints_dict and batch_constraints_dict[key] == 'CLASS_OFF':
                continue
            batch_constraints_dict[key] = c.constraint_type

        # UPDATE: Pass fixed_counts to exclude them from dynamic scheduling
        sorted_sessions = prepare_prioritized_sessions(courses_to_schedule, fixed_counts)
        
        # Calculate totals based on FULL course limits for accurate validation
        teacher_totals = {}
        batch_totals = {}
        for c in courses_to_schedule:
            if c.teacher:
                teacher_totals[c.teacher.id] = teacher_totals.get(c.teacher.id, 0) + c.credits
            batch_key = (c.department.id, c.semester.id)
            batch_totals[batch_key] = batch_totals.get(batch_key, 0) + c.credits

        constraints = ScheduleConstraint(days, time_slots, batch_constraints_dict, teacher_totals, batch_totals)

        # The newly inserted fixed routines will automatically be fetched here and mapped as Occupied!
        existing_routines = RoutineEntry.objects.select_related('day', 'time_slot', 'course', 'course__teacher', 'course__department', 'course__semester', 'room').filter(is_active=True)
        for r in existing_routines:
            constraints.assign(r.day, r.time_slot, r.course, r.room, r.group_name)

        scheduled_count = 0
        dropped_sessions = []
        routines_to_create = []

        for session in sorted_sessions:
            course = session['course']
            duration = session['duration']
            is_lab = session.get('is_lab', False)
            allowed_days = [course.fixed_day] if course.fixed_day else days

            valid_rooms = get_valid_rooms_for_course(course, all_active_rooms, is_lab)
            groups_to_schedule = [None]

            if valid_rooms and is_lab and valid_rooms[0].capacity < course.student_count:
                num_groups = math.ceil(course.student_count / valid_rooms[0].capacity)
                groups_to_schedule = [f"Group {chr(65+i)}" for i in range(num_groups)]

            if not valid_rooms:
                dropped_sessions.append(f"Dropped: {course.course_name} (No suitable room found)")
                continue

            for group_name in groups_to_schedule:
                group_assigned = False
                sorted_days = allowed_days
                if not course.fixed_day:
                    sorted_days = sorted(
                        allowed_days, 
                        key=lambda d: constraints.batch_day_loads.get((course.department.id, course.semester.id, d.id), 0)
                    )

                for day in sorted_days:
                    if group_assigned: break
                    
                    if not constraints.can_schedule_daily(day.id, course, duration):
                        continue

                    b_key = (day.id, course.department.id, course.semester.id)
                    occupied_slots = constraints.batch_schedule_map.get(b_key, set())
                    
                    possible_starts = list(range(len(time_slots) - duration + 1))
                    
                    def calculate_slot_score(start_idx):
                        score = 0
                        if start_idx == 0:
                            score += 100
                        if start_idx + duration == total_slots:
                            score += 100
                            
                        score += (start_idx * 2)

                        if occupied_slots:
                            min_dist = float('inf')
                            for o in occupied_slots:
                                if o < start_idx:
                                    dist = start_idx - o - 1
                                else:
                                    dist = o - (start_idx + duration)
                                
                                if dist < 0: dist = 0 
                                if dist < min_dist: min_dist = dist
                            
                            if min_dist == 0:
                                score -= 50
                            else:
                                score += (min_dist * 20)
                        
                        return score

                    possible_starts.sort(key=lambda idx: calculate_slot_score(idx))

                    for i in possible_starts:
                        if group_assigned: break
                        start_slot = time_slots[i]
                        if course.fixed_time_slot and start_slot.id != course.fixed_time_slot.id: continue

                        if not course.fixed_time_slot and not constraints.can_schedule_continuous(day.id, i, duration, course):
                            continue
                            
                        window_slots = time_slots[i : i + duration]
                        selected_room = None
                        
                        for room in valid_rooms:
                            if not any(constraints.is_conflict(day, w_slot, course, room, group_name) for w_slot in window_slots):
                                selected_room = room
                                break

                        if selected_room:
                            for slot in window_slots:
                                constraints.assign(day, slot, course, selected_room, group_name)
                                routines_to_create.append(RoutineEntry(
                                    day=day, time_slot=slot, course=course, 
                                    room=selected_room, group_name=group_name
                                ))
                            group_assigned = True
                            scheduled_count += 1

                if not group_assigned and not course.fixed_day:
                    for day in sorted_days:
                        if group_assigned: break
                        
                        b_key = (day.id, course.department.id, course.semester.id)
                        occupied_slots = constraints.batch_schedule_map.get(b_key, set())
                        possible_starts = list(range(len(time_slots) - duration + 1))
                        
                        def calculate_fallback_score(start_idx):
                            score = 0
                            if start_idx == 0: score += 100
                            if start_idx + duration == total_slots: score += 100
                            
                            if occupied_slots:
                                min_dist = float('inf')
                                for o in occupied_slots:
                                    dist = start_idx - o - 1 if o < start_idx else o - (start_idx + duration)
                                    if dist < 0: dist = 0
                                    if dist < min_dist: min_dist = dist
                                
                                if min_dist == 0: score -= 50
                                else: score += (min_dist * 20)
                            return score

                        possible_starts.sort(key=lambda idx: calculate_fallback_score(idx))

                        for i in possible_starts:
                            if group_assigned: break
                            start_slot = time_slots[i]
                            window_slots = time_slots[i : i + duration]
                            selected_room = None
                            for room in valid_rooms:
                                if not any(constraints.is_conflict(day, w_slot, course, room, group_name) for w_slot in window_slots):
                                    selected_room = room
                                    break
                            if selected_room:
                                for slot in window_slots:
                                    constraints.assign(day, slot, course, selected_room, group_name)
                                    routines_to_create.append(RoutineEntry(
                                        day=day, time_slot=slot, course=course, room=selected_room, group_name=group_name
                                    ))
                                group_assigned = True
                                scheduled_count += 1

                if not group_assigned:
                    grp_str = f" ({group_name})" if group_name else ""
                    percent = int((session['priority_score'] % 1000) * 100)
                    dropped_sessions.append(f"Dropped: {course.course_name}{grp_str} ({percent}% credit scheduled. Global conflict)")

        if routines_to_create:
            RoutineEntry.objects.bulk_create(routines_to_create)

        if len(dropped_sessions) > 0 and not ignore_warnings:
            transaction.set_rollback(True)
            return {
                "status": "Warning",
                "total_classes_required": len(sorted_sessions), # Excludes fixed classes
                "successful_classes": scheduled_count,
                "dropped_classes": len(dropped_sessions),
                "shortage_details": dropped_sessions,
                "message": "Unable to assign some classes in the system. You can ignore this error and save the partial routine."
            }

        summary_message = "Routine generated 100% successfully" if len(dropped_sessions) == 0 else "Partial routine successfully generated. Some classes could not be scheduled due to conflicts."
        
        return {
            "status": "Success",
            "total_classes_required": len(sorted_sessions),
            "successful_classes": scheduled_count,
            "dropped_classes": len(dropped_sessions),
            "shortage_details": dropped_sessions,
            "message": summary_message
        }

def rollback_routine_algorithm(department_id):
    latest_backup = RoutineBackup.objects.filter(department_id=department_id).order_by('-created_at').first()
    if not latest_backup: return {"status": "Error", "message": "No backup found."}
    
    setting = SystemSetting.objects.first()
    if setting and setting.is_routine_locked: return {"status": "Locked", "message": "System is locked."}

    RoutineEntry.objects.filter(course__department_id=department_id).delete()
    
    # UPDATE: Restore is_fixed properly from backup data
    routines = [
        RoutineEntry(
            day_id=item['day_id'], time_slot_id=item['time_slot_id'], 
            course_id=item['course_id'], room_id=item['room_id'], group_name=item.get('group_name'),
            is_fixed=item.get('is_fixed', False)
        ) for item in latest_backup.backup_data
    ]
    RoutineEntry.objects.bulk_create(routines)
    
    return {"status": "Success", "message": "Routine rolled back successfully."}