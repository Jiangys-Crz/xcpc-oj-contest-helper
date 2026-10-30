import { test } from 'node:test';
import assert from 'node:assert/strict';
import { roomTiming, publicScore, frozenRank } from '../lib/scoreboard';

test('frozen public scoring excludes compiler errors by default and never turns infrastructure failures into WA',()=>{
  const start=Date.parse('2030-01-01T00:00:00Z'),room={starts_at:new Date(start).toISOString(),duration_seconds:18000,freeze_minutes:60};
  const event=(id:string,seconds:number,verdict:string)=>({id,problem:'A',submittedAt:new Date(start+seconds*1000).toISOString(),verdict,practice:false});
  const row={started_at:room.starts_at,problems_json:'["A"]',rules_json:'{}',submissions_json:JSON.stringify([
    event('1',10,'COMPILER-ERROR'),event('2',20,'IGNORED'),event('3',30,'HACK-SUCCESSFUL'),event('4',40,'SYSTEM-ERROR'),event('5',50,'UNRECOGNIZED-RESULT'),event('6',70,'CORRECT'),event('7',14500,'WRONG-ANSWER')
  ])};
  const score=publicScore(row,room,start+15000e3,true);
  assert.equal(score.cells.A.tries,1);assert.equal(score.cells.A.pending,2);assert.equal(score.cells.A.solved,true);assert.equal(score.penalty,1);assert.equal(score.solved,1);
  row.rules_json='{"compile_penalty":true,"penalty_minutes":10}';
  const penalized=publicScore(row,room,start+15000e3,true);assert.equal(penalized.cells.A.tries,2);assert.equal(penalized.penalty,11);
});

test('short contests freeze from start and a rank received after the real cutoff is not trusted',()=>{
  const start=Date.parse('2030-01-01T00:00:00Z'),room={starts_at:new Date(start).toISOString(),duration_seconds:1800,freeze_minutes:60};
  const before=roomTiming(room,start-1),during=roomTiming(room,start),after=roomTiming(room,start+1800e3);
  assert.equal(before.frozen,false);assert.equal(during.freezeAt,room.starts_at);assert.equal(during.frozen,true);assert.equal(after.frozen,false);
  assert.equal(frozenRank({freeze_rank:'1',freeze_rank_at:new Date(start+1000).toISOString()},room),'?');
  assert.equal(frozenRank({freeze_rank:'87',freeze_rank_at:new Date(start-1000).toISOString()},room),'87');
});

test('filtering future timestamps preserves already-visible practice results without counting them as contest solves',()=>{
  const start=Date.parse('2030-01-01T00:00:00Z'),room={starts_at:new Date(start).toISOString(),duration_seconds:7200};
  const history={started_at:room.starts_at,problems_json:'["A","B"]',rules_json:'{}',submissions_json:JSON.stringify([
    {id:'1',problem:'A',submittedAt:new Date(start+7300e3).toISOString(),verdict:'CORRECT',practice:true},
    {id:'2',problem:'B',submittedAt:new Date(start+9000e3).toISOString(),verdict:'CORRECT',practice:true}
  ])};
  const score=publicScore(history,room,start+8000e3,false);
  assert.equal(score.solved,0);assert.equal(score.cells.A.practiceSolved,true);assert.equal(score.cells.A.tries,1);assert.equal(score.cells.B.practiceSolved,false);assert.equal(score.submissions.length,1);
});

test('a locally ended participant cannot publish practice results before the shared room ends',()=>{
  const start=Date.parse('2030-01-01T00:00:00Z'),room={starts_at:new Date(start).toISOString(),duration_seconds:18000,freeze_minutes:60};
  const history={started_at:room.starts_at,problems_json:'["A","B"]',rules_json:'{}',submissions_json:JSON.stringify([
    {id:'1',problem:'A',submittedAt:new Date(start+600e3).toISOString(),verdict:'CORRECT',practice:false},
    {id:'2',problem:'B',submittedAt:new Date(start+1200e3).toISOString(),verdict:'CORRECT',practice:true}
  ])};
  const publicView=publicScore(history,room,start+1800e3,false,true);
  assert.equal(publicView.solved,1);assert.equal(publicView.cells.B.practiceSolved,false);assert.equal(publicView.cells.B.tries,0);assert.equal(publicView.submissions.length,1);
  const ownView=publicScore(history,room,start+1800e3,false);
  assert.equal(ownView.cells.B.practiceSolved,true);assert.equal(ownView.submissions.length,2);
  const ended=publicScore(history,room,start+18000e3,false);
  assert.equal(ended.cells.B.practiceSolved,true);
});

