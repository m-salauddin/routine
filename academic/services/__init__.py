import pandas as pd
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
        """Helper method to handle NaN or empty values from pandas."""
        if pd.isna(val):
            return None
        if isinstance(val, str):
            return val.strip()
        return val

    @classmethod
    def import_data(cls, file_path):
        """
        Reads the master Excel file and safely imports/updates data into the database.
        Wraps the entire process in an atomic transaction to ensure data integrity.
        """
        try:
            # Read all sheets into a dictionary of DataFrames
            sheets = pd.read_excel(file_path, sheet_name=None)
            
            with transaction.atomic():
                
                # 1. Import Departments
                if 'Departments' in sheets:
                    for _, row in sheets['Departments'].iterrows():
                        name = cls._clean_value(row.get('Name'))
                        code = cls._clean_value(row.get('Code'))
                        if name and code:
                            Department.objects.update_or_create(
                                code=code,
                                defaults={'name': name}
                            )

                # 2. Import Semesters
                if 'Semesters' in sheets:
                    for _, row in sheets['Semesters'].iterrows():
                        name = cls._clean_value(row.get('Name'))
                        order = cls._clean_value(row.get('Order'))
                        if name:
                            Semester.objects.update_or_create(
                                name=name,
                                defaults={'order': int(order) if order else 0}
                            )

                # 3. Import Days
                if 'Days' in sheets:
                    for _, row in sheets['Days'].iterrows():
                        name = cls._clean_value(row.get('Name'))
                        order = cls._clean_value(row.get('Order'))
                        if name:
                            Day.objects.update_or_create(
                                name=name,
                                defaults={'order': int(order) if order else 0}
                            )

                # 4. Import TimeSlots
                if 'TimeSlots' in sheets:
                    for _, row in sheets['TimeSlots'].iterrows():
                        start_time = cls._clean_value(row.get('StartTime'))
                        end_time = cls._clean_value(row.get('EndTime'))
                        if start_time and end_time:
                            TimeSlot.objects.update_or_create(
                                start_time=start_time,
                                end_time=end_time
                            )

                # 5. Import Users (Teachers/Students)
                if 'Users' in sheets:
                    for _, row in sheets['Users'].iterrows():
                        username = cls._clean_value(row.get('Username'))
                        if not username:
                            continue
                            
                        dept_name = cls._clean_value(row.get('Department'))
                        department = Department.objects.filter(name=dept_name).first() if dept_name else None
                        
                        password = cls._clean_value(row.get('Password'))
                        
                        user, created = User.objects.update_or_create(
                            username=username,
                            defaults={
                                'first_name': cls._clean_value(row.get('FirstName')) or '',
                                'last_name': cls._clean_value(row.get('LastName')) or '',
                                'email': cls._clean_value(row.get('Email')) or '',
                                'role': cls._clean_value(row.get('Role')) or 'STUDENT',
                                'department': department
                            }
                        )
                        # Securely hash and set the password if provided
                        if password:
                            user.set_password(str(password))
                            user.save()

                # 6. Import RoomTypes
                if 'RoomTypes' in sheets:
                    for _, row in sheets['RoomTypes'].iterrows():
                        name = cls._clean_value(row.get('Name'))
                        if name:
                            RoomType.objects.update_or_create(name=name)

                # 7. Import RoomSubTypes
                if 'RoomSubTypes' in sheets:
                    for _, row in sheets['RoomSubTypes'].iterrows():
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
                    for _, row in sheets['Rooms'].iterrows():
                        room_number = cls._clean_value(row.get('RoomNumber'))
                        if not room_number:
                            continue
                            
                        r_type_name = cls._clean_value(row.get('RoomType'))
                        sub_type_name = cls._clean_value(row.get('SubType'))
                        dept_name = cls._clean_value(row.get('Department'))
                        
                        r_type = RoomType.objects.filter(name=r_type_name).first()
                        r_sub_type = RoomSubType.objects.filter(name=sub_type_name).first() if sub_type_name else None
                        dept = Department.objects.filter(name=dept_name).first()
                        
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
                    for _, row in sheets['Courses'].iterrows():
                        course_code = cls._clean_value(row.get('CourseCode'))
                        if not course_code:
                            continue
                            
                        dept = Department.objects.filter(name=cls._clean_value(row.get('Department'))).first()
                        semester = Semester.objects.filter(name=cls._clean_value(row.get('Semester'))).first()
                        teacher = User.objects.filter(username=cls._clean_value(row.get('TeacherUsername'))).first()
                        r_type = RoomType.objects.filter(name=cls._clean_value(row.get('RoomType'))).first()
                        
                        sub_type_name = cls._clean_value(row.get('SubType'))
                        r_sub_type = RoomSubType.objects.filter(name=sub_type_name).first() if sub_type_name else None
                        
                        Course.objects.update_or_create(
                            course_code=course_code,
                            defaults={
                                'course_name': cls._clean_value(row.get('CourseName')),
                                'department': dept,
                                'semester': semester,
                                'teacher': teacher,
                                'credits': int(cls._clean_value(row.get('Credits')) or 3),
                                'student_count': int(cls._clean_value(row.get('Students')) or 0),
                                'course_type': r_type,
                                'course_sub_type': r_sub_type
                            }
                        )

            return True, "All data imported/updated successfully without duplicates!"
            
        except FileNotFoundError:
            return False, f"Excel file not found at {file_path}. Please check the file path."
        except Exception as e:
            return False, f"An error occurred during import: {str(e)}"