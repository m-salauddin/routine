# user_api/serializers.py
from rest_framework import serializers
from .models import User
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from academic.models import Department, Semester, Batch 

class UserRegistrationSerializer(serializers.ModelSerializer):
    password2 = serializers.CharField(style={'input_type': 'password'}, write_only=True)
    
    department_id = serializers.IntegerField(write_only=True, required=False, allow_null=True)
    semester_id = serializers.IntegerField(write_only=True, required=False, allow_null=True)
    batch_id = serializers.IntegerField(write_only=True, required=False, allow_null=True)

    class Meta:
        model = User
        fields = [
            'username', 'email', 'password', 'password2', 
            'role', 'first_name', 'last_name',
            'department_id', 'semester_id', 'batch_id'
        ]
        extra_kwargs = {
            'password': {'write_only': True}
        }

    def validate(self, attrs):
        password = attrs.get('password')
        password2 = attrs.get('password2')
        if password != password2:
            raise serializers.ValidationError("Password and Confirm Password must match.")
        
        role = attrs.get('role')
        dept_id = attrs.get('department_id')
        sem_id = attrs.get('semester_id')
        batch_id = attrs.get('batch_id')
        
        department = None
        semester = None
        batch = None

        if dept_id:
            try:
                department = Department.objects.get(id=dept_id)
                attrs['department'] = department
            except Department.DoesNotExist:
                raise serializers.ValidationError("Invalid Department ID.")
        
        if sem_id:
            try:
                semester = Semester.objects.get(id=sem_id)
                attrs['semester'] = semester
            except Semester.DoesNotExist:
                raise serializers.ValidationError("Invalid Semester ID.")
                
        if batch_id:
            try:
                batch = Batch.objects.get(id=batch_id)
                attrs['batch'] = batch
            except Batch.DoesNotExist:
                raise serializers.ValidationError("Invalid Batch ID.")

        if role == 'TEACHER' and not department:
            raise serializers.ValidationError("Teachers must specify a valid department_id.")
        
        if role == 'STUDENT' and (not department or not semester or not batch):
            raise serializers.ValidationError("Students must specify valid department_id, batch_id, and semester_id.")
        
        if role == 'ADMIN':
            attrs['department'] = None
            attrs['semester'] = None
            attrs['batch'] = None
        
        if role == 'TEACHER':
            attrs['semester'] = None
            attrs['batch'] = None

        attrs.pop('department_id', None)
        attrs.pop('semester_id', None)
        attrs.pop('batch_id', None)
        
        return attrs

    def create(self, validated_data):
        validated_data.pop('password2')
        user = User.objects.create_user(**validated_data)
        return user


class MyTokenObtainPairSerializer(TokenObtainPairSerializer):
    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)
        token['username'] = user.username
        token['role'] = user.role
        return token

    def validate(self, attrs):
        data = super().validate(attrs)
        
        data['username'] = self.user.username
        data['email'] = self.user.email
        data['role'] = self.user.role
        data['status'] = self.user.role 
        
        data['department_id'] = self.user.department.id if self.user.department else None
        data['department_name'] = self.user.department.name if self.user.department else None
        
        data['semester_id'] = self.user.semester.id if self.user.semester else None
        data['semester_name'] = self.user.semester.name if self.user.semester else None
        
        data['batch_id'] = self.user.batch.id if self.user.batch else None
        data['batch_name'] = self.user.batch.name if self.user.batch else None

        return data
    
    
class UserProfileSerializer(serializers.ModelSerializer):
    department_name = serializers.CharField(source='department.name', read_only=True, default=None)
    semester_name = serializers.CharField(source='semester.name', read_only=True, default=None)
    batch_name = serializers.CharField(source='batch.name', read_only=True, default=None)
    name = serializers.SerializerMethodField()
    
    class Meta:
        model = User
        fields = [
            'id', 
            'username', 
            'email',
            'first_name', 
            'last_name',
            'name',  
            'role',
            'department', 
            'department_name',
            'batch',
            'batch_name', 
            'semester',
            'semester_name',
            'date_joined'
        ]
        
    def get_name(self, obj):
        if obj.first_name and obj.last_name:
            return f"{obj.first_name} {obj.last_name}".strip()
        elif obj.first_name:
            return obj.first_name.strip()
        elif obj.last_name:
            return obj.last_name.strip()
        else:
            return "N/A"