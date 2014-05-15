from django.test import TestCase

from .factories import UserFactory
from ..models import Competition, Game


class TestGameAnnouncement(TestCase):
    @classmethod
    def setUp(self):
        # Create 4 dummy users
        self.users = [UserFactory() for id in range(4)]
        self.default_competition = Competition.objects.get_default_competition()

    def test_game_announcement(self):
        Game.objects.announce(self.users[0], self.users[1],
                              self.default_competition)
        game = Game.objects.get()
        self.assertEqual(game.winner.users.get(), self.users[0])
        self.assertEqual(game.loser.users.get(), self.users[1])
        self.assertLess(game.loser.scores.get().score,
                        game.winner.scores.get().score)

    def test_game_date(self):
        game1 = Game.objects.announce(self.users[0], self.users[1],
                                      self.default_competition)
        game2 = Game.objects.announce(self.users[1], self.users[0],
                                      self.default_competition)
        self.assertGreater(game2.date, game1.date)
