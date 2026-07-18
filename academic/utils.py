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

MAX_CONTINUOUS = 4


# ==============================================================================
# CONSTRAINT TRACKER
# ==============================================================================
# Batch model (sections):
#   * group_name = None  -> the WHOLE batch (a theory class blocks every section)
#   * group_name = "Group A"/"Group B"/... -> ONE section of the batch
#   * Two DIFFERENT sections may run at the SAME time (parallel labs), because they
#     are different students, different rooms and different teachers.
#   * The SAME section can never be in two places at once.
#   * A teacher can never be in two places at once (STRICT — no bypass).
# ==============================================================================
class ScheduleConstraint:
    def __init__(self, days, time_slots, batch_constraints_dict, teacher_totals, batch_totals):
        self.teacher_occupied = set()        # (day, slot, teacher)  -- STRICT single occupancy
        self.room_occupied = set()           # (day, slot, room)
        self.teacher_batch_interaction = {}  # (day, teacher, dept, sem) -> course_id
        self.batch_slot_groups = {}          # (day, slot, dept, sem) -> {group_name,...}

        self.day_loads = {day.id: 0 for day in days}
        self.teacher_daily_count = {}
        self.batch_daily_count = {}          # (dept, sem, day, group) -> hours
        self.room_usage_count = {}

        self.batch_constraints = batch_constraints_dict
        self.lunch_indices = {i for i, s in enumerate(time_slots) if s.is_lunch_break}
        self.total_days = max(1, len(days))

        self.teacher_limits = {tid: math.ceil(t / self.total_days) + 2 for tid, t in teacher_totals.items()}
        self.batch_limits = {bid: math.ceil(t / self.total_days) + 2 for bid, t in batch_totals.items()}

        self.slot_index_map = {s.id: i for i, s in enumerate(time_slots)}
        self.teacher_schedule_map = {}
        self.batch_schedule_map = {}

    def get_batch_day_load(self, dept_id, sem_id, day_id, group_name=None):
        common = self.batch_daily_count.get((dept_id, sem_id, day_id, None), 0)
        if group_name:
            return common + self.batch_daily_count.get((dept_id, sem_id, day_id, group_name), 0)
        mx = 0
        for k, v in self.batch_daily_count.items():
            if k[0] == dept_id and k[1] == sem_id and k[2] == day_id and k[3] is not None:
                mx = max(mx, v)
        return common + mx

    def teacher_day_load(self, teacher_id, day_id):
        return self.teacher_daily_count.get((teacher_id, day_id), 0)

    def continuous_run(self, day_id, start_idx, duration, course, group_name):
        def run(occ):
            l, li = 0, start_idx - 1
            while li in occ and li not in self.lunch_indices:
                l += 1; li -= 1
            r, ri = 0, start_idx + duration
            while ri in occ and ri not in self.lunch_indices:
                r += 1; ri += 1
            return l + duration + r
        bg = self.batch_schedule_map.get((day_id, course.department.id, course.semester.id, group_name), set())
        ba = self.batch_schedule_map.get((day_id, course.department.id, course.semester.id, None), set())
        run_val = run(bg | ba)
        if course.teacher:
            run_val = max(run_val, run(self.teacher_schedule_map.get((day_id, course.teacher.id), set())))
        return run_val

    # -------- HARD conflict (physically impossible if True) --------
    def is_conflict(self, day, slot, course, room, group_name=None, is_fixed=False):
        day_id = day.id
        ctype = self.batch_constraints.get((course.department.id, course.semester.id, day_id, slot.id))
        if ctype == 'CLASS_OFF':
            return True
        if slot.is_lunch_break and ctype != 'FORCE_ALLOW_LUNCH_CLASS':
            return True

        # teacher: strict single occupancy
        if course.teacher and (day_id, slot.id, course.teacher.id) in self.teacher_occupied:
            return True
        # room: single occupancy
        if room and (day_id, slot.id, room.id) in self.room_occupied:
            return True

        # batch / section logic (this is what allows two DIFFERENT sections in parallel)
        groups_here = self.batch_slot_groups.get((day_id, slot.id, course.department.id, course.semester.id))
        if groups_here:
            if None in groups_here:
                return True                      # whole-batch (theory) already here -> blocks everyone
            if group_name is None:
                return True                      # can't add a whole-batch class while sections are running
            if group_name in groups_here:
                return True                      # THIS section already busy here

        # one teacher teaches only one course to a given batch
        if course.teacher:
            tb = self.teacher_batch_interaction.get((day_id, course.teacher.id, course.department.id, course.semester.id))
            if tb is not None and tb != course.id:
                return True
        return False

    def assign(self, day, slot, course, room, group_name=None):
        day_id = day.id
        idx = self.slot_index_map[slot.id]
        if course.teacher:
            if (day_id, slot.id, course.teacher.id) not in self.teacher_occupied:
                self.teacher_daily_count[(course.teacher.id, day_id)] = self.teacher_daily_count.get((course.teacher.id, day_id), 0) + 1
            self.teacher_occupied.add((day_id, slot.id, course.teacher.id))
            self.teacher_batch_interaction[(day_id, course.teacher.id, course.department.id, course.semester.id)] = course.id
            self.teacher_schedule_map.setdefault((day_id, course.teacher.id), set()).add(idx)
        if room:
            self.room_occupied.add((day_id, slot.id, room.id))
            self.room_usage_count[room.id] = self.room_usage_count.get(room.id, 0) + 1
        self.batch_slot_groups.setdefault((day_id, slot.id, course.department.id, course.semester.id), set()).add(group_name)
        self.day_loads[day_id] += 1
        self.batch_daily_count[(course.department.id, course.semester.id, day_id, group_name)] = \
            self.batch_daily_count.get((course.department.id, course.semester.id, day_id, group_name), 0) + 1
        self.batch_schedule_map.setdefault((day_id, course.department.id, course.semester.id, group_name), set()).add(idx)


