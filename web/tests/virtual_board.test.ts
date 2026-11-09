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
    ...Array.from({length:50},(_,i)=>({team:i===49?'s':String(i),id:String(i),problem:'A',time:i===0?58.75:59,verdict:'CORRECT'})),
    {team:'0',id:'51',problem:'B',time:119,verdict:'CORRECT'},
    {team:'0',id:'53',problem:'C',time:130,verdict:'WRONG-ANSWER'},
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
  assert.deepEqual(regional.rows[0].problemSlots?.map(({label,cell})=>[label,cell?.solved??false]),[
    ['A',true],[null,true],[null,false],
  ]);
  assert.equal(regional.rows[0].problemSlots?.[1].first,true);
  assert.equal(regional.rows[0].problemSlots?.[2].cell?.tries,1);
  assert.deepEqual(regional.rows[0].first,['A']);
  assert.deepEqual(regional.rows[1].first,[]);
  const frozenAt=start+510e3;
  const frozen=buildVirtualBoard(room,[],undefined,archive,null,frozenAt);
  assert.equal(frozen.rows.find(row=>row.id==='original:1')?.cells.C.pending,1);
  assert.equal(frozen.rows.find(row=>row.id==='original:1')?.cells.C.solved,false);
  const regionalFrozen=buildVirtualBoard(ccpc,[],undefined,archive,null,frozenAt);
  assert.deepEqual(regionalFrozen.problems.map(problem=>problem.label),['A','B','C']);
  assert.deepEqual(regionalFrozen.rows[0].first,['A','B']);
  const final=buildVirtualBoard(room,[],undefined,archive,null,start+600e3);
  assert.equal(final.rows.find(row=>row.id==='original:1')?.cells.C.solved,true);
});

test('CCPC packs anonymous solves and submissions on the left until the freeze starts',()=>{
  const ccpc={...room,scoring_system:'CCPC'};
  const history={id:'history',participant_key:'vp',participant_name:'Room VP',participant_members_json:'[]',
    started_at:starts_at,problems_json:'["A","B","C"]',submissions_json:JSON.stringify([
      {id:'0',problem:'B',submittedAt:new Date(start+25e3).toISOString(),verdict:'WRONG-ANSWER'},
      {id:'1',problem:'C',submittedAt:new Date(start+31e3).toISOString(),verdict:'CORRECT'},
      {id:'2',problem:'B',submittedAt:new Date(start+100e3).toISOString(),verdict:'PENDING'},
      {id:'5',problem:'B',submittedAt:new Date(start+200e3).toISOString(),verdict:'CORRECT'},
      {id:'3',problem:'A',submittedAt:new Date(start+47e3).toISOString(),verdict:'CORRECT'},
      {id:'4',problem:'C',submittedAt:new Date(start+60e3).toISOString(),verdict:'CORRECT'},
    ])};
  const slotsAt=(time:number)=>buildVirtualBoard(ccpc,[history],'vp',archive,null,start+time*1000)
    .mine?.problemSlots?.map(({label,cell})=>[label,cell?.solved,cell?.tries,cell?.pending]);
  assert.deepEqual(slotsAt(90),[['A',true,1,0],[null,true,1,0],[null,false,1,0]]);
  assert.deepEqual(slotsAt(150),[['A',true,1,0],[null,true,1,0],[null,false,1,1]]);
  const before=buildVirtualBoard(ccpc,[history],'vp',archive,null,start+479999);
  assert.equal(before.rowProblemOrder,true);
  assert.deepEqual(before.mine?.problemSlots?.map(({label,cell})=>[label,cell?.solved,cell?.tries]),[
    ['A',true,1],[null,true,1],[null,true,2],
  ]);
  assert.deepEqual(Object.keys(before.mine!.cells),['A']);
  const frozen=buildVirtualBoard(ccpc,[history],'vp',archive,null,start+480000);
  assert.equal(frozen.rowProblemOrder,false);
  assert.equal(frozen.mine?.problemSlots,undefined);
  assert.deepEqual(frozen.problems.map(problem=>problem.label),['A','B','C']);
  const ended=buildVirtualBoard({...ccpc,freeze_minutes:0},[history],'vp',archive,null,start+600000);
  assert.equal(ended.rowProblemOrder,false);
  assert.deepEqual(ended.problems.map(problem=>problem.label),['A','B','C']);
});

test('legacy native CCPC totals fill anonymous solved slots without inventing solve times',()=>{
  const ccpc={...room,contest_url:'https://codeforces.com/gym/12345',scoring_system:'CCPC'};
  const history={participant_key:'waiting',participant_name:'Waiting',participant_members_json:'[]',problems_json:'["A","B","C"]'};
  const row={id:'native',name:'Original',kind:'official' as const,solved:2,penalty:50,cells:{B:{solved:true,tries:1,pending:0,solveTime:1}}};
  const board=buildVirtualBoard(ccpc,[history],undefined,null,{elapsed:120,released:true,revealed:['B'],rows:[row]},start+120000);
  assert.deepEqual(board.problems.map(problem=>problem.label),['B',null,null]);
  assert.deepEqual(board.rows[0].problemSlots?.map(({label,cell})=>[label,cell?.solved??false,cell?.solveTime]),[
    ['B',true,1],[null,true,undefined],[null,false,undefined],
  ]);
});

test('medal bands use official ranks during the contest and exclude starred and scoreless teams',()=>{
  const awards={
    metadata:{...archive.metadata,medals:{gold:1,silver:1,bronze:1},
      teams:['g','s','b','zero','star'].map(id=>({id,name:id,official:id!=='star'}))},
    events:[['g',15],['s',70],['b',130],['star',5]].map(([team,time],i)=>
      ({id:String(i),team:String(team),time:Number(time),problem:'A',verdict:'CORRECT'})),
  };
  const history={id:'history',participant_key:'vp',participant_name:'VP',participant_members_json:'[]',
    started_at:starts_at,problems_json:'["A","B","C"]',submissions_json:JSON.stringify([
      {id:'1',problem:'B',submittedAt:new Date(start+20e3).toISOString(),verdict:'CORRECT'},
    ])};
  const live=buildVirtualBoard(room,[history],'vp',awards,null,start+200000);
  assert.equal(live.medalState,'provisional');
  assert.equal(live.mine?.medal,'gold');
  assert.deepEqual(Object.fromEntries(live.rows.map(row=>[row.name,row.medal])),{
    g:'gold',s:'silver',b:'bronze',star:undefined,zero:undefined,VP:'gold',
  });
  assert.equal(buildVirtualBoard(room,[],undefined,awards,null,start+600000).medalState,'final');
});

test('Qinhuangdao mirror resolves the published original archive unless the room selects another',()=>{
  const selected='https://board.xcpcio.com/icpc/51st/wuhan';
  assert.equal(roomBoardSource(room),'https://board.xcpcio.com/ccpc/9th/qinhuangdao');
  assert.equal(roomBoardSource({...room,external_rank_url:selected}),selected);
  assert.equal(roomBoardSource({...room,contest_url:'https://codeforces.com/gym/12345'}),null);
  assert.equal(buildVirtualBoard({...room,duration_seconds:700},[],undefined,archive,null,start+600e3).originalAvailable,false);
});
