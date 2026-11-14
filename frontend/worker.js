const workerId = location.pathname.split('/').filter(Boolean)[1];
const $ = (selector) => document.querySelector(selector);
const esc = (value) => String(value).replace(/[&<>"']/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]));
let currentWorker;
let pendingAction;
let toastTimer;

function announce(message) {
  const toast = $('#toast');
  toast.textContent = message;
  toast.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { toast.hidden = true; }, 3000);
}

function requestLabel(type) {
  return type === 'dessert' || type === 'water' ? type : null;
}

function taskTitle(task) {
  if (task.type === 'fulfill_request') {
    const requestType = requestLabel(task.request_type);
    return requestType ? `Bring ${requestType} to Table 1` : 'Guest request at Table 1';
  }
  return 'Check in at Table 1';
}

function taskButtons(task, buttons) {
  return buttons.map(({ action, label, pendingLabel }) => {
    const key = `task-${task.id}-${action}`;
    const style = action === 'decline' ? 'button button-outline' : 'button button-primary';
    return `<button class="${style}" type="button" data-worker-action="${action}" data-task="${esc(task.id)}" data-task-type="${esc(task.type)}" data-action-key="${esc(key)}" data-pending-label="${esc(pendingLabel)}">${esc(label)}</button>`;
  }).join('');
}

function openRequestStatus(state) {
  const requests = state.requests.filter((request) => request.status === 'open' && request.table_id === '1');
  if (!requests.length) return '';
  const summary = requests.map((request) => {
    const type = requestLabel(request.type);
    if (!type) return null;
    const task = state.tasks.find((item) => item.request_id === request.id);
    const assignment = task?.status === 'offered'
      ? `offered to Worker ${task.offered_to}`
      : task?.status === 'assigned'
        ? `Worker ${task.worker_id} is handling it`
        : task?.status === 'pending'
          ? 'waiting for an available teammate'
          : 'waiting for a teammate';
    return `${type[0].toUpperCase() + type.slice(1)} · ${assignment}`;
  }).filter(Boolean);
  return summary.length ? `<p class="open-request-status">${summary.map(esc).join('<br>')}</p>` : '';
}

function render(state) {
  currentWorker = state.workers.find((worker) => worker.id === workerId);
  if (!currentWorker) {
    $('#worker-title').textContent = 'Unknown worker';
    $('#availability-button').hidden = true;
    $('#availability-button').disabled = true;
    $('#my-task').innerHTML = '<p class="idle-state">This worker is not available.</p>';
    return;
  }

  document.title = `Worker ${workerId} · ServiceMind`;
  $('#worker-title').textContent = `Hi, ${currentWorker.name}.`;
  document.querySelectorAll('.view-switch a').forEach((link) => {
    link.removeAttribute('aria-current');
    if (link.getAttribute('href') === `/worker/${workerId}`) link.setAttribute('aria-current', 'page');
  });
  const availability = currentWorker.availability === 'available' ? 'Available' : 'Busy';
  const nextAvailability = currentWorker.availability === 'available' ? 'busy' : 'available';
  $('#availability-button').textContent = `${availability} · Mark ${nextAvailability}`;
  $('#availability-button').setAttribute('aria-label', `Current status: ${availability}. Mark ${nextAvailability}.`);
  $('#availability-button').hidden = false;

  const offered = state.tasks.find((task) => task.status === 'offered' && task.offered_to === workerId);
  const assigned = state.tasks.find((task) => task.status === 'assigned' && task.worker_id === workerId);
  const task = offered || assigned;
  if (!task) {
    $('#my-task').innerHTML = `<p class="idle-state">${currentWorker.availability === 'available' ? 'Waiting for a task' : 'No task assigned'}</p>`;
  } else if (offered) {
    const isCheck = offered.type === 'check_table';
    $('#my-task').innerHTML = `
      <p class="task-eyebrow">${isCheck ? 'Table 1 · Check-in' : 'Table 1 · Guest request'}</p>
      <h2>${esc(taskTitle(offered))}</h2>
      ${isCheck && offered.reason ? `<p class="task-reason">${esc(offered.reason)}</p>` : ''}
      <div class="task-actions">${taskButtons(offered, [
        { action: 'accept', label: isCheck ? 'Accept check-in' : 'Accept delivery', pendingLabel: 'Accepting…' },
        { action: 'decline', label: 'Decline', pendingLabel: 'Declining…' },
      ])}</div>`;
  } else {
    const isRequest = assigned.type === 'fulfill_request';
    const doneLabel = isRequest ? 'Mark delivered' : 'Finish check';
    const donePending = isRequest ? 'Marking delivered…' : 'Finishing check…';
    const doneMessage = isRequest ? 'Marked delivered.' : 'Table 1 check finished.';
    const secondaryActions = !isRequest ? `
      <div class="guest-actions" aria-label="Guest actions">
        <button class="button button-soft" type="button" data-worker-action="request-dessert" data-action-key="request-dessert" data-request="dessert" data-pending-label="Sending…">Dessert</button>
        <button class="button button-soft" type="button" data-worker-action="request-water" data-action-key="request-water" data-request="water" data-pending-label="Sending…">Water</button>
        <button class="button button-not-ready" type="button" data-worker-action="not-ready" data-action-key="not-ready" data-pending-label="Sending…">Not ready</button>
      </div>` : '';
    $('#my-task').innerHTML = `
      <p class="task-eyebrow">In progress</p>
      <h2>You're handling Table 1</h2>
      <p class="assigned-task-title">${esc(taskTitle(assigned))}</p>
      ${!isRequest ? openRequestStatus(state) : ''}
      <div class="task-actions">${taskButtons(assigned, [{ action: 'complete', label: doneLabel, pendingLabel: donePending }])}</div>
      ${secondaryActions}`;
    const complete = $('#my-task [data-worker-action="complete"]');
    complete.dataset.confirmation = doneMessage;
  }

  $('#availability-button').disabled = Boolean(assigned && currentWorker.availability === 'busy');
  applyPendingAction();
}

function applyPendingAction() {
  if (!pendingAction) return;
  document.querySelectorAll('[data-worker-action]').forEach((button) => {
    button.disabled = true;
    if (button.dataset.actionKey === pendingAction.key) button.textContent = pendingAction.label;
  });
}

async function refresh() {
  try {
    const response = await fetch('/api/state');
    if (!response.ok) throw new Error('State unavailable');
    render(await response.json());
    $('#connection-label').hidden = true;
  } catch {
    $('#connection-label').hidden = false;
  }
}

async function post(url, body, action) {
  if (pendingAction) return;
  pendingAction = action;
  applyPendingAction();
  try {
    const response = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {}),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail || 'Action unavailable.');
    pendingAction = null;
    await refresh();
    announce(action.confirmation);
  } catch (error) {
    pendingAction = null;
    await refresh();
    announce(error.message || 'ServiceMind is not connected.');
  }
}