# ==============================================================================
# ROOM HELPER
# ==============================================================================
def matching_rooms(course, all_active_rooms, required_capacity=None):
    if course.fixed_room and course.fixed_room.is_active:
        return [course.fixed_room]
    base = [r for r in all_active_rooms
            if r.room_type_id == course.course_type_id
            and (not course.course_sub_type_id or r.room_sub_type_id == course.course_sub_type_id)]
    dept = course.preferred_room_department or course.offering_department or course.department
    rooms = [r for r in base if r.department_id == dept.id]
    if required_capacity and required_capacity > 0:
        fit = [r for r in rooms if r.capacity >= required_capacity]
        rooms = fit if fit else rooms          # if nothing fits, keep all (avoid a false drop)
    return rooms


def is_lab_course(c):
    return bool(c.course_type and 'lab' in c.course_type.name.lower())


# ==============================================================================
# SECTION PLANNER  — decide how many sections (A/B/...) a batch is split into
# ==============================================================================
def plan_sections(courses, all_active_rooms):
    """Return {(dept_id, sem_id): num_sections}. A batch is split only when its
    labs are too big for the available lab rooms."""
    sections = {}
    batches = {}
    for c in courses:
        batches.setdefault((c.department.id, c.semester.id), []).append(c)

    for bkey, blist in batches.items():
        labs = [c for c in blist if is_lab_course(c)]
        if not labs:
            sections[bkey] = 1
            continue
        batch_size = max((c.student_count or 0) for c in blist)
        # biggest lab room available to this batch's labs
        best_cap = 0
        for c in labs:
            for r in matching_rooms(c, all_active_rooms):
                best_cap = max(best_cap, r.capacity)
        if batch_size > 0 and best_cap > 0 and batch_size > best_cap:
            sections[bkey] = math.ceil(batch_size / best_cap)
        else:
            sections[bkey] = 1
    return sections


