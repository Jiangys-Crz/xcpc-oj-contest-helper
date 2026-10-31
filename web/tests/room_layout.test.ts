import {test} from 'node:test';
import assert from 'node:assert/strict';
import {isRoomLayoutPath} from '../components/roomLayout';

test('only normal room tab paths use room-only navigation',()=>{
  for(const path of ['/vp/room-id','/vp/room-id/','/vp/room-id/rank','/vp/room-id/mysubmit','/vp/room-id/print','/vp/room-id/print/'])assert.equal(isRoomLayoutPath(path),true,path);
  for(const path of ['/','/vp','/vp/','/history','/history/record','/teams','/notifications','/install','/account','/login','/admin','/prints/job','/vp/room-id/print/job','/vp/room-id/rank/extra','/vp/room-id/unknown','/vp//rank'])assert.equal(isRoomLayoutPath(path),false,path);
});
