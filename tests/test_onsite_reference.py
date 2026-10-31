"""Public onsite archive discovery and exact native-contest validation."""
from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from vp_helper import onsite, srk
from vp_helper.gym import Gym
from vp_helper.models import Contest, Rules, Submission, VPError
from vp_helper.scoring import rank_of, score_submissions, statistics

PAGE = "https://rl.algoux.cn/ranklist/ccpc2026invitational-nanchang"
SOURCE = "https://cdn.algoux.cn/rankland/file/12345/ccpc2026invitational-nanchang.srk.json"


def contest():
    return Contest("gym", "777777", "https://codeforces.com/gym/777777",
                   "[Unofficial Mirror] 2026 CCPC National Invitational (Nanchang) and Provincial Contest", "fixture",
                   {"A": "A", "B": "B"}, Rules(7200), original_start=1780272000)


def archive():
    def status(code, at, solutions):
        return {"result": code, "time": [at, "s"], "tries": len(solutions),
                "solutions": [{"result": result, "time": [time, "s"]} for result, time in solutions]}
    return {"contest": {"startAt": "2026-06-01T00:00:00Z", "duration": [2, "h"]},
            "sorter": {"algorithm": "ICPC", "config": {"penalty": [20, "min"], "timePrecision": "min", "timeRounding": "floor",
                        "noPenaltyResults": ["FB", "AC", "?", "CE", "UKE", None]}},
            "problems": [{"alias": p, "link": f"https://codeforces.com/gym/777777/problem/{p}"} for p in "AB"],
            "rows": [{"user": {"id": "onsite-a", "name": "Onsite A", "organization": "School A", "official": True},
                      "score": {"value": 1, "time": [1800, "s"]},
                      "statuses": [status("AC", 659, [("WA", 200), ("AC", 659)]), status(None, 0, [])]},
                     {"user": {"id": "onsite-b", "name": "Onsite B", "organization": "School B", "official": True},
                      "score": {"value": 1, "time": [900, "s"]},
                      "statuses": [status("AC", 925, [("AC", 925)]), status(None, 0, [])]},
                     {"user": {"id": "starred", "name": "Starred", "organization": "School S", "official": False},
                      "score": {"value": 1, "time": [60, "s"]},
                      "statuses": [status("AC", 69, [("AC", 69)]), status(None, 0, [])]}]}


class Response:
    def __init__(self, body, status=200):
        self.body = body.encode() if isinstance(body, str) else body
        self.status_code = status
        self.close = Mock()
    def iter_content(self, size):
        yield self.body


class OnsiteReferenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cache = patch.dict(os.environ, {"XDG_CACHE_HOME": self.tmp.name})
        self.cache.start();self.addCleanup(self.cache.stop)

    def session(self, data=None):
        session = Mock()
        body = 'const p={"srkUrl":"' + SOURCE.replace("/", r"\u002F") + '"};'
        session.get.side_effect = [Response(body), Response(json.dumps(data or archive()))]
        return session

    def test_deterministic_name_discovery_and_json_escaped_source(self):
        self.assertEqual(onsite.rankland_pages(contest()), [PAGE])
        c = contest();c.name = "2025 CCPC Guilin Reg"
        self.assertEqual(onsite.rankland_pages(c), ["https://rl.algoux.cn/ranklist/ccpc2025guilin"])
        c.name = "Educational Codeforces Round 195"
        self.assertEqual(onsite.rankland_pages(c), [])
        self.assertEqual(onsite.archive_urls('"' + SOURCE.replace("/", r"\u002F") + '"'), [SOURCE])
        self.assertEqual(onsite.archive_urls('https://attacker.test/file/malicious.srk.json'), [])

    def test_modern_minute_floor_and_legacy_precision_are_both_validated(self):
        c = contest();data = archive()
        m = srk.metadata(data, c, SOURCE)
        events, rows = srk.results(data, m, c)
        self.assertEqual(rows[0].penalty, 30)
        self.assertEqual(len(events), 4)
        legacy = deepcopy(data)
        cfg = legacy["sorter"]["config"];del cfg["timePrecision"];del cfg["timeRounding"];cfg["rankingTimePrecision"] = "min"
        for row in legacy["rows"]:
            row["score"]["time"][0] = sum(s["time"][0] + 1200 * (s["tries"]-1) for s in row["statuses"] if s["result"] == "AC")
        old = srk.metadata(legacy, c, SOURCE)
        self.assertEqual(len(srk.submissions(legacy, old, c)), 4)
        for cfg in ({"timePrecision": "min", "timeRounding": "ceil"}, {"timePrecision": "s", "timeRounding": "floor"}):
            broken = deepcopy(data);broken["sorter"]["config"].update(cfg)
            with self.assertRaises(VPError):srk.metadata(broken, c, SOURCE)

    def test_explicit_no_output_attempt_counts_and_excluded_no_output_does_not(self):
        c = contest();data = archive()
        status = data["rows"][0]["statuses"][0]
        status["solutions"][0]["result"] = "NOUT"
        events, rows = srk.results(data, srk.metadata(data, c, SOURCE), c)
        self.assertEqual(events[0].verdict, "WRONG-ANSWER")
        self.assertEqual(rows[0].penalty, 30)
        data["sorter"]["config"]["noPenaltyResults"].append("NOUT")
        status["tries"] = 1;data["rows"][0]["score"]["time"] = [600, "s"]
        events, rows = srk.results(data, srk.metadata(data, c, SOURCE), c)
        self.assertEqual(rows[0].penalty, 10)
        self.assertEqual(len(events), 4)
        self.assertEqual(events[0].verdict,"IGNORED")

    def test_nonpenalized_nout_uke_and_ce_remain_physical_public_submissions(self):
        c=contest();data=archive()
        data["sorter"]["config"]["noPenaltyResults"].append("NOUT")
        data["rows"].append({"user":{"id":"ignored-only","name":"Ignored only","organization":"School I","official":True},
                             "score":{"value":0,"time":[0,"s"]},
                             "statuses":[{"result":None,"time":[0,"s"],"tries":0,"solutions":[
                                 {"result":"NOUT","time":[100,"s"]},{"result":"UKE","time":[110,"s"]},
                                 {"result":"CE","time":[120,"s"]}]},
                                 {"result":None,"time":[0,"s"],"tries":0}]})
        replay=onsite._replay(data,c,SOURCE,PAGE)
        score=replay.at(c,1800,released=True)
        ignored=next(row for row in score.rows if row.id=="ignored-only")
        self.assertEqual((ignored.solved,ignored.penalty,ignored.cells["A"].tries),(0,0,0))
        physical=replay.statistics_at(c,1800,released=True)
        self.assertEqual(physical["submitted"]["A"],7)
        self.assertEqual(physical["attempted"]["A"],4)
        self.assertEqual(physical["accepted"]["A"],3)
        for result in ("NOUT","UKE"):
            malformed=deepcopy(data);malformed["rows"][-1]["statuses"][0]["solutions"][0]={"result":result,"time":[7200,"s"]}
            with self.subTest(result=result),self.assertRaises(VPError):onsite._replay(malformed,c,SOURCE,PAGE)

    def test_final_cells_scores_and_exact_native_identity_reject_mismatches(self):
        for field in ("problem", "start", "duration", "penalty", "total", "event", "official"):
            with self.subTest(field=field):
                data=archive();c=contest()
                if field=="problem":data["problems"][0]["link"]="https://codeforces.com/gym/888888/problem/A"
                elif field=="start":data["contest"]["startAt"]="2026-06-02T00:00:00Z"
                elif field=="duration":data["contest"]["duration"]=[3,"h"]
                elif field=="penalty":data["sorter"]["config"]["penalty"]=[10,"min"]
                elif field=="total":data["rows"][0]["score"]["time"]=[31,"min"]
                elif field=="event":data["rows"][0]["statuses"][0]["solutions"][-1]["result"]="WA"
                elif field=="official":
                    for row in data["rows"]:row["user"]["official"]=False
                with self.assertRaises(VPError):onsite._replay(data,c,SOURCE,PAGE)

    def test_original_replay_and_statistics_include_all_original_teams_but_only_official_rank(self):
        c=contest();web=self.session()
        replay=onsite.load_replay(c,web=web)
        reference=replay.at(c,1800,released=True)
        me=score_submissions([Submission("mine","me","A",1200,"CORRECT")],list(c.problems),c.rules,1800)
        self.assertTrue(reference.complete);self.assertTrue(reference.statistics_complete)
        self.assertEqual(rank_of(me,reference.rows),2)
        self.assertEqual(statistics(reference.rows,list(c.problems))["accepted"]["A"],2)
        self.assertEqual(sum(row.cells["A"].solved for row in reference.rows),3)
        self.assertEqual(c.extra["original_eligibility"]["source_page"],PAGE)
        self.assertEqual([call.args[0] for call in web.get.call_args_list],[PAGE,SOURCE])
        for call in web.get.call_args_list:
            self.assertFalse(call.kwargs["allow_redirects"])
            self.assertNotIn("headers",call.kwargs)
            self.assertNotIn("cookies",call.kwargs)
        fresh=contest();no_network=Mock()
        cached=onsite.load_replay(fresh,web=no_network)
        self.assertEqual(len(cached.teams),3);no_network.get.assert_not_called()

    def test_untrusted_sources_redirects_and_oversized_content_are_rejected(self):
        session=Mock()
        for url in ("http://rl.algoux.cn/ranklist/ccpc2026invitational-nanchang", SOURCE+"?token=bad", "https://cdn.algoux.cn.attacker.test/rankland/file/1/a.srk.json"):
            with self.assertRaises(VPError):onsite._read(session,url)
        session.get.assert_not_called()
        session.get.return_value=Response(b"",302)
        with self.assertRaises(VPError):onsite._read(session,PAGE)
        session.get.return_value=Response(b"12345")
        with patch("vp_helper.onsite._MAX_BYTES",4),self.assertRaises(VPError):onsite._read(session,PAGE)

    def test_missing_source_leaves_original_rank_unavailable(self):
        c=contest();session=Mock();session.get.return_value=Response("No archive")
        with self.assertRaisesRegex(VPError,"Original rank and public statistics are unavailable"):
            onsite.load_replay(c,web=session)
        self.assertNotIn("original_eligibility",c.extra)

    def test_corrupt_cache_is_replaced_with_a_validated_public_archive(self):
        from vp_helper.originals import cache_path
        path=cache_path("onsite",contest().url);path.parent.mkdir(parents=True,exist_ok=True)
        for value in ('not json', '[]', json.dumps({"page":PAGE,"source":{},"ranklist":archive()})):
            with self.subTest(value=value[:30]):
                path.write_text(value)
                replay=onsite.load_replay(contest(),web=self.session())
                self.assertEqual(len(replay.teams),3)

    def test_gym_reuses_onsite_replay_when_native_mirror_has_no_original_rows(self):
        c=contest();api=Gym(c.url,{"handle":"local-vp"})
        api.api=Mock(return_value={"contest":{"type":"ICPC","phase":"FINISHED"},"problems":[{"index":"A"},{"index":"B"}],"rows":[]})
        replay=onsite._replay(archive(),c,SOURCE,PAGE)
        with patch("vp_helper.onsite.load_replay",return_value=replay) as loader:
            first=api.reference(c,1200);second=api.reference(c,1800)
        self.assertTrue(first.complete);self.assertTrue(second.complete)
        self.assertEqual(len(second.rows),3);loader.assert_called_once_with(c)