test('zero or absent freeze minutes never freezes, positive minutes use the configured end-relative cutoff',()=>{
  const start=Date.parse('2030-01-01T00:00:00Z'),room={starts_at:new Date(start).toISOString(),duration_seconds:7200};
  for(const extra of [{},{freeze_minutes:0}]) {const timing=roomTiming({...room,...extra},start+7100e3);assert.equal(timing.freezeMinutes,0);assert.equal(timing.freezeAt,null);assert.equal(timing.frozen,false);}
  const configured={...room,freeze_minutes:15};
  assert.equal(roomTiming(configured,start+6299e3).frozen,false);assert.equal(roomTiming(configured,start+6300e3).frozen,true);assert.equal(roomTiming(configured,start+7200e3).frozen,false);
});

test('graded freeze projections use native scores and OI latest versus IOI best without inventing accepted scores',()=>{
  const start=Date.parse('2030-01-01T00:00:00Z'),room={starts_at:new Date(start).toISOString(),duration_seconds:7200,freeze_minutes:30};
  const event=(id:string,problem:string,seconds:number,verdict:string,points?:number)=>({id,problem,submittedAt:new Date(start+seconds*1000).toISOString(),verdict,practice:false,...(points===undefined?{}:{points})});
  const row={mode:'vp',started_at:room.starts_at,problems_json:'["A","B","C"]',rules_json:'',submissions_json:JSON.stringify([
    event('1','A',100,'PARTIAL',40),event('2','A',200,'CORRECT',100),event('3','A',300,'WRONG-ANSWER',0),
    event('4','B',400,'CORRECT'),event('5','C',5500,'CORRECT',100)
  ])};
  row.rules_json=JSON.stringify({scoring:'OI',score_policy:'latest',problem_max_scores:{A:100,B:100,C:100}});
  const oi=publicScore(row,room,start+6000e3,true);assert.equal(oi.points,undefined);assert.equal(oi.solved,1);assert.equal(oi.cells.A.tries,3);assert.equal(oi.cells.A.solved,false);assert.equal(oi.cells.B.points,undefined);assert.equal(oi.penalty,0);
  assert.equal(oi.cells.C.pending,1);assert.equal(oi.cells.C.points,0);assert.equal(oi.submissions.at(-1)?.verdict,'FROZEN');assert.equal(oi.submissions.at(-1)?.points,undefined);
  for(const scoring of ['IOI','CF','WEEKLY']) {row.rules_json=JSON.stringify({scoring,score_policy:'best',problem_max_scores:{A:100}});const best=publicScore(row,room,start+6000e3,true);assert.equal(best.points,undefined);assert.equal(best.cells.A.solved,true);assert.equal(best.cells.A.tries,3);assert.equal(best.penalty,0);}
});

test('practice is attempts and acceptance only, even for a graded contest',()=>{
  const start=Date.parse('2030-01-01T00:00:00Z'),room={starts_at:new Date(start).toISOString(),duration_seconds:7200};
  const row={mode:'bt',problems_json:'["A"]',rules_json:'{"scoring":"IOI","score_policy":"best","problem_max_scores":{"A":100}}',submissions_json:JSON.stringify([
    {id:'1',problem:'A',submittedAt:new Date(start+1000).toISOString(),verdict:'PARTIAL',points:60,practice:true},
    {id:'2',problem:'A',submittedAt:new Date(start+2000).toISOString(),verdict:'CORRECT',points:100,practice:true},
    {id:'3',problem:'A',submittedAt:new Date(start+3000).toISOString(),verdict:'WRONG-ANSWER',points:0,practice:true}
  ])};
  const score=publicScore(row,room,start+4000,false);assert.equal(score.penalty,0);assert.equal(score.points,undefined);assert.equal(score.cells.A.points,undefined);assert.equal(score.cells.A.tries,3);assert.equal(score.cells.A.practiceSolved,true);assert.equal(score.cells.A.solveTime,undefined);
});

test('subtask IOI freeze retains the native aggregate instead of taking the maximum of whole submission scores',()=>{
  const start=Date.parse('2030-01-01T00:00:00Z'),room={starts_at:new Date(start).toISOString(),duration_seconds:7200,freeze_minutes:30};
  const history={mode:'vp',started_at:room.starts_at,problems_json:'["A"]',rules_json:'{"scoring":"IOI","score_policy":"subtasks"}',freeze_points:100,freeze_cells_json:'{"A":{"points":100,"maxPoints":100,"solved":true}}',submissions_json:JSON.stringify([
    {id:'1',problem:'A',submittedAt:new Date(start+1000).toISOString(),verdict:'PARTIAL',points:60,practice:false},
    {id:'2',problem:'A',submittedAt:new Date(start+2000).toISOString(),verdict:'PARTIAL',points:60,practice:false}
  ])};
  const result=publicScore(history,room,start+6000e3,true);assert.equal(result.points,100);assert.equal(result.cells.A.points,100);assert.equal(result.solved,1);
  const unknown=publicScore({...history,freeze_points:null,freeze_cells_json:null},room,start+6000e3,true);assert.equal(unknown.points,undefined);assert.equal(unknown.cells.A.points,undefined);
});
