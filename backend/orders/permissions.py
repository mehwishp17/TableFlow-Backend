from rest_framework.permissions import SAFE_METHODS, BasePermission

from accounts.models import User

Role = User.Role


class CanAccessOrders(BasePermission):
    """RBAC matrix (PDF sections 6 and 32).

    Reading: every authenticated role (what each one sees is decided in
    `OrderService.visible_orders`). Creating: Admin and Staff only."""

    def has_permission(self, request, view):
        user = request.user
        if not (user and user.is_authenticated):
            return False
        if request.method in SAFE_METHODS:
            return user.role in (Role.ADMIN, Role.STAFF, Role.KITCHEN, Role.PLATFORM)
        return user.role in (Role.ADMIN, Role.STAFF)
