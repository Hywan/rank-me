import operator
import json
import six
from collections import OrderedDict
from itertools import groupby

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.db.models import Q
from trueskill import Rating, rate_1vs1


class TeamManager(models.Manager):
    def get_or_create_from_players(self, player_ids):
        """
        Return the team associated to the given players, creating it first if
        it doesn't exist.

        Args:
            player_ids: a tuple of user ids, or a single user id.
        """
        if not isinstance(player_ids, tuple):
            player_ids = (player_ids,)

        # We need to get only the teams that have the exact number of player
        # ids, otherwise we would also get teams that have the given players
        # plus additional ones
        team = self.annotate(c=models.Count('users')).filter(c=len(player_ids))

        # Chain filter over all player ids
        for player_id in player_ids:
            team = team.filter(users=player_id)

        if not team:
            created = True
            team = self.create()

            for player_id in player_ids:
                team.users.add(player_id)
        else:
            created = False
            team = team.get()

        return (team, created)


class Team(models.Model):
    users = models.ManyToManyField(settings.AUTH_USER_MODEL,
                                   related_name='teams')

    objects = TeamManager()

    def __unicode__(self):
        return self.get_name()

    def get_name(self):
        return u" / ".join([user.username for user in self.users.all()])

    def get_games(self, competition):
        """
        Fetch the list of games played by the team, optionally filtered by
        competition.
        """
        games = Game.objects.filter(
            Q(winner_id=self.id) | Q(loser_id=self.id)
        ).filter(competitions=competition).order_by('-date').select_related(
            'winner', 'loser'
        )

        return games

    def get_head2head(self, competition):
        """
        Compute the amount of wins and defeats against all opponents the team
        played against. The returned value is an OrderedDict since the teams
        are ordered by their score.
        """
        head2head = {}

        games = self.get_games(competition)

        for game in games:
            opponent = game.get_opponent(self)

            if opponent not in head2head:
                head2head[opponent] = {
                    'wins': 0,
                    'defeats': 0,
                    'games': []
                }

            stat_to_increase = (
                'wins' if game.winner_id == self.id else 'defeats'
            )
            head2head[opponent][stat_to_increase] += 1
            head2head[opponent]['games'].append(game)

        return OrderedDict(
            sorted(head2head.items(), key=lambda t:
                t[0].scores.filter(competition=competition), reverse=True)
        )

    def get_recent_stats(self, competition, count=10):
        """
        Return a dictionary with the latest ``count`` played games and the
        number of wins and defeats.
        """
        games = self.get_games(competition)[:count]
        last_games = {
            'wins': 0,
            'defeats': 0,
            'games': []
        }

        for game in games:
            last_games['wins' if game.winner_id == self.id else 'defeats'] += 1
            last_games['games'].append(game)

        return last_games

    def get_longest_streak(self, competition):
        games = self.get_games(competition)
        # Create a list of booleans indicating won matches
        wins_defeats_list = [game.winner_id == self.id for game in games]

        if True in wins_defeats_list:
            # Sum won matches series and keep the max
            return max([sum(g) for k, g in groupby(wins_defeats_list) if k])
        else:
            return 0

    def get_or_create_score(self, competition):
        try:
            score = Score.objects.get(
                team=self,
                competition=competition
            )
        except Score.DoesNotExist:
            score = Score.objects.create(
                team=self,
                competition=competition,
                score=settings.GAME_INITIAL_MU,
                stdev=settings.GAME_INITIAL_SIGMA
            )

        return score


class GameManager(models.Manager):
    def get_latest(self, competition=None):
        games = (self.get_query_set()
                 .select_related('winner', 'loser')
                 .prefetch_related('winner__users', 'loser__users')
                 .order_by('-date'))

        if competition is not None:
            games = games.filter(competitions=competition)

        games = games[:20]

        return games

    def announce(self, winner, loser, competition):
        """
        Announce the results of a new game.

        Args:
            winner: the user id (or tuple of user ids) of the users who won the
            game.
            loser: the user id (or tuple of user ids) of the users who lost the
            game.
        """
        winner, created = Team.objects.get_or_create_from_players(winner)
        loser, created = Team.objects.get_or_create_from_players(loser)

        game = self.create(winner=winner, loser=loser)
        game.competitions.add(competition)

        game.update_score()

        return game


