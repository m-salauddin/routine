# academic/utils.py
import random
import math
from django.db import transaction, IntegrityError
from django.core.exceptions import ValidationError
from .models import (
    Day, Course, TimeSlot, RoutineEntry, Room,
    SystemSetting, RoutineBackup, BatchTimeConstraint, FixedClassSchedule,
    AlgorithmConfig
)

MAX_CONTINUOUS = 4  # a batch or teacher never gets more than 4 classes in a row


# ==============================================================================
# CONSTRAINT TRACKER
# ==============================================================================
class ScheduleConstraint:
    def __init__(self, days, time_slots, batch_constraints_dict, teacher_totals, batch_totals):
        self.teacher_occupied = set()
        self.room_occupied = set()
        self.course_daily_tracker = set()
        self.teacher_batch_interaction = {}
        self.batch_slot_groups = {}

        self.day_loads = {day.id: 0 for day in days}
        self.teacher_daily_count = {}
        self.batch_daily_count = {}
        self.room_usage_count = {}

        self.batch_constraints = batch_constraints_dict
        self.lunch_indices = {idx for idx, slot in enumerate(time_slots) if slot.is_lunch_break}
        self.total_days = max(1, len(days))

        # Comfortable daily caps (a small buffer keeps the routine feasible)
        self.teacher_limits = {
            tid: math.ceil(total / self.total_days) + 2
            for tid, total in teacher_totals.items()
        }
        self.batch_limits = {
            bid: math.ceil(total / self.total_days) + 2
            for bid, total in batch_totals.items()
        }

        self.slot_index_map = {slot.id: idx for idx, slot in enumerate(time_slots)}
        self.teacher_schedule_map = {}
        self.batch_schedule_map = {}

    # ---- batch daily load (a single student's view: common classes + own group) ----
    def get_batch_day_load(self, dept_id, sem_id, day_id, group_name=None):
        common_load = self.batch_daily_count.get((dept_id, sem_id, day_id, None), 0)
        if group_name:
            group_load = self.batch_daily_count.get((dept_id, sem_id, day_id, group_name), 0)
            return common_load + group_load
        # for a combined (None) session, use the busiest parallel group on that day
        max_group_load = 0
        for k, v in self.batch_daily_count.items():
            if k[0] == dept_id and k[1] == sem_id and k[2] == day_id and k[3] is not None:
                max_group_load = max(max_group_load, v)
        return common_load + max_group_load

    def can_schedule_daily(self, day_id, course, duration, group_name=None):
        dept_id, sem_id = course.department.id, course.semester.id
        b_limit = self.batch_limits.get((dept_id, sem_id), 6)
        if self.get_batch_day_load(dept_id, sem_id, day_id, group_name) + duration > b_limit:
            return False
        if course.teacher:
            t_limit = self.teacher_limits.get(course.teacher.id, 6)
            if self.teacher_daily_count.get((course.teacher.id, day_id), 0) + duration > t_limit:
                return False
        return True

    def can_schedule_continuous(self, day_id, start_idx, duration, course, group_name=None):
        def run_length(occupied):
            left, l_idx = 0, start_idx - 1
            while l_idx in occupied and l_idx not in self.lunch_indices:
                left += 1; l_idx -= 1
            right, r_idx = 0, start_idx + duration
            while r_idx in occupied and r_idx not in self.lunch_indices:
                right += 1; r_idx += 1
            return left + duration + right

        b_grp = (day_id, course.department.id, course.semester.id, group_name)
        b_all = (day_id, course.department.id, course.semester.id, None)
        batch_occupied = self.batch_schedule_map.get(b_grp, set()).union(
                         self.batch_schedule_map.get(b_all, set()))
        if run_length(batch_occupied) > MAX_CONTINUOUS:
            return False

        if course.teacher:
            teacher_occupied = self.teacher_schedule_map.get((day_id, course.teacher.id), set())
            if run_length(teacher_occupied) > MAX_CONTINUOUS:
                return False
        return True

    def is_conflict(self, day, slot, course, room, group_name=None, is_fixed=False):
        day_id = day.id
        ctype = self.batch_constraints.get((course.department.id, course.semester.id, day_id, slot.id))
        if ctype == 'CLASS_OFF':
            return True
        if slot.is_lunch_break and ctype != 'FORCE_ALLOW_LUNCH_CLASS':
            return True

        # 1) teacher can never be in two places at once
        if course.teacher and (day_id, slot.id, course.teacher.id) in self.teacher_occupied:
            return True
        # 2) a room can never hold two classes at once
        if room and (day_id, slot.id, room.id) in self.room_occupied:
            return True
        # 3) a batch can never attend two classes at once (parallel groups allowed)
        b_key = (day_id, slot.id, course.department.id, course.semester.id)
        if b_key in self.batch_slot_groups:
            occupied_groups = self.batch_slot_groups[b_key]
            if None in occupied_groups:
                return True                       # a combined class already blocks the whole batch
            if group_name is None and occupied_groups:
                return True                       # groups exist; a combined class cannot be added
            if group_name in occupied_groups:
                return True                       # this exact group is already here
        # 4) a non-lab course is not repeated twice on the same day
        is_lab = course.course_type and 'lab' in course.course_type.name.lower()
        if not is_fixed and not is_lab and (course.id, group_name, day_id) in self.course_daily_tracker:
            return True
        # 5) one teacher gives only one course to a given batch
        if course.teacher:
            tb_key = (day_id, course.teacher.id, course.department.id, course.semester.id)
            if tb_key in self.teacher_batch_interaction and self.teacher_batch_interaction[tb_key] != course.id:
                return True
        return False

    def assign(self, day, slot, course, room, group_name=None):
        day_id = day.id
        slot_idx = self.slot_index_map[slot.id]

        if course.teacher:
            t_key = (day_id, slot.id, course.teacher.id)
            if t_key not in self.teacher_occupied:
                self.teacher_daily_count[(course.teacher.id, day_id)] = \
                    self.teacher_daily_count.get((course.teacher.id, day_id), 0) + 1
            self.teacher_occupied.add(t_key)
            self.teacher_batch_interaction[(day_id, course.teacher.id, course.department.id, course.semester.id)] = course.id
            self.teacher_schedule_map.setdefault((day_id, course.teacher.id), set()).add(slot_idx)

        if room:
            self.room_occupied.add((day_id, slot.id, room.id))
            self.room_usage_count[room.id] = self.room_usage_count.get(room.id, 0) + 1

        self.batch_slot_groups.setdefault((day_id, slot.id, course.department.id, course.semester.id), set()).add(group_name)
        self.course_daily_tracker.add((course.id, group_name, day_id))
        self.day_loads[day_id] += 1

        b_key = (course.department.id, course.semester.id, day_id, group_name)
        self.batch_daily_count[b_key] = self.batch_daily_count.get(b_key, 0) + 1
        self.batch_schedule_map.setdefault((day_id, course.department.id, course.semester.id, group_name), set()).add(slot_idx)