document.addEventListener('click', (event) => {
  const button = event.target.closest('button[data-worker-action]');
  if (!button || pendingAction) return;
  const action = button.dataset.workerAction;
  const pendingLabel = button.dataset.pendingLabel || 'Working…';
  if (action === 'availability') {
    if (!currentWorker) {
      announce('Your shift is still loading. Try again in a moment.');
      return;
    }
    const availability = currentWorker.availability === 'available' ? 'busy' : 'available';
    post(`/api/workers/${encodeURIComponent(workerId)}/availability`, { availability }, {
      key: 'availability', label: pendingLabel, confirmation: `Marked ${availability}.`,
    });
    return;
  }
  const taskId = button.dataset.task;
  if (action === 'accept' || action === 'decline' || action === 'complete') {
    const taskType = button.dataset.taskType;
    const confirmation = action === 'accept'
      ? "You're handling Table 1."
      : action === 'decline'
        ? taskType === 'fulfill_request' ? 'Delivery declined.' : 'Check-in declined.'
        : button.dataset.confirmation || 'Task complete.';
    post(`/api/tasks/${encodeURIComponent(taskId)}/${action}`, { worker_id: workerId }, {
      key: `task-${taskId}-${action}`, label: pendingLabel, confirmation,
    });
    return;
  }
  if (action === 'request-dessert' || action === 'request-water') {
    const type = button.dataset.request;
      post('/api/tables/1/requests', { worker_id: workerId, request_type: type }, {
      key: action, label: pendingLabel, confirmation: `${type[0].toUpperCase() + type.slice(1)} sent to the team.`,
    });
    return;
  }
  if (action === 'not-ready') {
    post('/api/tables/1/correction', { correction: 'not_ready' }, {
      key: action, label: pendingLabel, confirmation: 'Guests marked not ready.',
    });
  }
});

refresh();
setInterval(refresh, 1000);
