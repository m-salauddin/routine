# academic/utils.py
import random
import math
from django.db import transaction
from .models import (
    Day, Course, TimeSlot, RoutineEntry, Room, 
    SystemSetting, RoutineBackup, BatchTimeConstraint, FixedClassSchedule,
    AlgorithmConfig  
)

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
        
        total_days = max(1, len(days))
        
        self.teacher_limits = {
            tid: math.ceil(total / total_days) + 2 
            for tid, total in teacher_totals.items()
        }
        self.batch_limits = {
            bid: math.ceil(total / total_days) + 2  
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
            
        max_group_load = 0
        for k, v in self.batch_daily_count.items():
            if k[0] == dept_id and k[1] == sem_id and k[2] == day_id and k[3] is not None:
                if v > max_group_load:
                    max_group_load = v
        return common_load + max_group_load

    def can_schedule_daily(self, day_id, course, duration, group_name=None):
        if course.teacher:
            t_limit = self.teacher_limits.get(course.teacher.id, 4)
            current_t_load = self.teacher_daily_count.get((course.teacher.id, day_id), 0)
            if current_t_load + duration > t_limit:
                return False
                
        dept_id, sem_id = course.department.id, course.semester.id
        b_limit = self.batch_limits.get((dept_id, sem_id), 6)
        
        current_b_load = self.get_batch_day_load(dept_id, sem_id, day_id, group_name)
        if current_b_load + duration > b_limit:
            return False
            
        return True

    def can_schedule_continuous(self, day_id, start_idx, duration, course, group_name=None):
        MAX_CONTINUOUS = 4

        b_map_key_grp = (day_id, course.department.id, course.semester.id, group_name)
        b_map_key_all = (day_id, course.department.id, course.semester.id, None)
        
        batch_occupied = self.batch_schedule_map.get(b_map_key_grp, set()).union(
                         self.batch_schedule_map.get(b_map_key_all, set()))

        left_idx = start_idx - 1
        left_count = 0
        while left_idx in batch_occupied and left_idx not in self.lunch_indices:
            left_count += 1
            left_idx -= 1

        right_idx = start_idx + duration
        right_count = 0
        while right_idx in batch_occupied and right_idx not in self.lunch_indices:
            right_count += 1
            right_idx += 1

        if left_count + duration + right_count > MAX_CONTINUOUS:
            return False

        if course.teacher:
            teacher_key = (day_id, course.teacher.id)
            teacher_occupied = self.teacher_schedule_map.get(teacher_key, set())

            left_idx = start_idx - 1
            left_count = 0
            while left_idx in teacher_occupied and left_idx not in self.lunch_indices:
                left_count += 1
                left_idx -= 1

            right_idx = start_idx + duration
            right_count = 0
            while right_idx in teacher_occupied and right_idx not in self.lunch_indices:
                right_count += 1
                right_idx += 1

            if left_count + duration + right_count > MAX_CONTINUOUS:
                return False

        return True

    def is_conflict(self, day, slot, course, room, group_name=None, is_fixed=False):
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
            
        b_key = (day_id, slot.id, course.department.id, course.semester.id)
        if b_key in self.batch_slot_groups:
            occupied_groups = self.batch_slot_groups[b_key]
            if None in occupied_groups:
                return True 
            if group_name is None and len(occupied_groups) > 0:
                return True  
            if group_name in occupied_groups:
                return True  

        is_lab = course.course_type and 'lab' in course.course_type.name.lower()
        
        if not is_fixed and not is_lab and (course.id, group_name, day_id) in self.course_daily_tracker:
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

def prepare_prioritized_sessions(courses, all_active_rooms, fixed_counts=None, course_fixed_groups=None):
    if fixed_counts is None:
        fixed_counts = {}
    if course_fixed_groups is None:
        course_fixed_groups = {}
        
    all_sessions = []
    for course in courses:
        is_lab_course = course.course_type and 'lab' in course.course_type.name.lower()
        
        valid_rooms = get_valid_rooms_for_course(course, all_active_rooms, is_lab_course, None)
        
        groups = [None]
        req_capacity = course.student_count
        
        if is_lab_course:
            if course.id in course_fixed_groups and None in course_fixed_groups[course.id]:
                groups = [None]
            elif valid_rooms and valid_rooms[0].capacity < course.student_count:
                num_groups = math.ceil(course.student_count / valid_rooms[0].capacity)
                groups = [f"Group {chr(65+i)}" for i in range(num_groups)]
                req_capacity = math.ceil(course.student_count / num_groups)
            
        total_credits = course.credits if course.credits > 0 else 1
        fixed_bonus = 1000 if course.fixed_room else 0
        if is_lab_course:
            fixed_bonus += 5000  

        for grp in groups:
            remaining_credits = course.credits - fixed_counts.get((course.id, grp), 0)
            if remaining_credits <= 0:
                continue  
                
            credits_filled = fixed_counts.get((course.id, grp), 0)
            
            if is_lab_course:
                temp_rem = remaining_credits
                while temp_rem >= 2:
                    credits_filled += 2
                    all_sessions.append({
                        'course': course, 'group': grp, 'duration': 2, 
                        'priority_score': (credits_filled / total_credits) + fixed_bonus, 
                        'is_lab': True, 'req_capacity': req_capacity
                    })
                    temp_rem -= 2
                if temp_rem > 0:
                    credits_filled += 1
                    all_sessions.append({
                        'course': course, 'group': grp, 'duration': 1, 
                        'priority_score': (credits_filled / total_credits) + fixed_bonus, 
                        'is_lab': True, 'req_capacity': req_capacity
                    })
            else:
                for _ in range(remaining_credits):
                    credits_filled += 1
                    all_sessions.append({
                        'course': course, 'group': grp, 'duration': 1, 
                        'priority_score': (credits_filled / total_credits) + fixed_bonus, 
                        'is_lab': False, 'req_capacity': req_capacity
                    })

    random.shuffle(all_sessions)
    all_sessions.sort(key=lambda x: (
        x['priority_score'], 
        -x['duration'],
        x['course'].department.id,
        x['course'].semester.id if x['course'].semester else 0
    ), reverse=True)
    
    return all_sessions

def generate_routine_algorithm(department_id, semester_id=None, ignore_warnings=False):
    setting = SystemSetting.objects.first()
    if setting and setting.is_routine_locked:
        return {"status": "Locked", "message": "System is locked. Cannot generate routine."}

    # =========================================================================
    # [NEW] Load Dynamic Configuration from the Database
    # =========================================================================
    config_obj = AlgorithmConfig.objects.first()
    
    class DefaultConfig:
        parallel_bonus = 50000
        edge_slot_penalty = 2000
        zero_gap_bonus = 1000
        gap_penalty_per_slot = 500
        center_gravity_bonus = 50
        continuous_class_penalty = 100
        day_load_penalty_multiplier = 150
        
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

        constraints_qs = BatchTimeConstraint.objects.filter(is_active=True)
        batch_constraints_dict = {}
        for c in constraints_qs:
            key = (c.department_id, c.semester_id, c.day_id, c.time_slot_id)
            if key in batch_constraints_dict and batch_constraints_dict[key] == 'CLASS_OFF':
                continue
            batch_constraints_dict[key] = c.constraint_type

        course_fixed_groups = {}
        for fs in fixed_schedules:
            if fs.course_id not in course_fixed_groups:
                course_fixed_groups[fs.course_id] = set()
            course_fixed_groups[fs.course_id].add(fs.group_name)

        teacher_totals = {}
        batch_totals = {}
        
        for c in courses_to_schedule:
            is_lab = c.course_type and 'lab' in c.course_type.name.lower()
            valid_rooms = get_valid_rooms_for_course(c, all_active_rooms, is_lab, None)
            
            num_groups = 1
            if is_lab:
                if c.id in course_fixed_groups and None in course_fixed_groups[c.id]:
                    num_groups = 1
                elif valid_rooms and valid_rooms[0].capacity < c.student_count:
                    num_groups = math.ceil(c.student_count / valid_rooms[0].capacity)
                
            total_teacher_credits = c.credits * num_groups
            if c.teacher:
                teacher_totals[c.teacher.id] = teacher_totals.get(c.teacher.id, 0) + total_teacher_credits
            
            batch_key = (c.department.id, c.semester.id)
            batch_totals[batch_key] = batch_totals.get(batch_key, 0) + c.credits

        constraints = ScheduleConstraint(days, time_slots, batch_constraints_dict, teacher_totals, batch_totals)

        existing_routines = RoutineEntry.objects.select_related('day', 'time_slot', 'course', 'course__teacher', 'course__department', 'course__semester', 'room').filter(is_active=True)
        for r in existing_routines:
            constraints.assign(r.day, r.time_slot, r.course, r.room, r.group_name)

        fixed_routines_to_insert = []
        fixed_counts = {}
        
        scheduled_count = 0
        dropped_sessions = []
        routines_to_create = []
        
        for fs in fixed_schedules:
            course = fs.course
            day = fs.day
            slot = fs.time_slot
            is_lab = course.course_type and 'lab' in course.course_type.name.lower()
            
            valid_rooms = get_valid_rooms_for_course(course, all_active_rooms, is_lab, None)
            groups_to_schedule = [fs.group_name]
                
            for grp in groups_to_schedule:
                assigned_room = fs.room
                
                if assigned_room and constraints.is_conflict(day, slot, course, assigned_room, grp, is_fixed=True):
                    assigned_room = None
                    
                if not assigned_room:
                    valid_rooms.sort(key=lambda r: (r.capacity, constraints.room_usage_count.get(r.id, 0)))
                    for r in valid_rooms:
                        if not constraints.is_conflict(day, slot, course, r, grp, is_fixed=True):
                            assigned_room = r
                            break
                            
                if assigned_room:
                    constraints.assign(day, slot, course, assigned_room, grp)
                    fixed_routines_to_insert.append(RoutineEntry(
                        day=day, time_slot=slot, course=course, room=assigned_room, group_name=grp, is_fixed=True
                    ))
                    fixed_counts[(course.id, grp)] = fixed_counts.get((course.id, grp), 0) + 1
                    scheduled_count += 1
                else:
                    grp_str = f" ({grp})" if grp else ""
                    dropped_sessions.append(f"Dropped Fixed: {course.course_code}{grp_str} at {day.name} {slot.start_time} (No available room)")

        if fixed_routines_to_insert:
            RoutineEntry.objects.bulk_create(fixed_routines_to_insert)

        sorted_sessions = prepare_prioritized_sessions(courses_to_schedule, all_active_rooms, fixed_counts, course_fixed_groups)
        total_required = scheduled_count + len(sorted_sessions)

        for session in sorted_sessions:
            course = session['course']
            duration = session['duration']
            is_lab = session['is_lab']
            group_name = session['group']  
            req_capacity = session.get('req_capacity', course.student_count)
            
            valid_rooms = get_valid_rooms_for_course(course, all_active_rooms, is_lab, req_capacity)

            if not valid_rooms:
                dropped_sessions.append(f"Dropped: {course.course_name} (No room available with capacity >= {req_capacity})")
                continue

            group_assigned = False
            
            sorted_days = sorted(
                days, 
                key=lambda d: constraints.get_batch_day_load(course.department.id, course.semester.id, d.id, group_name)
            )

            for day in sorted_days:
                if group_assigned: break
                
                if not constraints.can_schedule_daily(day.id, course, duration, group_name):
                    continue

                b_key_grp = (day.id, course.department.id, course.semester.id, group_name)
                b_key_all = (day.id, course.department.id, course.semester.id, None)
                
                occupied_slots = constraints.batch_schedule_map.get(b_key_grp, set()).union(
                                 constraints.batch_schedule_map.get(b_key_all, set()))
                
                possible_starts = list(range(len(time_slots) - duration + 1))
                
                def calculate_slot_score(start_idx):
                    score = 0
                    
                    # [DYNAMIC] Edge Control & Comfort Zone
                    for w in range(start_idx, start_idx + duration):
                        if w == 0: score -= config.edge_slot_penalty 
                        elif w == 1: score -= (config.edge_slot_penalty // 3)
                        elif w == total_slots - 1: score -= config.edge_slot_penalty
                        elif w == total_slots - 2: score -= (config.edge_slot_penalty // 3)
                        else: score += config.center_gravity_bonus  

                    # [DYNAMIC] Magnetic Gravity & Gap Penalty
                    if occupied_slots:
                        min_dist = float('inf')
                        for o in occupied_slots:
                            dist = start_idx - o - 1 if o < start_idx else o - (start_idx + duration)
                            if dist < 0: dist = 0 
                            if dist < min_dist: min_dist = dist
                        
                        if min_dist == 0:
                            score += config.zero_gap_bonus  
                        else:
                            score -= (min_dist * config.gap_penalty_per_slot)  
                            
                    # [DYNAMIC] Continuous class balancing
                    left_count, right_count, l_idx, r_idx = 0, 0, start_idx - 1, start_idx + duration
                    while l_idx in occupied_slots and l_idx not in constraints.lunch_indices:
                        left_count += 1; l_idx -= 1
                    while r_idx in occupied_slots and r_idx not in constraints.lunch_indices:
                        right_count += 1; r_idx += 1
                    if left_count + duration + right_count >= 3:
                        score -= config.continuous_class_penalty 
                        
                    # [DYNAMIC] Cross-Scheduling Parallel Groups
                    if group_name is not None:
                        parallel_bonus = 0
                        for w_slot in time_slots[start_idx : start_idx + duration]:
                            check_key = (day.id, w_slot.id, course.department.id, course.semester.id)
                            groups_here = constraints.batch_slot_groups.get(check_key, set())
                            sibling_groups = [g for g in groups_here if g is not None and g != group_name]
                            if sibling_groups:
                                parallel_bonus += config.parallel_bonus  
                        score += parallel_bonus
                    
                    return score

                possible_starts.sort(key=lambda idx: calculate_slot_score(idx), reverse=True)

                for i in possible_starts:
                    if group_assigned: break
                    start_slot = time_slots[i]
                    
                    if not constraints.can_schedule_continuous(day.id, i, duration, course, group_name):
                        continue
                        
                    window_slots = time_slots[i : i + duration]
                    selected_room = None
                    
                    valid_rooms.sort(key=lambda r: (r.capacity, constraints.room_usage_count.get(r.id, 0)))
                    
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

            if not group_assigned:
                for day in sorted_days:
                    if group_assigned: break
                    
                    b_key_grp = (day.id, course.department.id, course.semester.id, group_name)
                    b_key_all = (day.id, course.department.id, course.semester.id, None)
                    occupied_slots = constraints.batch_schedule_map.get(b_key_grp, set()).union(
                                     constraints.batch_schedule_map.get(b_key_all, set()))
                    
                    possible_starts = list(range(len(time_slots) - duration + 1))
                    
                    def calculate_fallback_score(start_idx):
                        score = 0
                        
                        # [DYNAMIC] Edge Control & Comfort Zone
                        for w in range(start_idx, start_idx + duration):
                            if w == 0: score -= config.edge_slot_penalty
                            elif w == 1: score -= (config.edge_slot_penalty // 3)
                            elif w == total_slots - 1: score -= config.edge_slot_penalty
                            elif w == total_slots - 2: score -= (config.edge_slot_penalty // 3)
                            else: score += config.center_gravity_bonus

                        # [DYNAMIC] Magnetic Gravity & Gap Penalty
                        if occupied_slots:
                            min_dist = float('inf')
                            for o in occupied_slots:
                                dist = start_idx - o - 1 if o < start_idx else o - (start_idx + duration)
                                if dist < 0: dist = 0
                                if dist < min_dist: min_dist = dist
                            if min_dist == 0:
                                score += config.zero_gap_bonus
                            else:
                                score -= (min_dist * config.gap_penalty_per_slot)
                                
                        # [DYNAMIC] Cross-Scheduling Parallel Groups
                        if group_name is not None:
                            parallel_bonus = 0
                            for w_slot in time_slots[start_idx : start_idx + duration]:
                                check_key = (day.id, w_slot.id, course.department.id, course.semester.id)
                                groups_here = constraints.batch_slot_groups.get(check_key, set())
                                sibling_groups = [g for g in groups_here if g is not None and g != group_name]
                                if sibling_groups:
                                    parallel_bonus += config.parallel_bonus  
                            score += parallel_bonus
                            
                        return score

                    possible_starts.sort(key=lambda idx: calculate_fallback_score(idx), reverse=True)

                    for i in possible_starts:
                        if group_assigned: break
                        window_slots = time_slots[i : i + duration]
                        selected_room = None
                        
                        valid_rooms.sort(key=lambda r: (r.capacity, constraints.room_usage_count.get(r.id, 0)))
                        
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
                if is_lab and group_name is None:
                    all_lab_rooms = get_valid_rooms_for_course(course, all_active_rooms, True, None)
                    smaller_rooms = [r for r in all_lab_rooms if r.capacity < course.student_count]
                    
                    if smaller_rooms:
                        split_cap = smaller_rooms[0].capacity
                    else:
                        split_cap = max(1, course.student_count // 2)
                        
                    num_groups = math.ceil(course.student_count / split_cap)
                    
                    if num_groups > 1:
                        new_req_capacity = math.ceil(course.student_count / num_groups)
                        
                        if course.teacher:
                            constraints.teacher_limits[course.teacher.id] = constraints.teacher_limits.get(course.teacher.id, 4) + (duration * (num_groups - 1))
                        batch_key = (course.department.id, course.semester.id)
                        constraints.batch_limits[batch_key] = constraints.batch_limits.get(batch_key, 6) + (duration * (num_groups - 1))
                        
                        new_groups = [f"Group {chr(65+i)}" for i in range(num_groups)]
                        for grp in new_groups:
                            sorted_sessions.append({
                                'course': course, 'group': grp, 'duration': duration, 
                                'priority_score': session['priority_score'] + config.parallel_bonus, 
                                'is_lab': True, 'req_capacity': new_req_capacity
                            })
                        
                        total_required += (num_groups - 1)
                        continue 

                grp_str = f" ({group_name})" if group_name else ""
                dropped_sessions.append(f"Dropped: {course.course_name}{grp_str} (Global conflict)")

        if routines_to_create:
            RoutineEntry.objects.bulk_create(routines_to_create)

        if len(dropped_sessions) > 0 and not ignore_warnings:
            transaction.set_rollback(True)
            return {
                "status": "Warning",
                "total_classes_required": total_required,
                "successful_classes": scheduled_count,
                "dropped_classes": len(dropped_sessions),
                "shortage_details": dropped_sessions,
                "message": "Unable to assign some classes. You can ignore this error and save the partial routine."
            }

        summary_message = "Routine generated 100% successfully" if len(dropped_sessions) == 0 else "Partial routine successfully generated. Some classes could not be scheduled due to conflicts."
        
        return {
            "status": "Success",
            "total_classes_required": total_required,
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
    
    routines = [
        RoutineEntry(
            day_id=item['day_id'], time_slot_id=item['time_slot_id'], 
            course_id=item['course_id'], room_id=item['room_id'], group_name=item.get('group_name'),
            is_fixed=item.get('is_fixed', False)
        ) for item in latest_backup.backup_data
    ]
    RoutineEntry.objects.bulk_create(routines)
    
    return {"status": "Success", "message": "Routine rolled back successfully."}