# ==============================================================================
# ROOM SELECTION  (department-exclusive, capacity-aware, load-balanced)
# ==============================================================================
def get_valid_rooms_for_course(course, all_active_rooms, is_lab, required_capacity=None):
    if course.fixed_room and course.fixed_room.is_active:
        return [course.fixed_room]

    base = [
        r for r in all_active_rooms
        if r.room_type_id == course.course_type_id
        and (not course.course_sub_type_id or r.room_sub_type_id == course.course_sub_type_id)
    ]
    dept_to_search = course.preferred_room_department or course.offering_department or course.department
    valid = [r for r in base if r.department_id == dept_to_search.id]
    if not valid:
        return []

    # only enforce a positive capacity requirement (0 / None means "size unknown")
    if required_capacity and required_capacity > 0:
        valid = [r for r in valid if r.capacity >= required_capacity]

    valid.sort(key=lambda x: x.capacity)  # smallest suitable room first
    return valid


# ==============================================================================
# GROUP PLANNER (Smart Split)  — decides how many parallel groups a lab needs
# ==============================================================================
def plan_course_groups(course, all_active_rooms, course_fixed_groups):
    """Return {'groups': [...], 'req_capacity': int, 'is_lab': bool}."""
    is_lab = course.course_type and 'lab' in course.course_type.name.lower()
    rooms = get_valid_rooms_for_course(course, all_active_rooms, is_lab, None)
    groups = [None]
    students = course.student_count or 0
    req_capacity = students

    if is_lab:
        # admin explicitly marked this lab as a single combined class
        if course.id in course_fixed_groups and None in course_fixed_groups[course.id]:
            return {'groups': [None], 'req_capacity': students, 'is_lab': True}

        best_cap = rooms[-1].capacity if rooms else 0   # largest matching room
        if students > 0 and best_cap > 0 and students > best_cap:
            num_groups = math.ceil(students / best_cap)
            if num_groups > 1:
                groups = [f"Group {chr(65 + i)}" for i in range(num_groups)]
                req_capacity = math.ceil(students / num_groups)

    return {'groups': groups, 'req_capacity': req_capacity, 'is_lab': is_lab}