# ==============================================================================
# SCORING  (compact week + REWARD parallel sections running together)
# ==============================================================================
def window_score(constraints, config, course, day, start_idx, duration, group_name, total_slots, window):
    score = 0
    bg = constraints.batch_schedule_map.get((day.id, course.department.id, course.semester.id, group_name), set())
    ba = constraints.batch_schedule_map.get((day.id, course.department.id, course.semester.id, None), set())
    occupied = bg | ba

    load = constraints.get_batch_day_load(course.department.id, course.semester.id, day.id, group_name)
    score -= load * config.day_load_penalty_multiplier
    if course.teacher:
        score -= constraints.teacher_day_load(course.teacher.id, day.id) * (config.day_load_penalty_multiplier // 2)

    if occupied:
        gaps = []
        for o in occupied:
            if o < start_idx:
                g = sum(1 for s in range(o + 1, start_idx) if s not in constraints.lunch_indices)
            else:
                g = sum(1 for s in range(start_idx + duration, o) if s not in constraints.lunch_indices)
            gaps.append(g)
        mn = min(gaps)
        score += config.zero_gap_bonus if mn == 0 else -mn * config.gap_penalty_per_slot
    else:
        score += config.zero_gap_bonus // 2

    for w in range(start_idx, start_idx + duration):
        if w == 0 or w == total_slots - 1:
            score -= config.edge_slot_penalty
        elif w == 1 or w == total_slots - 2:
            score -= config.edge_slot_penalty // 2
        else:
            score += config.center_gravity_bonus

    if constraints.continuous_run(day.id, start_idx, duration, course, group_name) >= MAX_CONTINUOUS:
        score -= config.continuous_class_penalty

    # *** THE KEY: reward running this section in PARALLEL with ANOTHER section ***
    if group_name is not None:
        parallel_hits = 0
        for w_slot in window:
            grps = constraints.batch_slot_groups.get((day.id, w_slot.id, course.department.id, course.semester.id), set())
            if any(g is not None and g != group_name for g in grps):
                parallel_hits += 1
        score += parallel_hits * config.parallel_bonus

    score -= start_idx
    return score


# ==============================================================================
# PLACE ONE SESSION  (a section-lab block, or a whole-batch theory/lab block)
# ==============================================================================
def place_session(constraints, config, course, duration, group_name, req_capacity,
                  days, time_slots, entries, relax=False):
    total_slots = len(time_slots)
    rooms = matching_rooms(course, all_active_rooms=course._all_rooms, required_capacity=req_capacity)
    if not rooms:
        return False
    best = None
    for day in days:
        if not relax:
            b_limit = constraints.batch_limits.get((course.department.id, course.semester.id), 6)
            if constraints.get_batch_day_load(course.department.id, course.semester.id, day.id, group_name) + duration > b_limit:
                continue
            if course.teacher:
                t_limit = constraints.teacher_limits.get(course.teacher.id, 6)
                if constraints.teacher_day_load(course.teacher.id, day.id) + duration > t_limit:
                    continue
        for i in range(total_slots - duration + 1):
            window = time_slots[i:i + duration]
            if any(w.is_lunch_break for w in window):
                continue
            if not relax and constraints.continuous_run(day.id, i, duration, course, group_name) > MAX_CONTINUOUS:
                continue
            # pick a free room (least used, smallest that fits)
            room = None
            for r in sorted(rooms, key=lambda r: (constraints.room_usage_count.get(r.id, 0), r.capacity)):
                if all(not constraints.is_conflict(day, w, course, r, group_name) for w in window):
                    room = r
                    break
            if not room:
                continue
            sc = window_score(constraints, config, course, day, i, duration, group_name, total_slots, window)
            if best is None or sc > best[0]:
                best = (sc, day, window, room)
    if best is None:
        return False
    _, day, window, room = best
    for slot in window:
        constraints.assign(day, slot, course, room, group_name)
        entries.append(RoutineEntry(day=day, time_slot=slot, course=course, room=room, group_name=group_name))
    return True


# ==============================================================================
# MAIN GENERATOR
# ==============================================================================
def generate_routine_algorithm(department_id, semester_id=None, ignore_warnings=False):
    setting = SystemSetting.objects.first()
    if setting and setting.is_routine_locked:
        return {"status": "Locked", "message": "System is locked. Cannot generate routine."}

    cfg = AlgorithmConfig.objects.first()

    class DefaultConfig:
        parallel_bonus = 8000          # strongly pull two sections into the same slot
        edge_slot_penalty = 300
        zero_gap_bonus = 500
        gap_penalty_per_slot = 200
        center_gravity_bonus = 50
        continuous_class_penalty = 150
        day_load_penalty_multiplier = 100
    config = cfg if cfg else DefaultConfig()

    with transaction.atomic():
        base = Course.objects.select_related(
            'teacher', 'department', 'semester', 'course_type', 'course_sub_type',
            'fixed_room', 'preferred_room_department', 'offering_department'
        ).filter(department_id=department_id, is_active=True)

        if semester_id:
            courses = list(base.filter(semester_id=semester_id))
            old = RoutineEntry.objects.filter(course__department_id=department_id, course__semester_id=semester_id)
            fixed_schedules = FixedClassSchedule.objects.filter(course__department_id=department_id, course__semester_id=semester_id)
        else:
            courses = list(base)
            old = RoutineEntry.objects.filter(course__department_id=department_id)
            fixed_schedules = FixedClassSchedule.objects.filter(course__department_id=department_id)

        if old.exists():
            RoutineBackup.objects.create(department_id=department_id, backup_data=[{
                'day_id': e.day_id, 'time_slot_id': e.time_slot_id, 'course_id': e.course_id,
                'room_id': e.room_id, 'group_name': e.group_name, 'is_fixed': getattr(e, 'is_fixed', False)
            } for e in old])
        old.delete()

        days = list(Day.objects.all().order_by('order'))
        time_slots = list(TimeSlot.objects.all().order_by('start_time'))
        all_rooms = list(Room.objects.filter(is_active=True))
        for c in courses:
            c._all_rooms = all_rooms                      # attach for the room helper

        batch_constraints = {}
        for c in BatchTimeConstraint.objects.filter(is_active=True):
            key = (c.department_id, c.semester_id, c.day_id, c.time_slot_id)
            if batch_constraints.get(key) == 'CLASS_OFF':
                continue
            batch_constraints[key] = c.constraint_type

        # ---- decide sections per batch ----
        section_count = plan_sections(courses, all_rooms)

        # ---- daily-load estimates ----
        teacher_totals, batch_totals = {}, {}
        for c in courses:
            ns = section_count.get((c.department.id, c.semester.id), 1)
            reps = ns if is_lab_course(c) else 1          # a lab teacher repeats for each section
            if c.teacher:
                teacher_totals[c.teacher.id] = teacher_totals.get(c.teacher.id, 0) + c.credits * reps
            bk = (c.department.id, c.semester.id)
            batch_totals[bk] = batch_totals.get(bk, 0) + c.credits   # a single student attends each course once

        constraints = ScheduleConstraint(days, time_slots, batch_constraints, teacher_totals, batch_totals)

        for r in RoutineEntry.objects.select_related(
                'day', 'time_slot', 'course', 'course__teacher',
                'course__department', 'course__semester', 'room').filter(is_active=True):
            constraints.assign(r.day, r.time_slot, r.course, r.room, r.group_name)

        entries, dropped = [], []
        scheduled = 0
        total_required = 0

        # ---------- STEP 1: fixed classes ----------
        for fs in fixed_schedules:
            course, day, slot, grp = fs.course, fs.day, fs.time_slot, fs.group_name
            course._all_rooms = all_rooms
            rooms = matching_rooms(course, all_rooms)
            room = fs.room
            if not (room and not constraints.is_conflict(day, slot, course, room, grp, is_fixed=True)):
                room = None
                for r in sorted(rooms, key=lambda r: (constraints.room_usage_count.get(r.id, 0), r.capacity)):
                    if not constraints.is_conflict(day, slot, course, r, grp, is_fixed=True):
                        room = r; break
            total_required += 1
            if room:
                constraints.assign(day, slot, course, room, grp)
                entries.append(RoutineEntry(day=day, time_slot=slot, course=course, room=room, group_name=grp, is_fixed=True))
                scheduled += 1
            else:
                dropped.append(f"Dropped Fixed: {course.course_code} at {day.name} {slot.start_time}")

        # ---------- STEP 2: build sessions ----------
        # labs first (they need parallel partners); each lab is repeated once per section
        lab_sessions, theory_sessions = [], []
        for c in courses:
            bkey = (c.department.id, c.semester.id)
            ns = section_count.get(bkey, 1)
            if is_lab_course(c):
                if ns > 1:
                    per_sec = math.ceil((c.student_count or 0) / ns) if (c.student_count or 0) > 0 else 0
                    groups = [f"Group {chr(65 + k)}" for k in range(ns)]
                else:
                    per_sec, groups = (c.student_count or 0), [None]
                for grp in groups:
                    rem = c.credits
                    while rem >= 2:
                        lab_sessions.append((c, 2, grp, per_sec)); rem -= 2
                    if rem > 0:
                        lab_sessions.append((c, 1, grp, per_sec))
            else:
                for _ in range(c.credits):
                    theory_sessions.append((c, 1, None, c.student_count or 0))

        random.shuffle(lab_sessions); random.shuffle(theory_sessions)
        total_required += len(lab_sessions) + len(theory_sessions)

        # ---------- STEP 3: place labs (parallel sections), then theories ----------
        def place_all(queue):
            nonlocal scheduled
            for course, duration, grp, cap in queue:
                if not matching_rooms(course, all_rooms):
                    dropped.append(f"Dropped: {course.course_code} (no matching room exists)")
                    continue
                ok = place_session(constraints, config, course, duration, grp, cap, days, time_slots, entries, relax=False)
                if not ok:
                    ok = place_session(constraints, config, course, duration, grp, cap, days, time_slots, entries, relax=True)
                if ok:
                    scheduled += 1
                else:
                    gs = f" ({grp})" if grp else ""
                    dropped.append(f"Dropped: {course.course_code}{gs} (no free room at any time)")

        place_all(lab_sessions)
        place_all(theory_sessions)

        # ---------- STEP 4: validate + commit ----------
        if entries:
            try:
                for e in entries:
                    e.full_clean(); e.save()
            except (ValidationError, IntegrityError) as ex:
                transaction.set_rollback(True)
                return {"status": "Error", "message": f"Database overlap prevented: {ex}"}

        if dropped and not ignore_warnings:
            transaction.set_rollback(True)
            return {
                "status": "Warning", "total_classes_required": total_required,
                "successful_classes": scheduled, "dropped_classes": len(dropped),
                "shortage_details": dropped,
                "message": "Unable to assign some classes. You can ignore this warning to save the partial routine."
            }
        return {
            "status": "Success", "total_classes_required": total_required,
            "successful_classes": scheduled, "dropped_classes": len(dropped),
            "shortage_details": dropped,
            "message": "Routine generated 100% successfully" if not dropped
                       else "Partial routine generated. Some classes could not be scheduled."
        }


# ==============================================================================
# ROLLBACK
# ==============================================================================
def rollback_routine_algorithm(department_id):
    latest = RoutineBackup.objects.filter(department_id=department_id).order_by('-created_at').first()
    if not latest:
        return {"status": "Error", "message": "No backup found."}
    setting = SystemSetting.objects.first()
    if setting and setting.is_routine_locked:
        return {"status": "Locked", "message": "System is locked."}
    RoutineEntry.objects.filter(course__department_id=department_id).delete()
    try:
        for item in latest.backup_data:
            e = RoutineEntry(day_id=item['day_id'], time_slot_id=item['time_slot_id'],
                             course_id=item['course_id'], room_id=item['room_id'],
                             group_name=item.get('group_name'), is_fixed=item.get('is_fixed', False))
            e.full_clean(); e.save()
    except (ValidationError, IntegrityError) as ex:
        return {"status": "Error", "message": f"Rollback failed: {ex}"}
    return {"status": "Success", "message": "Routine rolled back successfully."}