from copy import deepcopy
import io
from pathlib import Path
from unittest.mock import patch

from rich.console import Console

from vp_helper import cloud, vp
from vp_helper.identity import persist_display_identity
from vp_helper.models import Contest
from vp_helper.scoring import score_submissions
from vp_helper.sessions import save_state
from vp_helper.storage import read_json, write_json
from vp_helper.tui import Dashboard
from vp_helper.workspaces import display_name, name, promote, rename_existing
from test_workflows import Isolated, URL


class CloudRoomNameTests(Isolated):
    def setUp(self):
        super().setUp()
        self.context={'serverUrl':'https://vp.cwnupaa.com','roomId':'room','roomName':'CCPC Friday / Team Training',
                      'userId':'user','participantId':'participant','scoringSystem':'ICPC','freezeMinutes':0,'startsAt':'1970-01-01T00:50:00Z'}
        self.stack.enter_context(patch.object(cloud,'ensure_access'))
        self.stack.enter_context(patch.object(cloud,'queue_state'))

    def test_new_cloud_workspace_uses_room_name_and_scheduled_start_retains_native_identity(self):
        path,state=vp.prepare(URL,3000,cloud=self.context)
        directory=Path(state['directory'])
        self.assertEqual(directory.name,'CCPC Friday Team Training')
        self.assertEqual(state['contest']['name'],'test contest')
        self.assertEqual(state['contest']['directory_name'],'test contest')
        self.assertEqual(state['contest']['extra']['room_name'],self.context['roomName'])
        source=directory/'A.cpp';source.write_bytes(b'unchanged solution')
        self.clock=3000
        active=vp.activate(path,state)
        self.assertEqual(active['contest']['name'],'test contest')
        self.assertEqual(active['contest']['extra']['room_name'],self.context['roomName'])
        self.assertEqual(active['contest']['extra']['contest_format'],'ICPC')
        self.assertIsNone(active['contest']['rules']['problem_reveal_count'])
        self.assertEqual(active['directory'],str(directory));self.assertEqual(source.read_bytes(),b'unchanged solution')
        self.assertEqual(self.api.start.call_args.args[0].name,'test contest')
        config=read_json(directory/'contest.json')
        self.assertEqual(config['base_url'],URL);self.assertEqual(config['vp_context']['state_path'],str(path))
        payload=cloud.sync_payload(path,active)
        self.assertEqual(payload['contest']['name'],'test contest');self.assertEqual(payload['contest']['url'],URL)
        contest=Contest.from_dict(active['contest'])
        dashboard=Dashboard(contest,1,score_submissions([],list(contest.problems),contest.rules,1),'—','',None,[])
        output=io.StringIO();Console(file=output,width=180).print(dashboard.fixed(180))
        self.assertIn(self.context['roomName'],output.getvalue());self.assertEqual(contest.name,'test contest')

    def legacy(self):
        path,state=vp.prepare(URL,3000)
        state['cloud']=deepcopy(self.context)
        state['workspace_name']='Legacy folder label is not the official title'
        write_json(path,state)
        return path,state

    def test_existing_legacy_cloud_folder_and_source_are_not_renamed_or_opted_in(self):
        path,state=self.legacy();directory=Path(state['directory'])
        source=directory/'A.cpp';source.write_bytes(b'original solution\x00')
        config=(directory/'contest.json').read_bytes()
        state['workspace_target']=str(directory.parent/'New Room Name')
        write_json(path,state)
        rename_existing(path,state,Contest.from_dict(state['contest']))
        self.assertEqual(state['directory'],str(directory));self.assertEqual(source.read_bytes(),b'original solution\x00')
        self.assertEqual((directory/'contest.json').read_bytes(),config)
        self.assertNotIn('room_name',state['contest']['extra'])
        save_state(path,state)
        self.assertEqual(read_json(path)['contest']['name'],'test contest')
        self.assertFalse((directory.parent/'New Room Name').exists())

    def test_missing_existing_directory_is_recreated_at_old_path_and_unrelated_target_kept(self):
        path,state=self.legacy();directory=Path(state['directory'])
        for item in directory.iterdir():item.unlink()
        directory.rmdir()
        unrelated=directory.parent/'New Room Name';unrelated.mkdir();(unrelated/'keep.cpp').write_bytes(b'unrelated')
        state['workspace_target']=str(unrelated);write_json(path,state)
        promote(path,state)
        self.assertTrue(directory.is_dir());self.assertEqual(state['directory'],str(directory))
        self.assertEqual((unrelated/'keep.cpp').read_bytes(),b'unrelated')

    def test_completed_old_rename_is_adopted_only_at_its_proven_bound_target(self):
        path,state=self.legacy();source=Path(state['directory']);target=source.parent/'Already moved room'
        (source/'A.cpp').write_bytes(b'keep old move result');source.rename(target)
        state['workspace_target']=str(target);write_json(path,state)
        before=(target/'contest.json').read_bytes()
        promote(path,state)
        self.assertEqual(state['directory'],str(target));self.assertFalse(source.exists())
        self.assertEqual((target/'A.cpp').read_bytes(),b'keep old move result')
        self.assertEqual((target/'contest.json').read_bytes(),before)
        self.assertEqual(state['contest']['name'],'test contest')

    def test_opted_in_display_refresh_is_atomic_and_legacy_records_do_not_gain_display_metadata(self):
        path,state=vp.prepare(URL,3000,cloud=self.context)
        before=read_json(path);stale=deepcopy(before)
        fresh=read_json(path);fresh['submissions']=[{'id':'99','team':'me','problem':'A','time':10,'verdict':'CORRECT','practice':False}];write_json(path,fresh)
        stale['cloud']['roomName']='Renamed cloud room';stale['contest']['extra']['room_name']='Renamed cloud room'
        persist_display_identity(path,stale)
        saved=read_json(path);self.assertEqual(saved['submissions'],fresh['submissions'])
        self.assertEqual(saved['contest']['name'],'test contest');self.assertEqual(saved['contest']['extra']['room_name'],'Renamed cloud room')
        self.assertEqual(saved['directory'],before['directory'])
        stale['workspace_name']='CCPC must not become native title';save_state(path,stale)
        self.assertEqual(read_json(path)['contest']['name'],'test contest')
        legacy=deepcopy(saved);legacy['contest']['extra'].pop('room_name');legacy['cloud']['roomName']='Original legacy room'
        old_path=path.with_name('vp-qoj-123-legacy-display.json');write_json(old_path,legacy)
        incoming=deepcopy(legacy);incoming['cloud']['roomName']='New server title'
        persist_display_identity(old_path,incoming)
        self.assertNotIn('room_name',read_json(old_path)['contest']['extra'])
        self.assertEqual(read_json(old_path)['cloud']['roomName'],'Original legacy room')

    def test_room_folder_uses_existing_windows_reserved_name_and_forbidden_character_guards(self):
        contest=self.api.prepare();contest.extra['room_name']='CON'
        with patch('vp_helper.workspaces.os.name','nt'):
            self.assertEqual(name(contest),'_CON')
        contest.extra['room_name']='Training: A / B?'
        self.assertEqual(name(contest),'Training A B')
        self.assertEqual(contest.name,'test contest')