# ==============================================================================
# SESSION BUILDER  — turns each (course, group) into schedulable blocks
# ==============================================================================
def build_sessions(courses, groups_info, fixed_counts):
    sessions = []
    for course in courses:
        info = groups_info[course.id]
        is_lab = info['is_lab']
        req_capacity = info['req_capacity']
        total_credits = course.credits if course.credits > 0 else 1
        # labs are placed first (they are the hardest to fit)
        base_priority = 50000 if is_lab else 10000
        fixed_bonus = 1000 if course.fixed_room else 0

        for grp in info['groups']:
            remaining = course.credits - fixed_counts.get((course.id, grp), 0)
            if remaining <= 0:
                continue
            filled = fixed_counts.get((course.id, grp), 0)

            def add(duration):
                nonlocal filled
                filled += duration
                sessions.append({
                    'course': course, 'group': grp, 'duration': duration, 'is_lab': is_lab,
                    'req_capacity': req_capacity,
                    'priority_score': base_priority + fixed_bonus + (filled / total_credits),
                })

            if is_lab:
                rem = remaining
                while rem >= 2:      # labs run as 2-hour blocks
                    add(2); rem -= 2
                if rem > 0:
                    add(1)
            else:
                for _ in range(remaining):
                    add(1)

    random.shuffle(sessions)  # break ties between equal-priority sessions
    sessions.sort(key=lambda x: (x['priority_score'], x['duration']), reverse=True)
    return sessions


# ==============================================================================
# SLOT SCORING  — one clean, consistent policy: compact days, balanced week
# ==============================================================================
def score_slot(constraints, config, course, day, start_idx, duration, group_name,
               occupied_slots, total_slots):
    score = 0

    # (a) balance the week — gently prefer the less-loaded day (LINEAR, not squared)
    current_load = constraints.get_batch_day_load(course.department.id, course.semester.id, day.id, group_name)
    score -= current_load * config.day_load_penalty_multiplier

    # (b) keep the day compact — reward sitting next to an existing class, punish gaps
    if occupied_slots:
        min_gap = None
        for o in occupied_slots:
            if o < start_idx:
                gap = sum(1 for s in range(o + 1, start_idx) if s not in constraints.lunch_indices)
            else:
                gap = sum(1 for s in range(start_idx + duration, o) if s not in constraints.lunch_indices)
            min_gap = gap if min_gap is None else min(min_gap, gap)
        if min_gap == 0:
            score += config.zero_gap_bonus
        else:
            score -= min_gap * config.gap_penalty_per_slot
    else:
        score += config.zero_gap_bonus // 2  # first class of the day is fine

    # (c) mildly avoid the very first and very last periods
    for w in range(start_idx, start_idx + duration):
        if w == 0 or w == total_slots - 1:
            score -= config.edge_slot_penalty
        elif w == 1 or w == total_slots - 2:
            score -= config.edge_slot_penalty // 2
        else:
            score += config.center_gravity_bonus

    # (d) softly discourage very long unbroken runs (the hard cap of 4 still applies)
    left, l_idx = 0, start_idx - 1
    while l_idx in occupied_slots and l_idx not in constraints.lunch_indices:
        left += 1; l_idx -= 1
    right, r_idx = 0, start_idx + duration
    while r_idx in occupied_slots and r_idx not in constraints.lunch_indices:
        right += 1; r_idx += 1
    if left + duration + right >= 4:
        score -= config.continuous_class_penalty

    # (e) deterministic tie-break: prefer earlier starts
    score -= start_idx
    return score


