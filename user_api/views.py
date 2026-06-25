# user_api/views.py
from rest_framework import generics, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework_simplejwt.views import TokenObtainPairView

from .models import User
from .serializers import UserRegistrationSerializer, MyTokenObtainPairSerializer, UserProfileSerializer
from .permissions import IsAdminUser, IsTeacherUser, IsStudentUser

from academic.models import Course
from academic.serializers import CourseSerializer
from django.http import HttpResponse

def first_page(request):
    return HttpResponse("This is home page")

class RegisterView(generics.CreateAPIView):
    queryset = User.objects.all()
    permission_classes = (AllowAny,) 
    serializer_class = UserRegistrationSerializer

class MyTokenObtainPairView(TokenObtainPairView):
    serializer_class = MyTokenObtainPairSerializer

class AdminPanelView(APIView):
    permission_classes = [IsAuthenticated, IsAdminUser]
    def get(self, request):
        content = {
            'message': f'Welcome to Admin Panel, {request.user.username}!',
            'management_links': {
                'departments': '/api/academic/departments/',
                'semesters': '/api/academic/semesters/',
                'courses': '/api/academic/courses/',
            }
        }
        return Response(content)

class TeacherPanelView(APIView):
    permission_classes = [IsAuthenticated, IsTeacherUser]
    def get(self, request):
        courses = Course.objects.filter(teacher=request.user)
        serializer = CourseSerializer(courses, many=True)
        
        content = {
            'message': f'Welcome to Teacher Panel, {request.user.username}!',
            'my_courses': serializer.data
        }
        return Response(content)

class StudentPanelView(APIView):
    permission_classes = [IsAuthenticated, IsStudentUser]
    def get(self, request):
        user = request.user
        
        # check if the user has department, semester, and batch set
        if not user.department or not user.semester or not user.batch:
            content = {
                'message': f'Welcome, {user.username}!',
                'error': 'Your profile is incomplete. Please contact admin to set your department, batch, and semester.'
            }
            return Response(content, status=status.HTTP_400_BAD_REQUEST)

        courses = Course.objects.filter(
            department=user.department,
            semester=user.semester
        )
        serializer = CourseSerializer(courses, many=True)
        
        content = {
            'message': f'Welcome to Student Panel, {user.username}!',
            'my_courses': serializer.data
        }
        return Response(content)
    
class UserProfileView(APIView):
    permission_classes = [IsAuthenticated]
    def get(self, request):
        serializer = UserProfileSerializer(request.user)
        return Response(serializer.data)

class UserListView(generics.ListAPIView):
    serializer_class = UserProfileSerializer
    permission_classes = [IsAuthenticated, IsAdminUser] 
    def get_queryset(self):
        queryset = User.objects.all()
        role = self.request.query_params.get('role')
        if role:
            queryset = queryset.filter(role=role)
        return queryset

class UserDetailView(generics.RetrieveUpdateDestroyAPIView):
    queryset = User.objects.all()
    serializer_class = UserProfileSerializer
    permission_classes = [IsAuthenticated, IsAdminUser]