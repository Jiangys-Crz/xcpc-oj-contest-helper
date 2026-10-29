from unittest.mock import Mock, patch
from datetime import datetime, timezone

from bs4 import BeautifulSoup

from vp_helper import cli, vp
from vp_helper.models import Contest, Rules, StartRejected, VPError
from vp_helper.qoj import QOJ
from vp_helper.storage import read_json
from test_workflows import Isolated, URL


def page(body):
    return BeautifulSoup('<nav><a href="/user/profile/tester">me</a></nav>'+body, 'html.parser')


class NativeQOJStartTests(Isolated):
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

    def test_register_form_for_another_contest_cannot_start_vp(self):
        client=self.client()
        client._page=Mock(return_value=page('<form method="post" action="/contest/999/virtual/register">'
                                          '<input name="_token" value="fixture"></form>'))
        with self.assertRaises(StartRejected):
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
