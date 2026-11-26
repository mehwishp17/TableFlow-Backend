from rest_framework.permissions import BasePermission

from accounts.models import User


class IsRestaurantAdmin(BasePermission):
    """RBAC matrix, "Restaurant Profile": Admin only. Platform has no single restaurant
    (its client-account management is a separate module) and Staff/Kitchen have no access."""

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and user.role == User.Role.ADMIN)
