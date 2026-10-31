"""Native virtual entries must never stand in for the original onsite roster."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from vp_helper.gym import Gym
from vp_helper.models import Contest, Reference, Rules, Submission, VPError
from vp_helper.scoring import rank_of, score_submissions, statistics


def contest():
    return Contest("gym", "106554", "https://codeforces.com/gym/106554", "Onsite mirror", "Onsite mirror",
                   {"A": "A", "B": "B"}, Rules(7200))


def party(handle, kind="CONTESTANT", **extra):
    return {"participantType": kind, "members": [{"handle": handle}], **extra}


def row(handle, at, kind="CONTESTANT", **extra):
    return {"party": party(handle, kind, **extra), "rank": 1, "points": 1, "penalty": at // 60,
            "problemResults": [{"points": 1, "rejectedAttemptCount": 0, "bestSubmissionTimeSeconds": at},
                               {"points": 0, "rejectedAttemptCount": 0}]}


def data(rows=None):
    return {"contest": {"id": 106554, "type": "ICPC", "phase": "FINISHED", "frozen": False},
            "problems": [{"index": "A"}, {"index": "B"}], "rows": rows or []}


class CodeforcesOfficialReferenceTests(unittest.TestCase):
    def test_empty_mirror_is_neither_first_place_nor_complete_public_statistics(self):
        c = contest()
        api = Gym(c.url, {"handle": "local-vp"})
        api.api = Mock(return_value=data())
        api._original_replay = Mock()
        for elapsed in (600, 7200, 8000):
            with self.subTest(elapsed=elapsed):
                reference = api.reference(c, elapsed)
                self.assertFalse(reference.complete)
                self.assertFalse(reference.statistics_complete)
                self.assertEqual(reference.rows, [])
                self.assertIn("Original official participants", reference.message)
        api._original_replay.assert_not_called()
        self.assertTrue(all(call.args[0] == "contest.standings" for call in api.api.call_args_list))
        self.assertIsNone(api._replay)

    def test_virtual_practice_manager_and_unofficial_only_rows_do_not_form_an_original_roster(self):
        c = contest()
        for kind in ("VIRTUAL", "PRACTICE", "MANAGER", "OUT_OF_COMPETITION"):
            with self.subTest(kind=kind):
                api = Gym(c.url, {"handle": "local-vp"})
                api.api = Mock(return_value=data([row("other-vp", 60, kind, ghost=True)]))
                api._original_replay = Mock()
                reference = api.reference(c, 7200)
                self.assertFalse(reference.complete)
                self.assertFalse(reference.statistics_complete)
                self.assertEqual(reference.rows, [])
                api._original_replay.assert_not_called()

    def test_original_rank_compares_to_official_results_and_ignores_native_virtual_rank(self):
        c = contest()
        originals = data([row("onsite-a", 600), row("onsite-b", 900),
                          row("local-vp", 1200, "VIRTUAL"), row("manager", 60, "MANAGER"),
                          row("practice", 120, "PRACTICE"), row("starred", 180, "OUT_OF_COMPETITION")])
        api = Gym(c.url, {"handle": "local-vp"})
        api.api = Mock(return_value=originals)
        reference = api.reference(c, 7200)
        private = score_submissions([Submission("mine", "me", "A", 1200, "CORRECT")],
                                    list(c.problems), c.rules, 7200)
        self.assertTrue(reference.complete)
        self.assertEqual([r.id for r in reference.rows], ["user:onsite-a", "user:onsite-b"])
        self.assertEqual(rank_of(private, reference.rows), 3)
        self.assertEqual(statistics(reference.rows, list(c.problems))["accepted"]["A"], 2)

    def test_virtual_ghost_rows_cannot_trigger_eligibility_or_change_original_participants(self):
        c = contest()
        api = Gym(c.url, {"handle": "local-vp"})
        api.api = Mock(return_value=data([row("onsite", 600), row("local-vp", 60, "VIRTUAL", ghost=True)]))
        with patch("vp_helper.gym.load_metadata") as metadata:
            reference = api.reference(c, 7200)
        metadata.assert_not_called()
        self.assertTrue(reference.complete)
        self.assertEqual(len(reference.rows), 1)

    def test_original_event_replay_excludes_all_noncontestant_submissions(self):
        c = contest()
        original = data([row("onsite-a", 600), row("onsite-b", 900)])
        api = Gym(c.url, {"handle": "local-vp"})
        api._original = original
        events = [{"id": index, "author": party(handle, kind), "relativeTimeSeconds": at,
                   "problem": {"index": "A"}, "verdict": "OK"}
                  for index, (handle, at, kind) in enumerate([
                      ("onsite-a", 600, "CONTESTANT"), ("onsite-b", 900, "CONTESTANT"),
                      ("local-vp", 60, "VIRTUAL"), ("manager", 60, "MANAGER"),
                      ("practice", 60, "PRACTICE"), ("starred", 60, "OUT_OF_COMPETITION")], 1)]
        api.api = Mock(return_value=events)
        with tempfile.TemporaryDirectory() as tmp, patch("vp_helper.originals.cache_path", return_value=Path(tmp)/"events.json"):
            reference = api.reference(c, 1800)
        self.assertTrue(reference.complete)
        self.assertEqual({r.id for r in reference.rows}, {"user:onsite-a", "user:onsite-b"})
        self.assertEqual(statistics(reference.rows, list(c.problems))["accepted"]["A"], 2)
        self.assertEqual(api.api.call_args.args, ("contest.status",))

    def test_empty_original_replay_is_rejected_even_if_called_directly(self):
        c = contest()
        api = Gym(c.url, {"handle": "local-vp"})
        api._original = data()
        with self.assertRaisesRegex(VPError, "no verified participants"):
            api._original_replay(c, [])

    def test_final_api_cells_without_events_provide_rank_but_not_invented_submit_statistics(self):
        c=contest();api=Gym(c.url,{"handle":"local-vp"})
        api.api=Mock(return_value=data([row("onsite",600)]))
        api._original_replay=Mock(side_effect=VPError("No submission stream"))
        reference=api.reference(c,7200)
        self.assertTrue(reference.complete)
        self.assertFalse(reference.statistics_complete)
        self.assertEqual(len(reference.rows),1)
        self.assertIn("attempt statistics are unavailable",reference.message)

    def test_final_reference_reuses_verified_events_including_nonpenalized_submits(self):
        c=contest();api=Gym(c.url,{"handle":"local-vp"})
        api.api=Mock(return_value=data([row("onsite",600)]))
        replay=Mock();replay.at.return_value=Reference(complete=True,statistics_complete=True)
        api._replay=replay;api._original_replay=Mock()
        reference=api.reference(c,7200)
        self.assertTrue(reference.complete);self.assertTrue(reference.statistics_complete)
        replay.at.assert_called_once_with(c,7200,released=True)
        api._original_replay.assert_not_called()

    def test_empty_standings_are_refetched_after_a_transient_response(self):
        c = contest()
        api = Gym(c.url, {"handle": "local-vp"})
        api._original = data()
        api._next_original_attempt = 100
        api.api = Mock(return_value=data([row("onsite", 600)]))
        replay = Mock()
        replay.at.return_value = Reference(complete=True)
        api._original_replay = Mock(return_value=replay)
        with patch("vp_helper.gym.time.monotonic", return_value=99):
            self.assertFalse(api.reference(c, 1800).complete)
        api.api.assert_not_called()
        with patch("vp_helper.gym.time.monotonic", return_value=101):
            self.assertTrue(api.reference(c, 1800).complete)
        api.api.assert_called_once_with("contest.standings", contestId="106554", showUnofficial=False,
                                        participantTypes="CONTESTANT")
