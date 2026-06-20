#user_api/urls.py
from django.urls import path
from .views import (
    RegisterView, 
    MyTokenObtainPairView,
    AdminPanelView,
    TeacherPanelView,
    StudentPanelView,
    UserProfileView,
    UserListView,
     UserDetailView
)
from rest_framework_simplejwt.views import TokenRefreshView

urlpatterns = [
    path('register/', RegisterView.as_view(), name='register'),
    path('login/', MyTokenObtainPairView.as_view(), name='token_obtain_pair'),
    path('token/refresh/', TokenRefreshView.as_view(), name='token_refresh'),
    
    
    path('panel/admin/', AdminPanelView.as_view(), name='admin_panel'),
    path('panel/teacher/', TeacherPanelView.as_view(), name='teacher_panel'),
    path('panel/student/', StudentPanelView.as_view(), name='student_panel'),
    
    path('profile/', UserProfileView.as_view(), name='user_profile'),
    path('users/', UserListView.as_view(), name='user_list'),
    path('users/<int:pk>/', UserDetailView.as_view(), name='user_detail'),
]




