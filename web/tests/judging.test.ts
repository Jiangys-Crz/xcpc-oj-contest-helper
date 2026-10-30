import {test} from 'node:test';
import assert from 'node:assert/strict';
import {judgingState} from '../components/judging';

test('submission colours distinguish judge failures from unresolved and non-attempt results',()=>{
  for(const verdict of ['CORRECT','Accepted','OK','PRETESTS-PASSED'])assert.equal(judgingState(verdict),'accepted',verdict);
  for(const verdict of ['WRONG-ANSWER','TIME_LIMIT_EXCEEDED','IDLENESS-LIMIT','COMPILER-ERROR','PARTIAL'])assert.equal(judgingState(verdict),'rejected',verdict);
  for(const verdict of ['PENDING','FROZEN','UNAVAILABLE','HIDDEN','SYSTEM-ERROR','CANCELLED','INPUT_PREPARATION_CRASHED','unknown future result'])assert.equal(judgingState(verdict),'pending',verdict);
  for(const verdict of ['IGNORED','HACK-SUCCESSFUL','HACK-UNSUCCESSFUL','TOO-LATE'])assert.equal(judgingState(verdict),'ignored',verdict);
});
