<script setup lang="ts">
import {computed,reactive,ref} from 'vue';
import {useRoute} from 'vue-router';
import {Download,Check,PenLine,Undo2} from 'lucide-vue-next';
import {useResearchData} from '../hooks/useResearch';
import {useAuth} from '../stores/auth';
import {useAction} from '../hooks/useAction';
import {createNote,reviewNote,reviseNote,withdrawNote} from '../api/notes';
import {download} from '../api/client';
import Notice from '../components/common/Notice.vue';
import RunSummary from '../components/RunSummary.vue';
import {ReviewStatus} from '../constants/enums';
import {time} from '../utils/format';
import type {Note} from '../types/domain';

const s=useResearchData(),auth=useAuth(),a=useAction(),route=useRoute();
const selected=ref(Number(route.query.run)||0);
const title=ref(''),interpretation=ref(''),proposal=ref('');
// 每条建议链一份表单状态，键为 note_id
const reviews=reactive<Record<number,string>>({});
const withdrawReasons=reactive<Record<number,string>>({});
const editing=reactive<Record<number,boolean>>({});
const drafts=reactive<Record<number,{title:string;interpretation:string;proposal:string}>>({});

const runs=computed(()=>s.runs.filter(r=>r.status==='completed'));
const run=computed(()=>runs.value.find(r=>r.id===selected.value)||runs.value[0]);
const canWrite=computed(()=>auth.user?.role!=='viewer');

interface NoteChain{noteId:number;current:Note;history:Note[]}
// 按 note_id 聚合版本链：current 为最新版本，history 为按版本倒序的历次内容与批复
const chains=computed<NoteChain[]>(()=>{
  const map=new Map<number,Note[]>();
  for(const n of s.notes.filter(n=>n.run_id===run.value?.id)){
    if(!map.has(n.note_id))map.set(n.note_id,[]);
    map.get(n.note_id)!.push(n);
  }
  return [...map.entries()].map(([noteId,list])=>{
    const sorted=[...list].sort((x,y)=>y.version-x.version);
    return {noteId,current:sorted[0],history:sorted.slice(1)};
  }).sort((x,y)=>y.noteId-x.noteId);
});

function isOwner(n:Note){return auth.user?.role==='admin'||n.owner===auth.user?.name}
function startEdit(c:NoteChain){
  editing[c.noteId]=true;
  drafts[c.noteId]={title:c.current.title,interpretation:c.current.interpretation,proposal:c.current.proposal};
}
function cancelEdit(noteId:number){editing[noteId]=false}

function submit(){
  a.execute(async()=>{
    await createNote({run_id:run.value!.id,title:title.value,interpretation:interpretation.value,proposal:proposal.value});
    title.value='';interpretation.value='';proposal.value='';
    await s.refresh();
  });
}
function review(n:Note,status:'approved'|'rejected'){
  a.execute(async()=>{
    // 回传当前行 lock_version；后端据此拒绝旧页面覆盖
    await reviewNote(n.id,{status,review_note:reviews[n.note_id]||'',expected_lock:n.lock_version});
    reviews[n.note_id]='';
    await s.refresh();
  });
}
function revise(c:NoteChain){
  const d=drafts[c.noteId];
  a.execute(async()=>{
    await reviseNote(c.current.id,{...d,expected_lock:c.current.lock_version});
    editing[c.noteId]=false;
    await s.refresh();
  });
}
function withdraw(n:Note){
  a.execute(async()=>{
    await withdrawNote(n.id,{expected_lock:n.lock_version,reason:withdrawReasons[n.note_id]||''});
    withdrawReasons[n.note_id]='';
    await s.refresh();
  });
}
function exportRun(id:number){a.execute(async()=>{await download(`/runs/${id}/export`,`composition-run-${id}.csv`)})}
</script>

