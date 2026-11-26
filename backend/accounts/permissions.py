from rest_framework.permissions import BasePermission

from .models import User

Role = User.Role


class _RolePermission(BasePermission):
    roles = ()

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and user.role in self.roles)


class IsAdminRole(_RolePermission):
    """RBAC matrix, "Staff & Account CRUD": Admin only (Platform manages client
    accounts, not a restaurant's staff)."""

    roles = (Role.ADMIN,)


class CanSeeOnlineStaff(_RolePermission):
    """The kitchen picks an online waiter for a call; the Admin may look too."""

    roles = (Role.ADMIN, Role.KITCHEN)
