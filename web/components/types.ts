export interface User { id:string; useraccount:string; username:string; realname:string; numberid:string; email:string; role:string }
export interface Cell { tries:number; pending:number; solved:boolean; practiceSolved:boolean; solveTime?:number }
export interface Submission { id:string; problem:string; submittedAt:string; verdict:string; practice:boolean; url?:string }
export interface History { id:string; contestName:string; contestUrl:string; mode:'vp'|'live'|'bt'; rank:string; solved:number; penalty:number; problems:string[]; cells:Record<string,Cell>; startedAt:string|null; endedAt:string|null; participantLabel:string; participantType?:'individual'|'team'; updatedAt:string; status?:string; submissions?:Submission[] }
export interface Team { id:string; nameZh:string; nameEn:string; captainId:string; members:User[]; createdAt:string }
export interface Participant { id:string; type:'individual'|'team'; userId?:string; teamId?:string; label:string; captainId:string; members:User[] }
export interface Room { id:string; name:string; contestUrl:string; startsAt:string; createdBy:string; createdAt:string; participants:Participant[]; canManage:boolean; participation?:Participant|null; serverNow:string }
export interface Notice { id:string; kind:string; title:string; message:string; status:string; createdAt:string; teamId?:string; roomId?:string; canRespond:boolean }
export interface Board { room:Room; rows:{ participantId:string; label:string; rank:string; solved:number; penalty:number; cells:Record<string,Cell>; historyId?:string; lastSyncedAt?:string; status?:string }[]; problems:string[]; serverNow:string }
export interface PrintJob { id:string; roomId:string; participantLabel:string; filename:string; language:string; createdAt:string; code?:string }