<template>
  <div class="page">
    <div class="page-title">
      <div>
        <p class="eyebrow">RESAMPLE REVIEW / 05</p>
        <h1>复配补样审阅<span>同一建议可修订再审批、可撤回；每次写入带版本锁，旧页面操作会被拒绝。</span></h1>
      </div>
    </div>
    <Notice :message="s.error||a.error.value" @retry="s.refresh"/>
    <label>关联组成研究
      <select aria-label="关联组成研究" :value="run?.id||''" @change="selected=Number(($event.target as HTMLSelectElement).value)">
        <option v-for="r in runs" :key="r.id" :value="r.id">#{{r.id}} / {{r.name}}</option>
      </select>
    </label>
    <RunSummary v-if="run" :run="run"/>
    <div class="notes-grid">
      <section>
        <h2>提交复配补样建议</h2>
        <form v-if="canWrite" @submit.prevent="submit">
          <label>建议标题<input aria-label="建议标题" v-model="title" minlength="2" required placeholder="例如：三工艺独立补样复核"/></label>
          <label>组成差异与边界解释<textarea aria-label="证据解释" v-model="interpretation" minlength="8" required placeholder="说明δ、CLR组差与离散度解释边界。"/></label>
          <label>独立补样方案<textarea aria-label="补样方案" v-model="proposal" minlength="8" required placeholder="记录各工艺同规模独立补样和检出限复核。"/></label>
          <button class="primary" :disabled="a.busy.value||!run">提交材料负责人</button>
        </form>
        <p v-else class="role-note">阅读者可查阅补样建议与历次版本，获批后可下载组成CSV。</p>
      </section>

      <section class="note-list">
        <h2>审阅档案（按建议版本链归档）</h2>
        <article v-for="c in chains" :key="c.noteId">
          <div class="note-title">
            <span class="eyebrow">NOTE-{{c.noteId}} / RUN-{{c.current.run_id}} · 最新 v{{c.current.version}}</span>
            <span :class="['review-badge',c.current.status]">{{ReviewStatus[c.current.status]}}</span>
          </div>

          <!-- 最新版本内容；修订中切换为编辑表单 -->
          <template v-if="!editing[c.noteId]">
            <h3>{{c.current.title}}</h3>
            <p><b>解释</b>{{c.current.interpretation}}</p>
            <p><b>验证</b>{{c.current.proposal}}</p>
            <small>{{c.current.owner}} · 提交于 {{time(c.current.created_at)}} · 版本锁 {{c.current.lock_version}}</small>
          </template>
          <form v-else class="approval-form" @submit.prevent="revise(c)">
            <label>修订建议标题<input aria-label="修订标题" v-model="drafts[c.noteId].title" minlength="2" required/></label>
            <label>修订组成差异与边界解释<textarea aria-label="修订解释" v-model="drafts[c.noteId].interpretation" minlength="8" required/></label>
            <label>修订独立补样方案<textarea aria-label="修订方案" v-model="drafts[c.noteId].proposal" minlength="8" required/></label>
            <div>
              <button class="primary" type="submit" :disabled="a.busy.value"><PenLine :size="14"/>提交修订并重新送审</button>
              <button class="outline" type="button" :disabled="a.busy.value" @click="cancelEdit(c.noteId)">放弃修订</button>
            </div>
            <p class="caption">修订后当前版本归档为“已被修订替代”，原内容与批复保留；新版本重新进入待审阅，获批前不放行导出。</p>
          </form>

          <!-- 最新版本的批复 / 撤回记录 -->
          <div v-if="c.current.status==='approved'||c.current.status==='rejected'||c.current.status==='withdrawn'" class="review-note">
            <b>{{c.current.status==='withdrawn'?'撤回责任人':'批复人'}}：{{c.current.reviewer}} · {{time(c.current.reviewed_at)}}</b>
            <p>{{c.current.status==='withdrawn'?'撤回原因：':''}}{{c.current.review_note}}</p>
          </div>

          <!-- 待审阅：材料负责人审批 -->
          <div v-if="c.current.status==='submitted'&&auth.user?.role==='admin'&&!editing[c.noteId]" class="approval-form">
            <label>审阅意见<textarea :aria-label="'审阅意见 note '+c.noteId" v-model="reviews[c.noteId]" minlength="8" placeholder="至少8字，说明零替换/离散度假设和批准范围。"/></label>
            <div>
              <button class="primary" :disabled="a.busy.value||(reviews[c.noteId]||'').length<8" @click="review(c.current,'approved')"><Check :size="14"/>批准研究归档</button>
              <button class="outline" :disabled="a.busy.value||(reviews[c.noteId]||'').length<8" @click="review(c.current,'rejected')">退回补样</button>
            </div>
          </div>

          <!-- 操作区：修订 / 撤回 / 导出，均基于最新版本的 lock_version -->
          <div v-if="canWrite&&!editing[c.noteId]" class="note-actions">
            <button v-if="['submitted','approved','rejected'].includes(c.current.status)&&isOwner(c.current)" class="text-link" :disabled="a.busy.value" @click="startEdit(c)"><PenLine :size="14"/>修订（生成新版本重新审批）</button>
            <button v-if="['submitted','approved'].includes(c.current.status)&&isOwner(c.current)" class="text-link danger-link" :disabled="a.busy.value||(withdrawReasons[c.noteId]||'').length<8" @click="withdraw(c.current)"><Undo2 :size="14"/>撤回并停止放行</button>
            <label v-if="['submitted','approved'].includes(c.current.status)&&isOwner(c.current)" class="withdraw-input">撤回原因（至少8字）<input :aria-label="'撤回原因 '+c.noteId" v-model="withdrawReasons[c.noteId]" minlength="8" placeholder="例如：补样方案分组规模设定有误，需撤回重写。"/></label>
          </div>
          <button v-if="c.current.status==='approved'" class="text-link" @click="exportRun(c.current.run_id)"><Download :size="14"/>下载获批CSV（抬头标注 v{{c.current.version}} 批复）</button>

          <!-- 历次版本与批复，完整保留可读 -->
          <details v-if="c.history.length" class="version-history">
            <summary>历次版本与批复（{{c.history.length}}）</summary>
            <div v-for="h in c.history" :key="h.id" class="history-row">
              <div class="note-title">
                <span class="eyebrow">v{{h.version}} · {{time(h.created_at)}}</span>
                <span :class="['review-badge',h.status]">{{ReviewStatus[h.status]}}</span>
              </div>
              <h3>{{h.title}}</h3>
              <p><b>解释</b>{{h.interpretation}}</p>
              <p><b>验证</b>{{h.proposal}}</p>
              <small>{{h.owner}} 提交<template v-if="h.revised_at"> · {{h.revised_by}} 于 {{time(h.revised_at)}} 修订替代</template></small>
              <div v-if="h.reviewer" class="review-note">
                <b>{{h.status==='withdrawn'?'撤回责任人':'批复人'}}：{{h.reviewer}} · {{time(h.reviewed_at)}}</b>
                <p>{{h.review_note}}</p>
              </div>
            </div>
          </details>
        </article>
        <p v-if="!chains.length" class="empty">还没有补样建议。研究员提交后，由材料负责人审阅。</p>
      </section>
    </div>
  </div>
</template>
