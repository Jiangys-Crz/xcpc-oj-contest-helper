from contextlib import ExitStack
from email.utils import formatdate
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from vp_helper import cli, platform_clock, vp
from vp_helper.http import WebSession
from vp_helper.sessions import end_attempt
from test_workflows import Isolated, URL


def response(url, epoch, **headers):
    return Mock(url=url, status_code=200,
                headers={'Date':formatdate(epoch, usegmt=True), 'Content-Type':'application/json', **headers})


class ClockTests(unittest.TestCase):
    def setUp(self):
        self.stack=ExitStack()
        self.addCleanup(self.stack.close)
        root=self.stack.enter_context(tempfile.TemporaryDirectory())
        self.stack.enter_context(patch.dict(os.environ, {'XDG_STATE_HOME':root}))
        self.tick=self.stack.enter_context(patch.object(platform_clock,'tick',return_value=100))
        self.boot=self.stack.enter_context(patch.object(platform_clock,'boot_id',return_value='fixture-boot'))
        self.stack.enter_context(patch('time.time',return_value=5000))

    def test_all_judges_share_server_time_across_requests_and_ignore_wall_clock_jumps(self):
        for host in platform_clock.HOSTS:
            with self.subTest(host=host):
                self.tick.return_value=100
                url='https://'+host+'/contest/1'
                platform_clock.observe(response(url,2000),sent_at=98)
                self.tick.return_value=105
                self.assertEqual(platform_clock.platform_now(url),2006)
                with patch('time.time',return_value=-123456):
                    self.assertEqual(platform_clock.platform_now({'url':url}),2006)

    def test_cached_missing_or_untrusted_time_never_replaces_a_valid_clock(self):
        url='https://codeforces.com/contest/1'
        platform_clock.observe(response(url,2000))
        self.tick.return_value=103
        for item in (response(url,9000,Age='10'), response(url,9000,Date='invalid'),
                     response('https://example.com/contest/1',9000),
                     response(url.replace('https:','http:'),9000)):
            platform_clock.observe(item)
        self.assertEqual(platform_clock.platform_now(url),2003)

    def test_reboot_invalidates_old_monotonic_anchor_then_recalibrates(self):
        url='https://qoj.ac/contest/1'
        platform_clock.observe(response(url,2000))
        self.boot.return_value='next-boot'
        self.assertEqual(platform_clock.platform_now(url),5000)
        platform_clock.observe(response(url,3000))
        self.assertEqual(platform_clock.platform_now(url),3000)

    def test_real_websession_response_calibrates_clock(self):
        web=WebSession('https://qoj.ac',{},auto_refresh=False)
        web.session=Mock()
        web.session.get.return_value=response('https://qoj.ac/contest/1',2000)
        web.get('https://qoj.ac/contest/1')
        self.tick.return_value=107
        self.assertEqual(platform_clock.platform_now('https://qoj.ac/contest/1'),2007)


class ClockLifecycleTests(Isolated):
    def test_local_end_uses_the_same_judge_clock_as_the_timer(self):
        path,state=cli.prepare_start(URL)
        with patch.object(platform_clock,'tick',return_value=100):
            platform_clock.observe(response(URL,1100))
            self.clock=9000
            _,ended=end_attempt(state['attempt_id'])
        self.assertEqual(ended['ended_at'],1100)
        self.assertEqual(ended['status'],'archived')

    def test_qoj_waits_for_server_deadline_even_if_local_clock_is_far_ahead(self):
        path,state=cli.prepare_start(URL,scheduled_start=1180)
        self.api.start.side_effect=lambda *args,**kw: platform_clock.platform_now(URL)
        with patch.object(platform_clock,'tick',return_value=100) as tick:
            platform_clock.observe(response(URL,1179))
            self.clock=9000
            self.assertEqual(vp.activate(path,state)['status'],'scheduled')
            self.api.start.assert_not_called()
            tick.return_value=103
            platform_clock.observe(response(URL,1180))
            active=vp.activate(path,state)
        self.assertEqual(active['status'],'running')
        self.assertEqual(active['start_time'],1180)
        self.api.start.assert_called_once()
