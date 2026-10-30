import { test } from 'node:test';
import assert from 'node:assert/strict';
import { roomTiming, publicScore, frozenRank } from '../lib/scoreboard';

test('frozen public scoring excludes compiler errors by default and never turns infrastructure failures into WA',()=>{
  const start=Date.parse('2030-01-01T00:00:00Z'),room={starts_at:new Date(start).toISOString(),duration_seconds:18000};
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
  const start=Date.parse('2030-01-01T00:00:00Z'),room={starts_at:new Date(start).toISOString(),duration_seconds:1800};
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
  const start=Date.parse('2030-01-01T00:00:00Z'),room={starts_at:new Date(start).toISOString(),duration_seconds:18000};
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
