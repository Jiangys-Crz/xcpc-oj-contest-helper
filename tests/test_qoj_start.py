from unittest.mock import Mock, patch
from datetime import datetime, timezone

from bs4 import BeautifulSoup

from vp_helper import cli, vp
from vp_helper.models import Contest, Rules, StartRejected, VPError
from vp_helper.qoj import QOJ
from vp_helper.scheduling import prompt_start
from vp_helper.storage import read_json
from test_workflows import Isolated, URL


def page(body):
    return BeautifulSoup('<nav><a href="/user/profile/tester">me</a></nav>'+body, 'html.parser')


class NativeQOJStartTests(Isolated):
    def test_default_enter_reserves_cf_gym_now_but_qoj_posts_three_minutes_later(self):
        self.api.account['submit_config_format']='contest.json'
        self.api.web.session.headers={'User-Agent':'fixture'}
        self.api.start.side_effect=lambda c,requested_start=None,**kw: requested_start or self.clock
        for platform,segment,ident in (('cf','contest','123'),('gym','gym','100123'),('qoj','contest','123')):
            url=f'https://{"qoj.ac" if platform == "qoj" else "codeforces.com"}/{segment}/{ident}'
            self.api.prepare.side_effect=lambda p=platform,u=url,i=ident: Contest(
                p,i,u,'Default '+p,'Default '+p,{'A':'10' if p == 'qoj' else 'A'},Rules(7200))
            self.api.start.reset_mock()
            with patch('builtins.input',return_value=''):
                path,state=cli.prepare_start(url,scheduled_start=prompt_start())
            self.assertEqual(state['requested_start'],1180)
            if platform == 'qoj':
                self.api.start.assert_not_called()
                self.clock=1179
                vp.activate(path,state)
                self.api.start.assert_not_called()
                self.clock=1180
                state=vp.activate(path,state)
                self.api.start.assert_called_once()
                self.assertEqual(state['start_time'],1180)
            else:
                self.api.start.assert_called_once()
                self.assertEqual(self.api.start.call_args.kwargs['requested_start'],1180)
                self.assertEqual(state['start_time'],1180)

    def test_actual_qoj_register_vp_form_with_search_form_and_start_button(self):
        client=QOJ('https://qoj.ac/contest/3617',self.api.account)
        body=page('<title>The 2008 ACM-ICPC Asia Aizu Regional Contest - Virtual Contest Rules - QOJ.ac</title>'
                  '<h1>QOJ.ac</h1><h1>QOJ</h1><h1>Virtual Contest Rules</h1>'
                  '<form id="form-search-problem" method="get"><input name="search"><button type="submit" id="submit-search"></button></form>'
                  '<form id="form-register_vp" method="post" action="/contest/3617/virtual/register">'
                  '<input type="hidden" name="_token" value="fixture-token">'
                  '<button type="submit" id="button-submit-register_vp" name="submit-register_vp" value="register_vp">Start!</button></form>')
        client._page=Mock(return_value=body)
        client._registration=Mock(side_effect=[None,(1000,18000)])
        client.web=Mock()
        client.web.post.return_value=Mock(content='<h1>Contest</h1>')
        contest=self.api.prepare()
        self.assertEqual(client.start(contest,fresh=True),1000)
        call=client.web.post.call_args
        self.assertEqual(call.args[0],'https://qoj.ac/contest/3617/virtual/register')
        self.assertEqual(call.args[1],{'_token':'fixture-token','submit-register_vp':'register_vp'})
        self.assertEqual(client.contest_name(body),'The 2008 ACM-ICPC Asia Aizu Regional Contest')
        client.web.post.assert_called_once()

    def test_qoj_action_query_is_preserved_and_unrecognized_form_is_not_native_refusal(self):
        client=self.client()
        client._page=Mock(return_value=page('<form id="form-register_vp" method="post" action="/contest/123/virtual/register?locale=en">'
                                          '<input name="_token" value="fixture"></form>'))
        form,target=client._start_form()
        self.assertEqual(target,URL+'/virtual/register?locale=en')
        client._page.return_value=page('<form id="unknown" method="post"><input name="nonce" value="PRIVATE-FIXTURE"></form>')
        with self.assertRaises(VPError) as error:
            client._start_form()
        self.assertNotIsInstance(error.exception,StartRejected)
        self.assertNotIn('PRIVATE-FIXTURE',str(error.exception))
        client.web.post.assert_not_called()

    def test_cf_and_gym_register_future_start_immediately_instead_of_waiting_for_qoj_timer(self):
        self.api.account['submit_config_format']='contest.json'
        self.api.web.session.headers={'User-Agent':'fixture'}
        self.api.start.side_effect=lambda c,requested_start,**kw: requested_start
        for platform,segment,ident in (('cf','contest','123'),('gym','gym','100123')):
            url=f'https://codeforces.com/{segment}/{ident}'
            self.api.prepare.side_effect=lambda p=platform,u=url,i=ident: Contest(
                p,i,u,'Scheduled '+p,'Scheduled '+p,{'A':'A'},Rules(7200))
            self.api.start.reset_mock()
            path,state=cli.prepare_start(url,scheduled_start=3000)
            self.api.start.assert_called_once()
            self.assertEqual(self.api.start.call_args.kwargs['requested_start'],3000)
            self.assertEqual(state['start_time'],3000)
            self.assertEqual(self.clock,1000)
            vp.activate(path,state)
            self.api.start.assert_called_once()

    def client(self):
        client=QOJ(URL, self.api.account)
        client.web=Mock()
        client.web.session.headers={'User-Agent':'fixture'}
        self.posted=[]
        self.token='preflight-token'
        def get(url, **kwargs):
            if url==URL:
                return page('<h1>QOJ</h1><h2>The Regional Training Contest</h2>Ruleset: ICPC'
                            '<table><tr><td>A</td><td><a href="/contest/123/problem/10">A</a></td></tr></table>'
                            '<a href="/download.php?x=1">Statements</a>')
            if url==URL+'/virtual/register':
                return page('<form id="form-register" method="post" action="/contest/123/virtual/register">'
                            f'<input name="_token" value="{self.token}">'
                            '<input type="checkbox" name="confirm-rules" required value="yes">'
                            '<button name="submit-register" value="register">Start</button></form>')
            if url=='https://qoj.ac/contests':
                return page('<table><tr><td><a href="/contest/123">The Regional Training Contest</a>5 hours</td></tr></table>')
            raise AssertionError(url)
        def post(url,payload,**kwargs):
            self.posted.append((self.clock,url,payload))
            return Mock(content='<div>Virtual participation started</div>')
        client.web.soup.side_effect=get
        client.web.post.side_effect=post
        client._registration=Mock(side_effect=lambda **kw: (self.posted[0][0],18000) if self.posted else None)
        self.stack.enter_context(patch.object(cli,'adapter',return_value=client))
        return client

    def test_qoj_posts_register_endpoint_only_at_deadline_with_fresh_form(self):
        client=self.client()
        path,state=cli.prepare_start(URL, scheduled_start=3000)
        self.assertEqual(state['status'],'scheduled')
        client.web.post.assert_not_called()
        self.clock=2999
        self.assertEqual(vp.activate(path,state)['status'],'scheduled')
        client.web.post.assert_not_called()
        self.token='deadline-token'
        self.clock=3000
        activated=vp.activate(path,state)
        self.assertEqual(activated['start_time'],3000)
        self.assertEqual(activated['status'],'running')
        self.assertEqual(self.posted,[(3000,URL+'/virtual/register',{
            '_token':'deadline-token','confirm-rules':'yes','submit-register':'register'})])
        vp.activate(path,activated)
        client.web.post.assert_called_once()
        self.assertEqual(client.web.post.call_args.kwargs['headers'],{'Origin':'https://qoj.ac','Referer':URL+'/virtual/register'})
        self.assertEqual(read_json(path)['contest']['name'],'The Regional Training Contest')

    def test_slow_preflight_records_actual_post_time_instead_of_old_local_target(self):
        client=self.client()
        path,state=cli.prepare_start(URL,scheduled_start=3000)
        original_get=client.web.soup.side_effect
        def slow_get(url,**kwargs):
            if url==URL+'/virtual/register':
                self.clock+=120
            return original_get(url,**kwargs)
        client.web.soup.side_effect=slow_get
        self.clock=3000
        started=vp.activate(path,state)
        self.assertEqual(started['status'],'running')
        self.assertEqual(started['activation_target'],3120)
        self.assertEqual(started['start_time'],3120)
        client.web.post.assert_called_once()

    def test_interrupted_get_before_post_does_not_claim_a_start_request_was_sent(self):
        client=self.client()
        path,state=cli.prepare_start(URL,scheduled_start=3000)
        original_get=client.web.soup.side_effect
        def interrupted(url,**kwargs):
            if url==URL+'/virtual/register':
                raise KeyboardInterrupt
            return original_get(url,**kwargs)
        self.clock=3000
        client.web.soup.side_effect=interrupted
        with self.assertRaises(KeyboardInterrupt):
            vp.activate(path,state)
        self.assertFalse(read_json(path)['activation_uncertain'])
        client.web.post.assert_not_called()
        client.web.soup.side_effect=original_get
        self.assertEqual(vp.activate(path,read_json(path))['status'],'running')
        client.web.post.assert_called_once()

    def test_registrants_start_time_is_read_by_header_with_four_columns(self):
        client=self.client()
        del client._registration
        started=datetime(2026,10,29,12,0,tzinfo=timezone.utc).timestamp()
        client._page=Mock(return_value=page('<table><thead><tr><th>User</th><th>Start Time</th><th>End Time</th><th>Duration</th></tr></thead>'
            '<tr><td><a href="/user/profile/tester">tester</a></td><td><time datetime="2026-10-29T12:00:00Z"></time></td>'
            '<td><time datetime="2026-10-29T17:00:00Z"></time></td><td>300</td></tr></table>'))
        self.assertEqual(client._registration(duration=1),(started,18000))

    def test_register_form_for_another_contest_cannot_start_vp(self):
        client=self.client()
        client._page=Mock(return_value=page('<form method="post" action="/contest/999/virtual/register">'
                                          '<input name="_token" value="fixture"></form>'))
        with self.assertRaises(VPError):
            client.start(self.api.prepare(),fresh=True)
        client.web.post.assert_not_called()

    def test_qoj_timeout_is_query_only_on_recovery(self):
        client=self.client()
        client.web.post.side_effect=VPError('outcome unknown')
        with patch('time.sleep'):
            path,state=cli.prepare_start(URL)
        self.assertTrue(state['activation_uncertain'])
        with self.assertRaises(VPError):
            vp.activate(path,state)
        client.web.post.assert_called_once()

    def test_delayed_native_confirmation_recovers_actual_start_without_another_post(self):
        def timeout(*args,**kwargs):
            self.api.native_post_sent=True
            raise VPError('response lost')
        self.api.start.side_effect=timeout
        path,state=cli.prepare_start(URL)
        self.clock=2500
        self.api._registration.return_value=(1300,18000)
        restored=vp.activate(path,state)
        self.assertEqual(restored['status'],'running')
        self.assertEqual(restored['start_time'],1300)
        self.api.start.assert_called_once()

    def test_native_registration_row_confirms_actual_start_time(self):
        client=self.client()
        del client._registration
        self.clock=datetime(2026,10,29,12,0,tzinfo=timezone.utc).timestamp()
        original_get=client.web.soup.side_effect
        def get(url,**kwargs):
            if url==URL+'/registrants':
                row=''
                if self.posted:
                    started=datetime.fromtimestamp(self.posted[0][0],timezone.utc).isoformat()
                    row=f'<tr><td>1</td><td><a href="/user/profile/tester">tester</a></td><td><time datetime="{started}"></time></td></tr>'
                return page('<table><thead><tr><th>#</th><th>User</th><th>Start</th></tr></thead><tbody>'+row+'</tbody></table>')
            return original_get(url,**kwargs)
        client.web.soup.side_effect=get
        deadline=self.clock+60
        path,state=cli.prepare_start(URL,scheduled_start=deadline)
        client.web.post.assert_not_called()
        self.clock=deadline
        activated=vp.activate(path,state)
        self.assertEqual(activated['start_time'],deadline)
        self.assertEqual(activated['status'],'running')
        client.web.post.assert_called_once()

    def test_name_comes_from_heading_or_document_or_contest_list(self):
        client=self.client()
        for body in ('<header><h1>QOJ</h1></header><h2>Actual 比赛名称</h2>',
                     '<h1>QOJ</h1><title>Actual 比赛名称 - QOJ.ac</title>',
                     '<h1>QOJ</h1><a href="/contest/123">Actual 比赛名称</a>'):
            self.assertEqual(client.contest_name(page(body)),'Actual 比赛名称')
        self.assertEqual(client.contest_name(page('<h1>QOJ</h1>')),'The Regional Training Contest')
