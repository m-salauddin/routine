from rest_framework.permissions import BasePermission

class IsAdminUser(BasePermission):
    """
    Allows access only to admin users.
    """
    def has_permission(self, request, view):
        return request.user and request.user.is_authenticated and request.user.role == 'ADMIN'

class IsTeacherUser(BasePermission):
    """
    Allows access only to teacher users.
    """
    def has_permission(self, request, view):
        return request.user and request.user.is_authenticated and request.user.role == 'TEACHER'

class IsStudentUser(BasePermission):
    """
    Allows access only to student users.
    """
    def has_permission(self, request, view):
        return request.user and request.user.is_authenticated and request.user.role == 'STUDENT'