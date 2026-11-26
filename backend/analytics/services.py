"""Waiter performance scoring (PDF section 17 and 29).

Rules confirmed by the team lead:
  * +10 points for each Dine-In order a waiter creates,
  * +5 points for each Customer or Kitchen call the waiter picks and resolves,
  * +5 bonus points when that call was picked within 2 minutes of being triggered.

Every credit is a row of the `ScoreEvent` ledger. The unique constraints on that table
make crediting idempotent, and the scorecard is always computed from the ledger, so
Staff scorecards and Admin analytics share exactly the same numbers and code.
"""
from datetime import timedelta

from django.db.models import Count, Exists, OuterRef, Q, Sum
from django.db.models.functions import Coalesce

from accounts.models import User

from .models import ScoreEvent

DINE_IN_ORDER_POINTS = 10
CALL_RESOLVED_POINTS = 5
QUICK_PICK_BONUS_POINTS = 5
QUICK_PICK_WINDOW = timedelta(minutes=2)  # inclusive: exactly 2:00 still earns the bonus

Kind = ScoreEvent.Kind


def quick_pick_bonus_applies(triggered_at, picked_at):
    """True if a call was picked within the bonus window of when it was triggered."""
    return timedelta(0) <= picked_at - triggered_at <= QUICK_PICK_WINDOW


class ScoreService:
    @staticmethod
    def _credit(kind, points, waiter, restaurant_id, *, order=None, call=None):
        """Create the ledger row unless it already exists. Returns True if newly credited.

        `get_or_create` plus the unique constraints make this safe to call twice, even
        from two requests at the same moment."""
        _, created = ScoreEvent.objects.get_or_create(
            kind=kind, order=order, call=call,
            defaults={'waiter': waiter, 'restaurant_id': restaurant_id, 'points': points},
        )
        return created

    @staticmethod
    def credit_dine_in_order(order):
        """+10 for the Staff waiter who created a Dine-In order. Called once, from the
        single place orders are created (`OrderService.create_order`), inside its
        transaction. Orders created by an Admin earn nothing: only waiters have a score."""
        waiter = order.waiter
        if order.order_type != 'Dine-In' or waiter.role != User.Role.STAFF:
            return False
        return ScoreService._credit(
            Kind.DINE_IN_ORDER, DINE_IN_ORDER_POINTS, waiter, order.restaurant_id, order=order)

    @staticmethod
    def credit_resolved_call(call):
        """+5 for resolving a call, +5 more if it was picked within 2 minutes. Called when
        a call becomes RESOLVED; a second call for the same call credits nothing."""
        waiter = call.assigned_waiter
        credited = ScoreService._credit(
            Kind.CALL_RESOLVED, CALL_RESOLVED_POINTS, waiter, call.restaurant_id, call=call)
        if quick_pick_bonus_applies(call.triggered_at, call.picked_at):
            ScoreService._credit(
                Kind.QUICK_PICK_BONUS, QUICK_PICK_BONUS_POINTS, waiter, call.restaurant_id,
                call=call)
        return credited

    # ------------------------------------------------------------------
    # Scorecards
    # ------------------------------------------------------------------

    @staticmethod
    def annotated(waiters):
        """`waiters` (a User queryset) annotated with the numbers of a scorecard, in a
        single query. Waiters without any credit get zeros."""
        def rows(kind):
            return Count('score_events', filter=Q(score_events__kind=kind))

        def pts(kind):
            return Coalesce(Sum('score_events__points', filter=Q(score_events__kind=kind)), 0)

        return waiters.annotate(
            n_orders=rows(Kind.DINE_IN_ORDER), p_orders=pts(Kind.DINE_IN_ORDER),
            n_calls=rows(Kind.CALL_RESOLVED), p_calls=pts(Kind.CALL_RESOLVED),
            n_bonus=rows(Kind.QUICK_PICK_BONUS), p_bonus=pts(Kind.QUICK_PICK_BONUS),
        )

    @staticmethod
    def card(waiter):
        """The scorecard dict of an annotated waiter (see `annotated`)."""
        return {
            'waiter_id': waiter.pk,
            'is_active': waiter.is_active,
            'total_score': waiter.p_orders + waiter.p_calls + waiter.p_bonus,
            'dine_in_orders_served': waiter.n_orders,
            'calls_resolved': waiter.n_calls,
            'bonus_points': waiter.p_bonus,
            'breakdown': {
                'dine_in_orders': {'count': waiter.n_orders, 'points': waiter.p_orders},
                'calls_resolved': {'count': waiter.n_calls, 'points': waiter.p_calls},
                'quick_pick_bonus': {'count': waiter.n_bonus, 'points': waiter.p_bonus},
            },
        }

    @staticmethod
    def waiters_of(restaurant_id):
        """The people who have a score: active or former Staff (waiters) of a restaurant."""
        return User.objects.filter(restaurant_id=restaurant_id, role=User.Role.STAFF)

    @staticmethod
    def scorecard_of(waiter):
        annotated = ScoreService.annotated(User.objects.filter(pk=waiter.pk)).get()
        return ScoreService.card(annotated)

    @staticmethod
    def leaderboard(restaurant_id):
        """The active leaderboard, best score first (ties: id).

        Lists every active waiter (including those with no points yet) and any former
        waiter who has earned points, marked `is_active: false`. A deactivated waiter with
        no score is left out. Nothing is deleted: their history stays in the ledger and
        `GET /staff/{id}/scorecard/` still works for them."""
        has_score = Exists(ScoreEvent.objects.filter(waiter=OuterRef('pk')))
        waiters = ScoreService.waiters_of(restaurant_id).filter(Q(is_active=True) | has_score)
        cards = [ScoreService.card(w) for w in ScoreService.annotated(waiters)]
        return sorted(cards, key=lambda c: (-c['total_score'], c['waiter_id']))
