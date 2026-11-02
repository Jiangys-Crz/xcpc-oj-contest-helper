from copy import deepcopy
from unittest.mock import patch
import unittest

from vp_helper import cli, cloud, external_rank, sessions, vp
from vp_helper.models import VPError
from vp_helper.storage import read_json, write_json
import test_cloud_terminal
from test_workflows import Isolated, URL


PAGE = 'https://board.xcpcio.com/icpc/51st/wuhan'


class RoomRankPreparationTests(Isolated):
    def setUp(self):
        super().setUp()
        self.context = {'scoringSystem': 'ICPC', 'freezeMinutes': 0, 'externalRankUrl': PAGE}
        self.stack.enter_context(patch.object(cloud, 'ensure_access'))
        self.stack.enter_context(patch.object(cloud, 'queue_state'))

    def test_room_archive_is_validated_and_saved_before_native_start(self):
        payload = {'source': PAGE, 'events': []}
        def start(*args, **kwargs):
            saved = sessions.attempts()
            self.assertEqual(len(saved), 1)
            self.assertEqual(read_json(external_rank.attachment_path(saved[0][0])), payload)
            return self.clock
        self.api.start.side_effect = start
        with patch.object(external_rank, 'prepare_import', return_value=payload) as importer:
            path, state = vp.prepare(URL, self.clock, cloud=self.context)
        self.assertEqual(importer.call_args.args[0], PAGE)
        self.api.start.assert_called_once()
        self.assertEqual(state['start_time'], self.clock)
        self.assertEqual(state['cloud']['externalRankUrl'], PAGE)

    def test_invalid_room_archive_never_registers_native_vp(self):
        with patch.object(external_rank, 'prepare_import', side_effect=VPError('bad archive')):
            with self.assertRaisesRegex(VPError, 'bad archive'):
                vp.prepare(URL, self.clock, cloud=self.context)
        self.api.start.assert_not_called()
        self.assertEqual(sessions.attempts(), [])


class RoomRankCommandTests(unittest.TestCase):
    setUp = test_cloud_terminal.CloudTerminalTests.setUp
    room_reply = test_cloud_terminal.CloudTerminalTests.room_reply

    def test_failed_archive_download_does_not_rebind_existing_local_vp(self):
        reply = self.room_reply()
        reply['room']['externalRankUrl'] = PAGE
        original = deepcopy(self.state)
        write_json(external_rank.attachment_path(self.path), {'source': 'old-board'})
        with patch.object(cloud, 'authorize', return_value=(reply, self.saved)), \
                patch.object(cli, 'load_account', return_value={'qoj': {'handle': 'fixture'}}), \
                patch.object(sessions, 'latest_session', return_value=(self.path, self.state)), \
                patch.object(external_rank, 'prepare_import', side_effect=VPError('offline')), \
                patch.object(cli, 'reopen_saved') as reopen:
            with self.assertRaisesRegex(VPError, 'offline'):
                cloud.start_room('vp.cwnupaa.com/vp/room-1')
        self.assertEqual(read_json(self.path), original)
        self.assertEqual(read_json(external_rank.attachment_path(self.path)), {'source': 'old-board'})
        reopen.assert_not_called()

    def test_ccpc_source_normalizes_host_and_uuid_case(self):
        page = 'https://RANK.ICcPC.CN/r/E5697B3A-8326-4818-84CA-E2CE1688AF23/'
        self.assertEqual(external_rank.source_url(page), page.lower().rstrip('/'))

    def test_new_room_passes_source_into_native_preparation(self):
        reply = self.room_reply()
        reply['room']['externalRankUrl'] = PAGE
        with patch.object(cloud, 'authorize', return_value=(reply, self.saved)), \
                patch.object(cli, 'load_account', return_value={'qoj': {'handle': 'fixture'}}), \
                patch.object(sessions, 'latest_session', return_value=None), \
                patch.object(vp, 'prepare', return_value=(self.path, self.state)) as prepare, \
                patch.object(cli, 'open_state'), patch.object(cloud, 'queue_state'):
            cloud.start_room('vp.cwnupaa.com/vp/room-1')
        self.assertEqual(prepare.call_args.kwargs['cloud']['externalRankUrl'], PAGE)

    def test_restore_also_downloads_room_rank_before_opening(self):
        from vp_helper import cloud_restore
        reply = self.room_reply()
        reply.update(canRestore=True)
        reply['room']['externalRankUrl'] = PAGE
        with patch.object(cloud, 'authorize', return_value=(reply, self.saved)), \
                patch.object(cloud_restore, 'restore_room', return_value=(self.path, self.state)), \
                patch.object(external_rank, 'sync_room_rank') as sync, patch.object(cli, 'open_state'):
            cloud.start_room('vp.cwnupaa.com/vp/room-1')
        sync.assert_called_once_with(self.path, self.state, PAGE)

    def test_room_source_change_is_detected_on_authorization(self):
        state = deepcopy(self.state)
        state['cloud'] = {**self.context, 'externalRankUrl': PAGE}
        reply = self.room_reply()
        with self.assertRaisesRegex(VPError, '外榜'):
            cloud._check_room_context(state, reply['room'], reply['participant'])


class PublicRankHTTPTests(unittest.TestCase):
    def test_public_redirect_and_oversize_are_rejected_and_closed(self):
        from unittest.mock import Mock
        from vp_helper.external_rank_http import read_public_json
        for response in (Mock(status_code=302), Mock(status_code=200)):
            response.iter_content.return_value = [b'x' * (32 * 1024 * 1024 + 1)]
            web = Mock()
            web.get.return_value = response
            with self.assertRaises(VPError):
                read_public_json(web, PAGE + '/config.json')
            response.close.assert_called_once()
            self.assertFalse(web.get.call_args.kwargs['allow_redirects'])
