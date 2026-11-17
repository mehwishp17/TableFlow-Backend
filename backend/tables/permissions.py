from rest_framework.permissions import SAFE_METHODS, BasePermission

from accounts.models import User

Role = User.Role


class _RolePermission(BasePermission):
    roles = ()

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and user.role in self.roles)


class CanManageTables(BasePermission):
    """RBAC matrix, "Table & QR Code System": Admin has full CRUD, Staff can only
    read tables, Kitchen and Platform have no access."""

    def has_permission(self, request, view):
        user = request.user
        if not (user and user.is_authenticated):
            return False
        if request.method in SAFE_METHODS:
            return user.role in (Role.ADMIN, Role.STAFF)
        return user.role == Role.ADMIN


class CanUpdateTableStatus(_RolePermission):
    """Staff may update table occupancy; Admin has full access."""

    roles = (Role.ADMIN, Role.STAFF)


class CanExportQR(_RolePermission):
    """QR PDF export is Admin only."""

    roles = (Role.ADMIN,)
