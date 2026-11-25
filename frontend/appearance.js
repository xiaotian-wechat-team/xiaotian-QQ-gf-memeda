window.AppearanceTasks = (() => {
  const views = [['lprofile','左侧90°'],['l34','左45°'],['front','正面'],['r34','右45°'],['rprofile','右侧90°']];
  const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const labels = {queued:'等待开始',running:'正在生成',checking:'检查原图',face:'生成脸部五视图',body_front:'建立正面全身基准',body:'生成全身五视图',review:'等待检查',approved:'已确认候选',failed:'生成失败',interrupted:'生成已中断',cancelled:'已取消',cancelling:'正在取消'};
  let timer;
  function url(doc, id, name) { return `${API_BASE}/api/personalization/appearance/image?persona_id=${encodeURIComponent(doc.data.persona_id)}&task_id=${id}&name=${name}`; }
  async function render(root, doc, changed) {
    clearTimeout(timer);
    root.innerHTML = `<div class="appearance-intro"><p class="eyebrow">STANDARD ASSETS / 原图 SOP</p><h3>从原始照片开始</h3><p>提供清晰单人脸部与完整全身原图。系统依次制作脸部五视图、正面全身基准和全身五视图。</p><label class="appearance-upload">选择原始照片<input type="file" accept="image/jpeg,image/png,image/webp" multiple aria-label="原始照片"></label><p class="persona-hint">2–16张，单张最多12MB，总计最多64MB。人物身高体重可留空，不从照片猜数值。</p><div class="button-row"><button class="button" data-create-appearance>生成标准五视图</button><button class="button button-ghost" data-clear-candidate ${doc.candidate ? '' : 'disabled'}>清除候选选择</button></div><p class="persona-hint">当前草稿候选：${doc.candidate ? escape(doc.candidate.slice(0, 10)) : '尚未选择'}。生成和确认候选后，仍需保存草稿、预览并应用。</p><div class="appearance-error" role="alert"></div><div class="appearance-task-list">正在读取任务…</div></div>`;
    const error = root.querySelector('.appearance-error');
    root.querySelector('[data-clear-candidate]').onclick = () => { doc.candidate = null; changed(doc); render(root, doc, changed); };
    root.querySelector('[data-create-appearance]').onclick = async event => {
      const button = event.currentTarget, files = [...root.querySelector('input[type=file]').files];
      if (files.length < 2 || files.length > 16) { error.textContent = '请选择2至16张原图，包含清晰脸部和完整全身参考。'; return; }
      if (files.some(f => f.size > 12 * 1024 * 1024) || files.reduce((n,f) => n + f.size, 0) > 64 * 1024 * 1024) { error.textContent = '照片超出大小限制，请减少数量或调整原图大小。'; return; }
      error.textContent = '';
      await Personalization.run(doc, async () => {
        await Personalization.saveDraft(doc);
        const photos = await Promise.all(files.map(file => new Promise((resolve, reject) => {
          const reader = new FileReader(); reader.onload = () => resolve({name:file.name,data:String(reader.result).split(',')[1]}); reader.onerror = reject; reader.readAsDataURL(file);
        })));
        const task = await Personalization.post(doc, 'appearance/create', {revision:doc.data.draft.revision, photos});
        await Personalization.post(doc, 'appearance/start', {task_id:task.id});
      });
    };
    async function refreshTasks() {
      if (!root.isConnected) return;
      try {
        const result = await apiJSON(`/api/personalization/appearance?persona_id=${encodeURIComponent(doc.data.persona_id)}`);
        if (!root.isConnected) return;
        root.querySelector('.appearance-task-list').innerHTML = result.tasks.map(task => `<article class="appearance-task"><div class="card-header"><div><strong>候选 ${escape(task.id.slice(0,10))}</strong><p>${escape(labels[task.status] || task.status)}${task.stage && task.status === 'running' ? ` · ${escape(labels[task.stage])}` : ''}</p></div><span class="tag">${task.sources.length} 张原图</span></div>${task.error ? `<p class="appearance-error">${escape(task.error)}</p>` : ''}${task.completed.face ? `<figure class="appearance-sheet"><figcaption>脸部五视图 · 左侧 → 左45° → 正面 → 右45° → 右侧</figcaption><a href="${url(doc,task.id,'face/sheet.png')}" target="_blank" rel="noopener"><img src="${url(doc,task.id,'face/sheet.png')}" alt="脸部五视图总览"></a></figure><div class="appearance-views">${views.map(([view,label]) => `<figure><a href="${url(doc,task.id,`face/${view}.png`)}" target="_blank" rel="noopener"><img src="${url(doc,task.id,`face/${view}.png`)}" alt="脸部${label}"></a><figcaption>${label}</figcaption></figure>`).join('')}</div>` : ''}${task.completed.body ? `<figure class="appearance-sheet"><figcaption>全身五视图 · 检查头顶、双脚完整与身形比例</figcaption><a href="${url(doc,task.id,'body/sheet.png')}" target="_blank" rel="noopener"><img src="${url(doc,task.id,'body/sheet.png')}" alt="全身五视图总览"></a></figure><div class="appearance-views body-views">${views.map(([view,label]) => `<figure><a href="${url(doc,task.id,`body/${view}.png`)}" target="_blank" rel="noopener"><img src="${url(doc,task.id,`body/${view}.png`)}" alt="全身${label}"></a><figcaption>${label}</figcaption></figure>`).join('')}</div>` : ''}<div class="button-row">${['failed','interrupted','cancelled','queued'].includes(task.status) ? `<button class="button button-ghost" data-task-action="start" data-task="${task.id}">${task.status === 'queued' ? '开始生成' : '从失败阶段重试'}</button>` : ''}${['running','queued'].includes(task.status) ? `<button class="button button-ghost" data-task-action="cancel" data-task="${task.id}">取消任务</button>` : ''}${['review','approved'].includes(task.status) ? `<button class="button" data-task-action="approve" data-task="${task.id}">${doc.candidate === task.id ? '已选入草稿' : '确认这套标准素材'}</button>` : ''}</div>${['review','approved'].includes(task.status) ? '<p class="persona-hint">自动筛选不代表身份和角度正确。请检查两张总览与十张单图后确认。</p>' : ''}</article>`).join('') || '<p class="persona-hint">还没有外貌生成任务。上传原图即可开始。</p>';
        root.querySelectorAll('[data-task-action]').forEach(button => button.onclick = async () => {
          await Personalization.run(doc, async () => {
            await Personalization.post(doc, `appearance/${button.dataset.taskAction}`, {task_id:button.dataset.task});
            if (button.dataset.taskAction === 'approve') { doc.candidate = button.dataset.task; changed(doc); }
          });
        });
        if (result.tasks.some(t => ['queued','running','cancelling'].includes(t.status))) timer = setTimeout(refreshTasks, 2000);
      } catch (e) { error.textContent = e.message; }
    }
    await refreshTasks();
  }
  return {render};
})();
if (activeView === 'persona') renderPersona();
