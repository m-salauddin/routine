import pandas as pd
import datetime
from django.db import transaction
from django.contrib.auth import get_user_model
from academic.models import (
    Department, Semester, Day, TimeSlot, RoomType, 
    RoomSubType, Room, Course
)

User = get_user_model()

class ExcelImporter:
    @staticmethod
    def _clean_value(val):
        """Helper method to clean empty cells (NaN) from pandas."""
        if pd.isna(val):
            return None
        if isinstance(val, str):
            return val.strip()
        return val

    @staticmethod
    def _parse_time(val):
        """Helper method to intelligently parse 12-hour AM/PM and string times to Django's 24-hour format."""
        if pd.isna(val):
            return None
        
        if isinstance(val, (datetime.datetime, datetime.time)):
            return val.strftime('%H:%M:%S')
        
        if isinstance(val, str):
            val = val.strip()
            val = val.replace('“', '').replace('”', '').replace('"', '').replace("'", "")
            
            try:
                return datetime.datetime.strptime(val, '%I:%M %p').strftime('%H:%M:%S')
            except ValueError:
                pass
            
            try:
                return datetime.datetime.strptime(val, '%H:%M').strftime('%H:%M:%S')
            except ValueError:
                pass
            
            return val
            
        return val

    @classmethod
    def import_data(cls, file_path):
        """
        Reads the master Excel file and safely imports/updates data into the database.
        Includes department shortcode mapping and fixed routine parameters.
        """
        try:
            print("⏳ Loading Excel file into memory (This might take a few seconds)...")
            
            sheets = pd.read_excel(file_path, sheet_name=None)
            print("✅ File read successfully! Starting database synchronization...")
            
            with transaction.atomic():
                
                # Dictionary to hold Department Code -> Name mapping
                dept_mapping = {}

                # 1. Import Departments & Build Mapping
                if 'Departments' in sheets:
                    print("👉 Updating Departments...")
                    df = sheets['Departments'].dropna(how='all')
                    for _, row in df.iterrows():
                        name = cls._clean_value(row.get('Name'))
                        code = cls._clean_value(row.get('Code'))
                        
                        if name:
                            Department.objects.get_or_create(name=name)
                            if code:
                                # Map the shortcode (e.g., 'CSE') to full name
                                dept_mapping[code] = name

                # 2. Import Semesters
                if 'Semesters' in sheets:
                    print("👉 Updating Semesters...")
                    df = sheets['Semesters'].dropna(how='all')
                    for _, row in df.iterrows():
                        name = cls._clean_value(row.get('Name'))
                        order = cls._clean_value(row.get('Order'))
                        if name:
                            Semester.objects.update_or_create(
                                name=name, 
                                defaults={'order': int(order) if order else 0}
                            )

                # 3. Import Days
                if 'Days' in sheets:
                    print("👉 Updating Days...")
                    df = sheets['Days'].dropna(how='all')
                    for _, row in df.iterrows():
                        name = cls._clean_value(row.get('Name'))
                        order = cls._clean_value(row.get('Order'))
                        if name:
                            Day.objects.update_or_create(
                                name=name, 
                                defaults={'order': int(order) if order else 0}
                            )

                # 4. Import TimeSlots
                if 'TimeSlots' in sheets:
                    print("👉 Updating TimeSlots...")
                    df = sheets['TimeSlots'].dropna(how='all')
                    for _, row in df.iterrows():
                        start_time = cls._parse_time(row.get('StartTime'))
                        end_time = cls._parse_time(row.get('EndTime'))
                        if start_time and end_time:
                            TimeSlot.objects.get_or_create(
                                start_time=start_time, 
                                end_time=end_time
                            )

                # 5. Import Users (Teachers/Students)
                if 'Users' in sheets:
                    print("👉 Updating Users (Teachers/Students)...")
                    df = sheets['Users'].dropna(how='all')
                    for _, row in df.iterrows():
                        username = cls._clean_value(row.get('Username'))
                        if not username:
                            continue
                            
                        excel_dept = cls._clean_value(row.get('Department'))
                        actual_dept_name = dept_mapping.get(excel_dept, excel_dept)
                        department = Department.objects.filter(name=actual_dept_name).first() if excel_dept else None
                        
                        password = cls._clean_value(row.get('Password'))
                        
                        user, created = User.objects.update_or_create(
                            username=username,
                            defaults={
                                'first_name': cls._clean_value(row.get('FirstName')) or '',
                                'last_name': cls._clean_value(row.get('LastName')) or '',
                                'email': cls._clean_value(row.get('Email')) or '',
                                'role': cls._clean_value(row.get('Role')) or 'TEACHER',
                                'department': department
                            }
                        )
                        if password:
                            user.set_password(str(password))
                            user.save()

                # 6. Import RoomTypes
                if 'RoomTypes' in sheets:
                    print("👉 Updating RoomTypes...")
                    df = sheets['RoomTypes'].dropna(how='all')
                    for _, row in df.iterrows():
                        name = cls._clean_value(row.get('Name'))
                        if name:
                            RoomType.objects.get_or_create(name=name)

                # 7. Import RoomSubTypes
                if 'RoomSubTypes' in sheets:
                    print("👉 Updating RoomSubTypes...")
                    df = sheets['RoomSubTypes'].dropna(how='all')
                    for _, row in df.iterrows():
                        name = cls._clean_value(row.get('Name'))
                        parent_name = cls._clean_value(row.get('ParentType'))
                        if name and parent_name:
                            parent_type = RoomType.objects.filter(name=parent_name).first()
                            if parent_type:
                                RoomSubType.objects.update_or_create(
                                    name=name, 
                                    defaults={'room_type': parent_type}
                                )

                # 8. Import Rooms
                if 'Rooms' in sheets:
                    print("👉 Updating Rooms...")
                    df = sheets['Rooms'].dropna(how='all')
                    for _, row in df.iterrows():
                        room_number = cls._clean_value(row.get('RoomNumber'))
                        if not room_number:
                            continue
                            
                        r_type_name = cls._clean_value(row.get('RoomType'))
                        r_type = RoomType.objects.filter(name=r_type_name).first()
                        
                        if not r_type:
                            print(f"⚠️ Warning: Skipping Room '{room_number}' - Invalid RoomType '{r_type_name}'")
                            continue

                        sub_type_name = cls._clean_value(row.get('SubType'))
                        r_sub_type = RoomSubType.objects.filter(name=sub_type_name).first() if sub_type_name else None
                        
                        excel_dept = cls._clean_value(row.get('Department'))
                        actual_dept_name = dept_mapping.get(excel_dept, excel_dept)
                        dept = Department.objects.filter(name=actual_dept_name).first()
                        
                        Room.objects.update_or_create(
                            room_number=room_number,
                            defaults={
                                'room_type': r_type,
                                'room_sub_type': r_sub_type,
                                'capacity': int(cls._clean_value(row.get('Capacity')) or 0),
                                'department': dept
                            }
                        )

                # 9. Import Courses
                if 'Courses' in sheets:
                    print("👉 Updating Courses...")
                    df = sheets['Courses'].dropna(how='all')
                    for _, row in df.iterrows():
                        course_code = cls._clean_value(row.get('CourseCode'))
                        course_name = cls._clean_value(row.get('CourseName'))
                        if not course_code or not course_name:
                            continue
                            
                        # Smart Department Matching
                        excel_dept = cls._clean_value(row.get('Department'))
                        actual_dept_name = dept_mapping.get(excel_dept, excel_dept)
                        dept = Department.objects.filter(name=actual_dept_name).first()
                        
                        sem_name = cls._clean_value(row.get('Semester'))
                        semester = Semester.objects.filter(name=sem_name).first()
                        
                        teacher_username = cls._clean_value(row.get('TeacherUsername')) or cls._clean_value(row.get('Teacher'))
                        teacher = User.objects.filter(username=teacher_username).first() if teacher_username else None
                        
                        c_type_name = cls._clean_value(row.get('CourseType')) or cls._clean_value(row.get('RoomType'))
                        r_type = RoomType.objects.filter(name=c_type_name).first()
                        
                        if not r_type:
                            print(f"⚠️ Warning: Skipping Course '{course_code}' - Invalid CourseType '{c_type_name}'")
                            continue
                        if not dept:
                            print(f"⚠️ Warning: Skipping Course '{course_code}' - Invalid Department '{excel_dept}'")
                            continue
                        
                        sub_type_name = cls._clean_value(row.get('CourseSubType')) or cls._clean_value(row.get('SubType'))
                        r_sub_type = RoomSubType.objects.filter(name=sub_type_name).first() if sub_type_name else None
                        
                        # --- FIXED ROUTINE PARAMETERS ---
                        fixed_day_val = cls._clean_value(row.get('FixedDay'))
                        fixed_day = Day.objects.filter(name__iexact=fixed_day_val).first() if fixed_day_val else None
                        
                        fixed_time_val = cls._parse_time(row.get('FixedTimeSlot'))
                        fixed_time_slot = TimeSlot.objects.filter(start_time=fixed_time_val).first() if fixed_time_val else None
                        
                        fixed_room_val = cls._clean_value(row.get('FixedRoom'))
                        fixed_room = Room.objects.filter(room_number__iexact=fixed_room_val).first() if fixed_room_val else None
                        
                        Course.objects.update_or_create(
                            course_code=course_code,
                            defaults={
                                'course_name': course_name,
                                'department': dept,
                                'semester': semester,
                                'teacher': teacher,
                                'credits': int(cls._clean_value(row.get('Credits')) or 3),
                                'student_count': int(cls._clean_value(row.get('StudentCount')) or cls._clean_value(row.get('Students')) or 0),
                                'course_type': r_type,
                                'course_sub_type': r_sub_type,
                                # Save Fixed Routine info
                                'fixed_day': fixed_day,
                                'fixed_time_slot': fixed_time_slot,
                                'fixed_room': fixed_room
                            }
                        )

            return True, "All data imported/updated successfully without duplicates!"
            
        except FileNotFoundError:
            return False, f"Excel file not found at {file_path}. Please check the file path."
        except Exception as e:
            return False, f"An error occurred during import: {str(e)}"