# ==============================================================================
# MAIN GENERATOR
# ==============================================================================
def generate_routine_algorithm(department_id, semester_id=None, ignore_warnings=False):
    setting = SystemSetting.objects.first()
    if setting and setting.is_routine_locked:
        return {"status": "Locked", "message": "System is locked. Cannot generate routine."}

    config_obj = AlgorithmConfig.objects.first()

    class DefaultConfig:
        # balanced weights — no single term is allowed to dominate the rest
        edge_slot_penalty = 300
        zero_gap_bonus = 500
        gap_penalty_per_slot = 200
        center_gravity_bonus = 50
        continuous_class_penalty = 150
        day_load_penalty_multiplier = 100
        parallel_bonus = 0  # unused: one course has one teacher, so groups can't run in parallel

    config = config_obj if config_obj else DefaultConfig()

    with transaction.atomic():
        base_courses = Course.objects.select_related(
            'teacher', 'department', 'semester', 'course_type', 'course_sub_type',
            'fixed_room', 'preferred_room_department', 'offering_department'
        ).filter(department_id=department_id, is_active=True)

        if semester_id:
            courses_to_schedule = list(base_courses.filter(semester_id=semester_id))
            old_routines = RoutineEntry.objects.filter(course__department_id=department_id, course__semester_id=semester_id)
            fixed_schedules = FixedClassSchedule.objects.filter(course__department_id=department_id, course__semester_id=semester_id)
        else:
            courses_to_schedule = list(base_courses)
            old_routines = RoutineEntry.objects.filter(course__department_id=department_id)
            fixed_schedules = FixedClassSchedule.objects.filter(course__department_id=department_id)

        # backup then clear the old routine
        if old_routines.exists():
            backup_list = [{
                'day_id': e.day_id, 'time_slot_id': e.time_slot_id,
                'course_id': e.course_id, 'room_id': e.room_id,
                'group_name': e.group_name, 'is_fixed': getattr(e, 'is_fixed', False)
            } for e in old_routines]
            RoutineBackup.objects.create(department_id=department_id, backup_data=backup_list)
        old_routines.delete()

        days = list(Day.objects.all().order_by('order'))
        time_slots = list(TimeSlot.objects.all().order_by('start_time'))
        total_slots = len(time_slots)
        all_active_rooms = list(Room.objects.filter(is_active=True))

        # admin batch constraints
        batch_constraints_dict = {}
        for c in BatchTimeConstraint.objects.filter(is_active=True):
            key = (c.department_id, c.semester_id, c.day_id, c.time_slot_id)
            if batch_constraints_dict.get(key) == 'CLASS_OFF':
                continue
            batch_constraints_dict[key] = c.constraint_type

        course_fixed_groups = {}
        for fs in fixed_schedules:
            course_fixed_groups.setdefault(fs.course_id, set()).add(fs.group_name)

        # ---- plan groups + compute daily-load totals ----
        groups_info = {}
        teacher_totals = {}
        batch_totals = {}
        for course in courses_to_schedule:
            info = plan_course_groups(course, all_active_rooms, course_fixed_groups)
            groups_info[course.id] = info
            num_groups = len(info['groups'])
            # a teacher teaches EVERY group; a single student attends only ONE group
            if course.teacher:
                teacher_totals[course.teacher.id] = teacher_totals.get(course.teacher.id, 0) + course.credits * num_groups
            bkey = (course.department.id, course.semester.id)
            batch_totals[bkey] = batch_totals.get(bkey, 0) + course.credits

        constraints = ScheduleConstraint(days, time_slots, batch_constraints_dict, teacher_totals, batch_totals)

        # respect any other department's already-active routine
        for r in RoutineEntry.objects.select_related(
                'day', 'time_slot', 'course', 'course__teacher',
                'course__department', 'course__semester', 'room').filter(is_active=True):
            constraints.assign(r.day, r.time_slot, r.course, r.room, r.group_name)

        scheduled_count = 0
        dropped_sessions = []
        routines_to_create = []
        fixed_counts = {}

        # ---- STEP 1: place admin's fixed (pinned) classes first ----
        for fs in fixed_schedules:
            course, day, slot, grp = fs.course, fs.day, fs.time_slot, fs.group_name
            is_lab = course.course_type and 'lab' in course.course_type.name.lower()
            valid_rooms = get_valid_rooms_for_course(course, all_active_rooms, is_lab, None)

            assigned_room = fs.room
            if assigned_room and constraints.is_conflict(day, slot, course, assigned_room, grp, is_fixed=True):
                assigned_room = None
            if not assigned_room:
                valid_rooms.sort(key=lambda r: (constraints.room_usage_count.get(r.id, 0), r.capacity))
                for r in valid_rooms:
                    if not constraints.is_conflict(day, slot, course, r, grp, is_fixed=True):
                        assigned_room = r
                        break
            if assigned_room:
                constraints.assign(day, slot, course, assigned_room, grp)
                routines_to_create.append(RoutineEntry(
                    day=day, time_slot=slot, course=course, room=assigned_room, group_name=grp, is_fixed=True))
                fixed_counts[(course.id, grp)] = fixed_counts.get((course.id, grp), 0) + 1
                scheduled_count += 1
            else:
                dropped_sessions.append(f"Dropped Fixed: {course.course_code} at {day.name} {slot.start_time} (no free room)")

        # ---- STEP 2: build and sort the remaining sessions (labs first) ----
        sessions = build_sessions(courses_to_schedule, groups_info, fixed_counts)
        total_required = scheduled_count + len(sessions)

        # ---- STEP 3: place each session in its globally best-scoring slot ----
        def collect_options(session, respect_daily, respect_continuity):
            course = session['course']
            duration = session['duration']
            group_name = session['group']
            valid_rooms = get_valid_rooms_for_course(course, all_active_rooms, session['is_lab'], session['req_capacity'])
            if not valid_rooms:
                return None, []  # no room fits at all
            options = []
            for day in days:
                if respect_daily and not constraints.can_schedule_daily(day.id, course, duration, group_name):
                    continue
                b_grp = (day.id, course.department.id, course.semester.id, group_name)
                b_all = (day.id, course.department.id, course.semester.id, None)
                occupied = constraints.batch_schedule_map.get(b_grp, set()).union(
                           constraints.batch_schedule_map.get(b_all, set()))
                for i in range(total_slots - duration + 1):
                    if respect_continuity and not constraints.can_schedule_continuous(day.id, i, duration, course, group_name):
                        continue
                    window = time_slots[i:i + duration]
                    # pick the least-used room that has no clash for the whole window
                    room = None
                    valid_rooms.sort(key=lambda r: (constraints.room_usage_count.get(r.id, 0), r.capacity))
                    for r in valid_rooms:
                        if not any(constraints.is_conflict(day, w, course, r, group_name) for w in window):
                            room = r
                            break
                    if not room:
                        continue
                    sc = score_slot(constraints, config, course, day, i, duration, group_name, occupied, total_slots)
                    options.append((sc, random.random(), day, window, room))
            return valid_rooms, options

        for session in sessions:
            course = session['course']
            group_name = session['group']

            valid_rooms, options = collect_options(session, respect_daily=True, respect_continuity=True)
            if valid_rooms is not None and valid_rooms == []:
                dropped_sessions.append(f"Dropped: {course.course_code} ({group_name or 'combined'}) — no room fits {session['req_capacity']} students")
                continue
            if not options:  # relaxed fallback: keep hard clashes, drop soft limits
                _, options = collect_options(session, respect_daily=False, respect_continuity=False)

            if options:
                options.sort(key=lambda x: (x[0], x[1]), reverse=True)
                _, _, day, window, room = options[0]
                for slot in window:
                    constraints.assign(day, slot, course, room, group_name)
                    routines_to_create.append(RoutineEntry(
                        day=day, time_slot=slot, course=course, room=room, group_name=group_name))
                scheduled_count += 1
            else:
                grp_str = f" ({group_name})" if group_name else ""
                dropped_sessions.append(f"Dropped: {course.course_code}{grp_str} (no conflict-free slot available)")

        # ---- STEP 4: validate + commit safely (DB uniqueness is the final guard) ----
        if routines_to_create:
            try:
                for entry in routines_to_create:
                    entry.full_clean()   # validates fields AND unique_together (no overlap)
                    entry.save()
            except (ValidationError, IntegrityError) as e:
                transaction.set_rollback(True)
                return {"status": "Error", "message": f"Database overlap prevented: {e}"}

        if dropped_sessions and not ignore_warnings:
            transaction.set_rollback(True)
            return {
                "status": "Warning",
                "total_classes_required": total_required,
                "successful_classes": scheduled_count,
                "dropped_classes": len(dropped_sessions),
                "shortage_details": dropped_sessions,
                "message": "Unable to assign some classes. You can ignore this warning to save the partial routine."
            }

        return {
            "status": "Success",
            "total_classes_required": total_required,
            "successful_classes": scheduled_count,
            "dropped_classes": len(dropped_sessions),
            "shortage_details": dropped_sessions,
            "message": "Routine generated 100% successfully" if not dropped_sessions
                       else "Partial routine generated. Some classes could not be scheduled."
        }


# ==============================================================================
# ROLLBACK
# ==============================================================================
def rollback_routine_algorithm(department_id):
    latest_backup = RoutineBackup.objects.filter(department_id=department_id).order_by('-created_at').first()
    if not latest_backup:
        return {"status": "Error", "message": "No backup found."}

    setting = SystemSetting.objects.first()
    if setting and setting.is_routine_locked:
        return {"status": "Locked", "message": "System is locked."}

    RoutineEntry.objects.filter(course__department_id=department_id).delete()
    try:
        for item in latest_backup.backup_data:
            entry = RoutineEntry(
                day_id=item['day_id'], time_slot_id=item['time_slot_id'],
                course_id=item['course_id'], room_id=item['room_id'],
                group_name=item.get('group_name'), is_fixed=item.get('is_fixed', False))
            entry.full_clean()
            entry.save()
    except (ValidationError, IntegrityError) as e:
        return {"status": "Error", "message": f"Rollback failed: {e}"}

    return {"status": "Success", "message": "Routine rolled back successfully."}