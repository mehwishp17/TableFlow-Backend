from rest_framework.permissions import BasePermission

from accounts.models import User

Role = User.Role


class _RolePermission(BasePermission):
    roles = ()

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and user.role in self.roles)


class IsStaffRole(_RolePermission):
    """RBAC matrix, "Overview KPIs": Staff see their own personal score."""

    roles = (Role.STAFF,)


class IsAdminRole(_RolePermission):
    roles = (Role.ADMIN,)


class CanViewScorecard(_RolePermission):
    """Admin (any waiter of the restaurant) or Staff (only themselves, checked in the view)."""

    roles = (Role.ADMIN, Role.STAFF)
