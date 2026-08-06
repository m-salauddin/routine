# academic/utils.py
import random
import math
from django.db import transaction
from django.core.exceptions import ValidationError
from .models import (
    Day, Course, TimeSlot, RoutineEntry, Room,
    SystemSetting, RoutineBackup, BatchTimeConstraint, FixedClassSchedule,
    AlgorithmConfig
)

class ScheduleConstraint:
    def __init__(self, days, time_slots, batch_constraints_dict, teacher_totals, batch_totals):
        self.teacher_occupied = {}
        self.room_occupied = set()
        self.course_daily_tracker = set()
        self.batch_slot_groups = {}

        self.day_loads = {day.id: 0 for day in days}
        self.teacher_daily_count = {}
        self.batch_daily_count = {}
        self.room_usage_count = {}

        self.batch_constraints = batch_constraints_dict
        self.lunch_indices = {idx for idx, slot in enumerate(time_slots) if slot.is_lunch_break}
        self.total_days = max(1, len(days))

        self.teacher_limits = {
            tid: math.ceil(total / self.total_days) + 1
            for tid, total in teacher_totals.items()
        }
        self.batch_limits = {
            bid: math.ceil(total / self.total_days) + 1
            for bid, total in batch_totals.items()
        }

        self.slot_index_map = {slot.id: idx for idx, slot in enumerate(time_slots)}
        self.teacher_schedule_map = {}
        self.batch_schedule_map = {}

    def get_batch_day_load(self, dept_id, sem_id, day_id, group_name=None):
        common_load = self.batch_daily_count.get((dept_id, sem_id, day_id, None), 0)
        if group_name:
            group_load = self.batch_daily_count.get((dept_id, sem_id, day_id, group_name), 0)
            return common_load + group_load
        return common_load

    def can_schedule_daily(self, day_id, course, duration, group_name=None, is_emergency=False):
        extra_limit = 2 if is_emergency else 0
        if course.teacher:
            t_limit = self.teacher_limits.get(course.teacher.id, 5) + extra_limit
            current_t = self.teacher_daily_count.get((course.teacher.id, day_id), 0)
            if current_t + duration > t_limit:
                return False
        dept_id, sem_id = course.department.id, course.semester.id
        b_limit = self.batch_limits.get((dept_id, sem_id), 6) + extra_limit
        current_b = self.get_batch_day_load(dept_id, sem_id, day_id, group_name)
        if current_b + duration > b_limit:
            return False
        return True

    def can_schedule_continuous(self, day_id, start_idx, duration, course, group_name=None, is_emergency=False):
        MAX_CONTINUOUS = 4 if is_emergency else 3
        b_map_key_grp = (day_id, course.department.id, course.semester.id, group_name)
        b_map_key_all = (day_id, course.department.id, course.semester.id, None)
        batch_occupied = self.batch_schedule_map.get(b_map_key_grp, set()).union(
                         self.batch_schedule_map.get(b_map_key_all, set()))

        left_count = 0
        left_idx = start_idx - 1
        while left_idx in batch_occupied and left_idx not in self.lunch_indices:
            left_count += 1
            left_idx -= 1
        right_count = 0
        right_idx = start_idx + duration
        while right_idx in batch_occupied and right_idx not in self.lunch_indices:
            right_count += 1
            right_idx += 1

        if left_count + duration + right_count > MAX_CONTINUOUS:
            return False
        if course.teacher:
            teacher_key = (day_id, course.teacher.id)
            teacher_occupied = self.teacher_schedule_map.get(teacher_key, set())
            left_count = 0
            left_idx = start_idx - 1
            while left_idx in teacher_occupied and left_idx not in self.lunch_indices:
                left_count += 1
                left_idx -= 1
            right_count = 0
            right_idx = start_idx + duration
            while right_idx in teacher_occupied and right_idx not in self.lunch_indices:
                right_count += 1
                right_idx += 1
            if left_count + duration + right_count > MAX_CONTINUOUS:
                return False
        return True

    def is_conflict(self, day, slot, course, room, group_name=None, is_fixed=False):
        day_id = day.id
        constraint_type = self.batch_constraints.get((course.department.id, course.semester.id, day_id, slot.id))
        if constraint_type == 'CLASS_OFF': return True
        if slot.is_lunch_break and constraint_type != 'FORCE_ALLOW_LUNCH_CLASS': return True
        if course.teacher and (day_id, slot.id, course.teacher.id) in self.teacher_occupied:
            return True
        if room and (day_id, slot.id, room.id) in self.room_occupied:
            return True
        b_key = (day_id, slot.id, course.department.id, course.semester.id)
        if b_key in self.batch_slot_groups:
            occupied_groups = self.batch_slot_groups[b_key]
            if None in occupied_groups: return True
            if group_name is None and len(occupied_groups) > 0: return True
            if group_name in occupied_groups: return True
        is_lab = course.course_type and 'lab' in course.course_type.name.lower()
        if not is_fixed and not is_lab and (course.id, group_name, day_id) in self.course_daily_tracker:
            return True
        return False

    def assign(self, day, slot, course, room, group_name=None):
        day_id = day.id
        slot_idx = self.slot_index_map[slot.id]
        if course.teacher:
            self.teacher_occupied[(day_id, slot.id, course.teacher.id)] = course.id
            self.teacher_daily_count[(course.teacher.id, day_id)] = self.teacher_daily_count.get((course.teacher.id, day_id), 0) + 1
            sched_key = (day_id, course.teacher.id)
            if sched_key not in self.teacher_schedule_map:
                self.teacher_schedule_map[sched_key] = set()
            self.teacher_schedule_map[sched_key].add(slot_idx)
        if room:
            self.room_occupied.add((day_id, slot.id, room.id))
            self.room_usage_count[room.id] = self.room_usage_count.get(room.id, 0) + 1
        b_key_groups = (day_id, slot.id, course.department.id, course.semester.id)
        if b_key_groups not in self.batch_slot_groups:
            self.batch_slot_groups[b_key_groups] = set()
        self.batch_slot_groups[b_key_groups].add(group_name)
        self.course_daily_tracker.add((course.id, group_name, day_id))
        self.day_loads[day_id] += 1
        b_key = (course.department.id, course.semester.id, day_id, group_name)
        self.batch_daily_count[b_key] = self.batch_daily_count.get(b_key, 0) + 1
        b_map_key = (day_id, course.department.id, course.semester.id, group_name)
        if b_map_key not in self.batch_schedule_map:
            self.batch_schedule_map[b_map_key] = set()
        self.batch_schedule_map[b_map_key].add(slot_idx)


