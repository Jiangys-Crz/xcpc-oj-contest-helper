/** Normal room tabs use their own navigation; full print views keep the app shell. */
export function isRoomLayoutPath(pathname:string):boolean {
  return /^\/vp\/[^/]+(?:\/(?:rank|mysubmit|print))?\/?$/.test(pathname);
}
