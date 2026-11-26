from rest_framework.test import APITestCase

from analytics.models import ScoreEvent
from orders.testutils import OrderFixtureMixin, client_for

BOARD = '/api/v1/staff/scorecards/'
ME = '/api/v1/staff/me/scorecard/'


def card_url(user_id):
    return f'/api/v1/staff/{user_id}/scorecard/'


class LeaderboardInactiveWaiterTests(OrderFixtureMixin, APITestCase):
    def board(self, admin=None):
        r = client_for(admin or self.admin1).get(BOARD)
        self.assertEqual(r.status_code, 200)
        return [(c['waiter_id'], c['total_score'], c['is_active']) for c in r.json()['data']]

    def deactivate(self, user_id):
        r = client_for(self.admin1).delete(f'/api/v1/staff/{user_id}/')
        self.assertEqual(r.status_code, 200)

    def test_an_inactive_waiter_with_no_score_is_not_listed(self):
        self.deactivate('staff1b')
        self.assertEqual(self.board(), [('staff1', 0, True)])

    def test_an_inactive_waiter_who_earned_points_stays_listed_and_is_marked(self):
        self.place(self.staff1b, 'Dine-In', self.t1, [(self.tea, 1)])
        self.deactivate('staff1b')
        self.assertEqual(self.board(), [('staff1b', 10, False), ('staff1', 0, True)])

    def test_active_waiters_with_no_score_are_still_listed(self):
        self.assertEqual(self.board(), [('staff1', 0, True), ('staff1b', 0, True)])

    def test_the_history_of_an_inactive_waiter_is_kept_and_still_readable(self):
        self.place(self.staff1b, 'Dine-In', self.t1, [(self.tea, 1)])
        self.deactivate('staff1b')
        self.deactivate('staff1')                       # no score: hidden from the board
        self.assertEqual(ScoreEvent.objects.filter(waiter=self.staff1b).count(), 1)
        card = client_for(self.admin1).get(card_url('staff1b')).json()['data']
        self.assertEqual((card['total_score'], card['is_active']), (10, False))
        card = client_for(self.admin1).get(card_url('staff1')).json()['data']
        self.assertEqual((card['total_score'], card['is_active']), (0, False))
        self.assertEqual(self.board(), [('staff1b', 10, False)])

    def test_reactivating_a_waiter_brings_them_back_to_the_board(self):
        self.deactivate('staff1b')
        client_for(self.admin1).patch('/api/v1/staff/staff1b/', {'is_active': True},
                                      format='json')
        self.assertEqual(self.board(), [('staff1', 0, True), ('staff1b', 0, True)])

    def test_other_restaurants_are_unaffected(self):
        self.deactivate('staff1b')
        self.assertEqual(self.board(self.admin2), [('staff2', 0, True)])

    def test_the_personal_scorecard_reports_that_the_waiter_is_active(self):
        self.assertTrue(client_for(self.staff1).get(ME).json()['data']['is_active'])