def get_valid_rooms_for_course(course, all_active_rooms, is_lab, required_capacity=None):
    if course.fixed_room and course.fixed_room.is_active:
        return [course.fixed_room]

    base_matching_rooms = [
        r for r in all_active_rooms
        if r.room_type_id == course.course_type_id
        and (not course.course_sub_type_id or r.room_sub_type_id == course.course_sub_type_id)
    ]
    dept_to_search = course.preferred_room_department or course.offering_department or course.department
    valid_rooms = [r for r in base_matching_rooms if r.department_id == dept_to_search.id]
    if not valid_rooms:
        return []

    if required_capacity is None:
        valid_rooms.sort(key=lambda x: x.capacity, reverse=True)
        return valid_rooms

    rooms_fitting = [r for r in valid_rooms if r.capacity >= required_capacity]
    rooms_fitting.sort(key=lambda x: x.capacity)
    return rooms_fitting


def generate_routine_algorithm(department_id, semester_id=None, ignore_warnings=False):
    setting = SystemSetting.objects.first()
    if setting and setting.is_routine_locked:
        return {"status": "Locked", "message": "System is locked. Cannot generate routine."}

    config_obj = AlgorithmConfig.objects.first()
    class DefaultConfig:
        parallel_bonus = 50000
        edge_slot_penalty = 2000
        zero_gap_bonus = 30000          # বাড়ানো হয়েছে
        gap_penalty_per_slot = 1500     # বাড়ানো হয়েছে
        center_gravity_bonus = 50
        continuous_class_penalty = 100
        day_load_penalty_multiplier = 150
        break_after_block_bonus = 20000
        ideal_load_deviation_penalty = 5000
        load_balance_factor = 20000
        lab_slots_per_credit = 2
        lab_force_pair = True
        max_parallel_lab_groups = 2
        # নতুন গ্যাপ ফিল্ড
        gap_square_penalty = 800
        adjacent_cluster_bonus = 5000
    config = config_obj if config_obj else DefaultConfig()

    # ---------- ডাটা প্রস্তুতি ----------
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

    # ব্যাকআপ ট্রানজেকশনের বাইরে
    if old_routines.exists():
        backup_list = [{
            'day_id': e.day_id, 'time_slot_id': e.time_slot_id,
            'course_id': e.course_id, 'room_id': e.room_id,
            'group_name': e.group_name, 'is_fixed': getattr(e, 'is_fixed', False)
        } for e in old_routines]
        RoutineBackup.objects.create(department_id=department_id, backup_data=backup_list)

    with transaction.atomic():
        old_routines.delete()

        days = list(Day.objects.all().order_by('order'))
        time_slots = list(TimeSlot.objects.all().order_by('start_time'))
        total_slots = len(time_slots)
        all_active_rooms = list(Room.objects.filter(is_active=True))

        constraints_qs = BatchTimeConstraint.objects.filter(is_active=True)
        batch_constraints_dict = {}
        for c in constraints_qs:
            key = (c.department_id, c.semester_id, c.day_id, c.time_slot_id)
            if key not in batch_constraints_dict or batch_constraints_dict[key] != 'CLASS_OFF':
                batch_constraints_dict[key] = c.constraint_type

        course_fixed_groups = {}
        for fs in fixed_schedules:
            course_fixed_groups.setdefault(fs.course_id, set()).add(fs.group_name)

        course_groups_info = {}
        teacher_totals = {}
        batch_totals = {}

        for course in courses_to_schedule:
            is_lab = course.course_type and 'lab' in course.course_type.name.lower()
            all_possible_rooms = get_valid_rooms_for_course(course, all_active_rooms, is_lab, None)

            groups = [None]
            req_capacity = course.student_count

            if is_lab and (course.id not in course_fixed_groups or None not in course_fixed_groups.get(course.id, set())):
                if all_possible_rooms and all_possible_rooms[-1].capacity < course.student_count:
                    max_cap = all_possible_rooms[-1].capacity
                    num_groups = math.ceil(course.student_count / max_cap)
                    if num_groups > 1:
                        groups = [f"Group {chr(65+i)}" for i in range(num_groups)]
                        req_capacity = math.ceil(course.student_count / num_groups)

            course_groups_info[course.id] = {
                'groups': groups,
                'req_capacity': req_capacity,
                'is_lab': is_lab
            }

            # টিচার/ব্যাচ টোটাল হিসাব
            if is_lab:
                session_duration = 2 if config.lab_force_pair else 1
                total_lab_slots = course.credits * config.lab_slots_per_credit
                num_sessions = total_lab_slots // session_duration
                total_units = num_sessions * session_duration * len(groups)
            else:
                total_units = course.credits * len(groups)

            if course.teacher:
                teacher_totals[course.teacher.id] = teacher_totals.get(course.teacher.id, 0) + total_units
            batch_totals[(course.department.id, course.semester.id)] = batch_totals.get((course.department.id, course.semester.id), 0) + total_units

        constraints = ScheduleConstraint(days, time_slots, batch_constraints_dict, teacher_totals, batch_totals)

        # চলমান রুটিন লোড
        existing_routines = RoutineEntry.objects.select_related(
            'day', 'time_slot', 'course', 'course__teacher', 'course__department', 'course__semester', 'room'
        ).filter(is_active=True)
        for r in existing_routines:
            constraints.assign(r.day, r.time_slot, r.course, r.room, r.group_name)

        routines_to_create = []
        fixed_counts = {}
        scheduled_count = 0
        dropped_sessions = []

        # ========== ধাপ ১: ফিক্সড শিডিউল ==========
        for fs in fixed_schedules:
            course = fs.course
            day = fs.day
            slot = fs.time_slot
            is_lab = course.course_type and 'lab' in course.course_type.name.lower()
            valid_rooms = get_valid_rooms_for_course(course, all_active_rooms, is_lab, None)

            grp = fs.group_name
            assigned_room = fs.room
            reason = None
            if assigned_room and constraints.is_conflict(day, slot, course, assigned_room, grp, is_fixed=True):
                reason = f"Room {assigned_room.room_number} conflict"
                assigned_room = None
            if not assigned_room:
                valid_rooms.sort(key=lambda r: (r.capacity, constraints.room_usage_count.get(r.id, 0)))
                for r in valid_rooms:
                    if not constraints.is_conflict(day, slot, course, r, grp, is_fixed=True):
                        assigned_room = r
                        reason = None
                        break
                if not assigned_room:
                    if not reason:
                        if course.teacher and (day.id, slot.id, course.teacher.id) in constraints.teacher_occupied:
                            reason = f"Teacher {course.teacher.username} already occupied"
                        else:
                            b_key = (day.id, slot.id, course.department.id, course.semester.id)
                            if b_key in constraints.batch_slot_groups:
                                groups_here = constraints.batch_slot_groups[b_key]
                                if None in groups_here or (grp is None and len(groups_here) > 0) or (grp in groups_here):
                                    reason = "Batch/Group conflict"
                                else:
                                    reason = "No suitable room"
                            else:
                                reason = "No suitable room"

            if assigned_room and not reason:
                constraints.assign(day, slot, course, assigned_room, grp)
                routines_to_create.append(RoutineEntry(
                    day=day, time_slot=slot, course=course, room=assigned_room,
                    group_name=grp, is_fixed=True
                ))
                fixed_counts[(course.id, grp)] = fixed_counts.get((course.id, grp), 0) + 1
                scheduled_count += 1
            else:
                grp_str = f" ({grp})" if grp else ""
                drop_msg = f"Fixed-Dropped: {course.course_code}{grp_str} at {day.name} {slot.start_time}"
                if reason:
                    drop_msg += f" - {reason}"
                dropped_sessions.append(drop_msg)

        # ========== ধাপ ২: সেশন তৈরি (ল্যাব → থিওরি) ==========
        all_lab_sessions = []
        all_theory_sessions = []

        for course in courses_to_schedule:
            info = course_groups_info[course.id]
            is_lab = info['is_lab']
            req_capacity = info['req_capacity']
            groups = info['groups']

            for grp in groups:
                remaining_credits = course.credits - fixed_counts.get((course.id, grp), 0)
                if remaining_credits <= 0:
                    continue

                if is_lab:
                    session_duration = 2 if config.lab_force_pair else 1
                    total_slots_needed = remaining_credits * config.lab_slots_per_credit
                    num_sessions = total_slots_needed // session_duration
                    for _ in range(num_sessions):
                        all_lab_sessions.append({
                            'course': course, 'group': grp, 'duration': session_duration,
                            'is_lab': True, 'req_capacity': req_capacity
                        })
                else:
                    for _ in range(remaining_credits):
                        all_theory_sessions.append({
                            'course': course, 'group': grp, 'duration': 1,
                            'is_lab': False, 'req_capacity': req_capacity
                        })

        random.shuffle(all_lab_sessions)
        random.shuffle(all_theory_sessions)
        # গ্রুপ ল্যাব আগে (প্যারালালাইজেশনের জন্য)
        all_lab_sessions.sort(key=lambda x: (
            0 if len(course_groups_info[x['course'].id]['groups']) > 1 else 1,
            x['course'].id
        ))
        all_sessions = all_lab_sessions + all_theory_sessions

        total_required = scheduled_count + len(all_sessions)

        # আদর্শ দৈনিক লোড
        ideal_day_load = {
            bid: total / constraints.total_days for bid, total in batch_totals.items()
        }

        # প্যারালাল ট্র্যাকার
        parallel_slot_map = {}  # (course_id, day_id, start_slot) -> [group_names]

        for session in all_sessions:
            course = session['course']
            duration = session['duration']
            is_lab = session['is_lab']
            group_name = session['group']
            req_capacity = session['req_capacity']

            valid_rooms = get_valid_rooms_for_course(course, all_active_rooms, is_lab, req_capacity)
            if not valid_rooms:
                dropped_sessions.append(f"No Room: {course.course_code} (cap>={req_capacity})")
                continue

            best_options = []

            # দিন সাজাই কম লোড আগে
            sorted_days = sorted(days, key=lambda d: (
                constraints.get_batch_day_load(course.department.id, course.semester.id, d.id, group_name)
            ))

            groups_list = course_groups_info[course.id]['groups']
            is_group_lab = is_lab and len(groups_list) > 1

            for emergency_mode in [False, True]:
                if best_options:
                    break
                for day in sorted_days:
                    if not constraints.can_schedule_daily(day.id, course, duration, group_name, is_emergency=emergency_mode):
                        continue

                    b_key_grp = (day.id, course.department.id, course.semester.id, group_name)
                    b_key_all = (day.id, course.department.id, course.semester.id, None)
                    occupied_slots = constraints.batch_schedule_map.get(b_key_grp, set()).union(
                                     constraints.batch_schedule_map.get(b_key_all, set()))

                    # ---------- নতুন: গ্যাপ-সাইজ ভিত্তিক স্লট অর্ডারিং ----------
                    # প্রথমে occupied_slots থেকে ছোট গ্যাপ খুঁজে প্রায়োরিটি লিস্ট তৈরি
                    gap_candidates = []
                    all_slot_positions = set(range(len(time_slots) - duration + 1))

                    if occupied_slots:
                        # সম্ভাব্য গ্যাপ খুঁজি
                        sorted_occ = sorted(occupied_slots)
                        # প্রতিটি ফাঁকা জায়গা চেক করি
                        for start in all_slot_positions:
                            # এই স্লট উইন্ডোতে কোনো occupied overlap না থাকলে
                            window_range = set(range(start, start + duration))
                            if window_range.isdisjoint(occupied_slots):
                                # গ্যাপ সাইজ বের করি (সবচেয়ে কাছের occupied স্লটের দূরত্ব)
                                left_gap = start - (max([o for o in sorted_occ if o < start], default=-1)) - 1
                                right_gap = (min([o for o in sorted_occ if o >= start + duration], default=total_slots)) - (start + duration)
                                if left_gap < 0: left_gap = total_slots  # any big number
                                if right_gap < 0: right_gap = total_slots
                                min_gap = min(left_gap, right_gap)
                                gap_candidates.append((min_gap, start))
                    else:
                        # কোনো ক্লাস নেই, সব স্লট সমান
                        gap_candidates = [(0, start) for start in all_slot_positions]

                    # গ্যাপ ছোট থেকে বড় সাজাই, সমান হলে সেন্টার গ্র্যাভিটি বোনাসের জন্য ইন্ডেক্স
                    gap_candidates.sort(key=lambda x: (x[0], abs(x[1] - total_slots//2)))

                    # প্যারালাল টার্গেট প্রথমে
                    parallel_targets = []
                    if is_group_lab:
                        for (cid, did, s_start), grps in parallel_slot_map.items():
                            if cid == course.id and did == day.id:
                                if len(grps) < config.max_parallel_lab_groups:
                                    parallel_targets.append(s_start)

                    # ক্যান্ডিডেট স্টার্ট: parallel_targets তারপর gap_candidates (parallel_targets বাদে)
                    final_candidates = []
                    for p in parallel_targets:
                        if p not in [c[1] for c in gap_candidates]:
                            # force add
                            gap_candidates.append((-1, p))  # -1 gap size so it comes first
                    final_candidates = parallel_targets + [c[1] for c in gap_candidates if c[1] not in parallel_targets]

                    for i in final_candidates:
                        if not (0 <= i <= len(time_slots) - duration):
                            continue
                        if not constraints.can_schedule_continuous(day.id, i, duration, course, group_name, is_emergency=emergency_mode):
                            continue

                        window_slots = time_slots[i:i+duration]
                        selected_room = None
                        valid_rooms.sort(key=lambda r: (r.capacity, constraints.room_usage_count.get(r.id, 0)))
                        for room in valid_rooms:
                            if not any(constraints.is_conflict(day, w_slot, course, room, group_name) for w_slot in window_slots):
                                selected_room = room
                                break
                        if not selected_room:
                            continue

                        # ---------- স্কোরিং (পরিবর্ধিত গ্যাপ স্কোরিং সহ) ----------
                        score = 0

                        # ১) লোড ব্যালেন্স
                        bid = (course.department.id, course.semester.id)
                        current_b_load = constraints.get_batch_day_load(course.department.id, course.semester.id, day.id, group_name)
                        ideal = ideal_day_load.get(bid, current_b_load)
                        new_load = current_b_load + duration
                        balance_score = (ideal - new_load) * config.load_balance_factor
                        score += balance_score

                        # ২) এজ স্লট পেনাল্টি
                        for w_idx in range(i, i+duration):
                            if w_idx == 0 or w_idx == total_slots - 1:
                                score -= config.edge_slot_penalty * 2
                            elif w_idx == 1 or w_idx == total_slots - 2:
                                score -= config.edge_slot_penalty
                            else:
                                score += config.center_gravity_bonus

                        # ৩) প্যারালাল ল্যাব বোনাস
                        if is_group_lab:
                            parallel_bonus = 0
                            for w_slot in window_slots:
                                check_key = (day.id, w_slot.id, course.department.id, course.semester.id)
                                groups_here = constraints.batch_slot_groups.get(check_key, set())
                                siblings = [g for g in groups_here if g is not None and g != group_name]
                                if siblings:
                                    parallel_bonus += config.parallel_bonus
                            if i in parallel_targets:
                                parallel_bonus += config.parallel_bonus * 2
                            score += parallel_bonus

                        # ৪) গ্যাপ/ক্লাস্টারিং স্কোর (নতুন)
                        if occupied_slots:
                            min_gap = float('inf')
                            left_gap, right_gap = None, None
                            for o in occupied_slots:
                                if o < i:
                                    gap = sum(1 for s in range(o+1, i) if s not in constraints.lunch_indices)
                                    if gap < min_gap:
                                        min_gap = gap
                                        left_gap = gap
                                else:
                                    gap = sum(1 for s in range(i+duration, o) if s not in constraints.lunch_indices)
                                    if gap < min_gap:
                                        min_gap = gap
                                        right_gap = gap

                            if min_gap == 0:
                                # জিরো গ্যাপ = ঠিক পাশাপাশি
                                score += config.zero_gap_bonus * 2
                                # additional adjacent cluster bonus
                                score += config.adjacent_cluster_bonus
                            else:
                                # গ্যাপ পেনাল্টি (linear + squared)
                                score -= min_gap * config.gap_penalty_per_slot
                                score -= (min_gap ** 2) * config.gap_square_penalty
                        else:
                            # প্রথম ক্লাস, কোনো গ্যাপ নাই, জিরো-গ্যাপ বোনাস দিন
                            score += config.zero_gap_bonus

                        # ৫) একটানা পেনাল্টি
                        left_count, right_count = 0, 0
                        l_idx, r_idx = i-1, i+duration
                        while l_idx in occupied_slots and l_idx not in constraints.lunch_indices:
                            left_count += 1; l_idx -= 1
                        while r_idx in occupied_slots and r_idx not in constraints.lunch_indices:
                            right_count += 1; r_idx += 1
                        total_continuous = left_count + duration + right_count
                        if total_continuous > 2:
                            score -= (total_continuous - 2) * config.continuous_class_penalty

                        # ৬) বিরতি বোনাস
                        if i + duration < total_slots:
                            next_slot_idx = i + duration
                            if next_slot_idx not in occupied_slots and next_slot_idx not in constraints.lunch_indices:
                                score += config.break_after_block_bonus

                        if emergency_mode:
                            score -= 100000

                        best_options.append((score, random.random(), day, window_slots, selected_room))

            if best_options:
                best_options.sort(key=lambda x: (x[0], x[1]), reverse=True)
                best = best_options[0]
                day, window_slots, room = best[2], best[3], best[4]
                for slot in window_slots:
                    constraints.assign(day, slot, course, room, group_name)
                    routines_to_create.append(RoutineEntry(
                        day=day, time_slot=slot, course=course, room=room, group_name=group_name
                    ))
                scheduled_count += 1

                if is_group_lab:
                    start_slot_idx = constraints.slot_index_map[window_slots[0].id]
                    key = (course.id, day.id, start_slot_idx)
                    if key not in parallel_slot_map:
                        parallel_slot_map[key] = []
                    parallel_slot_map[key].append(group_name)

            else:
                grp_str = f" ({group_name})" if group_name else ""
                dropped_sessions.append(f"Dropped: {course.course_code}{grp_str} (Limit/No Slot)")

        # ড্রপ থাকলে সতর্কতা
        if dropped_sessions and not ignore_warnings:
            transaction.set_rollback(True)
            return {
                "status": "Warning",
                "total_classes_required": total_required,
                "successful_classes": scheduled_count,
                "dropped_classes": len(dropped_sessions),
                "shortage_details": dropped_sessions,
                "message": "Some classes could not be scheduled. You can ignore and save partial routine."
            }

        if routines_to_create:
            try:
                for entry in routines_to_create:
                    entry.clean()
                    entry.save()
            except ValidationError as e:
                transaction.set_rollback(True)
                return {"status": "Error", "message": f"Validation Error: {str(e)}"}

        msg = "Routine generated 100% successfully." if not dropped_sessions else "Partial routine saved (some dropped)."
        return {
            "status": "Success",
            "total_classes_required": total_required,
            "successful_classes": scheduled_count,
            "dropped_classes": len(dropped_sessions),
            "shortage_details": dropped_sessions,
            "message": msg
        }


def rollback_routine_algorithm(department_id):
    latest_backup = RoutineBackup.objects.filter(department_id=department_id).order_by('-created_at').first()
    if not latest_backup:
        return {"status": "Error", "message": "No backup found."}
    setting = SystemSetting.objects.first()
    if setting and setting.is_routine_locked:
        return {"status": "Locked", "message": "System is locked."}
    RoutineEntry.objects.filter(course__department_id=department_id).delete()
    routines = [
        RoutineEntry(
            day_id=item['day_id'], time_slot_id=item['time_slot_id'],
            course_id=item['course_id'], room_id=item['room_id'],
            group_name=item.get('group_name'),
            is_fixed=item.get('is_fixed', False)
        ) for item in latest_backup.backup_data
    ]
    for entry in routines:
        entry.clean()
        entry.save()
    return {"status": "Success", "message": "Routine rolled back successfully."}