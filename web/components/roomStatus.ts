export type RoomPhase='upcoming'|'ongoing'|'ended';
export type RoomSchedule={startsAt:string;endsAt?:string;durationSeconds?:number};

/** Prefer the server's end; use only an explicitly supplied duration otherwise. */
export function roomPhase(schedule:RoomSchedule,now:number):RoomPhase {
  const start=Date.parse(schedule.startsAt),end=Date.parse(schedule.endsAt||'');
  const duration=schedule.durationSeconds;
  const durationEnd=Number.isFinite(start)&&typeof duration==='number'&&Number.isFinite(duration)&&duration>0?start+duration*1000:NaN;
  const finish=Number.isFinite(end)?end:durationEnd;
  if(!Number.isFinite(start)||now<start)return 'upcoming';
  return Number.isFinite(finish)&&now>=finish?'ended':'ongoing';
}

export function roomPhaseLabel(phase:RoomPhase,english=false):string {
  return english?{upcoming:'Upcoming',ongoing:'In progress',ended:'Ended'}[phase]:{upcoming:'即将开始',ongoing:'进行中',ended:'已结束'}[phase];
}
