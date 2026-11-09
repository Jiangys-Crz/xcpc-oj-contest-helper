import assert from 'node:assert/strict';
import {test} from 'node:test';
import {buildVirtualBoard,roomBoardSource} from '../lib/virtual-board';

const starts_at='2030-01-01T00:00:00Z';
const start=Date.parse(starts_at);
const room={starts_at,contest_url:'https://codeforces.com/gym/104787',duration_seconds:600,freeze_minutes:2,scoring_system:'ICPC'};
const archive={
  metadata:{duration:600,penalty_minutes:20,penalty_rounding:'total_seconds',problems:['A','B','C'],teams:[
    ...Array.from({length:49},(_,i)=>({id:String(i),name:'Team '+i,official:true})),{id:'s',name:'Starred team',official:false}
  ]},
  events:[
    ...Array.from({length:50},(_,i)=>({team:i===49?'s':String(i),id:String(i),problem:'A',time:59,verdict:'CORRECT'})),
    {team:'0',id:'51',problem:'B',time:119,verdict:'CORRECT'},
    {team:'1',id:'52',problem:'C',time:500,verdict:'CORRECT'}
  ]
};

test('external original timeline respects room ICPC and CCPC display rules',()=>{
  const now=start+150e3,ccpc={...room,scoring_system:'CCPC'};
  const standard=buildVirtualBoard(room,[],undefined,archive,null,now);
  const regional=buildVirtualBoard(ccpc,[],undefined,archive,null,now);
  assert.deepEqual(standard.problems.map(problem=>problem.label),['A','B','C']);
  assert.deepEqual(regional.problems.map(problem=>problem.label),['A',null,null]);
  assert.equal(regional.problems[0].accepted,50);
  assert.equal(regional.rows[0].penalty,2);
  assert.equal(regional.rows[0].cells.B,undefined);
  const frozenAt=start+510e3;
  const frozen=buildVirtualBoard(room,[],undefined,archive,null,frozenAt);
  assert.equal(frozen.rows.find(row=>row.id==='original:1')?.cells.C.pending,1);
  assert.equal(frozen.rows.find(row=>row.id==='original:1')?.cells.C.solved,false);
  assert.deepEqual(buildVirtualBoard(ccpc,[],undefined,archive,null,frozenAt).problems.map(problem=>problem.label),['A','B','C']);
  const final=buildVirtualBoard(room,[],undefined,archive,null,start+600e3);
  assert.equal(final.rows.find(row=>row.id==='original:1')?.cells.C.solved,true);
});

test('Qinhuangdao mirror resolves the published original archive unless the room selects another',()=>{
  const selected='https://board.xcpcio.com/icpc/51st/wuhan';
  assert.equal(roomBoardSource(room),'https://board.xcpcio.com/ccpc/9th/qinhuangdao');
  assert.equal(roomBoardSource({...room,external_rank_url:selected}),selected);
  assert.equal(roomBoardSource({...room,contest_url:'https://codeforces.com/gym/12345'}),null);
  assert.equal(buildVirtualBoard({...room,duration_seconds:700},[],undefined,archive,null,start+600e3).originalAvailable,false);
});
