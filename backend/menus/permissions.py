from rest_framework.permissions import SAFE_METHODS, BasePermission

from accounts.models import User

Role = User.Role


class _RolePermission(BasePermission):
    roles = ()

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and user.role in self.roles)


class CanReadMenu(_RolePermission):
    """RBAC matrix, "Menu CRUD & Pricing": restaurant roles read their own menu.

    Platform is deliberately excluded: its matrix entry is "Master Templates",
    which is not built yet (see the review notes), not restaurant menus."""

    roles = (Role.ADMIN, Role.STAFF, Role.KITCHEN)


class CanManageMenu(BasePermission):
    """Admin has full CRUD; Staff and Kitchen are read-only; Platform has no access."""

    def has_permission(self, request, view):
        user = request.user
        if not (user and user.is_authenticated):
            return False
        if request.method in SAFE_METHODS:
            return user.role in CanReadMenu.roles
        return user.role == Role.ADMIN


class CanToggleAvailability(_RolePermission):
    """RBAC matrix, "Dish Availability Toggle": Admin and Staff only."""

    roles = (Role.ADMIN, Role.STAFF)