class Game(models.Model):
    winner = models.ForeignKey(Team, related_name='games_won')
    loser = models.ForeignKey(Team, related_name='games_lost')
    date = models.DateTimeField(default=timezone.now)

    objects = GameManager()

    def clean(self):
        if (self.winner_id is not None and self.loser_id is not None and
                self.winner_id == self.loser_id):
            raise ValidationError(
                "Winner and loser can't be the same team!"
            )

    def __unicode__(self):
        return u"%s beats %s" % (
            self.winner,
            self.loser
        )

    def update_score(self):
        winner = self.winner
        loser = self.loser

        for competition in self.competitions.all():
            winner_score = winner.get_or_create_score(competition)
            loser_score = loser.get_or_create_score(competition)

            winner_new_score, loser_new_score = rate_1vs1(
                Rating(winner_score.score, winner_score.stdev),
                Rating(loser_score.score, loser_score.stdev)
            )

            winner_score.score = winner_new_score.mu
            winner_score.stdev = winner_new_score.sigma
            winner_score.save()

            loser_score.score = loser_new_score.mu
            loser_score.stdev = loser_new_score.sigma
            loser_score.save()

            HistoricalScore.objects.create(
                game=self,
                score=winner_score.score,
                team=winner,
                competition=competition
            )

            HistoricalScore.objects.create(
                game=self,
                score=loser_score.score,
                team=loser,
                competition=competition
            )

    def get_opponent(self, team):
        """
        Return the opponent team relative to the given team.
        """
        return self.winner if self.winner_id != team.id else self.loser


class HistoricalScoreManager(models.Manager):
    def get_latest(self, nb_games, competition):
        return (self.get_queryset()
                .select_related('game', 'game__winner', 'game__loser')
                .filter(game__competitions=competition)
                .order_by('-id')[:nb_games])

    def get_latest_results_by_team(self, nb_games, competition,
                                   return_json=False):
        """
        Get nb_games latest scores for each team

        :param nb_games:int number of games
        :param return_json:boolean
        :return:dict Dict with key=team and value=list of score objects

        {team_a: [{skill: xx, played: xx, game: game_id}, ...]}
        """
        games = (Game.objects.filter(competitions=competition)
                 .order_by('-id')
                 .prefetch_related('historical_scores')[:nb_games])
        teams = Team.objects.all().select_related('winner', 'loser')
        scores_by_team = {}

        for game in reversed(games):
            all_skills_by_game = {}

            for team in teams:
                team_scores = scores_by_team.get(team, [])
                result = {'game': game.id}

                if team.id in [game.winner_id, game.loser_id]:
                    result['skill'] = game.historical_scores.get(
                        team=team, competition=competition
                    ).score

                    result['win'] = team.id == game.winner_id
                    result['played'] = True
                else:
                    result['played'] = False
                    if len(team_scores) == 0:
                        result['skill'] = self.get_last_score_for_team(
                            team, games[0], competition
                        )
                    else:
                        result['skill'] = team_scores[-1]['skill']

                all_skills_by_game[team] = result['skill']

                team_scores.append(result)
                scores_by_team[team] = team_scores

            positions_for_game = sorted(six.iteritems(all_skills_by_game), key=operator.itemgetter(1), reverse=True)
            for idx, position in enumerate(positions_for_game, start=1):
                scores_by_team[position[0]][-1]['position'] = idx

        if return_json:
            json_result = {}
            for team, results in scores_by_team.items():
                json_result[team.get_name()] = results

            return json.dumps(json_result)

        return scores_by_team

    def get_last_score_for_team(self, team, game, competition):
        """
        Returns the latest skill before game :game for team :team

        :param team:Team
        :param game:Game
        :return:float skill
        """
        historical_score_id = game.historical_scores.filter(
            competition=competition
        ).order_by('id').first().id
        last_score = (self.get_queryset()
                      .filter(team=team, id__lte=historical_score_id)
                      .select_related('game__winner', 'game__loser', 'game')
                      .order_by('-id')
                      .first())

        if last_score is None:
            return settings.GAME_INITIAL_MU

        return last_score.score


class ScoreManager(models.Manager):
    def get_score_board(self, competition):
        return (self.get_query_set().filter(competition=competition)
                .order_by('-score').prefetch_related('team__users'))


class Score(models.Model):
    competition = models.ForeignKey('Competition')
    team = models.ForeignKey(Team, related_name='scores')
    score = models.FloatField('skills', default=settings.GAME_INITIAL_MU)
    stdev = models.FloatField('standard deviation',
                              default=settings.GAME_INITIAL_SIGMA)

    objects = ScoreManager()


class HistoricalScore(models.Model):
    game = models.ForeignKey(Game, related_name='historical_scores')
    competition = models.ForeignKey('Competition')
    team = models.ForeignKey(Team, related_name='historical_scores')
    score = models.FloatField('Current team score')

    objects = HistoricalScoreManager()


class CompetitionManager(models.Manager):
    def get_default_competition(self):
        return self.get_queryset().get(pk=1)


class Competition(models.Model):
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    start_date = models.DateTimeField(default=timezone.now)
    end_date = models.DateTimeField(null=True)
    teams = models.ManyToManyField(Team, through=Score)
    games = models.ManyToManyField(Game, related_name='competitions')

    objects = CompetitionManager()
