import { useEffect, useState } from 'react';
import { Check, ChevronRight, Clock3, ListTodo, LoaderCircle, MessageCircle, Wrench, X } from 'lucide-react';
import { elapsedMilliseconds, eventLabel, formatDuration, formatLocalTime, publicEvents, stageLabel, type RunDetails, type RunQuestion } from './chat-features';
import { effortLabels } from './model-catalog';
import './activity-ui.css';

export function RunActivity({ run, hasReply }: { run?: RunDetails | null; hasReply?: boolean }) {
  const [now, setNow] = useState(Date.now);
  const [fallbackStart] = useState(Date.now);
  useEffect(() => { const timer = window.setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(timer); }, []);
  return <div className="tm-aui-run-activity" role="status" aria-live="polite">
    <span className="sc-live-dot" aria-hidden="true" />
    <span>{run?.stage ? stageLabel(run.stage) : hasReply ? '正在继续处理…' : '正在思考…'}</span>
    <span className="tm-aui-run-elapsed">{run?.started_at ? `已用时 ${formatDuration(elapsedMilliseconds(run, now))}` : `已等待 ${formatDuration(now - fallbackStart)}`}</span>
  </div>;
}
export function RunEvents({ run, active = false }: { run?: RunDetails | null; active?: boolean }) {
  const events = publicEvents(run?.events);
  if (!events.length) return null;
  return <details className="sc-run-events"><summary><ChevronRight size={13} aria-hidden="true" /><span>{active ? '查看当前活动' : '处理记录'}</span><small>{events.length} 项</small></summary>
    <ol>{events.map((event, index) => <li data-status={event.status} key={event.id || `${event.seq ?? index}-${event.item_id || ''}`}>
      <span className="sc-event-marker" aria-hidden="true">{event.status === 'error' || event.status === 'failed' ? <X size={13} /> : event.status === 'running' ? <LoaderCircle size={13} className="tm-aui-spin" /> : event.type === 'plan' ? <ListTodo size={13} /> : event.type === 'tool' ? <Wrench size={13} /> : <Check size={13} />}</span>
      <div><strong title={eventLabel(event)}>{eventLabel(event)}</strong><small>{[event.status ? stageLabel(event.status) : '', formatLocalTime(event.created_at || event.started_at)].filter(Boolean).join(' · ')}</small></div>
      {event.text && <pre>{event.text}</pre>}
      {!event.text && typeof event.command === 'string' && <pre>{event.command}</pre>}
      {Array.isArray(event.plan) && <ol className="sc-plan-steps">{event.plan.map((value, stepIndex) => {
        const step = value as { step?: string; status?: string };
        return <li key={stepIndex}><span aria-hidden="true">{step.status === 'completed' || step.status === 'complete' ? '✓' : '○'}</span><span>{step.step || '计划步骤'}</span>{step.status && <small>{stageLabel(step.status)}</small>}</li>;
      })}</ol>}
    </li>)}</ol></details>;
}
export function RunMetadata({ run }: { run?: RunDetails }) {
  if (!run) return null;
  const context = run.context && typeof run.context === 'object' ? run.context as Record<string, unknown> : {};
  const usageLabels: Record<string, string> = { input_tokens: '输入', output_tokens: '输出', cached_input_tokens: '缓存输入', reasoning_output_tokens: '推理输出', total_tokens: '合计' };
  const usage = Object.entries(usageLabels).flatMap(([key, label]) => typeof run.usage?.[key] === 'number'
    ? [{ key, label, value: (run.usage[key] as number).toLocaleString() }] : []);
  const contextRows: [string, string][] = [];
  if (typeof context.native_thread_resumed === 'boolean') contextRows.push(['对话', context.native_thread_resumed ? '继续已有上下文' : '从历史消息恢复上下文']);
  if (typeof context.available_messages === 'number') contextRows.push(['历史消息', `${context.available_messages.toLocaleString()} 条`]);
  if (typeof context.image_replay_count === 'number' && context.image_replay_count > 0) contextRows.push(['本轮图片', `${context.image_replay_count} 张`]);
  if (typeof context.context_tokens === 'number') contextRows.push(['上下文用量', `${context.context_tokens.toLocaleString()} tokens${typeof context.context_window === 'number' ? ` / ${context.context_window.toLocaleString()}` : ''}`]);
  if (typeof context.compaction_status === 'string') contextRows.push(['上下文整理', stageLabel(context.compaction_status)]);
  return <details className="sc-run-metadata"><summary aria-label="回复详情"><Clock3 size={12} aria-hidden="true" /><span>{run.started_at || typeof run.duration_ms === 'number' ? `用时 ${formatDuration(elapsedMilliseconds(run))}` : '回复详情'}</span><ChevronRight size={12} aria-hidden="true" /></summary>
    <div className="sc-run-detail-body"><dl><dt>模型</dt><dd>{run.model || '未记录'}{run.reasoning_effort && <span className="sc-run-effort">{effortLabels[run.reasoning_effort] || run.reasoning_effort}</span>}</dd>
      <dt>开始</dt><dd>{formatLocalTime(run.started_at) || '未记录'}</dd><dt>完成</dt><dd>{formatLocalTime(run.completed_at) || '进行中'}</dd>
      {run.status && <><dt>状态</dt><dd>{stageLabel(run.status)}</dd></>}</dl>
      {usage.length > 0 && <section className="sc-run-usage" aria-label="本轮用量"><h4>用量 <small>tokens</small></h4><dl>{usage.map(item => <div key={item.key}><dt>{item.label}</dt><dd>{item.value}</dd></div>)}</dl></section>}
      {contextRows.length > 0 && <section className="sc-run-context"><h4>使用的上下文</h4><dl>{contextRows.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl></section>}
      {typeof run.context === 'string' && run.context && <p className="sc-run-context-note">{run.context}</p>}
      {(context.history_truncated === true || context.image_replay_truncated === true) && <p className="sc-run-context-note">历史材料较多，本轮使用了其中最近的部分。</p>}
      <RunEvents run={run} />
    </div>
  </details>;
}

export function QuestionPanel({ requests, onAnswer, busy }: { requests: RunQuestion[]; onAnswer: (requestId: string, answers: Record<string, string[]>) => Promise<void>; busy: boolean }) {
  const [answers, setAnswers] = useState<Record<string, string>>({});
  return <div className="sc-question-list">{requests.map((request, requestIndex) => {
    const requestId = request.request_id || request.id || '';
    const questions = request.questions || [request];
    return <form className="sc-question-panel" key={`${requestId}-${requestIndex}`} onSubmit={(event) => {
      event.preventDefault();
      const values: Record<string, string[]> = {};
      for (const [index, question] of questions.entries()) {
        const id = question.id || String(index); const value = answers[`${requestId}:${id}`]?.trim();
        if (!value) return; values[id] = [value];
      }
      void onAnswer(requestId, values);
    }}>
      <header className="sc-question-heading"><MessageCircle size={16} aria-hidden="true" /><strong>需要你的回答</strong><span>{questions.length} 个问题</span></header>
      {questions.map((question, index) => {
        const id = question.id || String(index); const key = `${requestId}:${id}`;
        return <fieldset key={id} disabled={busy}><legend>{question.header ? `${question.header} · ` : ''}{question.question || '请选择或输入回答'}</legend>
          {question.options?.map((option) => <label className="sc-question-option" key={option.label}>
            <input type="radio" name={key} value={option.label} checked={answers[key] === option.label} onChange={() => setAnswers((previous) => ({ ...previous, [key]: option.label }))} />
            <span>{option.label}{option.description && <small>{option.description}</small>}</span>
          </label>)}
          <input type={question.isSecret ? 'password' : 'text'} aria-label={`回答：${question.header || question.question || id}`} placeholder="输入或补充你的回答" value={answers[key] || ''} onChange={(event) => setAnswers((previous) => ({ ...previous, [key]: event.target.value }))} />
        </fieldset>;
      })}
      <footer><span>选择选项，也可以直接填写回答</span><button type="submit" disabled={busy || !requestId || questions.some((question, index) => !answers[`${requestId}:${question.id || String(index)}`]?.trim())}>提交回答</button></footer>
    </form>;
  })}</div>;
}
