import {request} from './client';
import {Note} from '../types/domain';
const base='/notes';
export const getNotes=()=>request<Note[]>(base);
export const createNote=(data:{run_id:number;title:string;interpretation:string;proposal:string})=>request<Note>(base,{method:'POST',body:JSON.stringify(data)});
export const reviewNote=(id:number,data:{status:string;review_note:string;expected_lock:number})=>request<Note>(`${base}/${id}/review`,{method:'PATCH',body:JSON.stringify(data)});
export const reviseNote=(id:number,data:{title:string;interpretation:string;proposal:string;expected_lock:number})=>request<Note>(`${base}/${id}/revision`,{method:'POST',body:JSON.stringify(data)});
export const withdrawNote=(id:number,data:{expected_lock:number;reason:string})=>request<Note>(`${base}/${id}/withdrawal`,{method:'POST',body:JSON.stringify(data)});
