from rest_framework import serializers


class _BreakdownLineSerializer(serializers.Serializer):
    count = serializers.IntegerField(help_text='Number of credits of this kind.')
    points = serializers.IntegerField(help_text='Points earned from them.')


class _BreakdownSerializer(serializers.Serializer):
    dine_in_orders = _BreakdownLineSerializer(help_text='+10 per Dine-In order created.')
    calls_resolved = _BreakdownLineSerializer(help_text='+5 per call picked and resolved.')
    quick_pick_bonus = _BreakdownLineSerializer(
        help_text='+5 per call picked within 2 minutes of being triggered.')


class ScorecardSerializer(serializers.Serializer):
    """Documentation-only description of a scorecard (the dicts come from
    `analytics.services.ScoreService`)."""

    waiter_id = serializers.CharField()
    is_active = serializers.BooleanField(
        help_text='False for a former (deactivated) waiter whose history is kept.')
    total_score = serializers.IntegerField()
    dine_in_orders_served = serializers.IntegerField()
    calls_resolved = serializers.IntegerField()
    bonus_points = serializers.IntegerField()
    breakdown = _BreakdownSerializer()
