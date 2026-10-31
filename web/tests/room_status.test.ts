import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {Countdown} from '../components/shared';
import {roomPhase,roomPhaseLabel} from '../components/roomStatus';

const startsAt='2026-10-31T01:00:00.000Z',endsAt='2026-10-31T06:00:00.000Z';
const schedule={startsAt,endsAt,durationSeconds:60};
const start=Date.parse(startsAt),end=Date.parse(endsAt);

test('room status respects exact start and authoritative end boundaries',()=>{
  for(const [at,expected] of [[start-1,'upcoming'],[start,'ongoing'],[start+1,'ongoing'],[end-1,'ongoing'],[end,'ended'],[end+1,'ended']] as const)assert.equal(roomPhase(schedule,at),expected);
  assert.equal(roomPhase(schedule,start+60001),'ongoing','Explicit endsAt takes precedence over duration');
});

test('an explicitly supplied duration is a fallback and missing duration never invents an end',()=>{
  assert.equal(roomPhase({startsAt,durationSeconds:120},start+119999),'ongoing');
  assert.equal(roomPhase({startsAt,durationSeconds:120},start+120000),'ended');
  for(const durationSeconds of [undefined,0,-1,NaN])assert.equal(roomPhase({startsAt,durationSeconds},end+86400000),'ongoing');
});

test('changed schedules are recalculated rather than retaining an obsolete phase',()=>{
  const at=start+3600000;
  assert.equal(roomPhase(schedule,at),'ongoing');
  assert.equal(roomPhase({...schedule,startsAt:new Date(at+60000).toISOString()},at),'upcoming');
  assert.equal(roomPhase({...schedule,endsAt:new Date(at).toISOString()},at),'ended');
  assert.deepEqual(schedule,{startsAt,endsAt,durationSeconds:60});
});

test('all three list phases have Chinese and corresponding English labels',()=>{
  assert.deepEqual(['upcoming','ongoing','ended'].map(phase=>roomPhaseLabel(phase as 'upcoming'|'ongoing'|'ended')),['即将开始','进行中','已结束']);
  assert.deepEqual(['upcoming','ongoing','ended'].map(phase=>roomPhaseLabel(phase as 'upcoming'|'ongoing'|'ended',true)),['Upcoming','In progress','Ended']);
});

test('room countdown uses server time immediately despite a different browser clock',()=>{
  const original=Date.now;Date.now=()=>end+86400000;
  try{
    const render=(serverNow:string,english=true)=>renderToStaticMarkup(createElement(Countdown,{...schedule,serverNow,english}));
    const upcoming=render(new Date(start-65000).toISOString());
    assert(upcoming.includes('Starts in'));assert(upcoming.includes('00:01:05'));
    assert(render(startsAt).includes('In progress'));
    assert(render(endsAt).includes('Ended'));
    assert(render(endsAt,false).includes('已结束'));
    assert(!render(endsAt).includes('Start time reached'));
  }finally{Date.now=original;}
});
