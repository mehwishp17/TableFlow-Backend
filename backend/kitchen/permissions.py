from rest_framework.permissions import BasePermission

from accounts.models import User


class IsKitchenStaff(BasePermission):
    """RBAC matrix, "KDS Queue": Kitchen has full control of preparation statuses.
    Admin is view-only, Staff only receives calls and Platform has no KDS access."""

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and user.role == User.Role.KITCHEN)
