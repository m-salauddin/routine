# user_api/models.py
from django.contrib.auth.models import AbstractUser
from django.db import models

class User(AbstractUser):
    ROLE_CHOICES = (
        ('ADMIN', 'Admin'),
        ('TEACHER', 'Teacher'),
        ('STUDENT', 'Student'),
    )
    
    role = models.CharField(max_length=10, choices=ROLE_CHOICES, default='STUDENT')
   
    department = models.ForeignKey(
        'academic.Department', 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True
    )
    
    # Batch টি এখানে যোগ করা হলো
    batch = models.ForeignKey(
        'academic.Batch', 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True
    )
    
    semester = models.ForeignKey(
        'academic.Semester', 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True
    )